from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AudioUploadViewSet, FastFeedViewSet, ClipInteractionViewSet,
    ShareViewSet, CommentViewSet, FollowViewSet, 
    TagsViewSet, SuggestionViewSet,RegisterView,ProfileViewSet
)
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.conf import settings
from django.views.static import serve as static_serve

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
    # NOTE: intentionally unconditional (not gated on settings.DEBUG). Django's
    # own static() helper returns [] when DEBUG=False, which silently killed
    # media serving in every non-debug run. This is still a stopgap, not a
    # production answer — see note below.
    path('media/<path:path>', static_serve, {'document_root': settings.MEDIA_ROOT}),
]   