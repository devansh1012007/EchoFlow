"""Comment views.

Stage 2 (relational-to-event-driven plan): serializer handles validation
+ the model.save()/delete() F() side-effect lives in the model itself.
The service is invoked via perform_create/perform_update/perform_destroy
so the ViewSet surface (URLs, status codes, throttling) is unchanged.
"""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, permissions

from ..models import Comment
from ..serializers import CommentSerializer
from ..services import comments as comments_svc
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

    def perform_create(self, serializer):
        # Assign back so DRF's response renderer (get_success_headers,
        # serializer.data) sees the persisted instance, not validated_data.
        serializer.instance = comments_svc.create_comment(
            user=self.request.user,
            clip=serializer.validated_data['clip'],
            text=serializer.validated_data['text'],
            parent=serializer.validated_data.get('parent'),
        )

    def perform_update(self, serializer):
        comments_svc.update_comment(serializer.instance, text=serializer.validated_data['text'])

    def perform_destroy(self, instance):
        comments_svc.delete_comment(instance)
