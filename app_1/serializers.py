from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import AudioClip, UserInteraction, ShareEvent, Comment
from rest_framework.validators import UniqueValidator


User = get_user_model()

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'date_joined']
        read_only_fields = ['id', 'date_joined']

class AudioUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = AudioClip
        fields = ['id', 'title', 'category', 'original_file', 'status']
        read_only_fields = ['id', 'status']

    def create(self, validated_data):
        # Bound to creator as defined in models.py
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

class FeedClipSerializer(serializers.ModelSerializer):
    # Fixed from owner.username to creator.username
    creator_name = serializers.CharField(source='creator.username', read_only=True)
    is_liked = serializers.SerializerMethodField()

    class Meta:
        model = AudioClip
        fields = [
            'id', 'title', 'creator_name', 'category',
            'hls_playlist_url', 'likes', 'shares', 'skips', 
            'comment_count', 'is_liked'
        ]
        read_only_fields = [
            'likes', 'shares', 'skips', 'comment_count', 'hls_playlist_url', 'is_liked'
        ]

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
'''
class SharedClipsSerializer(serializers.ModelSerializer):
    received_clips = serializers.JSONField(source='received', read_only=True)
    class Meta:
        model = SharedClips
        fields = ['received_clips']'''


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
    
class InteractionTelemetrySerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=['view', 'like', 'share', 'skip'])
    watch_time_ms = serializers.IntegerField(min_value=0, required=True)

class ShareEventSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    clip_title = serializers.CharField(source='clip.title', read_only=True)
    clip_hls_url = serializers.CharField(source='clip.hls_playlist_url', read_only=True)
    
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