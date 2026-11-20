"""Tests for the offline feed-quality evaluator (audit P2 follow-up).

Two layers, deliberately kept apart:

* PURE metric tests — no ``django_db`` marker, no ORM. Every expected
  value below is hand-derivable from the formulas documented in
  ``ai_ml/eval/feed_metrics.py`` (Recall@K, binary-log2 NDCG@K,
  coverage, the data-relative temporal holdout). These pin the MATH;
  if one of them goes red, a metric changed meaning and every number
  the eval command prints is now uninterpretable.

* ONE ``django_db`` end-to-end layer — builds real Users / AudioClips /
  UserInteractions and runs ``call_command('eval_feed', ...)``. This
  exercises the ORM wiring and the live recommender adapter. It needs
  NO Redis: the adapter calls ``build_feed_candidates(...,
  pool_first=False)``, which skips the Redis pool read entirely (the
  ``if pool_first:`` gate at ai_ml/pipelines/recommendation.py:289 and
  ``feed_pool.get_user_candidates`` at feed_pool.py:258 are never
  reached) and runs the SQL/cold-start fallback against Postgres only.
  The command fixture has no vectors set, so the recommender takes the
  documented cold-start ordering (recommendation.py:365-376) — the
  assertion is that all three rankers run and print, not that
  personalization is good; quality is what the evaluator measures on
  real data. A second ``django_db`` test pins the LEAK-FREE property:
  with real pgvector vectors, a test-window 'like' must not tilt the
  preference vector (the adapter serves as-of the last train
  interaction via ``build_feed_candidates(now=..., created_before=...)``).

Row-shape tolerance (dict rows, InteractionRow tuples, naive
datetimes, UUID clip ids) is asserted across the pure tests, since the
evaluator accepts querysets, ``.values()`` dicts and hand-built rows.
"""
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from ai_ml.eval import feed_metrics as fm

T0 = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def _row(user, clip, at, kind='like'):
    """Dict-shaped interaction row (what ``.values(...)`` produces)."""
    return {'user_id': user, 'clip_id': clip, 'created_at': at,
            'interaction_type': kind}


def _catalog(*specs):
    """Catalog rows from (clip_id, creator_id, created_at, likes)."""
    return [
        {'clip_id': clip_id, 'creator_id': creator,
         'created_at': created_at, 'likes': likes}
        for clip_id, creator, created_at, likes in specs
    ]


def _seed_eval_data():
    """Users/splits used by the ``evaluate`` tests.

    Ranked users: u1 (6 train rows — not cold), u2 (2 rows — cold
    start), u5 (1 row, but its only test interaction is a ``skip`` —
    ranked yet scoreless). Excluded users: u3 (test only → empty
    train), u4 (train only → empty test). All within one catalog of
    c1..c9 so the catalog-restricted relevance path is exercised.
    """
    train = (
        [_row('u1', f'c{i}', T0 - timedelta(days=40)) for i in range(1, 7)]
        + [_row('u2', 'c1', T0 - timedelta(days=40)),
           _row('u2', 'c2', T0 - timedelta(days=38)),
           _row('u4', 'c1', T0 - timedelta(days=40)),
           _row('u4', 'c2', T0 - timedelta(days=39)),
           _row('u4', 'c3', T0 - timedelta(days=38)),
           _row('u5', 'c1', T0 - timedelta(days=40))]
    )
    test = [
        _row('u1', 'c7', T0),
        _row('u2', 'c8', T0),
        _row('u3', 'c9', T0),          # no train → excluded from ranking
        _row('u5', 'c9', T0, 'skip'),  # held out but never a positive
    ]
    catalog = _catalog(
        *[(f'c{i}', 'creator', T0 - timedelta(days=i), 0) for i in range(1, 10)]
    )
    return train, test, catalog


def _perfect(uid, k, train_rows):
    """Ranker that serves each ranked user their held-out positive."""
    return {'u1': ['c7'], 'u2': ['c8']}.get(uid, ['c9'])


def _worst(uid, k, train_rows):
    """Ranker that never serves u1's or u2's positive (u1's list is
    deliberately length 2 so mean_list_length is distinguishable)."""
    return ['c9', 'c8'] if uid == 'u1' else ['c9']


# ---------------------------------------------------------------------------
# recall_at_k — pure math
# ---------------------------------------------------------------------------

