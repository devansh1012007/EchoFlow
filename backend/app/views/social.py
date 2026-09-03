"""Social views: sharing and following.

DECISION: Split out of monolithic views.py in 2026-09.
"""
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import AudioClip, UserInteraction, ShareEvent
from ..serializers import ShareEventSerializer

User = get_user_model()


class ShareViewSet(viewsets.ModelViewSet):
    # SECURITY: 100 shares/hour/user prevents inbox-spam DoS. Targeted
    # only at send_share; inbox/list/mark_read don't abuse-target.
    throttle_scope = 'share_send'
    serializer_class = ShareEventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ShareEvent.objects.filter(receiver=self.request.user)

    @action(detail=False, methods=['get'], url_path='find-user')
    def find_user(self, request):
        username = request.query_params.get('username', '').strip()
        if not username:
            return Response({'error': 'Username required'}, status=400)
        try:
            user = User.objects.get(username__iexact=username)
            if user == request.user:
                return Response({'error': "You can't share with yourself"}, status=400)
            return Response({'id': user.id, 'username': user.username})
        except User.DoesNotExist:
            return Response({'error': f'No user found: @{username}'}, status=404)

    @action(detail=True, methods=['post'], url_path='send-share')
    def send_share(self, request, pk=None):
        clip = get_object_or_404(AudioClip, pk=pk)
        user = request.user

        receiver_id = request.data.get('receiver_id')
        if not receiver_id:
            return Response({'error': 'Receiver ID required'}, status=status.HTTP_400_BAD_REQUEST)
        receiver = get_object_or_404(User, id=receiver_id)

        # 1. Log the interaction for the algorithm (increments global share count)
        UserInteraction.objects.get_or_create(
            user=user, clip=clip, interaction_type='share'
        )
        # 2. Create the peer-to-peer inbox event
        ShareEvent.objects.create(sender=user, receiver=receiver, clip=clip)

        return Response({'status': 'shared successfully'}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path='share-delete')
    def share_delete(self, request, pk=None):
        ShareEvent.objects.filter(pk=pk, receiver=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        ShareEvent.objects.filter(pk=pk, receiver=request.user).update(is_read=True)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='inbox')
    def inbox(self, request):
        shares = (
            ShareEvent.objects
            .filter(receiver=request.user)
            .select_related('sender', 'clip')
            .order_by('-created_at')
        )
        serializer = ShareEventSerializer(shares, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = ShareEvent.objects.filter(receiver=request.user, is_read=False).count()
        return Response({'unread': count})


class FollowViewSet(viewsets.ViewSet):
    """
    Social graph: follow / unfollow.
    ENDPOINT: POST /follow/{user_id}/toggle-follow/
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
