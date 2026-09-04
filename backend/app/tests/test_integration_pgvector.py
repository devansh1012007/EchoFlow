"""Integration tests for pgvector + HNSW index behavior.

These tests REQUIRE a real Postgres+pgvector backend (the `integration`
marker is auto-skipped on SQLite via `conftest.py::_skip_integration_without_real_services`).

What they cover:
- The HNSW indexes declared on `AudioClip.semantic_vector` and
  `acoustic_vector` actually exist in `pg_indexes` (catches future model
  refactors that drop the index silently).
- `EXPLAIN ANALYZE` of a cosine-distance query plan uses the HNSW index
  (catches future migrations that change the index opclass).
- Vector nearest-neighbor queries return the expected top-K (catches
  bugs in the distance function or query construction).

Companion: backend/app/models.py (HnswIndex declarations at lines 79-99),
backend/app/services/feed_pool.py (CosineDistance usage), and
docs/EXPLAIN/database/05-read-replica-design.md.
"""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.mark.django_db(transaction=True)
class TestPgVectorHnswIndex:
    """Verify the HNSW indexes on AudioClip's vector fields exist and are
    actually used by the query planner."""

    def test_hnsw_index_exists_on_audioclip(self):
        # `pg_indexes` is the system catalog that lists every index in the
        # database. We query it for the two indexes declared in
        # AudioClip.Meta.indexes.
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN ('semantic_vector_index', 'acoustic_vector_index')
                ORDER BY indexname
                """
            )
            rows = [r[0] for r in cursor.fetchall()]

        assert 'acoustic_vector_index' in rows, (
            "HNSW index `acoustic_vector_index` is missing. "
            "Check AudioClip.Meta.indexes in backend/app/models.py."
        )
        assert 'semantic_vector_index' in rows, (
            "HNSW index `semantic_vector_index` is missing. "
            "Check AudioClip.Meta.indexes in backend/app/models.py."
        )

    def test_cosine_distance_query_uses_index(self):
        # Run EXPLAIN on a cosine-distance query against semantic_vector.
        # The plan must include `semantic_vector_index` for the HNSW
        # operator to fire; a sequential scan means the index is missing,
        # wrong opclass, or the query is malformed.
        from django.db import connection
        from pgvector.django import CosineDistance

        from backend.app.models import AudioClip

        # Build a dummy query vector. Any 384-dim vector works for EXPLAIN;
        # we don't need real data.
        query_vec = [0.1] * 384

        qs = (
            AudioClip.objects
            .annotate(distance=CosineDistance('semantic_vector', query_vec))
            .order_by('distance')[:10]
        )
        sql, params = qs.query.sql_with_params()
        explain_sql = f"EXPLAIN (FORMAT JSON) {sql}"

        with connection.cursor() as cursor:
            cursor.execute(explain_sql, params)
            plan_json = cursor.fetchone()[0]

        # The plan is a JSON array; we serialize and grep for the index name.
        # This is robust against plan-shape changes (e.g., adding a Sort node).
        import json
        plan_str = json.dumps(plan_json)
        assert 'semantic_vector_index' in plan_str or 'hnsw' in plan_str.lower(), (
            f"EXPLAIN did not reference the HNSW index. Plan: {plan_str}\n"
            "Possible causes: index was dropped, opclass mismatch, "
            "or the query bypasses the index."
        )

    def test_vector_query_returns_correct_top_k(self):
        # Insert N clips with known vectors; query for the nearest; assert
        # the expected clip wins. Catches bugs in the distance function,
        # the dimension check, or the query construction.
        from backend.app.models import AudioClip
        from pgvector.django import CosineDistance

        from django.contrib.auth import get_user_model
        User = get_user_model()

        creator = User.objects.create_user(
            username='creator-vec', email='c@example.com', password='x'
        )

        # Anchor vector: a known direction in 384-dim space.
        anchor = [0.0] * 384
        anchor[0] = 1.0

        # The target: a vector very close to anchor (cosine sim ~ 1.0).
        target = [0.0] * 384
        target[0] = 0.999

        # A distractor: a vector orthogonal to anchor (cosine sim = 0).
        distractor = [0.0] * 384
        distractor[1] = 1.0

        target_clip = AudioClip.objects.create(
            title='target',
            category='music',
            creator=creator,
            status='ready',
            duration_ms=60_000,
            likes=0, shares=0, skips=0, comment_count=0,
            semantic_vector=target,
            acoustic_vector=[0.0] * 128,
        )
        AudioClip.objects.create(
            title='distractor',
            category='music',
            creator=creator,
            status='ready',
            duration_ms=60_000,
            likes=0, shares=0, skips=0, comment_count=0,
            semantic_vector=distractor,
            acoustic_vector=[0.0] * 128,
        )

        qs = (
            AudioClip.objects
            .annotate(distance=CosineDistance('semantic_vector', anchor))
            .order_by('distance')
        )
        results = list(qs[:5])
        assert len(results) >= 1
        # The target must be the nearest neighbor (lowest distance).
        assert str(results[0].id) == str(target_clip.id), (
            f"Expected target clip {target_clip.id} to be nearest; got {results[0].id}. "
            f"Distances: {[(c.id, c.distance) for c in results]}"
        )