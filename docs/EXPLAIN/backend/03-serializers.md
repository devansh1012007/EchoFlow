# Backend Serializers

## Overview

All serializers defined in `backend/app/serializers.py`. Key responsibilities:
- Input validation (file types, sizes, required fields)
- Output serialization with computed fields (signed URLs, like status)
- Nested relationship handling

---

## Serializer Definitions

### UserProfileSerializer
```python
class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'date_joined']
        read_only_fields = ['id', 'date_joined']
```
Minimal user representation for nested serialization.

---

### AudioUploadSerializer
```python
class AudioUploadSerializer(serializers.ModelSerializer):
    ALLOWED_EXT = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.webm', '.opus'}
    MAX_SIZE = 100 * 1024 * 1024  # 100 MB

    class Meta:
        model = AudioClip
        fields = ['id', 'title', 'category', 'original_file', 'status']
        read_only_fields = ['id', 'status']

    def validate_original_file(self, value):
        if value.size > self.MAX_SIZE:
            raise ValidationError(f"File exceeds {self.MAX_SIZE//1024//1024}MB limit.")
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in self.ALLOWED_EXT:
            raise ValidationError(f"Unsupported file type: {ext}")
        return value

    def create(self, validated_data):
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)
```

**Validation:**
- File size ≤ 100MB
- Extension in allowlist (8 formats)
- **No magic byte validation** — only extension checked (security gap)

**Create:** Binds `creator` from request user.

---

### FeedClipSerializer
**Most critical serializer** — used in feed, suggestions, profile, share inbox.

```python
class FeedClipSerializer(serializers.ModelSerializer):
    creator_name = serializers.CharField(source='creator.username', read_only=True)
    creator_id = serializers.IntegerField(source='creator.id', read_only=True)
    is_liked = serializers.SerializerMethodField()
    hls_playlist_url = serializers.SerializerMethodField()

    class Meta:
        model = AudioClip
        fields = [
            'id', 'title', 'creator_name', 'category',
            'hls_playlist_url', 'likes', 'shares', 'skips', 
            'comment_count', 'is_liked', 'creator_id'
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
```

**Key behaviors:**
- `hls_playlist_url`: Calls `media_urls.get_hls_playback_url()` → **unsigned public URL** for `hls/` prefix
- `is_liked`: 
  - Prefers annotated `user_has_liked` (from queryset `Exists` subquery)
  - Falls back to DB query if not annotated
- All counter fields read-only (updated via `UserInteraction.save()`)

---

### SkipActionSerializer
```python
class SkipActionSerializer(serializers.Serializer):
    listen_duration_ms = serializers.IntegerField(min_value=0, required=True)
    reel_position_ms = serializers.IntegerField(min_value=0, required=True)
    reel_id = serializers.UUIDField(required=True)
```
Used by `/interactions/{id}/register-skip/` (legacy endpoint).

---

### ShareActionSerializer
```python
class ShareActionSerializer(serializers.Serializer):
    receiver_id = serializers.IntegerField(required=True)
```
Used by `/share/{id}/send-share/`.

---

### CommentSerializer
```python
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

    def create(self, validated_data):
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)
```

**Features:**
- `reply_count`: Only for top-level comments
- `create()`: Binds author from request

---

### InteractionTelemetrySerializer
```python
class InteractionTelemetrySerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=['view', 'like', 'share', 'skip'])
    watch_time_ms = serializers.IntegerField(min_value=0, required=True)
```
Used by `/interactions/{id}/log-telemetry/` (preferred endpoint).

---

### ShareEventSerializer
```python
class ShareEventSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    clip_title = serializers.CharField(source='clip.title', read_only=True)
    clip_hls_url = serializers.SerializerMethodField()
    clip = FeedClipSerializer(read_only=True)

    def get_clip_hls_url(self, obj):
        return get_hls_playback_url(obj.clip.hls_playlist_url)

    class Meta:
        model = ShareEvent
        fields = [
            'id', 'sender_name', 'clip', 'clip_title', 'clip_hls_url',
            'created_at', 'is_read'
        ]
```

**Note:** Includes full `FeedClipSerializer` for clip + separate `clip_hls_url` (duplicate but convenient for frontend).

---

### RegisterSerializer
```python
class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )

    class Meta:
        model = User
        fields = ('username', 'password', 'email')
        extra_kwargs = {'password': {'write_only': True}, 'email': {'write_only': True}}

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        return user
```

**Validation:**
- Email required + unique
- Password write-only
- Uses `create_user()` for proper hashing

---

### Profile Serializers

#### PublicProfileSerializer
```python
class PublicProfileSerializer(serializers.ModelSerializer):
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
```
Annotated counts from queryset (`Count` annotations in `ProfileViewSet`).

#### OwnProfileSerializer
```python
class OwnProfileSerializer(serializers.ModelSerializer):
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
            user=obj, interaction_type='like', is_active=True
        ).select_related('clip').order_by('-updated_at')[:50]
        return FeedClipSerializer(
            [i.clip for i in liked],
            many=True,
            context=self.context
        ).data
```
Includes user's liked clips (last 50) serialized with `FeedClipSerializer`.

#### ProfileUpdateSerializer
```python
class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'profile_picture']

    def validate_username(self, value):
        user = self.context['request'].user
        if User.objects.exclude(pk=user.pk).filter(username=value).exists():
            raise ValidationError("Username already taken.")
        return value
```
Only editable fields exposed for PATCH `/profile/me/update/`.

---

## HLS URL Generation Flow

```
AudioClip.hls_playlist_url (DB) = "hls/<uuid>/master.m3u8"  (object key)
        │
        ▼
FeedClipSerializer.get_hls_playlist_url()
        │
        ▼
media_urls.get_hls_playback_url(object_key)
        │
        ├── bucket = settings.STORAGES["default"]["OPTIONS"]["bucket_name"]
        ├── endpoint = settings.PUBLIC_MEDIA_ENDPOINT_URL (browser-facing)
        │
        ▼
Returns: f"{endpoint}/{bucket}/{object_key}"
        │
        ▼
Example: "http://localhost:9000/echoflow-media/hls/abc-123/master.m3u8"
```

**Why not signed URL?**
- HLS = multi-file protocol (master → variants → segments via relative paths)
- Signed URL signature in query string **dropped** on relative reference resolution (RFC 3986)
- `hls/` prefix made public-read via MinIO bucket policy (`mc anonymous set download`)
- Original uploads (`uploads/`) use `get_signed_media_url()` (private, signed)

---

## Validation Gaps

| Serializer | Gap | Risk |
|------------|-----|------|
| `AudioUploadSerializer` | No magic byte validation (python-magic) | Executable renamed to .mp3 passes |
| `SkipActionSerializer` | `reel_id` not validated against URL clip_id | Mismatch possible |
| `RegisterSerializer` | No password strength validation | Weak passwords allowed |
| All | No request size limit at serializer level | Large payloads hit Django middleware |

---

*Source: `backend/app/serializers.py`, `backend/app/media_urls.py`, `backend/EchoFlow/settings.py`*