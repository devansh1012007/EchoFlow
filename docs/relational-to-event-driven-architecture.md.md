# docs/relational-to-event-driven-architecture.md

## Executive Summary
EchoFlow currently operates on a hybrid architecture: a synchronous request/response model backed by PostgreSQL, paired with an asynchronous job-processing queue using Celery and Redis[cite: 13, 14, 15]. While Celery handles asynchronous background work (e.g., media transcoding and feed pre-computation)[cite: 13], the core application logic is strictly coupled to synchronous relational transactions in PostgreSQL[cite: 11, 15].

An Event-Driven Architecture (EDA) is not an all-or-nothing proposition. Evolving EchoFlow prematurely to a heavy distributed streaming platform (e.g., Apache Kafka with Kafka Connect, Flink, and schema registries) would introduce catastrophic operational overhead without solving the immediate bottleneck. The correct strategy is **bounded event-driven evolution**: maintaining PostgreSQL as the immutable, strongly consistent source of truth for authoritative entities (identity, permissions, metadata, and relations)[cite: 11], while systematically carving out high-throughput, volatile, and fan-out workloads (telemetry, counter aggregation, vector feature pipelines, and feed invalidation) into asynchronous, event-driven streams.

---

## Current Relational Architecture
EchoFlow’s persistence model is centered on a single relational database (PostgreSQL with `pgvector`)[cite: 11, 14]. 


```

+-----------------------------------------------------------------------------------+
|                                 CLIENT DEVICE                                     |
+-----------------------------------------------------------------------------------+
| (REST / JSON)                      | (REST / Telemetry)           | (HLS / ABR)
v                                    v                              v
+------------------------+        +------------------------+     +----------------------+
|  Gunicorn / Django API |        |  Gunicorn / Django API |     |  Direct S3 / CDN     |
| (Auth, Upload, Social) |        |   (Telemetry, Likes)   |     |    (Media Chunks)    |
+------------------------+        +------------------------+     +----------------------+
|               \                      |                              ^
| (ORM Txn)      \ (delay)             | (update_or_create)           | (FFmpeg write)
v                 v                    v                              |
+----------------+   +-------------------+  +----------------+     +--------------------+
|  PostgreSQL    |   | Redis Broker / DB |  | PostgreSQL     |     | Celery Media Worker|
|  (Core State)  |   | (Queues & Feeds)  |  | (Interactions) |     | (Whisper, Librosa) |
+----------------+   +-------------------+  +----------------+     +--------------------+

```

*   **Entities & State:** Authoritative state for `User`, `AudioClip`, `Comment`, `ShareEvent`, and `UserInteraction` resides inside PostgreSQL tables[cite: 11].
*   **Vector State:** High-dimensional embeddings (`semantic_vector`, `acoustic_vector`) are stored directly as column attributes on relational rows in PostgreSQL[cite: 11].
*   **Coupling:** Writes to domain entities immediately trigger synchronous side-effects (e.g., `UserInteraction.save()` executing atomic counter updates on `AudioClip` via `F()` expressions)[cite: 11].

---

## Current Synchronous and Asynchronous Flows

### 1. Synchronous Request/Response Flows
*   **Authentication & Profile Onboarding:** `TagsViewSet.initialize_vectors` synchronously queries the top 100 clips, calculates the vector mean, and writes `long_term_semantic` and `long_term_acoustic` to the `User` table[cite: 14, 15].
*   **Telemetry Ingestion:** `ClipInteractionViewSet.log_telemetry` and `toggle_like` synchronously execute `update_or_create` against `UserInteraction` and run inline SQL increments against `AudioClip`[cite: 11, 15].
*   **Social & Follow Operations:** `FollowViewSet.toggle_follow` directly mutates the `User.following` Many-to-Many relational join table[cite: 11, 15].
*   **Fast Feed Retrieval:** `FastFeedViewSet.list` attempts an atomic Redis `lpop`[cite: 15]. If the queue is empty, it executes a fallback synchronous refill task `refill_user_feed(user_id, count=10)` within the HTTP request thread[cite: 15].

### 2. Asynchronous Job Flows (Celery Task Invocations)
*   **Audio Ingestion Pipeline:** `AudioUploadViewSet.create` writes a row with `status='processing'` and dispatches `process_audio_to_hls.delay(clip.id)` to Redis[cite: 11, 14, 15].
*   **Background Feed Top-up:** `FastFeedViewSet` triggers `refill_user_feed.delay(user_id)` when the Redis queue length drops below 15[cite: 15].
*   **Scheduled Crons (Celery Beat):** `update_global_metrics` executes raw SQL mass updates every 10 minutes; `evolve_long_term_user_baselines` iterates through active users daily to recalculate long-term vector baselines[cite: 13, 14].

---

## Natural Domain Events
When decomposing EchoFlow, events represent facts that have already occurred within the domain.

