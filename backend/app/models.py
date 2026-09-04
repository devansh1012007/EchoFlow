import os
import uuid
import logging
from django.db import models, transaction
from django.db.models import F
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from pgvector.django import VectorField
from pgvector.django import HnswIndex

logger = logging.getLogger(__name__)


class User(AbstractUser):
    # N3 fix: encrypted_email removed. The previous design encrypted
    # plaintext email on save and stored it in a TextField with unique=True,
    # but: (a) nothing ever decrypted it (no lookup-by-email, no password
    # reset, no admin view), (b) Fernet is non-deterministic (random IV per
    # encrypt) so the unique=True constraint never fired for actual duplicate
    # emails, (c) TagsViewSet.initialize_vectors called user.save() on every
    # vector update, re-encrypting the email each time (wasted UPDATE), and
    # (d) plaintext AbstractUser.email is what RegisterSerializer validates
    # against via UniqueValidator — the encrypted column was misleading
    # theatre that provided zero security benefit.
    #
    # The plaintext email field (inherited from AbstractUser) is the source
    # of truth. It is unique=True at the DB level via Django's auto-generated
    # constraint, and RegisterSerializer's UniqueValidator catches duplicates
    # at the API boundary. For GDPR/privacy, the answer is to use a real
    # encryption-at-rest strategy (column encryption via RDS, or
    # deterministic encryption with HMAC for lookup) — not random-IV Fernet.
    following = models.ManyToManyField('self', symmetrical=False, related_name='followers', blank=True)
    long_term_semantic = VectorField(dimensions=384, null=True, blank=True)
    long_term_acoustic = VectorField(dimensions=128, null=True, blank=True)
    profile_picture = models.ImageField(upload_to='avatars/', null=True, blank=True)



class AudioClip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='audio_clips')
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=50, blank=True)
    
    original_file = models.FileField(upload_to='uploads/%Y/%m/%d/', null=True)
    hls_playlist_url = models.CharField(max_length=500, blank=True, null=True)
    # Provenance and licensing metadata for scraper imports
    source_name = models.CharField(max_length=100, blank=True, null=True)
    source_url = models.CharField(max_length=500, blank=True, null=True)
    license = models.CharField(max_length=100, blank=True, null=True)
    attribution_text = models.CharField(max_length=500, blank=True, null=True)
    imported_via_scraper = models.BooleanField(default=False)
    original_source_id = models.CharField(max_length=255, blank=True, null=True)
    
    # Global Metrics & Telemetry Context
    duration_ms = models.IntegerField(default=0) 
    avg_completion_rate = models.FloatField(default=0.0) 
    engagement_velocity = models.FloatField(default=0.0) 
    
    likes = models.BigIntegerField(default=0)
    shares = models.BigIntegerField(default=0)
    skips = models.BigIntegerField(default=0)
    comment_count = models.BigIntegerField(default=0)
    # DECISION: Added CheckConstraint in Meta to enforce non-negative
    # counters at DB level, protecting against raw SQL and ORM updates. 
    # Tradeoff: Slightly stricter DB writes vs. guaranteed data integrity.
    
    # AI Intelligence (vibe_vector completely removed)
    tags = models.JSONField(default=list, blank=True)
    #semantic_vector = VectorField(dimensions=1536, null=True, blank=True)
    semantic_vector = VectorField(dimensions=384, null=True, blank=True)
    acoustic_vector = VectorField(dimensions=128, null=True, blank=True)

    status = models.CharField(max_length=20, default='processing')
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return f"{self.title} by {self.creator.username}"
    class Meta:
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['status', '-engagement_velocity']),
            models.Index(fields=['category', '-likes']),
            HnswIndex(
                name='semantic_vector_index',
                fields=['semantic_vector'],
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops']
            ),

            # Repeat for acoustic_vector
            HnswIndex(
                name='acoustic_vector_index',
                fields=['acoustic_vector'],
                m=16,
                ef_construction=64,
                opclasses=['vector_cosine_ops']
            ),
        ]
        # DECISION: DB-level constraints prevent negative counter values
        # even via raw SQL or ORM bulk updates. Tradeoff: Migration required.
        constraints = [
            models.CheckConstraint(check=models.Q(likes__gte=0), name='likes_non_negative'),
            models.CheckConstraint(check=models.Q(shares__gte=0), name='shares_non_negative'),
            models.CheckConstraint(check=models.Q(skips__gte=0), name='skips_non_negative'),
            models.CheckConstraint(check=models.Q(comment_count__gte=0), name='comment_count_non_negative'),
        ]

