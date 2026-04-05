import os
import uuid
from cryptography.fernet import Fernet
from django.db import models
from django.db.models import F
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from pgvector.django import VectorField

# 1. Safe Fernet Initialization
# Fallback prevents a crash during initial setup or makemigrations 
# if the environment variable isn't set yet.
FERNET_KEY = os.getenv('FIELD_ENCRYPTION_KEY')
if FERNET_KEY:
    cipher_suite = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)
else:
    cipher_suite = None

# ---------------------------------------------------------
# USER & OWNERSHIP LAYER
# ---------------------------------------------------------
class User(AbstractUser):
    # Encrypted PII
    encrypted_email = models.TextField(unique=True, null=True, blank=True)
    
    # Proper Relational Follower System (Replaces the JSONField approach)
    # symmetrical=False means if A follows B, B doesn't automatically follow A.
    following = models.ManyToManyField('self', symmetrical=False, related_name='followers', blank=True)

    long_term_semantic = VectorField(dimensions=1536, null=True, blank=True)
    long_term_acoustic = VectorField(dimensions=128, null=True, blank=True)

    def set_email(self, raw_email):
        if cipher_suite:
            self.encrypted_email = cipher_suite.encrypt(raw_email.encode()).decode()

    def get_email(self):
        if self.encrypted_email and cipher_suite:
            return cipher_suite.decrypt(self.encrypted_email.encode()).decode()
        return None

class OwnedModel(models.Model):
    # Always use settings.AUTH_USER_MODEL for ForeignKeys to the User model
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name="%(class)s_objects"
    )
    
    class Meta:
        abstract = True

# ---------------------------------------------------------
# CONTENT LAYER
# ---------------------------------------------------------
class AudioClip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='audio_clips')
    
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=50, blank=True)
    
    # The Media Files
    original_file = models.FileField(upload_to='uploads/%Y/%m/%d/', null=True)
    hls_playlist_url = models.CharField(max_length=500, blank=True, null=True)
    
    # Telemetry Data
    likes = models.BigIntegerField(default=0)
    shares = models.BigIntegerField(default=0)
    skips = models.BigIntegerField(default=0) # Renamed to match the field_map
    
    # Intelligence Engine (pgvector)
    # 1536 dimensions matches standard OpenAI Whisper embeddings
    vibe_vector = VectorField(dimensions=1536, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    semantic_vector = VectorField(dimensions=1536, null=True, blank=True)
    acoustic_vector = VectorField(dimensions=128, null=True, blank=True)

    status = models.CharField(max_length=20, default='processing')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} by {self.creator.username}"

# comments 
class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.ForeignKey('AudioClip', on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    
    # Optional: Allows for 1 layer of replies (like Instagram)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    
    text = models.CharField(max_length=500) # Keep it short to prevent massive payloads
    likes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # CRITICAL PERFORMANCE INDEX
        # We will almost always query: "Get comments for Clip X, ordered by newest"
        # This index makes that query instant, even with millions of comments.
        indexes = [
            models.Index(fields=['clip', '-created_at']),
        ]
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # Atomically increment the clip's comment_count when a new comment is created
        if not self.pk and not self.parent: 
            AudioClip.objects.filter(pk=self.clip.pk).update(comment_count=F('comment_count') + 1)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Decrement when deleted
        if not self.parent:
            AudioClip.objects.filter(pk=self.clip.pk).update(comment_count=F('comment_count') - 1)
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"Comment by {self.author.username} on {self.clip.title}"

# ---------------------------------------------------------
# SOCIAL & INBOX LAYER
# ---------------------------------------------------------
class SharedClips(OwnedModel):
    """
    Acts as the inbox. Storing lightweight pointers in JSON is acceptable 
    here to prevent massive relational table bloat for temporary messages.
    """
    sent = models.JSONField(default=list, blank=True)
    received = models.JSONField(default=list, blank=True) # Fixed spelling

class UserInteraction(models.Model):
    TYPES = [
        ('like', 'Like'),
        ('share', 'Share'),
        ('skip', 'Skip'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    clip = models.ForeignKey(AudioClip, on_delete=models.CASCADE)
    interaction_type = models.CharField(max_length=10, choices=TYPES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'clip', 'interaction_type')
        indexes = [
            models.Index(fields=['user', 'interaction_type']),
        ]

    def save(self, *args, **kwargs):
        if not self.pk: 
            field_map = {
                'like': 'likes',
                'share': 'shares',
                'skip': 'skips'
            }
            field_to_update = field_map.get(self.interaction_type)
            
            if field_to_update:
                AudioClip.objects.filter(pk=self.clip.pk).update(
                    **{field_to_update: F(field_to_update) + 1}
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} {self.interaction_type}ed {self.clip.title}"