| Domain Event | Producer | Payload (Key Schema) | Consumers | Criticality |
| :--- | :--- | :--- | :--- | :--- |
| `AudioUploaded` | `AudioUploadViewSet`[cite: 15] | `{clip_id, creator_id, s3_key, timestamp}` | Media Transcoder, Security Scanner | High |
| `AudioTranscoded` | `Celery Media Worker`[cite: 13] | `{clip_id, duration_ms, hls_url, acoustic_vector}` | AI Transcription Worker, Vector Extractor | High |
| `AudioPublished` | `Celery Worker`[cite: 13] | `{clip_id, creator_id, category, tags, vectors}` | Feed Refresher, Creator Follower Fan-out, Search Index | High |
| `AudioPlayed` | Client Telemetry[cite: 15] | `{user_id, clip_id, session_id, timestamp}` | Real-time Context Aggregator, Play Counter | Low (Loss-tolerant) |
| `AudioCompleted` | Client Telemetry[cite: 15] | `{user_id, clip_id, watch_time_ms, completion_rate}` | User Vector Decay Engine, Recommendation Feedback | Medium |
| `AudioLiked` / `Unliked` | `ClipInteractionViewSet`[cite: 15] | `{user_id, clip_id, is_active, timestamp}` | Counter Aggregator, User Vector Blend, Creator Notification | Medium |
| `AudioShared` | `ShareViewSet`[cite: 15] | `{sender_id, receiver_id, clip_id, timestamp}` | Inbox Service, Notification Service, Velocity Scorer | High |
| `UserFollowed` | `FollowViewSet`[cite: 15] | `{follower_id, target_id, timestamp}` | Follow Graph Service, Feed Invalidation Worker | Medium |
| `CommentCreated` | `CommentViewSet`[cite: 15] | `{comment_id, clip_id, author_id, parent_id}` | Counter Aggregator, Moderation Worker, Notification | Medium |

---

## Where Relational Architecture Breaks Down


```

```
                                +-----------------------+
                                |  Concurrent Telemetry |
                                |  (5,000+ swipes/sec)  |
                                +-----------------------+
                                            |
                                            v
           +-----------------------------------------------------------------+
           |                   GUNICORN APPLICATION THREADS                   |
           +-----------------------------------------------------------------+
                      |                             |                      |
        (Row Lock 1)  v               (Row Lock 2)  v        (Row Lock 3)  v

```

+------------------------------------------------------------------------------------------------+
|                                    POSTGRESQL PRIMARY NODE                                     |
|                                                                                                |
|   UPDATE "app_1_audioclip" SET likes = likes + 1 WHERE id = 'hot-viral-uuid';                  |
|   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^                   |
|   * Lock Contention: Hundreds of connections queuing for the same row lock                     |
|   * WAL Starvation: Massive write-ahead log generated from constant tuple updates              |
|   * Autovacuum Saturation: Dead tuples accumulate faster than autovacuum can reclaim disk      |
|   * Connection Exhaustion: Gunicorn threads block waiting for Postgres; API goes down          |
+------------------------------------------------------------------------------------------------+

```

1.  **Row-Level Lock Contention on Viral Nodes:** Synchronous increments (`likes`, `shares`, `skips`) on the `AudioClip` table cause concurrent transactions to block on the same physical row tuple[cite: 11]. Under high concurrency (e.g., 500 likes/sec on a trending clip), transactions queue up, exhaust connection pools, and trigger 504 Gateway Timeouts across the entire API.
2.  **Autovacuum and MVCC Bloat:** PostgreSQL’s Multi-Version Concurrency Control (MVCC) writes a new tuple version on every single `UserInteraction` upsert and counter change[cite: 11]. At 10M events per day, the table and its associated indexes generate millions of dead tuples. The autovacuum process cannot keep pace, degrading index traversal speed and ballooning storage.
3.  **Cross-Domain Synchronous Coupling:** When a user creates a comment, the database synchronously manages parent-child relational tree lookups and updates parent clip counters inside the same transaction[cite: 11]. If an external notification or moderation system is added to this block, comment latency scales linearly with external system availability.
4.  **Feed Cache Thrashing:** Because the relational database directly handles updates, the caching layer (Redis `user_feed:{id}`) has no clean, decoupled way of knowing when an item in a user's pre-computed queue has been deleted, hidden, or moderated[cite: 14, 15].

---

## Relational vs Event-Driven Boundary


```

+-------------------------------------------------------+-------------------------------------------------------+
|              STRONGLY RELATIONAL CORE                 |                 EVENT-DRIVEN STREAM                   |
|       (PostgreSQL: ACID, Strong Consistency)          |         (Streams & Workers: Eventual Consistency)     |
+-------------------------------------------------------+-------------------------------------------------------+
|  * User Identity & Auth Credentials                   |  * Real-Time Telemetry (watch_time_ms, skips)    |
|  * Permissions & Role Boundaries                      |  * Aggregate Counters (likes, shares, views)     |
|  * Canonical Content Metadata (UUID, S3 Keys) |  * Vector Recalculation & Blending Pipelines  |
|  * Direct Social Graph State (Follow/Block lists)     |  * Notification Fan-Out & Social Inboxes   |
|  * Financial & Monetization Records                   |  * Content Moderation & Abuse Anomaly Triggers        |
+-------------------------------------------------------+-------------------------------------------------------+

```

