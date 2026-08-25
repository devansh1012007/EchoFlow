Audio reels 

- UI → last  
- AI / Recomendation → done  
  Next work on adding neural nets   
- Data gathering → i have set up some shit but this will be done after local deployment   
  Next → test it out   
- Backend – auth , 2 DB, reel sending \+ following \+ comments \+ all the things required for ranking and predicting audio \+ Tamper-Proof Metrics \+ adaptive bitrate → done   
  (next u work on making it hacker proof, add google login, thumb nail)  
- Deployment → make it work on windows and then on linux

## **Phase 1: Infrastructure and Foundation**

Your backend must be decoupled. The web server handles requests, the worker handles heavy lifting, the database stores truth, and the cache handles speed.

* **Core Stack:** Django (Web), Celery (Async Workers), PostgreSQL with `pgvector` (Primary Database), Redis (Queue/Cache).  
* **Containerization:** Fully Dockerized environment (`docker-compose.yml`) ensuring absolute parity between your local development and production servers.  
* **Security Baseline:** Field-level encryption using `cryptography` for PII (emails, phone numbers). Blind indexing (SHA-256) for secure credential searching. Role-based access control and JWT (JSON Web Tokens) for stateless authentication.

---

## **Phase 2: The Media Pipeline (Zero-Latency Audio)**

Serving raw MP3 files is unacceptable for a continuous scroll application. You must implement Adaptive Bitrate (ABR) streaming to prevent buffering.

1. **Ingestion:** User uploads a raw audio file to a Django endpoint. Django immediately saves the file to a temporary staging area and returns a `201 Created` to the client.  
2. **Processing (Celery):** A background worker picks up the file.  
   * Applies volume normalization using `librosa` or `ffmpeg`.  
   * Transcodes the audio into HTTP Live Streaming (HLS) format.  
   * Generates a Master Playlist (`.m3u8`) pointing to multiple quality variants (e.g., 192k, 96k, 48k).  
   * Slices the variants into 2-second `.ts` segments.  
3. **Storage & Delivery:** Move the processed HLS segments to an Object Storage bucket (AWS S3) and serve them globally through a Content Delivery Network (AWS CloudFront).

---

## **Phase 3: The Intelligence Engine (Recommendation System)**

To achieve the "addictive" factor, your app must learn user preferences in real-time. This requires a multi-tiered algorithmic approach.

### **Tier 1: Content-Based Filtering (The Cold Start)**

* **Audio-to-Text:** Pass every new upload through OpenAI Whisper via a Celery task. Extract the transcript.  
* **Semantic Vectors:** Generate a 1536-dimensional vector embedding of the transcript and store it in PostgreSQL using the `VectorField`.  
* **Initial Matching:** When a new user joins, use cosine similarity in `pgvector` to serve clips matching their explicit category selections.

### **Tier 2: Relational Discovery (Graph Neural Networks)**

* **Graph Construction:** Map your `UserInteraction` table into a Bipartite Graph. Nodes are Users and Clips; Edges are Likes, Shares, and Skips.  
* **Batch Training:** Export this graph daily. Use a separate PyTorch Geometric container to train a LightGCN model.  
* **Embedding Refinement:** The model outputs Graph-Aware embeddings that capture complex human behavioral patterns. Push these refined vectors back into PostgreSQL.

---

## **Phase 4: Frontend Mechanics (The "Trance" State)**

The frontend (Flutter or React Native) must mask all network latency. The user must never see a loading spinner after the initial app launch.

1. **Batch Pre-fetching:** Upon launch, the app requests the next 10 clips. The backend calculates this batch using the user's current vector and the Redis hot-queue.  
2. **Parallel Loading:** The video/audio player mounts the first clip and immediately begins downloading the first 2-second `.ts` segment of the second clip in the background.  
3. **Silent State Syncing:** When a user double-clicks to skip or taps to like, the frontend instantly updates the UI (optimistic UI rendering). It then sends a silent background POST request to the `/toggle-like/` or `/skip/` endpoints.  
4. **Dynamic Queue Adjustment:** If the backend registers three consecutive skips on a specific genre, it flushes the remaining items in the user's Redis queue and calculates a new batch with a pivoted vector.

