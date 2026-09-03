"""Comment views."""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, permissions

from ..models import Comment
from ..serializers import CommentSerializer
from ._pagination import CommentCursorPagination


class CommentViewSet(viewsets.ModelViewSet):
    # SECURITY: 60 comments/hour/user prevents comment spam. Default
    # 1000/hour/user lets one account post every 3.6s indefinitely.
    throttle_scope = 'comment'
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CommentCursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['clip', 'parent']

    def get_queryset(self):
        return Comment.objects.select_related('author').all()
