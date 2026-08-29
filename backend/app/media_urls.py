"""
Playback URL generation for the browser.

WHY THIS FILE EXISTS — TWO SEPARATE PROBLEMS SOLVED HERE:

1. ENDPOINT MISMATCH. django-storages' `default_storage.url()` reuses the
   SAME boto3 client the app uses to talk to the bucket internally — which
   bakes the bucket's INTERNAL endpoint into any URL it returns (e.g.
   `http://minio:9000`, a hostname that only resolves inside the Docker
   network). A browser on the host has no DNS entry for it at all.

2. HLS IS A MULTI-FILE PROTOCOL, SIGNED URLS ARE SINGLE-FILE. A signed URL's
   signature lives in its query string. `master.m3u8` references variant
   playlists via RELATIVE paths, and those reference segment files the same
   way — and per RFC 3986, resolving a relative reference against a base URL
   does NOT carry the base URL's query string forward. So even a correctly
   signed `master.m3u8` succeeds while every file it points to gets
   requested with no signature at all, which a private bucket correctly
   rejects with 403. One signed URL cannot authorize a stream made of dozens
   of objects — this isn't a MinIO quirk, it's true against real S3 too.

   The fix used by every real HLS-over-object-storage deployment (absent a
   CDN doing signed-cookie auth at the edge, which covers a whole path
   prefix instead of one object): the RENDERED/derived stream is
   public-read. The ORIGINAL uploaded file — the one thing actually worth
   protecting — stays private. See docker-compose.yml's `minio-init`
   service, which runs `mc anonymous set download .../hls` to make exactly
   that split at the bucket-policy level. Because `hls/` is genuinely
   public per that policy, signing those URLs would be theater — a URL that
   LOOKS like it expires but doesn't, since the underlying object needs no
   signature to be readable. That mismatch (looks access-controlled, isn't)
   is worse than no signature at all, so get_hls_playback_url() below
   returns a plain public URL, not a presigned one.

   `uploads/<original>` objects remain genuinely private and still need
   real presigned URLs — that's what get_signed_media_url() is for, kept
   separate and unused by anything HLS-related on purpose.
"""
import boto3
from django.conf import settings


def get_hls_playback_url(object_key):
    """Return a browser-playable URL for HLS content (master.m3u8 or
    anything under the same `hls/` prefix). Not signed, on purpose — see
    module docstring for why signing a multi-file HLS stream doesn't work
    and why `hls/` is bucket-policy public instead.

    Returns None if object_key is falsy.
    """
    if not object_key:
        return None

    bucket = settings.STORAGES["default"]["OPTIONS"]["bucket_name"]
    endpoint = (settings.PUBLIC_MEDIA_ENDPOINT_URL or "").rstrip("/")
    # addressing_style is "path" (see STORAGES config) — bucket is a path
    # segment, not a subdomain, which is what MinIO and most non-AWS
    # S3-compatible endpoints require.
    return f"{endpoint}/{bucket}/{object_key}"


def get_signed_media_url(object_key):
    """Return a browser-playable, time-limited SIGNED url for a genuinely
    PRIVATE object (e.g. an original upload under `uploads/`). Do not use
    this for HLS content — see get_hls_playback_url() and the module
    docstring for why per-object signing doesn't work for a multi-file
    stream.

    Returns None if object_key is falsy.
    """
    if not object_key:
        return None

    client = boto3.client(
        "s3",
        endpoint_url=settings.PUBLIC_MEDIA_ENDPOINT_URL,
        aws_access_key_id=settings.STORAGES["default"]["OPTIONS"]["access_key"],
        aws_secret_access_key=settings.STORAGES["default"]["OPTIONS"]["secret_key"],
        region_name=settings.STORAGES["default"]["OPTIONS"]["region_name"],
        config=boto3.session.Config(
            s3={"addressing_style": settings.STORAGES["default"]["OPTIONS"]["addressing_style"]},
            signature_version="s3v4",
        ),
    )
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.STORAGES["default"]["OPTIONS"]["bucket_name"],
            "Key": object_key,
        },
        ExpiresIn=settings.STORAGES["default"]["OPTIONS"]["querystring_expire"],
    )