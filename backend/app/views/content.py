"""Content/ingestion view: audio upload.

Stage 2 (relational-to-event-driven plan): the transaction.on_commit
dispatch into Celery is owned by services.uploads.finalize_upload.
"""
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response

from ..models import AudioClip
from ..serializers import AudioUploadSerializer
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
        return AudioClip.objects.filter(creator=self.request.user)

    def create(self, request, *args, **kwargs):
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
