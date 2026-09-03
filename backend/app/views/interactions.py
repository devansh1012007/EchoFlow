"""User-clip interaction views (likes, skips, telemetry).

DECISION: Split out of monolithic views.py in 2026-09. Single class
because all three actions share the same queryset (AudioClip) and
the same throttle_scope plumbing.
"""
import json as _json
from django.core.cache import cache
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from ..models import AudioClip, UserInteraction
from ..serializers import SkipActionSerializer, InteractionTelemetrySerializer


class ClipInteractionViewSet(viewsets.GenericViewSet):
    queryset = AudioClip.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'interaction'

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
            # DECISION: Toggle is_active instead of deleting to preserve
            # history & metrics accuracy. The (user, clip, 'like')
            # unique_together guarantees only one row; toggle keeps the
            # timestamp for time_decay weighting while allowing re-likes.
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
        expected_duration = reel_position if reel_position > 0 else 60000
        completion_rate = min(listen_duration / expected_duration, 1.0)

        UserInteraction.objects.update_or_create(
            user=request.user,
            clip=clip,
            interaction_type='view',
            defaults={
                'completion_rate': completion_rate,
                'is_active': True
            }
        )
        return Response({"status": "skip/view registered"}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='log-telemetry')
    def log_telemetry(self, request, pk=None):
        clip = self.get_object()
        user = request.user

        serializer = InteractionTelemetrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        watch_time_ms = serializer.validated_data['watch_time_ms']
        action_type = serializer.validated_data['action_type']

        clip_duration = max(clip.duration_ms, 1)
        completion_rate = min(watch_time_ms / clip_duration, 1.0)

        # SECURITY: Telemetry is the architecture audit's #1 lock-contention
        # risk. Instead of update_or_create on every request (which holds a
        # row lock on UserInteraction), we append the event to a Redis list
        # and a periodic Celery task flushes the list to the DB in batches.
        # If Redis is down, fall through to the synchronous path so we
        # don't drop the event.
        event = {
            'user_id': str(user.id),
            'clip_id': str(clip.id),
            'action_type': action_type,
            'watch_time_ms': watch_time_ms,
            'completion_rate': completion_rate,
        }
        try:
            cache.client.get_client().rpush('telemetry:queue', _json.dumps(event))
        except Exception:
            UserInteraction.objects.update_or_create(
                user=user, clip=clip, interaction_type=action_type,
                defaults={
                    'watch_time_ms': watch_time_ms,
                    'completion_rate': completion_rate,
                    'is_active': True,
                },
            )

        return Response({"status": "telemetry logged"}, status=status.HTTP_202_ACCEPTED)

    def get_throttles(self):
        # SECURITY: log_telemetry is the architecture audit's #1 abuse vector
        # (viewbot / engagement-velocity manipulation). Override the default
        # 'interaction' scope with the tighter 'telemetry' scope for this action.
        if self.action == 'log_telemetry':
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @property
    def throttle_scope(self):
        return 'telemetry' if self.action == 'log_telemetry' else 'interaction'