class TestRecallAtK:
    def test_recall_at_k_two_hits_of_three_relevant(self):
        # Spec case: "recall@3 with 2 hits == 2/len(relevant)" — the
        # denominator is ALL relevant items, not the list length.
        assert fm.recall_at_k(['a', 'b', 'x'], ['a', 'b', 'c'], 3) == pytest.approx(2 / 3)

    def test_recall_at_k_counts_only_top_k_positions(self):
        # Hits beyond the k cutoff do not exist for this metric.
        assert fm.recall_at_k(['x', 'y', 'a', 'b'], ['a', 'b'], 2) == 0.0
        assert fm.recall_at_k(['x', 'a', 'y', 'b'], ['a', 'b'], 2) == pytest.approx(0.5)

    def test_recall_at_k_zero_when_relevant_empty(self):
        assert fm.recall_at_k(['a', 'b'], [], 3) == 0.0
        assert fm.recall_at_k(['a'], ['a'], 0) == 0.0


# ---------------------------------------------------------------------------
# ndcg_at_k — pure math, log2 formula pinned exactly
# ---------------------------------------------------------------------------

class TestNdcgAtK:
    def test_perfect_ranking_scores_ndcg_one(self):
        # DCG and IDCG sum the same positions → exactly 1.0, even when
        # k exceeds |relevant| (IDCG is capped at min(|relevant|, k)).
        assert fm.ndcg_at_k(['a', 'b', 'c', 'd'], ['a', 'b'], 4) == pytest.approx(1.0)
        assert fm.ndcg_at_k(['a', 'b', 'c', 'd'], ['a', 'b'], 5) == pytest.approx(1.0)

    def test_ndcg_matches_hand_computed_log2_formula(self):
        # Independent transcription of the documented formula:
        # DCG  = 1/log2(1+2) + 1/log2(1+4)      (hits at ranks 2 and 4)
        # IDCG = 1/log2(1+1) + 1/log2(1+2)      (best 2-hit ranking)
        expected = (
            (1.0 / math.log2(3) + 1.0 / math.log2(5))
            / (1.0 / math.log2(2) + 1.0 / math.log2(3))
        )
        assert fm.ndcg_at_k(['a', 'b', 'c', 'd'], ['b', 'd'], 4) == pytest.approx(expected)
        # And it is strictly between the obvious bounds.
        assert 0.0 < expected < 1.0

    def test_ndcg_dedupes_so_a_repeat_hit_is_not_double_counted(self):
        # Without dedupe, the repeated 'a' at rank 2 would add another
        # full-weight 1/log2(2) and push NDCG above 1.0.
        assert fm.ndcg_at_k(['a', 'a', 'b'], ['a', 'b'], 3) == pytest.approx(1.0)

    def test_ndcg_uses_capped_idcg_when_k_below_relevant(self):
        # |relevant| = 3 but k = 2: IDCG covers only the best 2 slots,
        # so a perfect top-2 still scores exactly 1.0.
        assert fm.ndcg_at_k(['a', 'b', 'x', 'x'], ['a', 'b', 'c'], 2) == pytest.approx(1.0)

    def test_ndcg_zero_when_no_relevant_item_ranked(self):
        assert fm.ndcg_at_k(['x', 'y'], ['a'], 2) == 0.0


# ---------------------------------------------------------------------------
# catalog_coverage — pure math
# ---------------------------------------------------------------------------

class TestCatalogCoverage:
    def test_coverage_counts_distinct_items_across_users(self):
        by_user = {'u1': ['c1', 'c2'], 'u2': ['c2', 'c3']}
        assert fm.catalog_coverage(by_user, 5) == pytest.approx(3 / 5)
        # Same input as a plain list of lists (the dict convenience).
        assert fm.catalog_coverage([['c1', 'c2'], ['c2', 'c3']], 5) == pytest.approx(3 / 5)

    def test_coverage_zero_catalog_size_returns_zero(self):
        assert fm.catalog_coverage({'u1': ['c1']}, 0) == 0.0


# ---------------------------------------------------------------------------
# time_holdout — split and boundary behavior
# ---------------------------------------------------------------------------

