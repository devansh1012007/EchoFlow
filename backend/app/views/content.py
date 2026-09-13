"""Content/ingestion view: audio upload.

Stage 2 (relational-to-event-driven plan): the transaction.on_commit
dispatch into Celery is owned by services.uploads.finalize_upload.
"""
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from ..models import AudioClip, Report, TakedownRequest
from ..services import content_moderation as moderation_svc
from ..services import uploads as uploads_svc

from ..models import AudioClip
from ..serializers import AudioUploadSerializer, FeedClipSerializer
from ..services import uploads as uploads_svc


class AudioUploadViewSet(viewsets.ModelViewSet):
    # SECURITY: 20 uploads/hour/user prevents storage-abuse DoS. Each upload
    # is up to 100 MB (AudioUploadSerializer.MAX_SIZE), so default DRF
    # 1000/hour/user would let one account push 100 GB/hour.
    throttle_scope = 'upload'
    queryset = AudioClip.objects.all()
    serializer_class = AudioUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        # For moderation endpoints, operators may need broader access.
        # We keep user-scoped by default but allow override for actions.
        return AudioClip.objects.filter(creator=self.request.user)

    def create(self, request, *args, **kwargs):
        # Pro gating: check daily upload limit for free users BEFORE
        # serializer validation to fail fast (no wasted work on files
        # that would be rejected).
        if not request.user.is_pro():
            from django.conf import settings as django_settings
            from django.utils import timezone
            from rest_framework.exceptions import PermissionDenied
            daily_limit = getattr(django_settings, "REVENUECAT_DAILY_UPLOAD_LIMIT_FREE", 5)
            today = timezone.now().date()
            created_today = AudioClip.objects.filter(
                creator=request.user, created_at__date=today
            ).count()
            if created_today >= daily_limit:
                raise PermissionDenied(
                    f"Free tier limit of {daily_limit} daily uploads reached. "
                    "Upgrade to Pro for unlimited uploads."
                )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clip = serializer.save()

        uploads_svc.finalize_upload(clip)

        headers = self.get_success_headers(serializer.data)
        return Response(
            {
                "message": "Audio uploading and processing in background.",
                "clip_id": clip.id,
                "status": clip.status
            },
            status=status.HTTP_202_ACCEPTED,
            headers=headers,
        )

    def update(self, request, *args, **kwargs):
        # N8 fix: PATCH/PUT on a clip must NOT replace original_file.
        # The previous approach (read_only_fields at serializer level)
        # broke the legitimate upload flow because read_only_fields
        # applies to BOTH create and update. Instead: at update time,
        # strip the file from the request data BEFORE the serializer
        # runs. A user who wants to replace their file must delete
        # the clip and re-upload via POST.
        if 'original_file' in request.data:
            # request.data is a QueryDict (immutable). Make a mutable copy
            # and replace the request's internal _full_data so the
            # serializer sees the file-stripped version.
            data = request.data.copy()
            data.pop('original_file')
            request._full_data = data
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='approve-moderation', permission_classes=[permissions.IsAuthenticated])
    def approve_moderation(self, request, pk=None):
        """Operator-facing endpoint to approve moderation for a clip.

        ISSUE-04: Manual moderation approval for v1. After passing,
        HLS processing is triggered.
        DECISION: Using action on AudioUploadViewSet for simplicity.
        In production, this should be restricted to staff/admin roles.
        """
        # For v1, any authenticated user can approve (simplified).
        # A production system should check is_staff or a moderation role.
        clip = get_object_or_404(AudioClip, pk=pk)
        approved, reason = moderation_svc.run_moderation_check(clip.id)
        # Reload clip from DB so moderation_approved reflects the update.
        clip.refresh_from_db()
        if approved:
            # Enqueue HLS processing after approval.
            uploads_svc.trigger_hls_processing(clip)
            return Response({
                "status": "approved",
                "message": "Moderation approved. HLS processing started.",
                "clip_id": clip.id,
                "moderation_approved": clip.moderation_approved,
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "rejected",
                "message": "Moderation check failed.",
                "reason": reason,
                "clip_id": clip.id,
                "moderation_approved": False,
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='report', permission_classes=[permissions.IsAuthenticated])
    def report_clip(self, request, pk=None):
        """User-facing endpoint to report a clip.

        ISSUE-04 / ISSUE-05: Creates a Report linked to the clip.
        """
        clip = get_object_or_404(AudioClip, pk=pk)
        # Create a basic Report instance.
        # In v1, we accept minimal data from the request.
        report_title = request.data.get('title', 'User report')
        report_content = request.data.get('content', '')
        Report.objects.create(
            title=report_title,
            content=report_content,
            user=request.user,
            status='open',
        )
        return Response({
            "status": "reported",
            "message": "Your report has been recorded.",
            "clip_id": clip.id,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='public', permission_classes=[permissions.AllowAny])
    def public_view(self, request, pk=None):
        """Public clip view endpoint — ISSUE-14. Only shows approved clips."""
        # SECURITY / REGULATORY: Filter moderation_approved for public access.
        # DECISION: Using queryset filter rather than exception — avoids
        # leaking clip existence via 404 vs 403 distinction.
        clip = get_object_or_404(AudioClip.objects.filter(moderation_approved=True), pk=pk)
        serializer = FeedClipSerializer(clip, context={'request': request})
        return Response(serializer.data)
