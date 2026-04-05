from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from rest_framework.fields import CurrentUserDefault
from .models import (
    User,AudioClip,UserData,SavedList,
    Tags,Suggestions,LikedList,SharedClips,Comment
)
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import AudioClip, UserInteraction, SharedClips

User = get_user_model()

# ---------------------------------------------------------
# 1. USER & AUTHENTICATION LAYER
# ---------------------------------------------------------
class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializes the user's public and safe private data.
    Never expose the encrypted email or password hash here.
    """
    class Meta:
        model = User
        fields = ['id', 'username', 'date_joined']
        read_only_fields = ['id', 'date_joined']


# ---------------------------------------------------------
# 2. MEDIA INGESTION LAYER (Uploads)
# ---------------------------------------------------------
class AudioUploadSerializer(serializers.ModelSerializer):
    """
    Handles the multipart/form-data upload from the creator.
    """
    class Meta:
        model = AudioClip
        fields = ['id', 'title', 'category', 'original_file', 'status']
        
        # The user can only upload the file and set metadata.
        # The status is controlled exclusively by the Celery worker.
        read_only_fields = ['id', 'status']

    def create(self, validated_data):
        # Automatically assign the logged-in user as the owner
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)


# ---------------------------------------------------------
# 3. THE FEED LAYER (The "Trance" Engine)
# ---------------------------------------------------------
class FeedClipSerializer(serializers.ModelSerializer):
    """
    The highly optimized serializer for the main scrolling feed.
    """
    creator_name = serializers.CharField(source='owner.username', read_only=True)
    
    # We dynamically calculate if the requesting user has already liked this clip
    is_liked = serializers.SerializerMethodField()

    class Meta:
        model = AudioClip
        fields = [
            'id', 
            'title', 
            'creator_name', 
            'category',
            'hls_playlist_url', 
            'likes', 
            'shares', 
            'skips', 
            'is_liked'
        ]
        
        # Absolute lockout: The frontend cannot mutate these metrics directly.
        read_only_fields = [
            'likes', 'shares', 'skips', 'hls_playlist_url', 'is_liked'
        ]

    def get_is_liked(self, obj):
        """
        Determines if the heart icon should be red when the UI loads.
        """
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False

        # PERFORMANCE TRAP AVOIDANCE:
        # Instead of querying the database for every single clip in the feed 
        # (which causes the N+1 query problem), we check an annotated attribute.
        # Your views.py MUST annotate 'user_has_liked' onto the queryset.
        if hasattr(obj, 'user_has_liked'):
            return obj.user_has_liked
            
        # Fallback (slower, but safe)
        return UserInteraction.objects.filter(
            user=request.user, clip=obj, interaction_type='like'
        ).exists()


# ---------------------------------------------------------
# 4. INTERACTION PAYLOAD VALIDATORS
# ---------------------------------------------------------
class SkipActionSerializer(serializers.Serializer):
    """
    Validates the data sent when a user skips a clip.
    """
    listen_duration_ms = serializers.IntegerField(min_value=0, required=True)
    reel_position_ms = serializers.IntegerField(min_value=0, required=True)
    reel_id = serializers.UUIDField(required=True)


class ShareActionSerializer(serializers.Serializer):
    """
    Validates the target user ID when sharing to an inbox.
    """
    receiver_id = serializers.IntegerField(required=True)


# ---------------------------------------------------------
# 5. COMMUNITY & INBOX LAYER
# ---------------------------------------------------------
class InboxSerializer(serializers.ModelSerializer):
    """
    Formats the user's inbox array.
    """
    # Note: Corrected the spelling from your earlier code from 'recived' to 'received'
    # Ensure your models.py is updated to match this correct spelling.
    received_clips = serializers.JSONField(source='received', read_only=True)

    class Meta:
        model = SharedClips
        fields = ['received_clips']

# comments
class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = [
            'id', 
            'clip', 
            'author_username', 
            'parent', 
            'text', 
            'likes', 
            'reply_count', 
            'created_at'
        ]
        # Prevent users from hacking the metrics
        read_only_fields = ['id', 'author_username', 'likes', 'reply_count', 'created_at']

    def get_reply_count(self, obj):
        # Only count replies if this is a top-level comment
        if not obj.parent_id:
            return obj.replies.count()
        return 0

    def create(self, validated_data):
        # Automatically assign the logged-in user as the author
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)