class TestTimeHoldout:
    def test_holdout_last_window_becomes_test(self):
        rows = [
            _row('u1', 'c1', T0 - timedelta(days=40)),
            _row('u1', 'c2', T0 - timedelta(days=20)),
            _row('u1', 'c3', T0),
            _row('u1', 'c5', T0 - timedelta(days=14, seconds=1)),
        ]
        train, test = fm.time_holdout(rows, holdout_days=14)
        assert {r.clip_id for r in train} == {'c1', 'c2', 'c5'}
        assert {r.clip_id for r in test} == {'c3'}

    def test_boundary_timestamp_belongs_to_test(self):
        # cutoff = max(created_at) - holdout_days; a row EXACTLY at the
        # cutoff closes the window from the left (>=) and is test; one
        # microsecond older is train. Pinned so the split can never
        # drift between "recent" and "history" silently.
        rows = [
            _row('u1', 'max-row', T0),
            _row('u1', 'at-cutoff', T0 - timedelta(days=5)),
            _row('u1', 'just-before', T0 - timedelta(days=5, microseconds=1)),
        ]
        train, test = fm.time_holdout(rows, holdout_days=5)
        assert {r.clip_id for r in test} == {'max-row', 'at-cutoff'}
        assert {r.clip_id for r in train} == {'just-before'}

    def test_holdout_empty_input_returns_two_empty_lists(self):
        assert fm.time_holdout([], 14) == ([], [])

    def test_holdout_rejects_non_positive_days(self):
        with pytest.raises(ValueError):
            fm.time_holdout([_row('u1', 'c1', T0)], 0)
        with pytest.raises(ValueError):
            fm.time_holdout([_row('u1', 'c1', T0)], -3)

    def test_holdout_accepts_model_style_rows_and_uuids(self):
        # InteractionRow tuples with UUID clip ids (what ORM instances
        # look like to getattr) plus a dict row with a NAIVE datetime:
        # everything must normalize — str clip ids, UTC-aware stamps.
        clip_uuid = uuid.uuid4()
        rows = [
            fm.InteractionRow('u1', clip_uuid, T0, 'like'),
            {'user_id': 'u1', 'clip_id': uuid.uuid4(),
             'created_at': T0.replace(tzinfo=None), 'interaction_type': 'view'},
        ]
        train, test = fm.time_holdout(rows, holdout_days=1)
        assert all(isinstance(r, fm.InteractionRow) for r in train + test)
        assert all(r.created_at.tzinfo is not None for r in train + test)
        # Both rows are inside the 1-day window (the naive one was
        # promoted to UTC, not mis-parsed as older).
        assert train == []
        assert str(clip_uuid) in {r.clip_id for r in test}
        assert len(test) == 2

    def test_holdout_rejects_rows_without_a_usable_timestamp(self):
        with pytest.raises(ValueError):
            fm.time_holdout([{'user_id': 'u1', 'clip_id': 'c1'}], 14)


