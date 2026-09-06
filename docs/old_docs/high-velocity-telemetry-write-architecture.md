# docs/high-velocity-telemetry-write-architecture.md

## Executive Summary
EchoFlow’s current telemetry architecture treats PostgreSQL as an infinite-throughput event sink. By binding the highly volatile lifecycle of behavioral events (views, likes, watch time) to synchronous relational database transactions, the system introduces catastrophic lock contention and Write-Ahead Log (WAL) bloat. While the business logic of tracking engagement and dynamically updating vectors is mathematically sound, executing these operations synchronously via Django ORM will cause database connection exhaustion and instance failure at moderate scale.

To survive extreme write velocity, EchoFlow must decouple telemetry ingestion from relational storage. The architecture must evolve from a synchronous transactional model to an asynchronous, stream-based, eventually consistent data pipeline.

---

## Current Telemetry Write Paths
**Status: Implemented (Bottlenecked)**

Telemetry currently enters the database through synchronous HTTP requests mapped to ORM operations:
1.  **Ingestion:** The client POSTs to `/api/v1/interactions/{clip_id}/log-telemetry/` or `toggle_like`[cite: 15].
2.  **Row Upsert:** Django executes an `update_or_create` on the `UserInteraction` table[cite: 15].
3.  **Synchronous Triggers:** The `UserInteraction.save()` method intercepts the write. If the state changed, it executes an immediate `UPDATE` query on the `AudioClip` table using an `F()` expression (e.g., `likes = F('likes') + 1`)[cite: 11].
4.  **Global Updates:** A scheduled Celery task (`update_global_metrics`) periodically executes a massive raw SQL `UPDATE` across the entire `AudioClip` table to recalculate `engagement_velocity`[cite: 13].

---

## Event-Volume Model
To understand why this breaks, we must model the write throughput. In a short-form audio app, users scroll rapidly. Assume a conservative engagement metric of 100 clips viewed per user per day.

| Metric | 100K DAU | 1M DAU | 10M DAU |
| :--- | :--- | :--- | :--- |
| **Events / Day** | 10,000,000 | 100,000,000 | 1,000,000,000 |
| **Avg Events / Sec (RPS)** | ~115 | ~1,157 | ~11,574 |
| **Peak RPS (3x multiplier)** | ~345 | ~3,470 | ~34,700 |
| **Row Growth / Month** | 300 Million | 3 Billion | 30 Billion |

At **1M DAU**, the database must process ~3,400 telemetry writes per second during peak hours. Because EchoFlow executes an `update_or_create` followed by a counter `UPDATE`, this actually results in **~7,000+ Write IOPS**. 

---

## Database Contention Analysis
PostgreSQL uses Multi-Version Concurrency Control (MVCC). When EchoFlow updates a row (e.g., updating the `watch_time_ms` of a `UserInteraction`[cite: 11]), Postgres does not alter the row in place. It writes a completely new row version and marks the old one as dead.

1.  **MVCC Dead Tuple Bloat:** At 3,400 updates per second, the `UserInteraction` table will generate millions of dead tuples hourly. The autovacuum daemon will not be able to clean them fast enough, causing massive table and index bloat.
2.  **WAL Saturation:** Every update is written to the Write-Ahead Log. High-velocity small updates will saturate network I/O to the EBS volumes, increasing replication lag to read-replicas.
3.  **Connection Pool Exhaustion:** Django holds the database connection open until the HTTP request completes. If the database slows down by just 50ms under write pressure, the API worker threads will saturate, incoming requests will queue, and the application will crash.

---

## Hot-Row and Hot-Index Analysis
The most dangerous code in the repository is this exact line in `UserInteraction.save()`[cite: 11]:
```python
AudioClip.objects.filter(pk=self.clip.pk).update(**{field_to_update: F(field_to_update) + increment_val})

```

**The Viral Contention Problem:**
If a creator uploads a viral audio clip that receives 500 likes in one minute, 500 concurrent Django requests attempt to update the exact same `AudioClip` row.

