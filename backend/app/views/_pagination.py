"""Shared pagination classes for app views."""
from rest_framework.pagination import CursorPagination


class FeedCursorPagination(CursorPagination):
    page_size = 10
    ordering = '-created_at'


class CommentCursorPagination(CursorPagination):
    page_size = 20
    ordering = '-created_at'
