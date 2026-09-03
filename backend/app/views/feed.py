"""Feed, suggestion, and tag-init views.

DECISION: Split out of monolithic views.py in 2026-09. Each of these
touches the recommendation engine / Redis feed cache, so they share
a single module. ~270 lines.
"""
import logging
import numpy as np
from django.core.cache import cache
from django.db.models import Exists, OuterRef, Case, When
from pgvector.django import CosineDistance
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import AudioClip, UserInteraction
from ..serializers import FeedClipSerializer
from ..tasks import refill_user_feed, calculate_time_decayed_vectors
from ._pagination import FeedCursorPagination


class FastFeedViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user_id = request.user.id
        redis_key = f"user_feed:{user_id}"

        # DECISION: Wrap the entire Redis path in try/except. If Redis is
        # unreachable, return a trending-feed fallback (top clips by
        # engagement_velocity) instead of 500ing. The architecture audit
        # warns that a Redis outage during a 5k-user peak would otherwise
        # firehose the database with 5k concurrent refill_user_feed tasks
        # and crash PostgreSQL.
        try:
            redis_client = cache.client.get_client()
            clip_ids_bytes = redis_client.lpop(redis_key, 10)

            if not clip_ids_bytes:
                refill_user_feed.delay(user_id, count=40)
                clip_ids_bytes = redis_client.lpop(redis_key, 10)

                if not clip_ids_bytes:
                    return Response({"results": [], "message": "You've caught up!"})

            clip_ids = [vid.decode('utf-8') for vid in clip_ids_bytes]
            queue_length = redis_client.llen(redis_key)

            preserved_order = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(clip_ids)])
            user_like_subquery = UserInteraction.objects.filter(
                clip=OuterRef('pk'), user=request.user, interaction_type='like'
            )
            clips = (
                AudioClip.objects
                .filter(id__in=clip_ids)
                .annotate(user_has_liked=Exists(user_like_subquery))
                .order_by(preserved_order)
            )
            serializer = FeedClipSerializer(clips, many=True, context={'request': request})
            return Response({
                "next": "auto_trigger",
                "queue_health": queue_length,
                "results": serializer.data,
            })
        except Exception as e:
            logging.getLogger(__name__).warning(
                "feed service degraded for user %s; serving trending fallback: %s",
                user_id, e,
            )
            fallback = (
                AudioClip.objects
                .filter(status='ready')
                .annotate(user_has_liked=Exists(
                    UserInteraction.objects.filter(
                        clip=OuterRef('pk'), user=request.user, interaction_type='like'
                    )
                ))
                .order_by('-engagement_velocity', '-created_at')[:20]
            )
            serializer = FeedClipSerializer(fallback, many=True, context={'request': request})
            return Response({
                "next": "auto_trigger",
                "queue_health": 0,
                "degraded": True,
                "results": serializer.data,
            })


class SuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Category-specific recommendations using user's blended preference vectors.

    ENDPOINT: GET /suggestions/explore/?category=comedy
    """
    serializer_class = FeedClipSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = FeedCursorPagination

    def get_queryset(self):
        user = self.request.user
        category = self.request.query_params.get('category')

        queryset = AudioClip.objects.filter(status='ready', category=category)

        # DECISION: Wrap the vector search in try/except. The architecture
        # audit warns that a Postgres/Redis hiccup in
        # calculate_time_decayed_vectors would 500 the whole explore page.
        # With this fallback: rank by combined distance, or on failure
        # rank by engagement_velocity (trending within category), or as
        # a last resort serve the category unranked.
        try:
            sem_query, ac_query = calculate_time_decayed_vectors(user)
            if sem_query and ac_query:
                queryset = queryset.annotate(
                    combined_distance=(
                        CosineDistance('semantic_vector', sem_query) +
                        CosineDistance('acoustic_vector', ac_query)
                    )
                ).order_by('combined_distance')
        except Exception as e:
            logging.getLogger(__name__).warning(
                "vector ranking failed for user %s; falling back to engagement_velocity: %s",
                user.id, e,
            )
            queryset = queryset.order_by('-engagement_velocity', '-created_at')

        user_like_subquery = UserInteraction.objects.filter(
            clip=OuterRef('pk'), user=user, interaction_type='like'
        )
        return queryset.annotate(user_has_liked=Exists(user_like_subquery))


class TagsViewSet(viewsets.ViewSet):
    """
    Cold-start onboarding: Initialize user preferences from tag selection.

    ENDPOINT: POST /tags/initialize/
    """
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['post'], url_path='initialize')
    def initialize_vectors(self, request):
        user = request.user
        selected_tags = request.data.get('selected_tags', [])

        baseline_clips = AudioClip.objects.filter(
            tags__overlap=selected_tags,
            semantic_vector__isnull=False,
            acoustic_vector__isnull=False
        ).order_by('-likes')[:100]

        if not baseline_clips:
            return Response({"error": "Not enough data to build baseline."}, status=400)

        sem_vectors = [np.array(clip.semantic_vector) for clip in baseline_clips]
        ac_vectors = [np.array(clip.acoustic_vector) for clip in baseline_clips]

        user.long_term_semantic = (np.mean(sem_vectors, axis=0)).tolist()
        user.long_term_acoustic = (np.mean(ac_vectors, axis=0)).tolist()
        user.save()

        refill_user_feed.delay(user.id, count=30)

        return Response({"status": "Algorithm initialized. Feed is ready."}, status=200)