### 1. What Remains Relational (The Authoritative Source of Truth)
*   **User Identity and Credentials:** User records, encrypted emails, and permission flags require strict ACID compliance and immediate read-after-write consistency during authentication[cite: 11].
*   **Canonical Metadata:** The definitive existence, title, category, owner/creator ID, and original object storage key of an `AudioClip`[cite: 11].
*   **Social Graph Topology:** Authoritative state of who follows whom[cite: 11]. (The *fan-out* of content across that graph is event-driven; the *existence* of the edge is relational).

### 2. What Becomes Event-Driven (The Derived & Volatile Ecosystem)
*   **Telemetry & Behavioral Ingestion:** Raw play progress, dwell duration, and skip metrics[cite: 11, 15].
*   **Counters and Engagement Velocity:** Aggregate tallies (`likes`, `shares`, `skips`)[cite: 11].
*   **Contextual Feature Stores:** User short-term mood vectors and dynamic vector recalculation[cite: 13, 14].
*   **Feed Queue Materialization:** The asynchronous population and eviction of Redis feed queues[cite: 13, 15].
*   **Downstream Fan-Out:** Inbox delivery of shared clips and push notification dispatches[cite: 11, 15].

---

## Event Taxonomy
To ensure decoupled systems do not break downstream consumers, events must follow a strict schema contract.

```json
{
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "event_type": "interaction.telemetry.logged",
  "schema_version": "1.2.0",
  "timestamp": 1787140257123,
  "producer": "echoflow-api-telemetry",
  "data": {
    "user_id": 48291,
    "clip_id": "a8c9823e-6f89-4d2a-89a3-19f864192b01",
    "action_type": "view",
    "watch_time_ms": 14200,
    "clip_duration_ms": 15000,
    "completion_rate": 0.946,
    "session_id": "sess_810fa2bc"
  },
  "metadata": {
    "trace_id": "c8a49102-4b21-44aa-9c02-39294821a910",
    "client_version": "ios-2.4.1"
  }
}

```

### Event Categorization

1. **Notification Events (Thin Events):** Emit only the IDs (e.g., `{"clip_id": "uuid", "event": "published"}`). Forces consumers to query the relational DB for details. *Use case: Low-frequency administrative changes.*
2. **Event-Carried State Transfer (Fat Events):** Contains all data necessary to process the event without querying the primary database (e.g., the telemetry schema above). *Use case: High-volume streaming, vector updates, and telemetry pipelines.*

---

## Event Delivery Guarantees

```
+---------------------------------------------------------------------------------------------------+
|                                  THE AT-LEAST-ONCE REALITY                                        |
+---------------------------------------------------------------------------------------------------+
|                                                                                                   |
|   Producer                 Broker / Stream                     Consumer             Idempotency   |
|   +--------+  Publish Event  +--------+   Deliver Message 1   +----------+          Storage       |
|   |  API   | --------------->| Redis/ | --------------------->| Consumer | ------> (Redis SET)    |
|   +--------+                 | Kafka  |                       +----------+         Is processed?  |
|                              +--------+                             |               No -> Execute |
|                                   |                                 | Network Fail        & Mark  |
|                                   |       Redeliver Message 1       v                             |
|                                   +--------------------------->+----------+                       |
|                                                                | Consumer | ------> (Redis SET)   |
|                                                                +----------+         Is processed? |
|                                                                                     Yes -> ACK    |
+---------------------------------------------------------------------------------------------------+

```

### 1. The Myth of "Exactly-Once"

True end-to-end "exactly-once" delivery across distributed boundaries (Client $\rightarrow$ API $\rightarrow$ Broker $\rightarrow$ DB/Cache) is a physical impossibility without severe distributed locking protocols (e.g., Two-Phase Commit / 2PC) that destroy system availability and latency. EchoFlow explicitly designs for **At-Least-Once Delivery with Idempotent Consumer Execution**.

### 2. Idempotent Consumer Mechanics

Every event consumer must defend against duplicate message delivery:

* **Natural Idempotency:** Overwriting state using immutable assignments rather than relative mutations (e.g., setting `status = 'ready'` is naturally idempotent; executing `counter += 1` is not).


* **Deduplication Keys via Atomic Check-and-Set:** Non-idempotent operations (such as processing a payment or incrementing a counter) must write the `event_id` to an atomic in-memory cache (Redis) with a TTL (e.g., 24 hours):
```python
def process_event(event):
    dedup_key = f"processed_event:{event['event_id']}"
    # Set NX returns True only if key did not previously exist
    if not redis_client.set(dedup_key, "1", nx=True, ex=86400):
        logger.info(f"Duplicate event {event['event_id']} dropped.")
        return

    # Execute business logic safely
    apply_telemetry_to_feature_store(event['data'])

```



