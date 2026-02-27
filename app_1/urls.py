from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LikeViewSet,NextViewSet,PreloadViewSet,
    Save2ListViewSet,ShareViewSet,SuggestionViewSet,
    CreatingAudioViewSet,PrevViewSet,FollowViewSet,
    TagsAndSuggestionsViewSet,ChatInterfaceViewSet,
    CommentsViewSet,
)
router = DefaultRouter()
router.register(r'Like',LikeViewSet, basename='Like')
router.register(r'Next',NextViewSet, basename='Next')
router.register(r'Prev',PrevViewSet, basename='Prev')
router.register(r'Follow',FollowViewSet, basename='Follow')
router.register(r'TagsAndSuggestions',TagsAndSuggestionsViewSet, basename='TagsAndSuggestions')
router.register(r'ChatInterface',ChatInterfaceViewSet, basename='ChatInterface')
router.register(r'Comments',CommentsViewSet, basename='Comments')
router.register(r'Preload', PreloadViewSet, basename='Preload')
router.register(r'Save2List', Save2ListViewSet, basename='Save2List')
router.register(r'Share', ShareViewSet, basename='Share')
router.register(r'Suggestion', SuggestionViewSet, basename='Suggestion')
router.register(r'CreatingAudio', CreatingAudioViewSet, basename='CreatingAudio')
urlpatterns = [
    path('', include(router.urls))
    
    ]