* PostgreSQL implements row-level locking for `UPDATE` statements.
* Request 1 acquires the lock. Requests 2 through 500 must wait in a queue.
* This creates a "hot row." The database CPU spikes managing the lock queue, connection pools drain, and the entire API stalls because of one popular clip.

---

## Event Ingestion Architecture

**Target Architecture**

Telemetry must be decoupled from the API's critical path.

1. **API Layer (Ingestion):** The `/log-telemetry/` endpoint authenticates the user, validates the payload, and immediately pushes the JSON payload to an in-memory stream (e.g., Redis Streams or Kafka). It returns `202 Accepted` in < 5ms.
2. **Stream Processor (Celery/Go worker):** A background worker consumes the stream in micro-batches (e.g., 500 events at a time).
3. **Bulk Database Write:** The worker performs a single `bulk_update` or `bulk_create` into PostgreSQL, reducing 500 transactions into 1.

---

## Counter and Aggregation Strategy

Synchronous `F()` expressions must be deleted.

**The Distributed Counter Pattern:**

1. When an event processor consumes a "like", it increments an atomic counter in Redis: `INCR clip:1234:likes`.
2. The API reads from this Redis key to display real-time likes to users.
3. A cron job runs every 5 minutes, pulls all modified counters from Redis, executes a single batched `UPDATE` against the `AudioClip` table in PostgreSQL, and flushes the Redis counters.
4. This reduces database lock contention on hot rows to exactly one lock every 5 minutes, regardless of virality.

---

## PostgreSQL Scaling Limits and Evolution

PostgreSQL is excellent for authoritative relationships (Users, AudioClip metadata, Social Graph). It is a terrible choice for a persistent time-series event ledger.

* **When Postgres Breaks:** Around 500M rows, index lookups on `UserInteraction` (even with the composite unique index) will require memory exceeding the instance's RAM. Cache hit ratios will drop, and queries will fall back to disk I/O.


* **The Evolution:** Telemetry should ultimately bypass Postgres entirely. The event stream (Kafka) should sink directly into an OLAP database designed for massive ingestion and columnar analytics (e.g., ClickHouse). Feed generation would query ClickHouse for telemetry aggregations and PostgreSQL for clip metadata.

---

## Event Semantics & Idempotency Strategy

* **Semantics:** EchoFlow requires **at-least-once delivery**. It is acceptable if an event is processed slightly late, but it must not be dropped.
* **Idempotency:** The database currently enforces uniqueness via `unique_together = ('user', 'clip', 'interaction_type')`.


* **The Conflict:** If a stream processor crashes mid-batch and replays the events, the bulk insert will fail with a Unique Violation. The ingestion worker must use `ON CONFLICT DO UPDATE` (PostgreSQL `INSERT ... ON CONFLICT`) to ensure safely re-playable event consumption without throwing exceptions.

---

## Recommendation-System Integration

Currently, the recommendation engine calculates the time-decayed vectors by executing a live SQL query against `UserInteraction`.

As telemetry volume explodes, this query will become too slow for real-time feed refills.

* **Solution:** Feature Materialization.
* The stream processor consuming the telemetry should concurrently update the user's `long_term_semantic` and `long_term_acoustic` vectors in near real-time, storing them in Redis. The feed generation task should only query these pre-calculated Redis features, completely avoiding PostgreSQL telemetry queries during the feed refill path.



---

## Retention and Data Lifecycle

Do not store infinite telemetry in PostgreSQL.

* **Hot Data (PostgreSQL/Redis):** Retain only the last 14 days of `UserInteraction` records to serve immediate feed context and UI state (e.g., "Has this user liked this clip?").
* **Warm Data (OLAP):** Retain 12 months of aggregated vectors and metrics for periodic model retraining.
* **Cold Data (S3):** Stream raw, unaggregated JSON events to an AWS S3 bucket (via Kinesis Firehose or Kafka Connect) for permanent archival and compliance.
* **Execution:** Implement a daily Celery Beat task that executes `DELETE FROM app_1_userinteraction WHERE updated_at < NOW() - INTERVAL '14 days'`.