---

## Transactional Outbox Analysis

A fundamental failure mode in dual-write architectures is the **Dual-Write Consistency Gap**:

```
Scenario A: DB Commit succeeds -> Network dies -> Event Publish fails (Event Lost forever)
Scenario B: Event Publish succeeds -> DB Commit fails/aborts (Phantom Event processed by workers)

```

To eliminate phantom events and lost domain state, EchoFlow adopts the **Transactional Outbox Pattern** for critical business transitions (such as `AudioPublished` or `UserFollowed`).

```
+----------------------------------------------------------------------------------------------------+
|                                    TRANSACTIONAL OUTBOX PATTERN                                    |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  [ API Request Thread ]                                                                            |
|        |                                                                                           |
|        v                                                                                           |
|  BEGIN TRANSACTION;                                                                                |
|    INSERT INTO app_1_audioclip (id, title, status, ...) VALUES (...);                              |
|    INSERT INTO outbox_table (id, event_type, payload, status) VALUES (uuid, 'AudioCreated', ...); |
|  COMMIT;  <--- Guaranteed atomic consistency via Postgres ACID engine                             |
|                                                                                                    |
|                                                                                                    |
|  [ Outbox Relay Daemon / Debezium CDC Engine ]                                                     |
|        |                                                                                           |
|        | Polls outbox_table OR tails Postgres WAL stream via Logical Decoding                      |
|        v                                                                                           |
|  Publishes message to Redis Stream / Kafka Topic                                                   |
|        |                                                                                           |
|        v                                                                                           |
|  UPDATE outbox_table SET status = 'PUBLISHED' WHERE id = uuid; (or advances CDC LSN offset)        |
+----------------------------------------------------------------------------------------------------+

```

### Outbox Schema in PostgreSQL

```sql
CREATE TABLE event_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed_at TIMESTAMP WITH TIME ZONE NULL
);
CREATE INDEX idx_outbox_unprocessed ON event_outbox (created_at) WHERE processed_at IS NULL;

```

---

## Broker Comparison

| Feature | Redis Streams | RabbitMQ | Apache Kafka | AWS Kinesis |
| --- | --- | --- | --- | --- |
| **Throughput Target** | Up to 50k msg/sec | Up to 30k msg/sec | 1M+ msg/sec | 100k+ msg/sec |
| **Ordering Guarantees** | Strict per Stream | Strict per Queue | Strict per Partition | Strict per Shard |
| **Replayability** | Yes (Retains history) | No (Destructive read) | Yes (Offset-based) | Yes (Time-based window) |
| **Operational Overhead** | Extremely Low (Already in stack)

 | Medium (Erlang runtime, clustering) | High (Zookeeper/KRaft, JVM, tuning) | Low (Fully managed AWS) |
| **Cost at Low-to-Mid Scale** | Included in existing Redis RAM

 | Low (Small compute instance) | High (Minimum 3 broker nodes) | Pay-per-shard cost |
| **Storage Architecture** | RAM with optional RDB/AOF | Disk/RAM queues | Append-only Disk Log | Append-only Cloud Log |

### The Pragmatic Broker Verdict for EchoFlow

* **Phase 1 & 2 (< 50,000 DAU):** **Redis Streams**. EchoFlow already operates a Redis cluster for feed caching and Celery queues. Redis Streams provides Consumer Groups, message persistence, explicit acknowledgments (`XACK`), and replay capabilities without introducing a new infrastructure dependency.


* **Phase 3+ (> 1,000,000 DAU / Enterprise Scale):** **Apache Kafka** (or Managed AWS Kinesis / GCP Pub/Sub). Justified *only* when telemetry volume exceeds Redis RAM constraints and requires streaming analytics engines (e.g., Apache Flink) for complex event processing.

---

## Recommendation-System Event Flow

