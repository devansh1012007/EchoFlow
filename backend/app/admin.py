from django.contrib import admin, messages

from .models import AudioClip
from .services import cover_images


@admin.register(AudioClip)
class AudioClipAdmin(admin.ModelAdmin):
    """Review queue for cover images.

    Covers are user-supplied images that no automated check looks at, so they
    are served only after a reviewer approves them here. Filter by
    "Awaiting review" to see the queue, oldest first.
    """

    list_display = ("title", "creator", "status", "cover_status", "created_at")
    list_filter = ("cover_status", "status")
    search_fields = ("title", "creator__username")
    ordering = ("created_at",)
    readonly_fields = ("cover_moderated_at",)
    actions = ("approve_selected_covers", "reject_selected_covers")

    @admin.action(description="Approve selected covers")
    def approve_selected_covers(self, request, queryset):
        done = 0
        for clip in queryset.filter(cover_status=cover_images.COVER_PENDING):
            cover_images.approve_cover(clip, reviewer=request.user)
            done += 1
        self.message_user(request, f"Approved {done} cover(s).", messages.SUCCESS)

    @admin.action(description="Reject selected covers and delete the image")
    def reject_selected_covers(self, request, queryset):
        done = 0
        for clip in queryset.filter(cover_status=cover_images.COVER_PENDING):
            cover_images.reject_cover(clip, reason="Rejected by reviewer", reviewer=request.user)
            done += 1
        self.message_user(request, f"Rejected {done} cover(s).", messages.WARNING)
