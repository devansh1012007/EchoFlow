# docs/ml-transcoding-resource-isolation.md

## Executive Summary
EchoFlow’s current architecture dangerously intertwines lightweight application logic with severely resource-intensive Machine Learning (ML) inference and media transcoding. By loading transformer models (Whisper, MiniLM, KeyBERT) at the module level in Celery tasks[cite: 13] and executing unconstrained FFmpeg subprocesses[cite: 13], the system practically guarantees that a sudden spike in media uploads will trigger CPU starvation and Out-of-Memory (OOM) cascading failures. 

To guarantee that heavy asynchronous workloads cannot take down the API, database, or authentication layers, EchoFlow must immediately physically isolate these components. Relying on Celery as a generic job queue does not solve resource contention; in fact, Celery's pre-fork worker model aggressively multiplies the memory footprint of loaded ML models, turning a single compute node into a memory bomb.

---

## Current ML and Media Workloads

**Status: Implemented[cite: 13]**

**ML Inference Workloads:**
*   **Acoustic Feature Extraction:** Uses `librosa` to compute 128-dimensional vectors (MFCC, Chroma, Mel Spectrogram)[cite: 13]. (CPU-bound, memory scales with audio duration).
*   **Transcription:** Uses `faster_whisper.WhisperModel("base")` on CPU[cite: 13]. (Highly CPU-bound).
*   **Semantic Embeddings:** Uses `sentence_transformers.SentenceTransformer('all-MiniLM-L6-v2')`[cite: 13]. (CPU-bound).
*   **Automated Tagging:** Uses `KeyBERT` to extract unigrams[cite: 13]. (CPU-bound).

**Media Processing Workloads:**
*   **Transcoding & HLS Generation:** Uses `subprocess.run(['ffmpeg', ...])` to transcode the original file into 192k, 128k, and 64k AAC HLS streams[cite: 13]. (Extremely CPU-bound and disk I/O intensive).

---

## Resource Consumption Model

Celery defaults to a pre-fork concurrency model, spawning one worker process per CPU core. 

If EchoFlow is deployed on a standard 4-core, 16GB RAM server:
*   **API (Gunicorn/Django):** ~500MB RAM.
*   **ML Model Footprint (Idle):** Whisper base + MiniLM + KeyBERT ≈ 1.5GB RAM.
*   **The Celery Pre-fork Trap:** Because models are loaded at the module level[cite: 13], 4 Celery worker processes will load 4 independent copies of the models into memory. (4 × 1.5GB = 6GB RAM just for idle weights).
*   **Active Processing:** When an upload arrives, `librosa` loads the audio array into RAM[cite: 13], FFmpeg spawns a multi-threaded subprocess[cite: 13], and Whisper executes inference.

**Scenario: 10 Simultaneous Uploads**
The 4 Celery workers immediately accept 4 tasks. FFmpeg attempts to utilize all available CPU threads. Simultaneously, Whisper attempts to utilize CPU threads for inference. The system hits 100% CPU saturation and severe context-switching overhead. API requests (e.g., users fetching their feeds[cite: 15]) queued on the same server time out. The OS OOM-killer steps in and terminates the Celery processes. The remaining 6 uploads fail or are infinitely retried.

---

## Memory Exhaustion Analysis

Loading ML models natively inside Celery worker processes is a severe architectural flaw at scale.
*   **Model Duplication:** Python does not easily share memory across forked processes for complex objects. You are paying a 1:1 memory tax for every worker concurrency slot.
*   **Fragmentation:** `librosa` expands compressed audio (e.g., MP3) into uncompressed floating-point NumPy arrays[cite: 13]. A 5-minute audio clip can consume hundreds of megabytes of RAM during STFT (Short-Time Fourier Transform) computations.
*   **Container Eviction:** In Kubernetes, if a pod exceeds its memory limit due to this concurrent memory spike, the `kubelet` will evict the pod instantly, abandoning the media processing halfway through and leaving temporary files stranded.

---

## CPU Exhaustion Analysis

Both FFmpeg and modern ML inference engines (like CTranslate2 used by `faster-whisper`) are designed to aggressively parallelize across available CPU cores.
*   **Contention:** When `process_audio_to_hls` runs[cite: 13], it executes FFmpeg and Whisper sequentially. However, if multiple workers run simultaneously, FFmpeg on Worker 1 will compete for CPU cache and cycles with Whisper on Worker 2.
*   **API Starvation:** If the Django API[cite: 15] shares this CPU, Gunicorn threads will fail to respond to health checks, causing load balancers to mark the instance as dead and drop active user traffic.

---

## Worker Concurrency Analysis & Queue Architecture

Celery is an asynchronous task queue, not a resource scheduler. It blindly feeds tasks to workers until they crash. We must enforce strict queue topology.

**Recommended Queue Classes:**
1.  **`q_high_priority` (API Tier):** Password resets, email sends, lightweight DB updates. Runs on application nodes.
2.  **`q_feed_generation` (Feed Tier):** Fast vector math for `calculate_time_decayed_vectors`[cite: 13]. Runs on CPU-optimized nodes. High concurrency (e.g., 8-16 workers per node).
3.  **`q_media_transcode` (Media Tier):** Strictly for FFmpeg[cite: 13]. Concurrency capped strictly to `number_of_cores / 2`.
4.  **`q_ml_inference` (ML Tier):** Strictly for Whisper, MiniLM, and Librosa[cite: 13]. Concurrency capped at `1` per GPU, or heavily throttled on CPU.

---

## ML and Media Worker Isolation

**Status: Missing**