```
                                  RECOMMENDATION EVENT PIPELINE
                                  
+----------------------+
|  User Interaction    |
| (Dwell time / Swipe) |
+----------------------+
           |
           | POST /api/v1/interactions/log-telemetry/[cite: 15]
           v
+----------------------+
|   API Gateway / Edge | ---> [ Returns 202 Accepted instantly (<5ms) ]
+----------------------+
           |
           | XADD stream:telemetry:events[cite: 15]
           v
+------------------------------------------------------------------------------------------------+
|                                      REDIS EVENT STREAM                                        |
+------------------------------------------------------------------------------------------------+
           |                                             |
           | Consumer Group: feature-engineers           | Consumer Group: counter-accumulators
           v                                             v
+------------------------------------+         +------------------------------------+
| Dynamic Vector Processing Worker   |         | High-Speed Counter Micro-Batcher   |
| (Calculate Log Decay & Weights)    |         | (Aggregates likes, shares, views)  |
+------------------------------------+         +------------------------------------+
           |                                             |
           | Updates volatile Context Vector             | Flushes micro-batch to Postgres
           v                                             v
+------------------------------------+         +------------------------------------+
| Redis User Context Feature Store   |         | PostgreSQL Core DB (`AudioClip`)  |
| `user:context:{id}`[cite: 13, 14] |         | (1 Write per 500 events)[cite: 11]|
+------------------------------------+         +------------------------------------+
           |
           | Read by Celery Feed Top-up Worker
           v
+------------------------------------+
| Pre-Calculated User Hot Queue      |
| `user_feed:{id}`[cite: 13, 14]    |
+------------------------------------+

```

1. **Ingestion:** The client emits telemetry events asynchronously. The API pushes the raw event into `stream:telemetry:events` using `XADD` and immediately responds to the client.


2. **Streaming Context Updates:** A dedicated Python consumer worker reads from the stream in real-time, calculates the logarithmic time-decayed context vector using NumPy, and stores the resulting vector in a Redis key `user:context:{id}`.


3. **Decoupled Feed Assembly:** The `refill_user_feed` task no longer executes expensive, multi-row historical joins against PostgreSQL `UserInteraction` tables. It reads the pre-computed user context vector directly from Redis in $<1\text{ms}$ and performs vector retrieval against candidate pools.


4. **Asynchronous Retraining:** A nightly batch worker reads cold telemetry data from long-term storage to re-train global embeddings and recalculate user long-term baselines.



---

## Eventual Consistency Model

```
+----------------------------------+-----------------------+------------------------+---------------------------------------+
| Subsystem                        | Consistency Model     | Max Tolerable Latency  | UX Degradation Mitigation Strategy   |
+----------------------------------+-----------------------+------------------------+---------------------------------------+
| User Follow Status[cite: 11]    | Strong (ACID)         | 0 ms (Immediate)       | Read directly from primary relational |
|                                  |                       |                        | database upon authorization checks    |
+----------------------------------+-----------------------+------------------------+---------------------------------------+
| Clip Like / Share Counts         | Eventual Consistency  | 2 - 5 seconds          | Client-side Optimistic UI update      |
|[cite: 11, 15]                   |                       |                        | (toggles heart icon immediately)      |
+----------------------------------+-----------------------+------------------------+---------------------------------------+
| Recommendation Vector Mood Shift | Eventual Consistency  | 5 - 30 seconds         | Current feed queue contains enough    |
|[cite: 13]                       |                       |                        | buffer clips to mask latency[cite: 15]|
+----------------------------------+-----------------------+------------------------+---------------------------------------+
| Shared Inbox Delivery[cite: 11] | Eventual Consistency  | 500 ms - 2 seconds     | Background WebSocket / Push notify    |
|                                  |                       |                        | alerts recipient when event commits   |
+----------------------------------+-----------------------+------------------------+---------------------------------------+
| Global Velocity Scorer[cite: 13]| Eventual Consistency  | 10 minutes             | Exploit/Explore bandit algorithm      |
|                                  |                       |                        | introduces natural entropy[cite: 13] |
+----------------------------------+-----------------------+------------------------+---------------------------------------+

```

---

## Failure and Recovery Architecture

```
                                  CONSUMER FAILURE & DEAD-LETTER QUEUE
                                  
+----------------------------------------------------------------------------------------------------+
| Stream Consumer Worker                                                                             |
|                                                                                                    |
| Read Event from Stream                                                                             |
|   |                                                                                                |
|   v                                                                                                |
| Process Logic (e.g., Vector math) ----> [ Exception / Corrupt Payload ]                           |
|                                              |                                                     |
|                                              | Retry Count < 3                                     |
|                                              +-----------------> Exponential Backoff Sleep         |
|                                              |                   Retry Task                        |
|                                              |                                                     |
|                                              | Retry Count >= 3                                    |
|                                              v                                                     |
|                                        Route to Dead-Letter Queue (`stream:telemetry:dlq`)         |
|                                        Emit PagerDuty / Sentry Alert                               |
|                                        Acknowledge Original Stream (`XACK`) to unblock pipeline    |
+----------------------------------------------------------------------------------------------------+

```

### Failure Scenarios & Self-Healing Protocols

1. **Broker Crash (Redis Outage):**
* *Behavior:* The API cannot write to `stream:telemetry:events`.
* *Recovery:* The API gracefully catches connection exceptions, buffers telemetry events to an ephemeral local disk append-only log file, and returns `202 Accepted` to the client. When Redis reconnects, a sidecar daemon replays the local disk logs into the stream.