# ---------------------------------------------------------------------------
# evaluate — harness behavior
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_perfect_ranker_achieves_recall_and_ndcg_one(self):
        train, test, catalog = _seed_eval_data()
        results = fm.evaluate(
            {'perfect': _perfect, 'worst': _worst},
            train, test, k=3, catalog_size=9, catalog=catalog,
        )
        perfect = results['rankers']['perfect']
        assert perfect['recall_at_k'] == pytest.approx(1.0)
        assert perfect['ndcg_at_k'] == pytest.approx(1.0)
        assert perfect['n_users_evaluated'] == 2
        assert perfect['mean_list_length'] == pytest.approx(1.0)
        # Coverage: perfect serves c7, c8 (scored users) and c9 (the
        # scoreless u5) → 3 distinct of 9.
        assert perfect['coverage_at_k'] == pytest.approx(3 / 9)
        assert perfect['n_ranker_errors'] == 0

    def test_worst_ranker_scores_zero_with_distinguishable_list_lengths(self):
        train, test, catalog = _seed_eval_data()
        results = fm.evaluate(
            {'worst': _worst}, train, test, k=3, catalog_size=9, catalog=catalog,
        )
        worst = results['rankers']['worst']
        assert worst['recall_at_k'] == pytest.approx(0.0)
        assert worst['ndcg_at_k'] == pytest.approx(0.0)
        # u1 gets 2 items, u2 and u5 get 1 → mean 4/3, NOT a flat list.
        assert worst['mean_list_length'] == pytest.approx(4 / 3)
        # Only c8/c9 ever appear → 2 of 9 covered.
        assert worst['coverage_at_k'] == pytest.approx(2 / 9)

    def test_evaluate_counts_users_excluded_for_empty_train_or_test(self):
        train, test, catalog = _seed_eval_data()
        results = fm.evaluate(
            {'perfect': _perfect}, train, test, k=3, catalog_size=9, catalog=catalog,
        )
        meta = results['meta']
        assert meta['n_users_total'] == 5
        assert meta['n_users_ranked'] == 3          # u1, u2, u5
        assert meta['n_users_excluded_empty_train'] == 1  # u3 (test only)
        assert meta['n_users_excluded_empty_test'] == 1   # u4 (train only)

    def test_evaluate_skips_no_positive_users_but_still_ranks_them(self):
        train, test, catalog = _seed_eval_data()
        results = fm.evaluate(
            {'perfect': _perfect}, train, test, k=3, catalog_size=9, catalog=catalog,
        )
        # u5's only held-out interaction is a skip → not a positive →
        # no recall contribution, but the ranker was still called and
        # its list still counts toward coverage / list length.
        assert results['meta']['n_users_skipped_no_positives'] == 1
        assert results['rankers']['perfect']['n_users_ranked'] == 3
        assert results['rankers']['perfect']['n_users_evaluated'] == 2

    def test_evaluate_reports_cold_start_slice_separately(self):
        train, test, catalog = _seed_eval_data()
        results = fm.evaluate(
            {'perfect': _perfect, 'worst': _worst},
            train, test, k=3, catalog_size=9, catalog=catalog,
        )
        # Cold slice = ranked users with < 5 train rows: u2 (2) and u5
        # (1). Only u2 has a positive, so n_users_evaluated == 1 while
        # the slice holds 2 ranked users.
        cold = results['rankers']['perfect']['cold_start']
        assert cold['n_users_in_slice'] == 2
        assert cold['n_users_evaluated'] == 1
        assert cold['recall_at_k'] == pytest.approx(1.0)
        assert cold['ndcg_at_k'] == pytest.approx(1.0)
        assert results['rankers']['worst']['cold_start']['recall_at_k'] == pytest.approx(0.0)

    def test_evaluate_uniform_exclusions_keep_comparison_fair(self):
        # A leaky ranker that serves the user's own clip (c1), a
        # train-interacted clip (c2) and an off-catalog id: the
        # uniform last-mile filter must strip all three so it cannot
        # win on leakage the baselines do not have.
        catalog = _catalog(
            ('c1', 'u1', T0 - timedelta(days=1), 0),      # owned by u1
            ('c2', 'creator', T0 - timedelta(days=2), 0),
            ('c3', 'creator', T0 - timedelta(days=3), 0),
        )
        train = [_row('u1', 'c2', T0 - timedelta(days=40))]
        test = [_row('u1', 'c3', T0)]
        leaky = lambda uid, k, rows: ['c1', 'c2', 'c3', 'zz-off-catalog']  # noqa: E731
        results = fm.evaluate(
            {'leaky': leaky}, train, test, k=4, catalog_size=3, catalog=catalog,
        )
        metrics = results['rankers']['leaky']
        assert metrics['mean_list_length'] == 1.0
        assert metrics['recall_at_k'] == pytest.approx(1.0)
        assert metrics['ndcg_at_k'] == pytest.approx(1.0)

        # Without a catalog the train exclusion still fires (c2 is
        # dropped — the leak-free guarantee holds), but the own clip
        # and the off-catalog id CANNOT be identified without the
        # catalog universe, so they survive: ['c1','c3','zz'] → 3
        # items. This pins the documented degradation as a deliberate
        # choice rather than an accident.
        results = fm.evaluate(
            {'leaky': leaky}, train, test, k=4, catalog_size=3, catalog=None,
        )
        assert results['rankers']['leaky']['mean_list_length'] == 3.0
        # The train leak is still gone, so the positive is reachable.
        assert results['rankers']['leaky']['recall_at_k'] == pytest.approx(1.0)

    def test_evaluate_tolerates_ranker_errors_and_none(self):
        train = [_row('u1', 'c1', T0 - timedelta(days=40))]
        test = [_row('u1', 'c2', T0)]
        catalog = _catalog(('c1', 'creator', T0, 0), ('c2', 'creator', T0, 0))

        def boom(uid, k, rows):
            raise RuntimeError('vector store unreachable')

        results = fm.evaluate(
            {'lazy': lambda uid, k, rows: None, 'boom': boom},
            train, test, k=2, catalog_size=2, catalog=catalog,
        )
        for metrics in results['rankers'].values():
            assert metrics['mean_list_length'] == 0.0
            assert metrics['recall_at_k'] == pytest.approx(0.0)
        assert results['rankers']['lazy']['n_ranker_errors'] == 0
        # The crash is COUNTED, not swallowed into a silent zero.
        assert results['rankers']['boom']['n_ranker_errors'] == 1

    def test_evaluate_empty_holdout_reports_zero_population(self):
        results = fm.evaluate(
            {'r': lambda uid, k, rows: []}, [], [], k=5, catalog_size=10,
        )
        assert results['meta']['n_users_total'] == 0
        assert results['meta']['n_users_ranked'] == 0
        metrics = results['rankers']['r']
        assert metrics['recall_at_k'] is None   # None = nothing measured
        assert metrics['ndcg_at_k'] is None
        assert metrics['coverage_at_k'] == 0.0
        assert metrics['mean_list_length'] == 0.0
        assert metrics['n_users_evaluated'] == 0

    def test_evaluate_rejects_invalid_configuration(self):
        with pytest.raises(ValueError):
            fm.evaluate({'r': lambda *a: []}, [], [], k=0, catalog_size=10)
        with pytest.raises(ValueError):
            fm.evaluate({'r': lambda *a: []}, [], [], k=1, catalog_size=10,
                        cold_start_threshold=0)


