# docs/stateful-media-storage-at-scale.md

## Executive Summary
EchoFlow’s current architecture is operating under a monolithic, stateful storage paradigm that relies heavily on a shared local filesystem[cite: 14]. While this functions in a single-machine Docker Compose environment where volumes are artificially mapped across containers, it will instantly fracture in a horizontally scaled production environment. If EchoFlow scales to multiple API instances and distributed worker nodes, media uploads will succeed on one server, fail to process on another due to missing files, and return 404s to users attempting playback. 

This document outlines the exact architectural overhaul required to decouple EchoFlow's media state from its application servers, transitioning to a highly durable, stateless, object-storage-driven architecture that can scale to millions of concurrent users and petabytes of audio.

---

## Current Media Architecture
**Status: Implemented (but fatally flawed for distributed scaling)**

1.  **Ingestion:** The client POSTs a multipart form payload containing the audio file to `AudioUploadViewSet.create`[cite: 16]. Django saves this directly to the server's local disk via `models.FileField` configured with `upload_to='uploads/%Y/%m/%d/'`[cite: 12].
2.  **Task Handoff:** The API triggers `process_audio_to_hls.delay(clip.id)` and returns a `202 ACCEPTED`[cite: 16].
3.  **Processing:** A Celery worker picks up the task, retrieves the `AudioClip` from the database, and accesses `clip.original_file.path`[cite: 14]. It executes FFmpeg, Whisper, and librosa locally, writing the output `.m3u8` and `.ts` files to a dynamically created local directory: `os.path.join(settings.MEDIA_ROOT, 'hls', str(clip.id))`[cite: 14].
4.  **Delivery:** The worker saves a relative URL (`/media/hls/...`) to the database[cite: 14], assuming the Django API will serve the media directly from the same disk.

---

## Hidden Stateful Assumptions
The codebase is riddled with assumptions that bind the application to a single physical disk.

*   **`clip.original_file.path`[cite: 14]:** This assumes a POSIX file path exists on the worker executing the task. If Worker Node B picks up the task for a file uploaded to API Node A, it will throw a `FileNotFoundError` and the task will die.
*   **`os.makedirs(os.path.join(settings.MEDIA_ROOT, 'hls', str(clip.id)))`[cite: 14]:** This assumes the worker has persistent, shared block storage. In a Kubernetes or ECS environment, writing to the container's root filesystem means the HLS chunks disappear forever the moment the container scales down or restarts.
*   **Media Serving:** Saving the URL as `/media/hls/{clip.id}/master.m3u8`[cite: 14] inherently assumes the Django web server is acting as the static file server for media. Django is single-threaded per worker; serving binary HLS chunks through Python will immediately consume all Gunicorn threads, taking down the entire API under minimal load.

---

## Horizontal Scaling Failure Modes
If the current system scales to 10 API servers and 50 workers across multiple availability zones:

1.  **The Split-Brain Processing Failure:** 
    *   *Scenario:* User uploads to API Server 1. Celery Worker on Server 4 picks up the task.
    *   *Result:* Worker 4 cannot find the file in its local `/uploads/` directory. The upload is permanently stalled in a `processing` state[cite: 12].
2.  **The Disk Exhaustion Cascade:** 
    *   *Scenario:* A viral trend causes 100,000 uploads. 
    *   *Result:* API servers, handling binary uploads through RAM and writing to local disk, run out of EBS volume space. The OS halts, causing complete node failure.
3.  **The "Ghost Playlist" Phenomenon:** 
    *   *Scenario:* Worker 2 successfully transcodes an HLS playlist and saves it locally. API Server 7 receives the client's HTTP request for the `.m3u8` file.
    *   *Result:* Server 7 returns a 404 because the file exists only on Worker 2's ephemeral disk.

---

## Recommended Media Storage Architecture
**Status: Recommended / Missing**

To achieve horizontal scalability, EchoFlow must enforce strict **statelessness** on all compute nodes. 
*   **Primary Storage:** Amazon S3 (or compatible object storage).
*   **Database:** PostgreSQL remains the source of truth for metadata and object keys[cite: 12].
*   **Workers:** Ephemeral. They download from S3 to a `/tmp` RAM-disk, process, upload to S3, and immediately purge local state.

### Upload Architecture
**Current:** Client → API → Local Disk (Synchronous)[cite: 16]
**Target:** Client → Object Storage (Direct)