2. **Poison Pill Messages (Malformed Event Payloads):**
* *Behavior:* A client emits an invalid payload that causes the vector processor to throw an unhandled exception.
* *Recovery:* The consumer catches the failure, increments the event’s delivery attempt counter, and after 3 failures, moves the message into a **Dead-Letter Queue (DLQ)** (`stream:telemetry:dlq`). The main stream consumer acknowledges the message and moves to the next offset without stalling.


3. **Consumer Lag Accumulation:**
* *Behavior:* Ingestion rate exceeds consumer processing speed during a traffic spike.
* *Recovery:* Autoscale consumer worker pods horizontally based on the lag metric (`XPENDING` count in Redis). Because consumer groups partition the workload, adding workers linearly scales processing throughput.



---

## Observability Requirements

Operating an event-driven system blind is fatal. The following telemetry and monitoring primitives are non-negotiable:

* **End-to-End Distributed Tracing (OpenTelemetry):** Inject a `traceparent` correlation ID into the client request header. Carry this `trace_id` through the API, into the outbox table, inside the event metadata payload, and across all asynchronous workers and Celery tasks.


* **Consumer Lag Metrics (Prometheus / Grafana):**
* `event_stream_lag_seconds`: The time difference between the newest message timestamp and the message currently being processed by the consumer group.
* `event_stream_pending_messages`: Total messages read but not yet acknowledged via `XACK`.


* **Outbox Backlog Depth:** Metric tracking unprocessed rows in `event_outbox`. Alert fires if count exceeds 1,000 for $> 2$ minutes.
* **Dead-Letter Queue Alerts:** Any message arriving in `stream:*:dlq` must immediately trigger an alert in Sentry.

---

## Migration Strategy

```
+--------------------------------------------------------------------------------------------------+
|                                    7-STAGE MIGRATION ROADMAP                                     |
+--------------------------------------------------------------------------------------------------+
|                                                                                                  |
|  Stage 1: Monolithic Relational (Current State)[cite: 11, 14, 15]                               |
|    * All writes hit PostgreSQL directly; Celery handles ad-hoc background tasks.                 |
|                                                                                                  |
|  Stage 2: Interface Boundary Isolation (Code-level refactor)                                     |
|    * Wrap all direct ORM calls in Service Layer abstractions (e.g., `InteractionService`).       |
|    * Remove model `save()` trigger overrides[cite: 11].                                         |
|                                                                                                  |
|  Stage 3: Outbox & Dual-Writing Validation                                                       |
|    * Introduce `event_outbox` table in Postgres. Write domain events inside transactions.         |
|    * Validate event generation fidelity without altering read paths.                             |
|                                                                                                  |
|  Stage 4: Telemetry Stream Extraction (Highest Load Carver)                                      |
|    * Route `/api/v1/interactions/log-telemetry/` directly to Redis Streams[cite: 15].           |
|    * Workers consume stream and batch-write to Postgres; API DB load drops by 90%.                |
|                                                                                                  |
|  Stage 5: Recommendation Decoupling                                                              |
|    * Stream consumers update Redis User Context Vectors[cite: 13, 14].                          |
|    * Feed generation queries Redis context instead of joining Postgres interaction history.       |
|                                                                                                  |
|  Stage 6: Social Fan-out & Inbox Decoupling                                                      |
|    * Migrate `ShareViewSet` and `CommentViewSet` to emit events[cite: 15].                      |
|    * Background consumers handle recipient inbox populations and push notifications.             |
|                                                                                                  |
|  Stage 7: Dedicated Streaming Engine (Only at Massive Scale: > 1M DAU)                           |
|    * Transition stream broker from Redis Streams to Apache Kafka / Managed Kinesis.              |
|    * Stream cold analytics directly into ClickHouse.                                             |
+--------------------------------------------------------------------------------------------------+

```

---

## Target Architecture

