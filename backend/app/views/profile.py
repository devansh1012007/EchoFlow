"""Profile view: /profile/me/ and /profile/{id}/."""
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, permissions, parsers
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import AudioClip
from ..serializers import (
    FeedClipSerializer, OwnProfileSerializer, PublicProfileSerializer,
    ProfileUpdateSerializer,
)
from ._pagination import FeedCursorPagination

User = get_user_model()


class ProfileViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def _annotate_user(self, user):
        return User.objects.annotate(
            followers_count=Count('followers', distinct=True),
            following_count=Count('following', distinct=True),
            uploads_count=Count('audio_clips', distinct=True)
        ).get(pk=user.pk)

    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        user = self._annotate_user(request.user)
        serializer = OwnProfileSerializer(user, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['patch'], url_path='me/update',
            parser_classes=[parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser])
    def update_me(self, request):
        serializer = ProfileUpdateSerializer(
            request.user, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        target = get_object_or_404(
            User.objects.annotate(
                followers_count=Count('followers', distinct=True),
                following_count=Count('following', distinct=True),
                uploads_count=Count('audio_clips', distinct=True)
            ),
            pk=pk,
        )
        serializer = PublicProfileSerializer(target, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='clips')
    def user_clips(self, request, pk=None):
        target = get_object_or_404(User, pk=pk)
        clips = (
            AudioClip.objects
            .filter(creator=target, status='ready')
            .order_by('-created_at')
        )
        paginator = FeedCursorPagination()
        page = paginator.paginate_queryset(clips, request)
        serializer = FeedClipSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)
