import os
import uuid
import logging
from cryptography.fernet import Fernet
from django.db import models
from django.db.models import F
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from pgvector.django import VectorField

logger = logging.getLogger(__name__)

# Safe Fernet Initialization with Production Warning
FERNET_KEY = os.getenv('FIELD_ENCRYPTION_KEY')
if FERNET_KEY:
    cipher_suite = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)
else:
    cipher_suite = None
    logger.critical("CRITICAL: FIELD_ENCRYPTION_KEY is missing. PII (emails) will not be encrypted.")

class User(AbstractUser):
    encrypted_email = models.TextField(unique=True, null=True, blank=True)
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
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name="%(class)s_objects"
    )
    
    class Meta:
        abstract = True

class AudioClip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Changed to match serializer expectation, or adjust serializer. Using creator.
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='audio_clips')
    
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=50, blank=True)
    
    original_file = models.FileField(upload_to='uploads/%Y/%m/%d/', null=True)
    hls_playlist_url = models.CharField(max_length=500, blank=True, null=True)
    
    # Telemetry Data
    likes = models.BigIntegerField(default=0)
    shares = models.BigIntegerField(default=0)
    skips = models.BigIntegerField(default=0)
    comment_count = models.BigIntegerField(default=0) # Added to fix FieldError
    
    # Intelligence Engine - Removed duplicate vibe_vector
    tags = models.JSONField(default=list, blank=True)
    semantic_vector = VectorField(dimensions=1536, null=True, blank=True)
    acoustic_vector = VectorField(dimensions=128, null=True, blank=True)

    status = models.CharField(max_length=20, default='processing')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} by {self.creator.username}"

class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.ForeignKey('AudioClip', on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    text = models.CharField(max_length=500)
    likes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['clip', '-created_at'])]
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.pk and not self.parent: 
            AudioClip.objects.filter(pk=self.clip.pk).update(comment_count=F('comment_count') + 1)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.parent:
            AudioClip.objects.filter(pk=self.clip.pk).update(comment_count=F('comment_count') - 1)
        super().delete(*args, **kwargs)

class SharedClips(OwnedModel):
    sent = models.JSONField(default=list, blank=True)
    received = models.JSONField(default=list, blank=True)

class UserInteraction(models.Model):
    TYPES = [
        ('like', 'Like'),
        ('share', 'Share'),
        ('skip', 'Skip'),
        ('view', 'View') # Added to track explicit views/completions
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    clip = models.ForeignKey(AudioClip, on_delete=models.CASCADE)
    interaction_type = models.CharField(max_length=10, choices=TYPES)
    
    # New fields to fix re-likes and track completion
    is_active = models.BooleanField(default=True)
    completion_rate = models.FloatField(null=True, blank=True) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'clip', 'interaction_type')
        indexes = [models.Index(fields=['user', 'interaction_type'])]

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        state_changed = False
        increment_val = 0

        if is_new:
            state_changed = True
            increment_val = 1 if self.is_active else 0
        else:
            old_instance = UserInteraction.objects.get(pk=self.pk)
            if old_instance.is_active != self.is_active:
                state_changed = True
                increment_val = 1 if self.is_active else -1

        super().save(*args, **kwargs)

        if state_changed and increment_val != 0:
            field_map = {'like': 'likes', 'share': 'shares', 'skip': 'skips'}
            field_to_update = field_map.get(self.interaction_type)
            
            if field_to_update:
                AudioClip.objects.filter(pk=self.clip.pk).update(
                    **{field_to_update: F(field_to_update) + increment_val}
                )