1.  **Presigned URL Generation:** The client requests an upload token. The Django API authenticates the user, generates a short-lived (e.g., 5-minute) AWS S3 Presigned URL, creates an `AudioClip` record in `pending_upload` state, and returns the URL.
2.  **Direct-to-S3:** The client POSTs the binary file directly to S3. Django’s compute/bandwidth is entirely bypassed.
3.  **Webhook/Finalization:** S3 triggers a Lambda/Webhook back to EchoFlow, or the client calls a `/finalize-upload` endpoint, transitioning the clip to `processing` and triggering Celery.

### Media Processing State Machine
The current binary state (`processing` vs `ready`)[cite: 12] is inadequate for distributed recovery. EchoFlow must implement this state machine:
*   `pending_upload`: Record exists, waiting for S3 bytes.
*   `processing`: Celery has acquired the task.
*   `ready`: HLS chunks exist in S3, vector metadata is committed.
*   `failed`: Permanent failure (e.g., corrupt audio).

### Object Storage Strategy
*   **Key Design:** Predictable, UUID-based keys.
    *   Raw uploads: `s3://echoflow-raw/uploads/{user_id}/{clip_uuid}/original.mp3`
    *   HLS output: `s3://echoflow-media/hls/{clip_uuid}/192k/segment_0.ts`
*   **Immutability:** Audio files are immutable. If a creator "edits" a clip, it generates a new UUID.
*   **Lifecycle Policies:** The `echoflow-raw` bucket should automatically transition raw files to Glacier/Deep Archive after 7 days, as they are no longer needed once the HLS chunks are generated.

### CDN Strategy
*   **Architecture:** AWS CloudFront distribution pointing to the `echoflow-media` S3 bucket.
*   **Cache Behavior:** HLS `.ts` segments are inherently immutable and should be cached aggressively (TTL of 1 year). The `master.m3u8` playlist is also static for VOD (Video on Demand) and can be cached indefinitely.
*   **Range Requests:** CloudFront must be configured to support HTTP Range Requests, which the client ABR player relies on for seamless bitrate switching.
*   **Origin Shielding:** Protect S3 from request floods during viral events by utilizing a regional edge cache.

---

## Consistency Model
*   **Source of Truth:** PostgreSQL is the definitive authority on *state* (is this clip published?). S3 is the authority on *bytes* (the actual media).
*   **Eventual Consistency:** When a clip is uploaded, the DB is updated. The CDN edge locations may take milliseconds to cache the object. 
*   **Orphan Mitigation (Transactional Outbox):** If a user deletes a clip, you cannot perform a synchronous S3 delete during the HTTP request. If S3 times out, the DB transaction rolls back. Instead, soft-delete the `AudioClip` in Postgres (making it instantly invisible to the app) and dispatch a Celery task to perform the hard S3 delete asynchronously.

## Idempotency and Retry Strategy
Celery tasks will fail and be retried (network blips, spot instance termination). The `process_audio_to_hls` task[cite: 14] is currently **not idempotent**. If it fails halfway, it leaves garbage on the disk.

**Target Implementation:**
1.  Worker downloads raw file to `/tmp/{task_id}/`.
2.  Worker executes FFmpeg.
3.  Worker uploads HLS chunks to S3 under `hls/{clip.id}/`. (S3 PUTs are atomic).
4.  Worker updates DB: `status = 'ready'`.
5.  `finally:` block securely wipes the `/tmp/{task_id}/` directory regardless of success or failure.

---

## Failure Scenarios and Recovery

| Scenario | Impact | Safe Recovery Behavior |
| :--- | :--- | :--- |
| **Worker crashes mid-transcode** | Orphaned files in `/tmp/` | Task is redelivered. Next worker uses a fresh `/tmp/` path, overwriting any partial S3 uploads atomically. |
| **Client retries upload** | Duplicate files | Deduplication via SHA-256 hash checking at the Presigned URL generation stage. |
| **DB succeeds, S3 fails (Deletion)** | Orphaned S3 bytes (Cost leak) | Soft-delete in DB. A daily Celery Beat "reconciliation" task scans for deleted DB records and aggressively scrubs corresponding S3 prefixes. |
| **Viral clip causes traffic spike** | Origin overload | CloudFront Origin Shield collapses simultaneous edge requests into a single origin pull, protecting S3. |

---

