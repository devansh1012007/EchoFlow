import os
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .media_urls import get_hls_playback_url
from .models import AudioClip, UserInteraction, ShareEvent, Comment
from rest_framework.validators import UniqueValidator


User = get_user_model()

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'date_joined']
        read_only_fields = ['id', 'date_joined']

class AudioUploadSerializer(serializers.ModelSerializer):
    # DECISION: Added file type/size validation at serializer boundary
    # rather than model level to provide fast feedback to client.
    # Tradeoff: Slightly more code in serializer vs. guaranteed validation.
    ALLOWED_EXT = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.webm', '.opus'}
    MAX_SIZE = 100 * 1024 * 1024  # 100 MB
    # SECURITY: Magic-byte MIME allowlist. An attacker can rename evil.exe
    # to evil.mp3 and bypass extension-only checks. python-magic reads the
    # first ~1KB of the file and returns the inferred MIME type. We accept
    # only audio/* MIME types. If libmagic is unavailable, fall back to
    # extension-only (with a logged warning) so the service doesn't break
    # on minimal Docker images.
    ALLOWED_MIMES = frozenset({
        'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav',
        'audio/wave', 'audio/x-vorbis+ogg', 'audio/ogg', 'audio/flac',
        'audio/x-flac', 'audio/mp4', 'audio/aac', 'audio/x-m4a',
        'audio/webm', 'audio/opus',
    })

    class Meta:
        model = AudioClip
        fields = ['id', 'title', 'category', 'original_file', 'status']
        # N8 fix: original_file is read-only after creation. PATCH that
        # swaps the file silently leaves hls_playlist_url and status
        # pointing at the OLD (stale) HLS — the new file is stored but
        # never transcribed, vectorized, or transcoded. To replace an
        # upload, the user must delete the clip and create a new one.
        # If product needs in-place replace, add an explicit
        # /clips/{id}/replace-file/ @action (not via PATCH).
        read_only_fields = ['id', 'status', 'original_file']

    def validate_original_file(self, value):
        if value.size > self.MAX_SIZE:
            raise serializers.ValidationError(f"File exceeds {self.MAX_SIZE//1024//1024}MB limit.")
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in self.ALLOWED_EXT:
            raise serializers.ValidationError(f"Unsupported file type: {ext}")
        # Magic-byte sniff: read the first 8KB and check the MIME.
        # Done at upload time (not deferred to ffmpeg) so the malicious
        # file is rejected before it ever lands in object storage.
        #
        # libmagic is unreliable for truncated audio frames (it returns
        # application/octet-stream for valid MP3/OGG headers without
        # enough frame data to identify the codec). The check below
        # rejects anything that libmagic confidently identifies as
        # non-audio (PE/ELF/scripts/etc) but accepts octet-stream for
        # allowed extensions, since the real validation happens at
        # process_audio_to_hls via ffmpeg.
        try:
            import magic
            head = value.read(8192)
            value.seek(0)
            mime = magic.from_buffer(head, mime=True)
            if mime and not mime.startswith('audio/') and mime != 'application/octet-stream':
                raise serializers.ValidationError(
                    f"File content does not match audio format. Detected: {mime}"
                )
        except ImportError:
            # python-magic not installed — log and fall back to extension check.
            import logging
            logging.getLogger(__name__).warning(
                "python-magic unavailable; magic-byte validation skipped"
            )
        return value

    def create(self, validated_data):
        # Bound to creator as defined in models.py
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

class FeedClipSerializer(serializers.ModelSerializer):
    # Fixed from owner.username to creator.username
    creator_name = serializers.CharField(source='creator.username', read_only=True)
    creator_id = serializers.IntegerField(source='creator.id', read_only=True)
    is_liked = serializers.SerializerMethodField()
    # `AudioClip.hls_playlist_url` stores a relative object-storage KEY
    # (e.g. "hls/<clip_id>/master.m3u8"), not a servable URL — the bucket is
    # private, so a real playable URL has to be signed fresh on every read.
    # A signed URL persisted in the DB would silently expire after
    # AWS_S3_QUERYSTRING_EXPIRE regardless of whether the clip is still
    # valid, so we generate it here instead of trusting the stored field.
    hls_playlist_url = serializers.SerializerMethodField()

    class Meta:
        model = AudioClip
        fields = [
            'id', 'title', 'creator_name', 'category',
            'hls_playlist_url', 'likes', 'shares', 'skips', 
            'comment_count', 'is_liked','creator_id'
        ]
        read_only_fields = [
            'likes', 'shares', 'skips', 'comment_count', 'hls_playlist_url', 'is_liked'
        ]

    def get_hls_playlist_url(self, obj):
        return get_hls_playback_url(obj.hls_playlist_url)

    def get_is_liked(self, obj):
        if hasattr(obj, 'user_has_liked'):
            return obj.user_has_liked
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return UserInteraction.objects.filter(
            user=request.user, clip=obj, interaction_type='like', is_active=True
        ).exists()

