from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AudioUploadViewSet, FastFeedViewSet, ClipInteractionViewSet,
    ShareViewSet, CommentViewSet, FollowViewSet, 
    TagsViewSet, SuggestionViewSet,RegisterView,ProfileViewSet
)
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

router = DefaultRouter()
router.register(r'feed', FastFeedViewSet, basename='feed')
router.register(r'clips', AudioUploadViewSet, basename='clips')
router.register(r'interactions', ClipInteractionViewSet, basename='interactions')
router.register(r'share', ShareViewSet, basename='share')
router.register(r'comments', CommentViewSet, basename='comments')
router.register(r'follow', FollowViewSet, basename='follow')
router.register(r'tags', TagsViewSet, basename='tags')
router.register(r'suggestions', SuggestionViewSet, basename='suggestions')
router.register(r'profile', ProfileViewSet, basename='profile')


urlpatterns = [
    path('', include(router.urls)),
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # NOTE: no /media/ route anymore, on purpose. Media now lives in S3-
    # compatible object storage (see settings.STORAGES["default"]), not on
    # this container's disk — there is nothing local left to serve, and a
    # django.views.static.serve route here would 404 on every request
    # regardless of DEBUG. Playback URLs come from FeedClipSerializer, which
    # asks default_storage for a freshly signed URL per request instead.
]