You must physically separate the blast radius. 

**1. The API / Application Node Pool**
*   Runs Django, Gunicorn, and lightweight Celery queues. 
*   *Rule:* No FFmpeg, no ML models loaded in memory.

**2. The Transcoding Worker Node Pool**
*   Dedicated EC2/EKS instances (Compute Optimized, e.g., AWS C6i).
*   Runs only FFmpeg.

**3. The Inference Abstraction (The Microservice)**
*   Extract `faster_whisper`, `SentenceTransformers`, and `KeyBERT` out of Celery entirely[cite: 13].
*   Wrap them in a dedicated FastAPI or Triton Inference Server container.
*   Celery workers do not load weights; they make an HTTP/gRPC call to the internal Inference Service. This allows you to load the model exactly *once* into memory and handle inference requests concurrently via batching.

---

## GPU Strategy

**Do you need GPUs right now? No.**
`faster-whisper` (base model) and `all-MiniLM-L6-v2` run exceptionally fast on standard CPUs[cite: 13]. A compute-optimized CPU node is significantly cheaper than a GPU node.

**When do you need GPUs?**
GPU inference becomes economically sensible only when you have high, sustained concurrent throughput that can be **dynamically batched**. Celery cannot natively batch HTTP requests. If you scale to 100,000 DAU, you will deploy a Triton Inference Server with a T4 or L4 GPU, configure dynamic batching (e.g., waiting 50ms to combine 16 transcription requests), and achieve massive throughput. Until then, stick to isolated CPU nodes.

---

## Autoscaling Strategy

*   **API Nodes:** Autoscale on **CPU Utilization** (Target 60%).
*   **Transcoding Workers:** CPU utilization is a false signal here (1 FFmpeg job will spike CPU to 100%). Autoscale based on **Queue Depth** (e.g., if `q_media_transcode` > 50 messages, add a node).
*   **ML Inference Service:** Autoscale based on **Request Latency / Queue Time**. If inference starts taking > 5 seconds, spin up another replica.

---

## Resource Abuse Protection

**Status: Missing**

Currently, any authenticated user can upload an audio file of infinite length or complexity[cite: 15].
*   **Decompression / Audio Bombs:** An attacker uploads a 10-hour highly compressed audio file. FFmpeg and `librosa`[cite: 13] will attempt to process it, consuming terabytes of RAM/Disk and crashing your infrastructure.
*   **Protection:** 
    1.  Enforce a hard file size limit (e.g., 50MB) at the API layer.
    2.  Use `ffprobe` in a lightweight pre-flight task to verify the actual media duration is under 3 minutes *before* routing it to the heavy processing queues. If it exceeds limits, reject the task.

---

## Recommended Deployment Topology

*   **Tier 1 (Synchronous):** Application Load Balancer → Django API Servers (ECS/EKS).
*   **Tier 2 (Asynchronous Fast):** Celery Workers (Feed & DB maintenance) → Connecting to RDS/Redis.
*   **Tier 3 (Asynchronous Heavy):** Celery Workers (FFmpeg) → Connecting to S3 for ingress/egress.
*   **Tier 4 (Inference):** Internal FastAPI/Triton Service → Holding ML models in RAM, answering HTTP requests from Tier 3.

---

## What Should Change in EchoFlow

| Component | Current Implementation | Target Implementation | Impact |
| :--- | :--- | :--- | :--- |
| **Model Loading** | Module-level in `tasks_2.py`[cite: 13] | Moved to standalone HTTP microservice | Stops Celery from multiplying model RAM footprint by worker count. |
| **Routing** | Implicit shared queue | Explicit `CELERY_ROUTES` for heavy/light tasks | Prevents transcoding spikes from delaying feed generation. |
| **Concurrency** | Celery default (cores) | Explicit concurrency caps (e.g., `-c 2`) on media nodes | Prevents FFmpeg thread collisions and CPU starvation. |

---

## P0/P1/P2/P3 Roadmap

*   **P0 (Immediate/Pre-Production):** Implement `ffprobe` duration limits to prevent resource exhaustion attacks. Configure strict queue routing in Django settings so `process_audio_to_hls`[cite: 15] never runs on the same physical server as the API.
*   **P1 (Near-Term):** Cap Celery concurrency on media workers to explicitly match available CPU threads, accounting for FFmpeg's multi-threading behavior.
*   **P2 (Scale):** Extract Whisper, MiniLM, and KeyBERT[cite: 13] from `tasks_2.py` into a separate internal containerized service to solve the memory duplication problem.
*   **P3 (Extreme Scale):** Migrate the internal inference service to GPU-backed nodes with dynamic batching.

---

## Final Senior-Engineer Verdict

**How do we guarantee that a massive spike in transcoding or ML workload cannot take down the API, database, authentication, or other user-critical services?**

You guarantee it through **physical isolation and asynchronous backpressure**. 

Currently, the code loads massive models into Celery and executes FFmpeg alongside standard tasks[cite: 13]. If deployed on a unified cluster, a spike in uploads will consume all CPU cycles, causing the API to fail health checks and load balancers to kill the application nodes. 

Celery does not solve this; it merely shifts the failure from a synchronous HTTP timeout to an asynchronous OOM crash. You must sever the compute pools. The API must run on a node pool that handles nothing but HTTP requests. Media tasks must be routed to a strictly separated node pool where autoscaling is driven entirely by queue depth. If 10,000 uploads hit the system, the queue depth will spike, media processing will be delayed, but the core application—authentication, swiping, playback, and database reads—will remain lightning fast and 100% responsive.