---

## Failure and Recovery Model

* **Redis/Kafka Outage:** If the stream broker goes down, the API must fail open. It should drop the telemetry payload, log an error, and return a 200 OK to the client. *Never let telemetry outages break core app navigation or playback.*
* **Database Outage:** The API remains up (reading from Cache). The stream processors pause, allowing events to buffer in Kafka/Redis until the database recovers.

---

## Migration Strategy (How to avoid a rewrite later)

You must prepare the codebase *now* so the eventual migration to an event-driven architecture is seamless.

1. **Interface Isolation:** Ensure the frontend *only* sends data to the `/log-telemetry/` endpoint. Do not allow random endpoints to write interaction state.


2. **Logic Decoupling:** Remove the `save()` method override on `UserInteraction`. Create an internal Python service function (e.g., `record_interaction(user, clip, payload)`) that wraps the DB write and the counter logic.


3. **The Pivot:** When traffic scales, you only have to change the internals of `record_interaction()` to publish to Redis Streams instead of executing SQL, leaving the API layer and frontend completely untouched.

---

## Missing Components

* **In-Memory Stream Broker:** Redis Streams (available in the current stack) or Apache Kafka (required at extreme scale).
* **Batching Workers:** Dedicated Celery workers responsible only for consuming streams and executing bulk SQL operations.
* **OLAP Database:** ClickHouse or Apache Pinot (planned for future scale).

---

## P0/P1/P2/P3 Roadmap

* **P0 (Immediate):** Remove `F()` counter updates from the `UserInteraction.save()` signal. Move counter increments to Redis, flushed to Postgres via a 5-minute Celery cron job.


* **P1 (Near-Term / 10K DAU):** Rewrite `update_global_metrics` to batch its updates. A single `UPDATE` on 100,000 rows will lock the table and cause downtime.


* **P2 (Scale / 100K DAU):** Route `/log-telemetry/` directly into Redis Streams. Implement bulk-insert consumers to pull events into Postgres asynchronously.


* **P3 (Extreme / 1M+ DAU):** Introduce ClickHouse. Sink the event stream directly into ClickHouse. Remove the `UserInteraction` table from PostgreSQL entirely.

---

## Architectural Blind Spots & Second-Order Effects

* **Blind Spot:** If the Redis stream consumers fall behind (e.g., during a massive viral event), the stream memory will grow until Redis OOMs (Out of Memory) and crashes. You must configure strict `MAXLEN` limits on Redis Streams to drop old telemetry if consumers die.
* **Second-Order Effect of Async Counters:** By making likes async via Redis, the user who just clicked "Like" might refresh the app and see the counter decrement back to its old state (because the DB flush hasn't happened yet). The client UI must optimistically cache the user's action locally to hide this eventual consistency from them.

---

## Final Verdict

**At what point does directly writing telemetry into PostgreSQL become architecturally dangerous?**
It becomes dangerous immediately upon a viral event, but systemically fails around **10,000 to 50,000 Daily Active Users (DAU)**. At this scale, the synchronous row-level locks generated by the `F()` expressions, combined with MVCC bloat from constant row updates, will saturate your database connections and I/O, resulting in API timeouts across the entire platform.

**What should EchoFlow build now so that moving to an event-driven telemetry system later does not require rewriting the entire backend?**
You must immediately decouple the telemetry API from the database logic. Remove all database signals (`save()` overrides) related to interactions and comments. Force all telemetry through a single internal Python interface. Today, that interface can execute synchronous SQL. Tomorrow, you will simply replace the contents of that interface with a `redis_client.xadd()` command to push the event to a stream. This strictly defined boundary ensures the eventual migration to an event-driven architecture requires changing exactly one function, rather than rewriting the entire backend application.
