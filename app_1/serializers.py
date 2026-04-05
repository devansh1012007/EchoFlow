from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import AudioClip, UserInteraction, ShareEvent, Comment

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
    
    class Meta:
        model = ShareEvent
        fields = ['id', 'sender_name', 'clip', 'created_at', 'is_read']