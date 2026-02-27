from django.shortcuts import render
from rest_framework import viewsets, permissions, generics, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from django.shortcuts import get_object_or_404
#from django.http import StreamingHttpResponse
from django.contrib.auth.models import User
from rest_framework.decorators import authentication_classes
# Create your views here.

# like 
class LikeViewSet(viewsets.ModelViewSet):
    pass

# skip/next
class NextViewSet(viewsets.ModelViewSet):
    pass

# Prev
class PrevViewSet(viewsets.ModelViewSet):
    pass

# preload reel
class PreloadViewSet(viewsets.ModelViewSet):
    pass

# save to list 
class Save2ListViewSet(viewsets.ModelViewSet):
    pass

# share
class ShareViewSet(viewsets.ModelViewSet):
    pass

# suggestion engine 
class SuggestionViewSet(viewsets.ModelViewSet):
    pass

# adding reel
class CreatingAudioViewSet(viewsets.ModelViewSet):
    pass

# foller list or added new follow and all follow related stuff 
class FollowViewSet(viewsets.ModelViewSet):
    pass

# tags and suggested catagory -- if there is changes made by user or to return suggested catagory and tags 
class TagsAndSuggestionsViewSet(viewsets.ModelViewSet):
    pass

class ChatInterfaceViewSet(viewsets.ModelViewSet):
    pass

class CommentsViewSet(viewsets.ModelViewSet):
    pass