class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.ForeignKey('AudioClip', on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    text = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['clip', '-created_at'])]
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # NOTE: use _state.adding, NOT `not self.pk` — UUID pks with a callable
        # default are assigned at __init__, so self.pk is never None on create.
        if self._state.adding and not self.parent_id:
            AudioClip.objects.filter(pk=self.clip_id).update(comment_count=F('comment_count') + 1)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.parent_id:
            AudioClip.objects.filter(pk=self.clip_id).update(comment_count=F('comment_count') - 1)
        super().delete(*args, **kwargs)

class ShareEvent(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_shares', on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_shares', on_delete=models.CASCADE)
    clip = models.ForeignKey(AudioClip, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['receiver', '-created_at', 'is_read'])]

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
    #created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    watch_time_ms = models.IntegerField(default=0)
    completion_rate = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'clip', 'interaction_type')
        indexes = [models.Index(fields=['user', 'interaction_type'])]

    def save(self, *args, **kwargs):
        # N2 fix: the previous code did the F() counter update OUTSIDE the
        # transaction.atomic() block, opening a race window between
        # releasing the row lock and writing the counter. Two concurrent
        # toggle-like requests for the same (user, clip) could each read
        # the old is_active=True and each bump the counter, double-counting.
        # The fix is to wrap the entire read-decide-write sequence in one
        # transaction.atomic() block. The F() UPDATE is row-atomic at the
        # DB level; we keep it inside the atomic block to document the
        # single-unit-of-work contract.
        #
        # Group B item 9: this is the LEGACY path. The architectural
        # fix is in backend.app.services.counter_store (Redis INCRBY +
        # batched flusher). During Phase 1 of the rollout, the F() and
        # the Redis path both run (dual-write). When
        # ECHOFLOW_DUAL_WRITE_COUNTERS=False is set in compose env, the
        # F() is bypassed here and the flusher becomes the only path.
        # Phase 3 (post-rollout) removes this code entirely.
        is_new = self._state.adding
        state_changed = False
        increment_val = 0
        counter_type = None

        with transaction.atomic():
            if is_new:
                state_changed = True
                increment_val = 1 if self.is_active else 0
            else:
                # Lock the row to prevent concurrent saves from double-counting
                old_instance = UserInteraction.objects.select_for_update().get(pk=self.pk)
                if old_instance.is_active != self.is_active:
                    state_changed = True
                    increment_val = 1 if self.is_active else -1

            super().save(*args, **kwargs)

            # Group B item 9: write to Redis counter store ALWAYS
            # (Phase 1 and Phase 2). In Phase 1 the F() also runs
            # (dual-write). In Phase 2 the F() is bypassed via the
            # dual_write_enabled() check below, and only the Redis
            # counter advances. The flusher moves deltas to Postgres
            # once per beat.
            if state_changed and increment_val != 0:
                from .services import counter_store
                _field_map = {'like': 'likes', 'share': 'shares', 'skip': 'skips'}
                counter_type = _field_map.get(self.interaction_type)
                if counter_type:
                    try:
                        counter_store.increment(
                            str(self.clip.pk), counter_type, increment_val,
                        )
                    except Exception as exc:
                        # SECURITY: never let a metrics/counter hook
                        # break the user-facing write. The counter is
                        # observability; the F() (when dual-write is
                        # on) is the source of truth.
                        logger.debug(
                            "counter_store.increment failed for %s/%s: %s",
                            self.clip.pk, counter_type, exc,
                        )

                # Phase 2 of the Group B item 9 rollout: if the env
                # flag is set, skip the F() update. The flusher is
                # the source of truth. (Phase 1 default: F() runs
                # alongside Redis INCRBY; Phase 3 will remove this
                # whole block.)
                if not counter_store.dual_write_enabled():
                    return

                if counter_type:
                    AudioClip.objects.filter(pk=self.clip.pk).update(
                        **{counter_type: F(counter_type) + increment_val}
                    )