```
+==================================================================================================+
|                                     ECHOFLOW TARGET ARCHITECTURE                                 |
+==================================================================================================+
                                                 |
                                                 | HTTPS / JSON (REST API)
                                                 v
                     +-------------------------------------------------------+
                     |         API GATEWAY / LOAD BALANCER (Stateless)       |
                     +-------------------------------------------------------+
                                        |                         |
               (Authoritative Write / Auth)                     (High-Throughput Telemetry)
                                        |                         |
                                        v                         v
        +-----------------------------------------+   +------------------------------------+
        |      CORE APPLICATION SERVICE (Django)  |   |    TELEMETRY INGESTION SERVICE     |
        +-----------------------------------------+   +------------------------------------+
                            |                                           |
                   (ACID Transaction)                           (XADD Event Stream)
                            v                                           v
        +-----------------------------------------+   +------------------------------------+
        |         POSTGRESQL PRIMARY NODE         |   |         REDIS STREAMS BROKER       |
        |   - Users & Auth Credentials[cite: 11] |   |   - `stream:telemetry:events`      |
        |   - Canonical AudioClip Metadata        |   |   - `stream:domain:outbox`         |
        |   - Social Relationships (Follows)      |   +------------------------------------+
        |   - Transactional Event Outbox Table    |             |                 |
        +-----------------------------------------+             |                 |
                            |                                   |                 |
               (Logical Decoding / CDC Relay)                   |                 |
                            |                                   |                 |
                            v                                   |                 |
        +-----------------------------------------+             |                 |
        |      OUTBOX STREAM RELAY WORKER         |             |                 |
        +-----------------------------------------+             |                 |
                            |                                   |                 |
                            +-----------------------------------+                 |
                                             |                                    |
                                             v                                    v
        +----------------------------------------------------------------------------------+
        |                          ASYNC EVENT WORKER POOLS                                |
        |                                                                                  |
        |  [ Feature Stream Workers ]     [ Counter Batchers ]     [ Feed Refill Workers ] |
        |  Computes log time decay &      Micro-batches counters   Consumes context and    |
        |  writes volatile vector to      and executes single      materializes Redis      |
        |  Redis context feature store    bulk UPDATE to Postgres  user feed queues        |
        | [cite: 13, 14]                [cite: 11]              [cite: 13, 14, 15]      |
        +----------------------------------------------------------------------------------+
                                             |                     |
                                             v                     v
        +----------------------------------------------------------------------------------+
        |                               PERSISTENCE & CACHE TIERS                          |
        |                                                                                  |
        |  [ Redis Feature & Feed Cluster ]               [ Analytical Data Sink ]         |
        |  - `user:context:{id}` (Volatile Vectors)       - S3 Parquet Data Lake /         |
        |  - `user_feed:{id}` (Pre-computed Hot Queues)     ClickHouse (Cold Historical)   |
        | [cite: 13, 14]                                                                  |
        +----------------------------------------------------------------------------------+

```

---

## What Should Change in EchoFlow

| Component / Module | Current Implementation | Target Implementation | Architectural Reason |
| --- | --- | --- | --- |
| `models.py` (`UserInteraction`)

 | Synchronous `save()` executes `AudioClip.objects.update(likes=F('likes')+1)`<br> | Strip out `save()` override completely; record pure state in DB or route to stream

 | Eliminates row-level locking on hot `AudioClip` rows during high-velocity engagement. |
| `views.py` (`log_telemetry`)

 | Performs synchronous `UserInteraction.objects.update_or_create`<br> | Pushes raw payload to `Redis Stream` via `XADD`; returns `202 Accepted`<br> | Drops API telemetry latency from ~60ms (DB write) to <2ms (In-Memory Stream). |
| `views.py` (`FastFeedViewSet`)

 | Synchronously invokes `refill_user_feed(user_id, count=10)` on queue starvation

 | Serves fallback static trending cache; asynchronously dispatches feed top-up | Prevents cascading database thread exhaustion when Redis queues run dry under spike traffic. |
| `tasks.py` (`calculate_blended_query_vectors`)

 | Queries relational table `UserInteraction` over 7-day windows and computes math in Celery

 | Reads pre-calculated blended vector from Redis key `user:context:{id}`<br> | Eliminates multi-row relational read queries from the critical path of feed replenishment. |
| `tasks.py` (`update_global_metrics`)

 | Executes an unconstrained raw SQL `UPDATE` across the entire `AudioClip` table

 | Replaced by stream-based incremental decay workers and batched range updates | Prevents global database write-lock freezes every 10 minutes. |

---

## Components to Add

1. **`EventPublisher` Service Class:** Centralized abstraction inside Django to publish typed JSON events with tracing metadata to Redis Streams.
2. **`event_outbox` Table & Relay Worker:** PostgreSQL table for atomic business events and a lightweight Celery Beat/daemon runner to publish them.
3. **Stream Consumer Base Classes:** Standardized consumer scaffolding implementing batch fetching (`XREADGROUP`), exponential backoff, retry counters, and Dead-Letter Queue dispatching.
4. **Telemetry Micro-Batcher Worker:** An asynchronous consumer that accumulates 500 interaction tallies in RAM and executes single bulk `UPDATE` operations against PostgreSQL.

---

## Components That Should NOT Be Added Yet

* **Apache Kafka & Zookeeper/KRaft:** Operating a 3-node Kafka cluster for an application handling under 50,000 DAU introduces massive operational complexity, memory costs, and failure modes with zero performance gain over Redis Streams.
* **Apache Flink / Spark Streaming:** Heavy distributed compute frameworks are completely unnecessary. Single-process Python workers utilizing NumPy can calculate tens of thousands of vector time-decay transformations per second.


* **Debezium CDC (Change Data Capture) Cluster:** Running a dedicated Debezium Kafka Connect cluster to tail PostgreSQL WAL logs is premature. An application-level transactional outbox table is vastly simpler to debug, operate, and maintain at current scale.
* **Microservices Infrastructure (gRPC, Service Meshes):** Do not split the Django codebase into 5 separate deployable repositories. Maintain a **Modular Monolith** where asynchronous boundaries are separated by message streams, but the codebase remains unified.

