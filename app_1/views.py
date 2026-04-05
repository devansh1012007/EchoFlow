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

# app_1/views.py
from rest_framework import viewsets, permissions
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Case, When
from .models import AudioClip
from .serializers import FeedClipSerializer
from .tasks import process_audio_to_hls

class FastFeedViewSet(viewsets.ViewSet):
    """
    Endpoint: GET /api/v1/feed/
    Delivers the feed in < 20ms using Redis Hot Queues.
    """
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user_id = request.user.id
        redis_key = f"user_feed:{user_id}"
        redis_client = cache.client.get_client()

        # 1. Pop the next 10 clip IDs from the front of the Redis queue
        # lpop removes them from Redis so they aren't shown again
        clip_ids_bytes = redis_client.lpop(redis_key, 10)
        
        # If queue is completely empty (e.g., first login or heavy usage)
        if not clip_ids_bytes:
            # Force a synchronous refill for 10 items, then async the rest
            process_audio_to_hls(user_id, count=10) # Synchronous call
            process_audio_to_hls.delay(user_id, count=40) # Async background call
            clip_ids_bytes = redis_client.lpop(redis_key, 10)
            
            # If still empty, they've consumed all content on the app
            if not clip_ids_bytes:
                return Response({"results": [], "message": "You've caught up!"})

        # Decode bytes from Redis into standard Python strings
        clip_ids = [vid.decode('utf-8') for vid in clip_ids_bytes]

        # 2. Trigger background refill if queue is running low (< 15 items)
        queue_length = redis_client.llen(redis_key)
        if queue_length < 15:
            process_audio_to_hls.delay(user_id)

        # 3. Fetch from DB and PRESERVE THE REDIS ORDER
        # Using id__in loses the strict algorithmic order from Redis. 
        # We use Case/When to force PostgreSQL to return them in the exact order requested.
        preserved_order = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(clip_ids)])
        
        # Fetch the clips. Because we pre-filtered "seen" clips in Celery, 
        # we can safely assume 'is_liked' is False for this fresh batch, 
        # saving us a massive DB join operation.
        clips = AudioClip.objects.filter(id__in=clip_ids).order_by(preserved_order)

        # 4. Serialize and send
        serializer = FeedClipSerializer(clips, many=True, context={'request': request})
        
        # We simulate CursorPagination behavior so the frontend doesn't break
        return Response({
            "next": "auto_trigger", # Frontend knows to just call /feed/ again when ready
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
    """
    Consolidated viewset for Likes and Skips.
    Endpoints: 
    - POST /api/v1/interactions/{id}/toggle-like/
    - POST /api/v1/interactions/{id}/register-skip/
    """
    queryset = AudioClip.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='toggle-like')
    def toggle_like(self, request, pk=None):
        clip = self.get_object()
        user = request.user

        interaction, created = UserInteraction.objects.get_or_create(
            user=user, 
            clip=clip, 
            interaction_type='like'
        )

        if not created:
            interaction.delete()
            AudioClip.objects.filter(pk=pk).update(likes=F('likes') - 1)
            return Response({'status': 'unliked'}, status=status.HTTP_200_OK)

        # Note: Model save() override handles the +1 increment automatically
        return Response({'status': 'liked'}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='register-skip')
    def register_skip(self, request, pk=None):
        clip = self.get_object()
        user = request.user
        
        serializer = SkipActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        UserInteraction.objects.create(
            user=user,
            clip=clip,
            interaction_type='skip'
        )
        # Model save() override handles the +1 increment automatically

        return Response({"status": "skip registered"}, status=status.HTTP_201_CREATED)

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