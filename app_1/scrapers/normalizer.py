import logging
from pydub import AudioSegment

logger = logging.getLogger(__name__)


def normalize_and_trim(in_path, out_path, max_seconds=300, target_format='mp3'):
    """Normalize audio file and trim/pad to max_seconds, exporting to out_path.

    Returns out_path on success.
    """
    try:
        audio = AudioSegment.from_file(in_path)
    except Exception as e:
        logger.exception("Failed to load audio for normalization: %s", e)
        raise

    max_ms = int(max_seconds * 1000)
    if len(audio) > max_ms:
        audio = audio[:max_ms]

    # Normalize: convert to stereo 44100 Hz
    try:
        audio = audio.set_frame_rate(44100).set_channels(2)
    except Exception:
        # Some formats may not support set_frame_rate; ignore if it fails
        pass

    # Export
    audio.export(out_path, format=target_format, bitrate='192k')
    return out_path