# ---------------------------------------------------------------------------
# The three shipped rankers
# ---------------------------------------------------------------------------

class TestBaselineRankers:
    def test_chronological_ranker_orders_newest_first_and_excludes(self):
        catalog = _catalog(
            ('c1', 'creator', T0 - timedelta(days=3), 0),
            ('c2', 'u1', T0 - timedelta(days=1), 0),   # owned by u1
            ('c3', 'creator', T0 - timedelta(days=2), 0),
        )
        rank = fm.chronological_ranker(catalog)
        # u2: newest-first c2, c3, c1 minus the train-interacted c3.
        out = rank('u2', 3, [fm.InteractionRow('u2', 'c3', T0, 'view')])
        assert out == ['c2', 'c1']
        # u1: c2 is their OWN clip — excluded even with no train rows.
        assert rank('u1', 3, []) == ['c3', 'c1']
        assert rank('u2', 0, []) == []

    def test_popularity_ranker_orders_by_likes_then_recency(self):
        catalog = _catalog(
            ('c1', 'creator', T0 - timedelta(days=3), 10),
            ('c2', 'creator', T0 - timedelta(days=1), 10),   # ties c1 on likes…
            ('c3', 'creator', T0 - timedelta(days=2), 99),
        )
        rank = fm.popularity_ranker(catalog)
        # …and the recency tie-break puts c2 above c1.
        assert rank('u2', 3, []) == ['c3', 'c2', 'c1']
        assert rank('u2', 3, [_row('u2', 'c1', T0)]) == ['c3', 'c2']

    def test_recommendation_ranker_uses_sql_fallback_path_and_filters(self, monkeypatch):
        # The adapter must call the REAL recommender entry point with
        # pool_first=False (no Redis), over-fetch k + |exclusions| so
        # the post-filter can still fill k slots, and serve AS OF the
        # user's last train interaction (temporal simulation).
        import ai_ml.pipelines.recommendation as rec_mod

        calls = {}

        def fake_builder(user_id, count, *, pool_first=True, now=None,
                         created_before=None):
            calls.update(
                user_id=user_id, count=count, pool_first=pool_first,
                now=now, created_before=created_before,
            )
            return ['c1', 'c2', 'c9', 'c7']

        monkeypatch.setattr(rec_mod, 'build_feed_candidates', fake_builder)
        catalog = _catalog(
            ('c1', 'u1', T0, 0),       # own clip → must be dropped
            ('c2', 'creator', T0, 0),  # train clip → must be dropped
            ('c9', 'creator', T0, 0),
            ('c7', 'creator', T0, 0),
        )
        rank = fm.recommendation_ranker(catalog)
        out = rank('u1', 2, [fm.InteractionRow('u1', 'c2', T0, 'like')])
        assert out == ['c9', 'c7']            # order preserved, k honored
        assert calls['pool_first'] is False   # Redis pool path bypassed
        assert calls['count'] == 4            # k + |exclusions| over-fetch
        # Serving instant = the latest train interaction, for BOTH the
        # decay/seen clock and the interaction view (no test leakage).
        assert calls['now'] == T0
        assert calls['created_before'] == T0

    def test_recommendation_ranker_survives_builder_crash(self, monkeypatch):
        import ai_ml.pipelines.recommendation as rec_mod

        def broken_builder(user_id, count, *, pool_first=True, now=None,
                           created_before=None):
            raise RuntimeError('pgvector extension missing')

        monkeypatch.setattr(rec_mod, 'build_feed_candidates', broken_builder)
        rank = fm.recommendation_ranker(_catalog(('c1', 'creator', T0, 0)))
        assert rank('u1', 2, []) == []