class SkipActionSerializer(serializers.Serializer):
    listen_duration_ms = serializers.IntegerField(min_value=0, required=True)
    reel_position_ms = serializers.IntegerField(min_value=0, required=True)
    reel_id = serializers.UUIDField(required=True)

class ShareActionSerializer(serializers.Serializer):
    receiver_id = serializers.IntegerField(required=True)


class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ['id', 'clip', 'author_username', 'parent', 'text', 'likes', 'reply_count', 'created_at']
        read_only_fields = ['id', 'author_username', 'likes', 'reply_count', 'created_at']

    def get_reply_count(self, obj):
        if not obj.parent_id:
            return obj.replies.count()
        return 0

    def validate_text(self, value):
        # SECURITY: strip control characters (NUL, BEL, etc.) from
        # comment text before storage. The React frontend auto-escapes
        # JSON strings, so a stored `<script>` payload is rendered as
        # text — but defense-in-depth: reject obviously malicious
        # characters server-side too. Limit to 500 chars (model
        # CharField max_length) and reject NUL bytes which can break
        # downstream loggers.
        if '\x00' in value:
            raise serializers.ValidationError("Comment contains null bytes.")
        # Strip ASCII control characters except common whitespace (\t, \n, \r).
        cleaned = ''.join(
            ch for ch in value
            if ch >= ' ' or ch in '\t\n\r'
        )
        return cleaned.strip()

    def create(self, validated_data):
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)
    
class InteractionTelemetrySerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=['view', 'like', 'share', 'skip'])
    # SECURITY: cap watch_time_ms at 10 hours = 36,000,000ms. Anything
    # longer is a client bug or a viewbot inflating completion_rate.
    # Real short-form audio is < 5 min (300,000ms); 10h is a generous
    # upper bound for any legitimate use.
    watch_time_ms = serializers.IntegerField(min_value=0, max_value=36_000_000, required=True)

class ShareEventSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    clip_title = serializers.CharField(source='clip.title', read_only=True)
    # See FeedClipSerializer.get_hls_playlist_url — same reasoning: the model
    # field is a storage key, not a URL, so it must be signed here rather
    # than passed through as a plain CharField.
    clip_hls_url = serializers.SerializerMethodField()
    clip = FeedClipSerializer(read_only=True)

    def get_clip_hls_url(self, obj):
        return get_hls_playback_url(obj.clip.hls_playlist_url)
    
    class Meta:
        model = ShareEvent
        fields = [
            'id', 
            'sender_name', 
            'clip',
            'clip_title',
            'clip_hls_url',
            'created_at', 
            'is_read'
        ]


class RegisterSerializer(serializers.ModelSerializer):
    # Ensure email is unique and required
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )

    class Meta:
        model = User #built-in User model
        fields = ('username', 'password', 'email')
        # Ensure password is never returned in a GET request
        extra_kwargs = {'password': {'write_only': True}, 'email': {'write_only': True}}

    def create(self, validated_data):
        # .create_user() handles password hashing automatically
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        return user

class PublicProfileSerializer(serializers.ModelSerializer):
    """For viewing any user's profile"""
    followers_count = serializers.IntegerField(read_only=True)
    following_count = serializers.IntegerField(read_only=True)
    uploads_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'profile_picture',
            'followers_count', 'following_count', 'uploads_count',
            'date_joined'
        ]

class OwnProfileSerializer(serializers.ModelSerializer):
    """For the logged-in user's own profile — includes private data"""
    followers_count = serializers.IntegerField(read_only=True)
    following_count = serializers.IntegerField(read_only=True)
    uploads_count = serializers.IntegerField(read_only=True)
    liked_clips = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'profile_picture',
            'followers_count', 'following_count', 'uploads_count',
            'liked_clips', 'date_joined'
        ]

    def get_liked_clips(self, obj):
        liked = UserInteraction.objects.filter(
            user=obj,
            interaction_type='like',
            is_active=True
        ).select_related('clip').order_by('-updated_at')[:50]
        return FeedClipSerializer(
            [i.clip for i in liked],
            many=True,
            context=self.context
        ).data

class ProfileUpdateSerializer(serializers.ModelSerializer):
    """For PATCH — only editable fields exposed"""
    class Meta:
        model = User
        fields = ['username', 'profile_picture']

    def validate_username(self, value):
        user = self.context['request'].user
        if User.objects.exclude(pk=user.pk).filter(username=value).exists():
            raise serializers.ValidationError("Username already taken.")
        return value