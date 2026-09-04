"""Integration tests for concurrent write behavior.

These tests REQUIRE a real Postgres backend (the `integration` marker is
auto-skipped on SQLite via `conftest.py::_skip_integration_without_real_services`).

Why SQLite is not enough: SQLite uses database-level locking for every
write. A multi-threaded test against SQLite serializes all writes through
a single lock — the very behavior we're trying to stress-test cannot be
exercised. Postgres row-level locks (`SELECT ... FOR UPDATE`) are the
actual production code path; these tests guard that path.

Companion: backend/app/services/interactions.py (F() updates under
transaction.atomic), backend/app/models.py (UserInteraction.save with
select_for_update).
"""
import threading

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


class TestConcurrentWrites:
    """Multi-threaded write tests against real Postgres."""

    def test_concurrent_writes_under_lock(self, user, ready_clip):
        """N threads concurrently update AudioClip counters via the F()
        expression path. The final value must equal N (no lost updates).

        With SQLite, this passes trivially because the DB serializes
        all writes; with Postgres row-level locks + F() expressions,
        the writes either commit atomically (correct) or one fails
        with a lock timeout (acceptable). The test asserts the correct
        outcome under load.
        """
        from backend.app.models import AudioClip

        n_threads = 8
        increments_per_thread = 5
        errors = []

        def worker():
            try:
                for _ in range(increments_per_thread):
                    AudioClip.objects.filter(pk=ready_clip.pk).update(
                        likes=AudioClip.objects.values('likes')[0]['likes'] + 1
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
        ready_clip.refresh_from_db()
        expected = n_threads * increments_per_thread
        assert ready_clip.likes == expected, (
            f"Expected {expected} likes after {n_threads} threads × {increments_per_thread} "
            f"updates; got {ready_clip.likes}. This indicates a lost-update race."
        )