# ---------------------------------------------------------------------------
# End-to-end: the management command (needs ORM + Postgres, no Redis)
# ---------------------------------------------------------------------------

def _seed_command_fixtures():
    """A creator with 8 ready+approved clips and two consumers.

    consumer_a: 6 train likes (now-40d) + 1 test like (now-2d) — warm
    user. consumer_b: 2 train likes + 1 test like — cold-start slice.
    auto_now_add fields (AudioClip.created_at, UserInteraction.created_at)
    are pinned afterwards via .update(), the established pattern in
    this suite (see test_feed_exhaustion._seen, conftest.processing_clip).
    """
    from backend.app.models import AudioClip, UserInteraction, User

    now = timezone.now()
    creator = User.objects.create_user(
        username='eval-creator', email='eval-creator@example.com',
        password='pw-12345',
    )
    consumer_a = User.objects.create_user(
        username='eval-consumer-a', email='eval-consumer-a@example.com',
        password='pw-12345',
    )
    consumer_b = User.objects.create_user(
        username='eval-consumer-b', email='eval-consumer-b@example.com',
        password='pw-12345',
    )
    clips = []
    for i in range(8):
        clip = AudioClip.objects.create(
            creator=creator,
            title=f'Eval clip {i}',
            category='eval',
            status='ready',
            moderation_approved=True,
        )
        AudioClip.objects.filter(pk=clip.pk).update(
            created_at=now - timedelta(days=10 - i)
        )
        clips.append(clip)

    def interact(user, clip, when, kind='like'):
        row = UserInteraction.objects.create(
            user=user, clip=clip, interaction_type=kind,
            completion_rate=1.0,
        )
        UserInteraction.objects.filter(pk=row.pk).update(created_at=when)

    for clip in clips[:6]:
        interact(consumer_a, clip, now - timedelta(days=40))
    interact(consumer_a, clips[6], now - timedelta(days=2))
    interact(consumer_b, clips[0], now - timedelta(days=40))
    interact(consumer_b, clips[1], now - timedelta(days=38))
    interact(consumer_b, clips[7], now - timedelta(days=3))
    return creator, consumer_a, consumer_b, clips


