from rest_framework import viewsets, permissions, status, parsers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import CursorPagination
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from django.db.models import F, Exists, OuterRef
from django.utils import timezone
from django.contrib.auth import get_user_model
import numpy as np
import json
import redis
import time
import logging
import uuid
import os
from .tasks import process_audio_to_hls, calculate_blended_query_vectors
from .utils import CosineDistance
# Import Models and Serializers
from .models import (
    AudioClip, UserInteraction, SharedClips, Comment
)
from .serializers import (
    AudioUploadSerializer, FeedClipSerializer, 
    CommentSerializer, SharedClipsSerializer, SkipActionSerializer
)

User = get_user_model()

# ---------------------------------------------------------
# 1. PAGINATION CLASSES
# ---------------------------------------------------------
class FeedCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-created_at'

class CommentCursorPagination(CursorPagination):
    page_size = 20
    ordering = '-created_at'

# ---------------------------------------------------------
# 2. MEDIA INGESTION LAYER
# ---------------------------------------------------------
class AudioUploadViewSet(viewsets.ModelViewSet):
    """
    Endpoint: POST /api/v1/clips/
    """
    queryset = AudioClip.objects.all()
    serializer_class = AudioUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clip = serializer.save()

        # TODO: Trigger Celery Task here
        process_audio_to_hls.delay(clip.id)

        headers = self.get_success_headers(serializer.data)
        return Response(
            {
                "message": "Audio uploading and processing in background.",
                "clip_id": clip.id,
                "status": clip.status
            }, 
            status=status.HTTP_202_ACCEPTED, 
            headers=headers
        )

# ---------------------------------------------------------
# 3. FEED & PLAYBACK LAYER
# ---------------------------------------------------------
from django.core.cache import cache
from rest_framework.response import Response

from rest_framework import viewsets, permissions, status, parsers
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import F, Exists, OuterRef, Case, When
from django.core.cache import cache
from pgvector.django import CosineDistance # Fixed import
from .models import AudioClip, UserInteraction, SharedClips, Comment
from .serializers import FeedClipSerializer, SkipActionSerializer
from .tasks import process_audio_to_hls, refill_user_feed

class FastFeedViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user_id = request.user.id
        redis_key = f"user_feed:{user_id}"
        redis_client = cache.client.get_client()

        clip_ids_bytes = redis_client.lpop(redis_key, 10)
        
        if not clip_ids_bytes:
            # Fixed: Calling the correct feed refill task
            refill_user_feed(user_id, count=10) 
            refill_user_feed.delay(user_id, count=40) 
            clip_ids_bytes = redis_client.lpop(redis_key, 10)
            
            if not clip_ids_bytes:
                return Response({"results": [], "message": "You've caught up!"})

        clip_ids = [vid.decode('utf-8') for vid in clip_ids_bytes]

        queue_length = redis_client.llen(redis_key)
        if queue_length < 15:
            refill_user_feed.delay(user_id) # Fixed task

        preserved_order = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(clip_ids)])
        clips = AudioClip.objects.filter(id__in=clip_ids).order_by(preserved_order)

        serializer = FeedClipSerializer(clips, many=True, context={'request': request})
        
        return Response({
            "next": "auto_trigger",
            "queue_health": queue_length,
            "results": serializer.data
        })
class FeedViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Endpoint: GET /api/v1/feed/
    """
    serializer_class = FeedClipSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        user = self.request.user
        
        # Subquery to check if the user has already liked the clip
        user_like_subquery = UserInteraction.objects.filter(
            clip=OuterRef('pk'),
            user=user,
            interaction_type='like'
        )

        return AudioClip.objects.filter(status='ready').annotate(
            user_has_liked=Exists(user_like_subquery)
        )

# ---------------------------------------------------------
# 4. INTERACTION LAYER (Likes & Skips)
# ---------------------------------------------------------

class ClipInteractionViewSet(viewsets.GenericViewSet):
    queryset = AudioClip.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='toggle-like')
    def toggle_like(self, request, pk=None):
        clip = self.get_object()
        user = request.user

        interaction, created = UserInteraction.objects.get_or_create(
            user=user, 
            clip=clip, 
            interaction_type='like',
            defaults={'is_active': True}
        )

        if not created:
            # Fixed: Toggle is_active instead of deleting to preserve history & metrics accurately
            interaction.is_active = not interaction.is_active
            interaction.save()

        status_text = 'liked' if interaction.is_active else 'unliked'
        return Response({'status': status_text}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='register-skip')
    def register_skip(self, request, pk=None):
        clip = self.get_object()
        serializer = SkipActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        listen_duration = serializer.validated_data['listen_duration_ms']
        reel_position = serializer.validated_data['reel_position_ms']
        
        # Calculate completion rate. Assuming max clip length 60s for fallback
        expected_duration = reel_position if reel_position > 0 else 60000 
        completion_rate = min(listen_duration / expected_duration, 1.0)

        UserInteraction.objects.update_or_create(
            user=request.user,
            clip=clip,
            interaction_type='view', # Store explicit view/completion data
            defaults={
                'completion_rate': completion_rate,
                'is_active': True
            }
        )
        return Response({"status": "skip/view registered"}, status=status.HTTP_201_CREATED)

# ---------------------------------------------------------
# 5. COMMUNITY LAYER (Sharing & Inbox)
# ---------------------------------------------------------
class ShareViewSet(viewsets.ModelViewSet):
    serializer_class = SharedClipsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SharedClips.objects.filter(owner=self.request.user)

    @action(detail=True, methods=['post'], url_path='send-share')
    def send_share(self, request, pk=None):
        clip = get_object_or_404(AudioClip, pk=pk)
        user = request.user
        
        receiver_id = request.data.get('receiver_id')
        if not receiver_id:
            return Response({'error': 'Receiver ID required'}, status=status.HTTP_400_BAD_REQUEST)
            
        receiver = get_object_or_404(User, id=receiver_id)

        UserInteraction.objects.get_or_create(
            user=user, 
            clip=clip, 
            interaction_type='share'
        )
        # Model save() override handles the +1 increment automatically

        target_data, _ = SharedClips.objects.get_or_create(owner=receiver)
        
        now = timezone.now()
        new_share = {
            "sender_id": user.id,
            "sender_name": user.username,
            "clip_id": str(clip.id),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
        }
        
        current_received = target_data.received or []
        current_received.append(new_share)
        target_data.received = current_received
        target_data.save()
        
        return Response({'status': 'shared successfully'}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path='share-delete')
    def share_delete(self, request, pk=None):
        user = request.user
        shared_data = get_object_or_404(SharedClips, owner=user)
        
        current_list = shared_data.received or []
        updated_list = [item for item in current_list if str(item.get('clip_id')) != str(pk)]
        
        shared_data.received = updated_list
        shared_data.save()
        
        # We DO NOT decrement AudioClip shares here. Inbox cleanup does not undo the share action.
        return Response({'status': 'deleted from inbox'}, status=status.HTTP_204_NO_CONTENT)

# ---------------------------------------------------------
# 6. COMMENTS LAYER
# ---------------------------------------------------------
class CommentViewSet(viewsets.ModelViewSet):
    """
    Endpoint: GET/POST /api/v1/comments/
    """
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CommentCursorPagination
    
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['clip', 'parent']

    def get_queryset(self):
        return Comment.objects.select_related('author').all()

# ---------------------------------------------------------
# 7. FOLLOWER LAYER
# ---------------------------------------------------------
class FollowViewSet(viewsets.ViewSet):
    """
    Endpoints for following and unfollowing users.
    """
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='toggle-follow')
    def toggle_follow(self, request, pk=None):
        target_user = get_object_or_404(User, pk=pk)
        current_user = request.user
 
        if target_user == current_user:
            return Response({'error': 'You cannot follow yourself.'}, status=status.HTTP_400_BAD_REQUEST)

        if current_user.following.filter(pk=target_user.pk).exists():
            current_user.following.remove(target_user)
            return Response({'status': 'unfollowed'}, status=status.HTTP_200_OK)
        else:
            current_user.following.add(target_user)
            return Response({'status': 'followed'}, status=status.HTTP_201_CREATED)

# ---------------------------------------------------------
# 8. ALGORITHM & SUGGESTION LAYER (Placeholders)
# ---------------------------------------------------------
class SuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Endpoint: GET /api/v1/suggestions/explore/?category=comedy
    """
    serializer_class = FeedClipSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        user = self.request.user
        category = self.request.query_params.get('category')
        
        # Base filter by exact category
        queryset = AudioClip.objects.filter(status='ready', category__iexact=category)
        
        # Get their highly personalized blended vector
        sem_query, ac_query = calculate_blended_query_vectors(user)
        
        if sem_query and ac_query:
            # Sort the category by their specific AI vector preference
            queryset = queryset.annotate(
                combined_distance=(
                    CosineDistance('semantic_vector', sem_query) + 
                    CosineDistance('acoustic_vector', ac_query)
                )
            ).order_by('combined_distance')
            
        # Add the 'user_has_liked' annotation to solve the N+1 query problem
        user_like_subquery = UserInteraction.objects.filter(
            clip=OuterRef('pk'), user=user, interaction_type='like'
        )
        return queryset.annotate(user_has_liked=Exists(user_like_subquery))

class TagsViewSet(viewsets.ViewSet):
    """
    Endpoint: POST /api/v1/tags/initialize/
    Payload: {"selected_tags": ["science", "motivation", "lo-fi"]}
    """
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='initialize')
    def initialize_vectors(self, request):
        user = request.user
        selected_tags = request.data.get('selected_tags', [])
        
        # Find the top 100 most liked clips across those selected tags
        baseline_clips = AudioClip.objects.filter(
            category__in=selected_tags,
            semantic_vector__isnull=False,
            acoustic_vector__isnull=False
        ).order_by('-likes')[:100]
        
        if not baseline_clips:
            return Response({"error": "Not enough data to build baseline."}, status=400)
            
        sem_vectors = [np.array(clip.semantic_vector) for clip in baseline_clips]
        ac_vectors = [np.array(clip.acoustic_vector) for clip in baseline_clips]
        
        # Set the user's Long-Term baseline to the average of their selected tags
        user.long_term_semantic = (np.mean(sem_vectors, axis=0)).tolist()
        user.long_term_acoustic = (np.mean(ac_vectors, axis=0)).tolist()
        user.save()
        
        # Trigger an immediate Redis feed refill using this new baseline
        from .tasks import refill_user_feed
        refill_user_feed.delay(user.id, count=30)
        
        return Response({"status": "Algorithm initialized. Feed is ready."}, status=200)