---

## **Phase 5: Production Deployment and Scalability**

Deploying this to the public requires a robust cloud architecture.

* **Database Management:** Migrate from local Docker Postgres to a managed database like AWS RDS for PostgreSQL. Ensure `pgvector` is supported by your instance class.  
* **Worker Auto-scaling:** Celery workers handling Whisper transcriptions will be CPU-bound. Use Kubernetes (EKS) or AWS ECS to automatically spin up additional worker nodes when the Redis queue exceeds a specific threshold.  
* **Load Balancing:** Deploy your Django API behind an Application Load Balancer. Run multiple identical Django containers across different availability zones to ensure high availability.  
* **Continuous Integration (CI/CD):** Implement GitHub Actions. Every push to the main branch must run automated tests, build the Docker images, and push them to an Elastic Container Registry (ECR) before triggering a rolling update on the servers.

---

## **Strategic Add-On Features**

To elevate the product beyond a basic MVP, incorporate these systems:

* **Creator Analytics Dashboard:** Provide creators with aggregated, anonymized data on retention graphs (where users skip within the audio) and share-to-listen ratios.  
* **Deep Linking:** Ensure every shared URL opens directly into the app and immediately plays the specific HLS stream, bypassing the main feed.  
* **Offline Mode Integration:** Allow the frontend to cache the next 50 MB of HLS segments locally so the app remains functional during temporary network drops (e.g., entering a subway).

# Features :

## 1\. The Core User Experience (The "Trance")

These features ensure the user never sees a loading spinner and stays entirely immersed in the audio feed.

* Zero-Latency Playback: Audio starts in under 500ms using HTTP Live Streaming (HLS). Instead of downloading heavy MP3s, the app streams tiny, 2-second audio chunks (.ts files).  
* Adaptive Bitrate Streaming (ABR): The app automatically detects the user's internet speed and seamlessly shifts audio quality (192kbps to 48kbps) without pausing or buffering, even if they enter a subway or lose 5G.  
* Predictive Pre-Fetching: The backend anticipates the user's behavior. When a user logs in, the app downloads a "Batch" of the next 10 stream URLs into local memory so the "Next" action is always instantaneous.  
* Real-Time Mood Pivoting: If a user double-clicks to skip three "Science" clips in a row, the app intercepts this behavior, flushes the current pre-fetched queue, and instantly refills it with a different genre (e.g., "Comedy") based on sequential Transformer AI logic.

## 2\. The Interaction & Social Engine

Echo Flow moves beyond static listening by building a web of user-to-user and user-to-content interactions.

* Frictionless Engagement: Single-tap actions for liking and sharing, backed by atomic database updates (F() expressions) to prevent race conditions even under heavy server load.  
* Direct "Share-to-Inbox" Routing: Users can send clips directly to another user's internal Echo Flow inbox. This creates a high-retention loop where users open the app just to see what their friends sent them.  
* Tamper-Proof Metrics: Read-only engagement numbers. Users cannot spoof "Likes" or "Skips" via the API; all metrics are calculated securely on the backend via a dedicated UserInteraction ledger.  
* Invisible Skip Tracking: A silent tracking system that records exactly when a user skips an audio clip, feeding critical negative-feedback data to the recommendation algorithm.

## 3\. The "Echo Brain" (AI & Recommendation Funnel)

This is the multi-layered intelligence system that makes Echo Flow competitive with TikTok's "For You" page.