---

## P0/P1/P2/P3 Roadmap

### P0 (Must Exist Before Production Launch)

* Remove all `F()` counter updates and synchronous side-effects from `models.py` (`UserInteraction.save()`, `Comment.save()`).


* Replace synchronous `refill_user_feed` execution inside `FastFeedViewSet` with a fallback to a cached trending list.


* Batch the `update_global_metrics` raw SQL query into chunks of 2,000 IDs to avoid long table locks.



### P1 (Scale: 10,000 – 50,000 DAU)

* Route `/api/v1/interactions/log-telemetry/` into Redis Streams.


* Deploy a dedicated Celery/Python stream consumer group to process telemetry events in micro-batches.
* Implement the Transactional Outbox pattern for `AudioPublished` and `UserFollowed` events.



### P2 (Scale: 50,000 – 500,000 DAU)

* Decouple recommendation vector calculation: stream consumers write context vectors to Redis `user:context:{id}`, and feed generators read exclusively from this cache.


* Implement Dead-Letter Queues (`stream:*:dlq`) and consumer lag Prometheus alerts.
* Implement asynchronous notification and inbox fan-out workers.

### P3 (Extreme Scale: > 1,000,000 DAU)

* Transition high-throughput streams from Redis Streams to Apache Kafka or AWS Kinesis.
* Deploy an OLAP analytical sink (ClickHouse) for long-term telemetry storage and model retraining.
* Implement Change Data Capture (CDC) via PostgreSQL logical decoding.

---

## Architectural Blind Spots

* **The Schema Evolution Blind Spot:** If an event producer changes its JSON payload format (e.g., renaming `watch_time_ms` to `dwell_duration_ms`), downstream asynchronous consumers running older code will silently fail or drop records. EchoFlow must enforce strict semantic versioning (`schema_version`) on all event schemas and maintain backwards compatibility in consumer parsing code.
* **The Unbounded Redis Stream Blind Spot:** Unlike Celery queues that evict tasks upon consumption, Redis Streams retain message history until explicitly trimmed. If the engineering team forgets to configure `MAXLEN ~ 100000` on stream writes, the stream will slowly consume all available server RAM, causing Redis to crash.
* **The Feed Staleness Blind Spot:** If a creator deletes a clip, an event must be emitted to invalidate that clip ID across all downstream Redis feed lists. If feed invalidation is not implemented, users will pop deleted IDs from their Redis queues, generating playback 404 errors.



---

## Second-Order Effects

```
Obvious Solution: Move Telemetry from Postgres to Event Stream
  │
  ├──> Second-Order Effect 1: Like counts become eventually consistent (UI shows stale data)
  │      └──> Fix: Frontend must implement Optimistic UI updates.
  │
  ├──> Second-Order Effect 2: Consumers can crash mid-batch, causing duplicated events on replay
  │      └──> Fix: All consumers must implement atomic idempotency checks via Redis SETNX.
  │
  └──> Second-Order Effect 3: Redis RAM grows continuously as stream messages accumulate
         └──> Fix: Enforce strict approximate stream trimming (`XADD MAXLEN ~ 50000`).

```

---

## Final Senior-Engineer Verdict

### What is the earliest point at which EchoFlow genuinely needs event-driven architecture?

EchoFlow genuinely requires an event-driven architecture at **the very first viral spike exceeding 500 concurrent interactions per second (~10,000 to 25,000 DAU)**. At this exact threshold, PostgreSQL row-level locks on the `AudioClip` table (triggered by synchronous `likes` and `shares` updates) and connection pool exhaustion from the `/log-telemetry/` endpoint will cause the relational database to stall, taking down authentication and playback for all users.

### What can I build today so that the system remains simple now but does not trap me in a relational architecture later?

You must implement three foundational architectural patterns immediately:

1. **Enforce a Strict Service Boundary:** Remove all database triggers and `save()` overrides from `models.py`. Never let an API view directly execute multi-table mutations. Route all operations through an explicit Service Layer (e.g., `InteractionService.record_view()`). Today, this service writes to PostgreSQL; tomorrow, you can change its internal implementation to publish to a stream with zero changes to your API controllers or frontend clients.


2. **Define Structured Event Schemas:** Create a `/events/` schema module defining strongly typed dataclasses/schemas for all core domain occurrences (`AudioUploaded`, `AudioPlayed`, `UserFollowed`).
3. **Use Redis Streams for High-Velocity Telemetry:** Do not install Kafka. Use the Redis instance you already operate. Route `/log-telemetry/` payloads to a Redis Stream and process them asynchronously in micro-batches.



By establishing clean service boundaries and decoupling high-velocity telemetry today, you preserve the operational simplicity of a unified Django codebase while ensuring the platform can scale gracefully into an enterprise-grade distributed streaming system when traffic demands it.
