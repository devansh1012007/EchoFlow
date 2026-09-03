"""Comment views.

Stage 2 (relational-to-event-driven plan): serializer handles validation
+ the model.save()/delete() F() side-effect lives in the model itself.
The service is invoked via perform_create/perform_update/perform_destroy
so the ViewSet surface (URLs, status codes, throttling) is unchanged.

N1 fix: any authenticated user can PATCH/DELETE any other user's comment.
CommentViewSet was a ModelViewSet with no per-object permission. The
fix is two-layered:
1. get_queryset() scopes write actions (update/destroy) to comments
   owned by request.user. Reads (list/retrieve) stay global so the
   ?clip=X filter still works for everyone.
2. An IsAuthorOrReadOnly object-level permission denies unsafe methods
   even if get_queryset is bypassed. Defense in depth.
"""
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, permissions
from rest_framework.permissions import BasePermission

from ..models import Comment
from ..serializers import CommentSerializer
from ..services import comments as comments_svc
from ._pagination import CommentCursorPagination


class IsAuthorOrReadOnly(BasePermission):
    """Object-level: only the comment author may update or destroy.

    GET/HEAD/OPTIONS remain public (the audit's design intent: public
    reads for threaded conversations; private writes).
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author_id == request.user.id


class CommentViewSet(viewsets.ModelViewSet):
    # SECURITY: 60 comments/hour/user prevents comment spam. Default
    # 1000/hour/user lets one account post every 3.6s indefinitely.
    throttle_scope = 'comment'
    serializer_class = CommentSerializer
    # N1: two layers of defense. IsAuthenticated gates the endpoint;
    # IsAuthorOrReadOnly gates the per-object write. (get_queryset also
    # scopes writes below, so an attacker who somehow passes the
    # object-perm check still gets a 404 from a filtered queryset.)
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]
    pagination_class = CommentCursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['clip', 'parent']

    def get_queryset(self):
        # Reads (list/retrieve) keep the full queryset so ?clip=X and
        # ?parent=Y work for everyone. Writes (update/destroy) are
        # scoped to comments the requester owns. The same row is
        # invisible to a non-author on PATCH/DELETE, so they get 404
        # rather than 403 — which is the standard DRF pattern and
        # doesn't leak comment existence.
        qs = Comment.objects.select_related('author').all()
        if self.action in ('update', 'partial_update', 'destroy'):
            qs = qs.filter(author=self.request.user)
        return qs

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