## Security Architecture
*   **Malicious Files:** Direct-to-S3 uploads bypass Django, preventing server-side arbitrary code execution during ingestion. 
*   **Content-Type Spoofing:** FFmpeg acts as a natural sanitizer[cite: 14]. If a user uploads a disguised executable, FFmpeg will fail to parse it as an audio stream, the task will fail, and the `status` will remain stuck, isolating the threat.
*   **Predictable Object Keys:** Object keys use UUIDv4 (`clip.id`)[cite: 12], making them unguessable. Banning sequential IDs prevents scraping attacks.
*   **Public Access:** The `echoflow-media` bucket must be restricted via Origin Access Control (OAC), ensuring the objects can *only* be accessed via CloudFront, preventing direct S3 bandwidth theft.

---

## Cost and Capacity Analysis
*   **Ingestion:** 1M uploads/day ≈ 10TB of raw ingress. (AWS S3 Ingress is free).
*   **Storage:** 10M media objects (HLS format, ~3 qualities) ≈ 150TB. S3 Standard costs ~$3,500/month.
*   **Delivery (The Threat):** If viral media causes extreme read amplification (e.g., 10M users streaming 10MB each), that is 100TB of egress. At standard AWS CloudFront rates ($0.085/GB), this costs $8,500 **per day**. 
*   **Mitigation:** You must implement a Cloudflare R2 / Bandwidth Alliance architecture, or negotiate committed-use discounts with AWS, otherwise scaling EchoFlow will financially ruin the company.

---

## What Should Change in EchoFlow

### Missing Infrastructure
*   **AWS S3 Integration:** `django-storages` and `boto3` must be added to `requirements.txt`.
*   **Presigned URL Endpoint:** A new API endpoint must be created to hand out S3 upload tokens.
*   **CDN Configuration:** Terraform/IaC scripts to provision CloudFront with appropriate caching headers for `.m3u8` and `.ts` files.

### Current vs Target Architecture

*   **Current `AudioClip` Model[cite: 12]:**
    ```python
    original_file = models.FileField(upload_to='uploads/%Y/%m/%d/', null=True)
    ```
*   **Target `AudioClip` Model:**
    ```python
    # Replaced with a CharField indicating the S3 Object Key
    raw_s3_key = models.CharField(max_length=255, blank=True)
    ```

*   **Current `tasks.py`[cite: 14]:**
    ```python
    input_file_path = clip.original_file.path
    os.makedirs(output_dir, exist_ok=True)
    ```
*   **Target `tasks.py`:**
    ```python
    # Boto3 download to ephemeral storage
    input_file_path = f"/tmp/{clip.id}.raw"
    s3_client.download_file('echoflow-raw', clip.raw_s3_key, input_file_path)
    # Process...
    # Boto3 upload output dir
    ```

---

## P0/P1/P2/P3 Implementation Roadmap

*   **P0 (Blocker for Production):** Rip out `models.FileField`[cite: 12]. Implement `django-storages` pointing to an S3 bucket. Modify `process_audio_to_hls`[cite: 14] to use Python's `tempfile` module for intermediate disk I/O instead of `settings.MEDIA_ROOT`.
*   **P1 (Before Viral Growth):** Implement Direct-to-S3 Presigned URL uploads to unblock the Gunicorn/API workers from handling binary streams.
*   **P2 (Cost Optimization):** Implement CloudFront caching layers and S3 lifecycle rules to move raw uploads to Glacier.
*   **P3 (Extreme Scale):** Implement cross-region S3 replication for the media bucket to reduce latency for global listeners.

---

## Architectural Blind Spots
*   **My Blind Spot:** I am assuming FFmpeg operates quickly enough on the selected compute nodes. If audio files are long (e.g., >3 minutes), the worker could hit Celery's `soft_time_limit` and be killed, abandoning the `/tmp` files.
*   **Your Blind Spot:** Transcoding requires massive CPU. If you run the Celery worker on the same EC2 instance as the API, an upload spike will starve the API of CPU cycles, taking down the feed endpoint[cite: 16]. The workers *must* be physically isolated on different compute clusters.

---

## Final Senior-Engineer Verdict
The current stateful implementation of media handling in `tasks.py` and `views.py` is a classic development-environment anti-pattern that guarantees data loss, 404s, and disk exhaustion the moment a second server is added to the cluster. 

The API layer must be treated as a stateless traffic cop, never touching the actual audio bytes. By decoupling storage (S3), processing (isolated ephemeral Celery workers), and delivery (CloudFront), EchoFlow will transform from a fragile local script into a distributed, infinite-scale media engine. Execute the P0 roadmap immediately before provisioning any production infrastructure.