from rest_framework import viewsets, permissions, status, parsers, generics
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
from rest_framework.permissions import AllowAny
from django.core.cache import cache
from rest_framework.response import Response
from django.db.models import Count
from rest_framework import viewsets, permissions, status, parsers
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import F, Exists, OuterRef, Case, When
from django.core.cache import cache
from pgvector.django import CosineDistance # Fixed import
from .models import AudioClip, UserInteraction, ShareEvent, Comment
from .serializers import FeedClipSerializer, SkipActionSerializer, InteractionTelemetrySerializer, CommentSerializer, ShareEventSerializer
from .tasks import process_audio_to_hls, refill_user_feed
from .tasks import process_audio_to_hls, calculate_time_decayed_vectors
from django.db import transaction
# Import Models and Serializers
from .models import (
    AudioClip, UserInteraction, ShareEvent, Comment
)
from .serializers import (
    AudioUploadSerializer, FeedClipSerializer, 
    CommentSerializer, ShareEventSerializer, SkipActionSerializer,
    InteractionTelemetrySerializer,RegisterSerializer,PublicProfileSerializer, 
    OwnProfileSerializer, ProfileUpdateSerializer
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
    REST API endpoint for uploading new audio clips.
    
    ENDPOINT: POST /clips/
    
    PURPOSE:
    - Accepts audio files from authenticated users
    - Creates AudioClip database record with 'processing' status
    - Triggers async Celery task for AI-powered pipeline
    
    ASYNC PROCESSING PIPELINE (happens in background):
    1. Acoustic feature extraction (128-dim vector)
    2. Audio transcription (Whisper API)
    3. Semantic embeddings (OpenAI text-embedding-3-small) 
    4. Automated tagging (GPT-3.5 LLM)
    5. HLS video encoding (FFmpeg with 3 bitrate variants)
    
    RECOMMENDATION SYSTEM INTEGRATION:
    - Once processing completes (status='ready'), the clip is eligible for recommendations
    - Acoustic + Semantic vectors enable hybrid vector-based matching
    - Tags enable category-based exploration and cold-start recommendations
    
    REQUEST PAYLOAD:
    {
        "title": "My Audio Clip",
        "category": "music",
        "original_file": <audio file>
    }
    
    RESPONSE (202 Accepted - async processing):
    {
        "message": "Audio uploading and processing in background.",
        "clip_id": "550e8400-e29b-41d4-a716-446655440000",
        "status": "processing"
    }
    """
    queryset = AudioClip.objects.all()
    serializer_class = AudioUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    def get_queryset(self):
        return AudioClip.objects.filter(creator=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clip = serializer.save()
        # fist v need to make sure the clip is on small size
        
        transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))

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

class FastFeedViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user_id = request.user.id
        redis_key = f"user_feed:{user_id}"
        redis_client = cache.client.get_client()

        clip_ids_bytes = redis_client.lpop(redis_key, 10)
        
        if not clip_ids_bytes:
            # Fixed: Calling the correct feed refill task
            refill_user_feed.delay(user_id, count=10) 
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
'''class FeedViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Fallback slow-but-reliable feed endpoint for traditional pagination.
    
    ENDPOINT: GET /api/v1/feed/?cursor=xxx
    
    DIFFERENCE FROM FastFeedViewSet:
    - Uses database queries (NOT Redis cache)
    - Computes recommendations on-demand (slower: 500-1000ms)
    - Guaranteed freshness (no pre-computed queue staleness)
    - Better for discovery/explore sections
    
    RECOMMENDATION QUALITY:
    - Applies same cosine/vector matching as FastFeedViewSet
    - NO engagement velocity boost (fair, quality-only ranking)
    - Cursor pagination allows deep pagination without memory bloat
    
    OPTIMIZATION: N+1 Query Prevention
    - Uses .annotate(user_has_liked=Exists(...)) to solve N+1 problem
    - Single query fetches clips + like status in one join
    - Performance: ~50-100ms query time for typical page
    
    RESPONSE PAYLOAD (paginated):
    {
        "next": "cD0xODk2...",  # Opaque cursor for next page
        "previous": null,
        "results": [
            {
                "id": "clip_uuid",
                "title": "Clip Title",
                "creator_name": "username",
                "is_liked": false,
                ...
            }
        ]
    }
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
            interaction_type='like',
            is_active=True
        )

        return AudioClip.objects.filter(status='ready').annotate(
            user_has_liked=Exists(user_like_subquery)
        )
'''
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
            # This preserves the timestamp for time_decay weighting while allowing re-likes
            interaction.is_active = not interaction.is_active
            interaction.save()

        status_text = 'liked' if interaction.is_active else 'unliked'
        return Response({'status': status_text}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='register-skip')
    def register_skip(self, request, pk=None):
        """
        Register skip or view completion data.
        
        ENDPOINT: POST /api/v1/interactions/{clip_id}/register-skip/
        
        PURPOSE:
        - Records how far user listened before skipping
        - Calculates completion_rate as a signal for recommendation weighting
        - Creates 'view' interaction (not 'skip' - naming is for historical reasons)
        
        CALCULATION:
        completion_rate = min(listen_duration_ms / reel_position_ms, 1.0)
        - listen_duration_ms: Actual playback time (e.g., 15,000ms = 15 seconds)
        - reel_position_ms: Total duration of clip (e.g., 60,000ms = 60 seconds)
        - Capping at 1.0 prevents >100% completion (network delays)
        
        SIGNAL TO RECOMMENDATION ENGINE:
        - comp_weight = completion_rate in calculate_time_decayed_vectors()
        - 10% listen → weight ≈ 0.1 (weak signal, user didn't find it interesting)
        - 50% listen → weight = 0.5 (moderate signal)
        - 100% listen → weight = 1.0 (strong signal, user fully engaged)
        
        COMBINATION WITH OTHER SIGNALS:
        - completion_rate is MULTIPLIED with time_weight and intent_weight
        - A recent like with 100% completion = strongest possible signal
        - An old skip with 10% completion = weak negative signal
        
        REQUEST PAYLOAD:
        {
            "listen_duration_ms": 45000,
            "reel_position_ms": 60000,
            "reel_id": "550e8400-e29b-41d4-a716-446655440000"
        }
        
        RESPONSE:
        {
            "status": "skip/view registered"
        }
        """
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
    
    @action(detail=True, methods=['post'], url_path='log-telemetry')
    def log_telemetry(self, request, pk=None):
        """
        Log detailed watch-time telemetry and update interaction metrics.
        
        ENDPOINT: POST /api/v1/interactions/{clip_id}/log-telemetry/
        
        PURPOSE:
        - More flexible version of register_skip (supports view/like/share/skip)
        - Records watch_time_ms with action_type (gives better data quality)
        - Calculates completion_rate server-side (prevents client manipulation)
        
        DIFFERENCE FROM register_skip:
        register_skip:
        - Expects listen_duration_ms and reel_position_ms (client provides both)
        - Assumes max 60s for fallback (crude)
        - Stores as 'view' interaction only
        
        log_telemetry (recommended):
        - Single watch_time_ms input (cleaner API)
        - Uses actual clip.duration_ms from database (more accurate)
        - action_type can be 'view', 'like', 'share', 'skip' (flexible)
        - Server-side calculation prevents timeline fraud (user claims 100% when they didn't)
        
        RECOMMENDATION SIGNALS:
        - completion_rate fed into comp_weight calculation
        - action_type fed into intent_weight calculation:
          * 'like' → intent_weight = 1.5x (strong explicit signal)
          * 'share' → intent_weight = 1.5x (strongest signal)
          * 'view' → intent_weight = 1.0x (neutral/default)
          * 'skip' + low completion → intent_weight = -0.5x (negative signal)
        
        REQUEST PAYLOAD:
        {
            "action_type": "view",  # or "like", "share", "skip"
            "watch_time_ms": 45000  # Milliseconds user watched
        }
        
        RESPONSE:
        {
            "status": "telemetry logged"
        }
        """
        clip = self.get_object()
        user = request.user
        
        serializer = InteractionTelemetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        watch_time_ms = serializer.validated_data['watch_time_ms']
        action_type = serializer.validated_data['action_type']
        
        # Calculate completion rate securely on the backend
        # Uses actual clip duration from database, prevents client manipulation
        clip_duration = max(clip.duration_ms, 1) # Prevent division by zero
        completion_rate = min(watch_time_ms / clip_duration, 1.0)

        # Log or update the interaction with telemetry
        interaction, created = UserInteraction.objects.update_or_create(
            user=user,
            clip=clip,
            interaction_type=action_type,
            defaults={
                'watch_time_ms': watch_time_ms,
                'completion_rate': completion_rate,
                'is_active': True 
            }
        )

        return Response({"status": "telemetry logged"}, status=status.HTTP_201_CREATED)
# ---------------------------------------------------------
# 5. COMMUNITY LAYER (Sharing & Inbox)
# ---------------------------------------------------------
class ShareViewSet(viewsets.ModelViewSet):
    """
    Social sharing system: Users can send clips to each other and track sent/received clips.
    
    ENDPOINTS:
    - GET /api/v1/share/ - Get user's shared clips inbox
    - POST /api/v1/share/{clip_id}/send-share/ - Send a clip to another user
    - DELETE /api/v1/share/{clip_id}/share-delete/ - Remove from inbox
    
    PURPOSE:
    - Enables peer-to-peer content discovery and viral growth
    - Tracks who shared what (for social analytics)
    - Separate from likes: sharing is a higher-intent signal (friend endorsement)
    
    RECOMMENDATION IMPACT:
    - Shares create 'share' interactions with intent_weight = 1.5x (STRONGEST signal)
    - Indicates user believes clip is valuable enough to recommend to friends
    - Higher weight than likes in calculate_time_decayed_vectors()
    - Contributes to engagement_velocity metric (shares × 2 in formula)
    
    DATABASE STRUCTURE:
    - SharedClips has 'sent' and 'received' JSONField arrays
    - Each entry: {sender_id, sender_name, clip_id, date, time}
    - Enables efficient inbox queries without separate join tables
    """
    serializer_class = ShareEventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ShareEvent.objects.filter(receiver=self.request.user)

    @action(detail=True, methods=['post'], url_path='send-share')
    def send_share(self, request, pk=None):
        """
        Send a clip to another user (friend share).
        
        ENDPOINT: POST /api/v1/share/{clip_id}/send-share/
        
        LOGIC:
        1. Sender creates a 'share' UserInteraction (triggers share count increment)
        2. Create/get SharedClips for receiver if doesn't exist
        3. Append new_share dict to receiver's 'received' array
        4. Save with timestamp for inbox UI display
        
        RECOMMENDATION SIGNALS:
        - Creates UserInteraction with interaction_type='share'
        - intent_weight = 1.5x in calculate_time_decayed_vectors() (HIGHEST weight!)
        - Contributes to engagement_velocity: (likes + shares*2) / time^1.5
          * Shares weighted 2x over likes (friend endorsement > personal like)
        - Indicates strong user approval (willing to put reputation on line)
        
        SOCIAL GRAPH IMPLICATIONS:
        - Receiver doesn't automatically follow sender (prevents spam)
        - But share appears in receiver's inbox for discovery
        - Enables serendipitous content discovery via friend networks
        
        REQUEST PAYLOAD:
        {
            "receiver_id": 123
        }
        
        RESPONSE:
        {
            "status": "shared successfully"
        }
        """
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
        '''    
        target_data, _ = ShareEvent.objects.get_or_create(receiver=receiver)
        
        now = timezone.now()
        new_share = {
            "sender_id": user.id,
            "sender_name": user.username,
            "clip_id": str(clip.id),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
        }
        ShareEvent.objects.create(sender=user, receiver=receiver, clip=clip)
        UserInteraction.objects.update_or_create(
            user=user, clip=clip, interaction_type='share',
            defaults={'is_active': True}
        )
        
        current_received = target_data.received or []
        current_received.append(new_share)
        target_data.received = current_received
        target_data.save()
        '''
        return Response({'status': 'shared successfully'}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path='share-delete')
    def share_delete(self, request, pk=None):
        ShareEvent.objects.filter(
            pk=pk, 
            receiver=request.user
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    """
    @action(detail=True, methods=['delete'], url_path='share-delete')
    def share_delete(self, request, pk=None):
        user = request.user
        ShareEvent.objects.filter(pk=pk, receiver=request.user).delete()
        # We DO NOT decrement AudioClip shares here. Inbox cleanup does not undo the share action.
        return Response({'status': 'deleted from inbox'}, status=status.HTTP_204_NO_CONTENT)
    """

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        ShareEvent.objects.filter(pk=pk, receiver=request.user).update(is_read=True)
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=False, methods=['get'], url_path='inbox')
    def inbox(self, request):
        shares = ShareEvent.objects.filter(
            receiver=request.user
        ).select_related('sender', 'clip').order_by('-created_at')

        serializer = ShareEventSerializer(shares, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = ShareEvent.objects.filter(
            receiver=request.user, 
            is_read=False
        ).count()
        return Response({'unread': count})
# ---------------------------------------------------------
# 6. COMMENTS LAYER
# ---------------------------------------------------------
class CommentViewSet(viewsets.ModelViewSet):
    """
    REST API for creating, viewing, and managing nested comments on clips.
    
    ENDPOINTS:
    - GET /api/v1/comments/?clip={clip_id} - Fetch all comments for a clip
    - POST /api/v1/comments/ - Create new comment
    - DELETE /api/v1/comments/{comment_id}/ - Delete a comment
    
    PURPOSE:
    - Community engagement: Users discuss and share opinions on clips
    - Nested replies: Comments can have parent-child relationships (threaded)
    - Engagement metric: comment_count contributes to clip popularity
    
    RECOMMENDATION SYSTEM CONSIDERATIONS:
    - Comment count is NOT directly used in recommendation algorithm
    - But high comment-count clips signal engagement (cultural indicator)
    - Could be extended to use comment sentiment for future iterations
    - Currently collected for analytics, not ML
    
    FILTERING:
    - Filter by clip: GET /api/v1/comments/?clip={uuid}
    - Filter by parent: GET /api/v1/comments/?parent={comment_id} (get replies)
    
    OPTIMIZATION:
    - Uses .select_related('author') to prevent N+1 queries
    - Cursor pagination for efficient deep pagination
    - Indexes on clip + created_at for fast filtering
    
    MODEL MECHANICS:
    - Comment.save() auto-increments AudioClip.comment_count on creation
    - Comment.delete() auto-decrements AudioClip.comment_count
    - Parent column enables thread nesting (replies to replies)
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
    Social graph management: Users can follow/unfollow other users.
    
    ENDPOINT: POST /api/v1/follow/{user_id}/toggle-follow/
    
    PURPOSE:
    - Enables users to subscribe to specific creators
    - Builds social graph for potential future use cases
    - Stored but NOT currently used in recommendation algorithm
    
    CURRENT USAGE:
    - Follow relationship is tracked in User.following (ManyToMany)
    - Enables features like "followers' recent uploads" (not yet implemented)
    - Foundation for future social-based recommendations
    
    NOT CURRENTLY IN RECOMMENDATION ENGINE:
    - Algorithm uses user vectors (semantic/acoustic), not social graph
    - Could be integrated in future: "Users similar to my followers"
    - Current focus: content-based similarity, not collaborative filtering
    
    HOW IT WORKS:
    - Toggle operation: follow if not following, unfollow if already following
    - Prevents self-follow (sanity check)
    - ManyToMany relationship is symmetrical-friendly (User.following and .followers)
    
    REQUEST PAYLOAD:
    {} (empty body)
    
    RESPONSE:
    {
        "status": "followed"  OR  "unfollowed"
    }
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
# for searh and explore feed
class SuggestionViewSet(viewsets.ReadOnlyModelViewSet):

    """
    Category-specific recommendations using user's blended preference vectors.
    
    ENDPOINT: GET /api/v1/suggestions/explore/?category=comedy
    
    PURPOSE:
    - "Explore by Category" feature: Give user personalized picks within a category
    - Combines category filtering with AI vector-based ranking
    - Similar to main feed, but narrower scope (single category)
    
    ALGORITHM: Category-Scoped Vector Similarity (CSVS)
    
    STAGE 1: CATEGORY FILTERING
    - Accepts ?category query parameter (e.g., "comedy", "music", "education")
    - Uses case-insensitive matching (iexact)
    - Base queryset = all 'ready' clips in category
    
    STAGE 2: PERSONALIZATION (if user has vectors)
    - Calls calculate_blended_query_vectors(user):
      * Gets user's current semantic + acoustic preference vectors
      * These vectors embody "what this user is currently in the mood for"
      * Blends 70% recent (7-day) context with 30% long-term baseline
    
    STAGE 3: VECTOR RANKING (within category)
    - Sorts by combined_distance (sum of semantic + acoustic cosine distance)
    - Lower distance = closer match = higher ranking
    - DOES NOT include engagement_velocity (unlike main feed)
    - Rationale: Trust user's taste in small category, not just trending
    
    STAGE 4: N+1 OPTIMIZATION
    - Uses Exists(...) subquery to annotate user_has_liked in single query
    - Fetches like status alongside clip data (no separate requests per clip)
    
    COLD START (No user vectors):
    - Falls back to natural C order (no sorting applied)
    - User still gets category-filtered results
    
    CONTRAST TO MAIN FEED (FastFeedViewSet):
    Main Feed: Global ML ranking (semantic + acoustic + engagement_velocity + explore)
    Suggestions: Category-scoped ranking (semantic + acoustic only, no velocity)
    
    REQUEST:
    GET /suggestions/?category=music&cursor=xyz
    
    RESPONSE (paginated):
    {
        "next": "cursor_xyz...",
        "results": [
            {
                "id": "clip_uuid",
                "title": "...",
                "category": "music",
                "is_liked": false
            }
        ]
    }
    """
    serializer_class = FeedClipSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        user = self.request.user
        category = self.request.query_params.get('category')
        
        # Base filter by exact category
        queryset = AudioClip.objects.filter(status='ready', category=category)
        
        # Get their highly personalized blended vector
        sem_query, ac_query = calculate_time_decayed_vectors(user)
        
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

# mood based feed initialization and refresh
class TagsViewSet(viewsets.ViewSet):
    """
    Cold-start onboarding: Initialize user preferences from tag selection.
    
    ENDPOINT: POST /tags/
    
    PURPOSE:
    - First-time user onboarding: "Pick your favorite genres"
    - Solves cold-start problem: no interaction history for new users
    - Bootstraps long_term_semantic and long_term_acoustic with curated vectors
    
    HOW IT WORKS:
    1. User selects 3-5 tags on signup/onboarding (e.g., ["jazz", "comedy", "education"])
    2. Frontend POSTs selected_tags to this endpoint
    3. Backend fetches all 'ready' clips matching those tags
    4. Computes average semantic + acoustic vectors from matching clips
    5. Stores as user.long_term_semantic and user.long_term_acoustic
    6. Future recommendations use these as baseline until user generates interactions
    
    ALGORITHM: Tag-Based Vector Bootstrapping
    - Unlike calculate_time_decayed_vectors() which needs interaction history
    - This uses clip.tags directly (generated during process_audio_to_hls)
    - Enables personalization from day 1 (no cold-start wait)
    
    VECTOR INITIALIZATION:
    - Semantic: Average of 1536-dim embeddings from matching clips
    - Acoustic: Average of 128-dim audio features from matching clips
    - Both normalized for cosine similarity
    
    IMPACT ON RECOMMENDATIONS:
    - NextRefill will use these vectors as baseline
    - 70% weight blended with new interactions (via ALPHA=0.7)
    - As user likes/shares, vectors gradually shift toward their taste
    - Old tag-based vectors never fully disappear (30% baseline weight)
    
    LATER EVOLUTION:
    - daily update via update_long_term_vectors() (100 interactions)
    - 3am refresh via evolve_long_term_user_baselines() (500 interactions)
    - Vectors continuously refined as interactions accumulate
    
    REQUEST PAYLOAD:
    {
        "selected_tags": ["jazz", "lo-fi", "meditation"]
    }
    
    RESPONSE:
    {
        "status": "initialization_complete",
        "message": "Your vector preferences initialized from 42 clips"
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='initialize')
    def initialize_vectors(self, request):
        user = request.user
        selected_tags = request.data.get('selected_tags', [])
        
        # Find the top 100 most liked clips across those selected tags
        baseline_clips = AudioClip.objects.filter(
            tags__overlap=selected_tags,
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

class RegisterView(generics.CreateAPIView): # generic view for user registration built-in create behavior
    queryset = User.objects.all() # queryset set to all users so that we can create new ones
    # Everyone must be able to hit this endpoint to sign up!
    permission_classes = (AllowAny,)
    serializer_class = RegisterSerializer


class ProfileViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def _annotate_user(self, user):
        return User.objects.annotate(
            followers_count=Count('followers', distinct=True),
            following_count=Count('following', distinct=True),
            uploads_count=Count('audio_clips', distinct=True)
        ).get(pk=user.pk)

    # GET /profile/me/
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        user = self._annotate_user(request.user)
        serializer = OwnProfileSerializer(user, context={'request': request})
        return Response(serializer.data)

    # PATCH /profile/me/update/
    @action(
        detail=False,
        methods=['patch'],
        url_path='me/update',
        parser_classes=[parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    )
    def update_me(self, request):
        serializer = ProfileUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # GET /profile/{user_id}/
    def retrieve(self, request, pk=None):
        target = get_object_or_404(
            User.objects.annotate(
                followers_count=Count('followers', distinct=True),
                following_count=Count('following', distinct=True),
                uploads_count=Count('audio_clips', distinct=True)
            ),
            pk=pk
        )
        serializer = PublicProfileSerializer(target, context={'request': request})
        return Response(serializer.data)

    # GET /profile/{user_id}/clips/
    @action(detail=True, methods=['get'], url_path='clips')
    def user_clips(self, request, pk=None):
        target = get_object_or_404(User, pk=pk)
        clips = AudioClip.objects.filter(
            creator=target,
            status='ready'
        ).order_by('-created_at')

        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(clips, request)
        serializer = FeedClipSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)