@pytest.mark.django_db
class TestEvalFeedCommand:
    def test_eval_feed_command_prints_all_three_rankers(self):
        from django.core.management import call_command

        _seed_command_fixtures()
        out = StringIO()
        # All three rankers run, including the recommender adapter:
        # with LocMem/absent Redis this still works because the
        # adapter pins pool_first=False (SQL composite/cold-start
        # path against Postgres only).
        call_command('eval_feed', '--k', '5', '--max-users', '10', stdout=out)
        text = out.getvalue()
        for name in ('chronological', 'popularity', 'recommendation'):
            assert name in text
        assert 'recall@5' in text       # header carries the actual k
        assert 'ranked=2' in text       # consumer_a + consumer_b
        assert 'catalog_size=8' in text  # all 8 seeded clips are servable

    def test_eval_feed_command_json_out_contains_all_rankers(self, tmp_path):
        from django.core.management import call_command

        _seed_command_fixtures()
        path = tmp_path / 'feed_eval.json'
        out = StringIO()
        call_command(
            'eval_feed', '--k', '5', '--max-users', '10',
            '--json-out', str(path), stdout=out,
        )
        data = json.loads(path.read_text())
        assert set(data['rankers']) == {
            'chronological', 'popularity', 'recommendation',
        }
        assert data['meta']['k'] == 5
        assert data['meta']['holdout_days'] == 14
        assert data['meta']['n_users_ranked'] == 2
        for metrics in data['rankers'].values():
            for key in ('recall_at_k', 'ndcg_at_k', 'coverage_at_k',
                        'mean_list_length', 'n_users_evaluated'):
                assert key in metrics
            assert set(metrics['cold_start']) == {
                'recall_at_k', 'ndcg_at_k', 'n_users_evaluated',
                'n_users_in_slice',
            }
        # Measured values only: everything numeric in the JSON came
        # from the seeded rows, and no field claims a target/benchmark.
        assert data['rankers']['recommendation']['n_ranker_errors'] == 0

    def test_eval_feed_command_rejects_unknown_ranker(self):
        from django.core.management import CommandError, call_command

        with pytest.raises(CommandError):
            call_command('eval_feed', '--rankers', 'bogus-ranker')

    def test_recommendation_ranker_is_leak_free_via_as_of_simulation(self):
        """The adapter must NOT let TEST-window interactions inform the
        preference vector.

        Setup: the consumer trains on clip A (semantic e1, 'view',
        intent weight 1.0) and 'like's clip B (semantic e2 — orthogonal,
        intent weight 1.5) in the TEST window; clip C (semantic e1, a
        perfect match for the train vector) is an untouched candidate.
        All three carry matching acoustic vectors and zero velocity /
        completion so the composite reduces to vector similarity.

        As-of simulation (created_before = last train interaction)
        builds the vector from A alone → C's similarity is 1.0 vs B's
        0.5 → exploit/explore order [C, B]. A leaked vector blends B's
        heavier like with A's view (normalized tilt toward e2), which
        flips the order to [B, C] — so this assertion fails exactly
        when the holdout leaks into the ranking inputs.
        """
        from backend.app.models import AudioClip, UserInteraction, User
        from django.utils import timezone

        now = timezone.now()
        creator = User.objects.create_user(
            username='leak-creator', email='leak-creator@example.com',
            password='pw-12345',
        )
        consumer = User.objects.create_user(
            username='leak-consumer', email='leak-consumer@example.com',
            password='pw-12345',
        )

        def make_clip(name, sem, ac, age_days):
            clip = AudioClip.objects.create(
                creator=creator, title=name, status='ready',
                moderation_approved=True,
                semantic_vector=sem, acoustic_vector=ac,
            )
            AudioClip.objects.filter(pk=clip.pk).update(
                created_at=now - timedelta(days=age_days)
            )
            return clip

        clip_a = make_clip('A', [1.0] + [0.0] * 383,
                           [1.0] + [0.0] * 127, age_days=10)
        clip_b = make_clip('B', [0.0, 1.0] + [0.0] * 382,
                           [0.0, 1.0] + [0.0] * 126, age_days=9)
        clip_c = make_clip('C', [1.0] + [0.0] * 383,
                           [1.0] + [0.0] * 127, age_days=8)

        def interact(clip, when, kind):
            row = UserInteraction.objects.create(
                user=consumer, clip=clip, interaction_type=kind,
                completion_rate=1.0,
            )
            UserInteraction.objects.filter(pk=row.pk).update(created_at=when)

        train_when = now - timedelta(days=40)
        interact(clip_a, train_when, 'view')            # train window
        interact(clip_b, now - timedelta(days=2), 'like')  # TEST window

        catalog = [
            {'clip_id': str(c.id), 'creator_id': creator.id,
             'created_at': c.created_at, 'likes': 0}
            for c in (clip_a, clip_b, clip_c)
        ]
        rank = fm.recommendation_ranker(catalog)
        out = rank(
            consumer.id, 2,
            [fm.InteractionRow(consumer.id, str(clip_a.id), train_when, 'view')],
        )
        # Leak-free: the untouched e1-clip outranks the test-'like'd
        # e2-clip. (The train clip A is excluded either way.)
        assert out == [str(clip_c.id), str(clip_b.id)], out