* Automated Audio Intelligence (Ingestion): Every uploaded clip is processed in the background by OpenAI Whisper to generate a full text transcript.  
* Semantic Vectorizing: The transcript is converted into a 1536-dimensional mathematical vector that represents the "vibe" and meaning of the audio, stored natively in PostgreSQL using pgvector.  
* Multi-Stage Recommendation Funnel:  
  * Stage 1 (Retrieval): A Two-Tower Neural Network rapidly scans millions of clips and uses vector similarity to pull the top 500 clips that match the user's historical profile.  
  * Stage 2 (Relational Discovery): Graph Neural Networks (LightGCN) analyze the "web" of user interactions to find hyper-specific niche communities (e.g., bridging users who like both "Astrophysics" and "Lo-Fi Beats").  
  * Stage 3 (Sequential Ranking): Transformer models (like SASRec) look at the exact sequence of the user's current session to predict the very next clip they want to hear right now.

## 4\. The Security & Trust Center

User data is locked down using enterprise-grade cryptographic standards.

* Field-Level Encryption: Personally Identifiable Information (PII), such as email addresses, is encrypted at rest using symmetric Fernet encryption. Even if the database is breached, the data remains unreadable.  
* Blind Indexing for Authentication: User emails are run through a one-way SHA-256 hash. The system uses this hash to verify logins quickly without ever exposing the decrypted email to search queries.  
* Stateless JWT Authentication: Secure, token-based user sessions that allow the mobile app to communicate with the API without storing sensitive session data on the server.  
* API Throttling & Bot Protection: Strict rate-limiting on interaction endpoints (e.g., maximum 10 likes per minute) to prevent botnets from artificially boosting a clip's virality.

## 5\. The Infrastructure Backbone

Designed to be decoupled, scalable, and resilient.

* Asynchronous Media Processing: A Celery task queue (backed by Redis) handles all heavy lifting—like FFmpeg audio transcoding and Whisper AI transcription—so the main web server never freezes or drops user requests.  
* Hybrid Database Architecture: \* PostgreSQL (Long-term Memory): Stores relational data, user profiles, and complex AI vectors.  
  * Redis (Short-term Memory): Acts as the "Hot Queue" message broker, serving the next clips instantly and handling rapid interaction logging.  
* Fully Containerized Environment: A complete docker-compose setup ensuring that the environment running on your local machine is identically mirrored in the production cloud deployment.

---

# Issues faced 1(migration sequence error):

- Django's `swappable_dependency(settings.AUTH_USER_MODEL)` resolves to the **first migration** of `app_1`. When we had `0000_enable_pgvector` as a separate file, Django thought the User model was satisfied after pgvector was enabled — so `admin.0001_initial` ran before the User table was ever created. 

  #### **Everything we tried before finding it**

*  `docker compose down -v` multiple times — DB was clean, problem was in migration files  
*  Separate `0000_enable_pgvector.py` \+ `0001_initial.py` with explicit dependencies — didn't fix swappable\_dependency resolution  
*  `MIGRATION_MODULES = {'account': None}` — bypassed allauth but not the core issue  
*  `--run-syncdb` — made it worse, caused a different `app_1_user` table error  
*  Manual `makemigrations` — kept saying "No changes detected" due to stale container state  
*  `pg_isready` wait script — `python:3.11-slim` doesn't have PostgreSQL client tools

#### **What actually fixed it**

**Merged `0000_enable_pgvector` into `0001_initial`** as the first `RunSQL` operation, with `initial = True` and `swappable_dependency` in dependencies.

This made `0001_initial` the true first migration, so Django correctly waited for User to exist before running admin/allauth migrations.

#### 

#### **Permanent lessons for next Django project**

* Always put `CREATE EXTENSION` inside `0001_initial` as a `RunSQL` op, not a separate `0000` file  
* Always include `migrations.swappable_dependency(settings.AUTH_USER_MODEL)` in `0001_initial.py` dependencies when using a custom User model  
* Use `python wait_for_db.py` not `sleep N` or `pg_isready` for DB readiness  
* `docker compose down -v` only helps if the migration files themselves are correct

