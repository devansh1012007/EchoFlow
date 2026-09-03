"""Content/ingestion view: audio upload."""
from django.db import transaction
from rest_framework import viewsets, permissions, parsers, status
from rest_framework.response import Response

from ..models import AudioClip
from ..serializers import AudioUploadSerializer
from ..tasks import process_audio_to_hls


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

        # transaction.on_commit: only enqueue after the DB write commits.
        # If the transaction rolls back, the task is never enqueued.
        transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))

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
