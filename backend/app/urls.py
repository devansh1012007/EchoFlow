from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from .views import (
    AudioUploadViewSet, FastFeedViewSet, ClipInteractionViewSet,
    ShareViewSet, CommentViewSet, FollowViewSet,
    TagsViewSet, SuggestionViewSet,RegisterView,ProfileViewSet
)
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.throttling import ScopedRateThrottle


class ThrottledTokenObtainPairView(TokenObtainPairView):
    # SECURITY: 10 login attempts/min/IP. Default AnonRateThrottle
    # 100/hour = ~1.7/min — too loose for credential stuffing.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

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


class LogoutView(APIView):
    # SECURITY: Blacklists the provided refresh token. The blacklist table
    # is created by the token_blacklist migration (added to INSTALLED_APPS).
    # Access tokens are short-lived (15 min) so no need to track them; once
    # the refresh token is blacklisted, no new access tokens can be minted.
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            if not refresh_token:
                return Response({'detail': 'refresh token required'}, status=400)
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({'detail': 'logged out'})
        except TokenError as e:
            return Response({'detail': str(e)}, status=400)


urlpatterns = [
    path('', include(router.urls)),
    path('auth/login/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    # NOTE: no /media/ route anymore, on purpose. Media now lives in S3-
    # compatible object storage (see settings.STORAGES["default"]), not on
    # this container's disk — there is nothing local left to serve, and a
    # django.views.static.serve route here would 404 on every request
    # regardless of DEBUG. Playback URLs come from FeedClipSerializer, which
    # asks default_storage for a freshly signed URL per request instead.
]