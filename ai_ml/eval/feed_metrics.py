"""Offline quality evaluation for the feed recommender (audit P2, 2026-11).

WHY THIS EXISTS
---------------
The P2 audit finding against this repo: the ranking implementation
(``ai_ml/pipelines/recommendation.py``) was verified for formula
fidelity but never for *quality* — nothing in the tree answered "is
the feed actually good?". This module is the offline answer: a
temporal holdout split of real ``UserInteraction`` rows, Recall@K /
NDCG@K against held-out positives, catalog coverage, a cold-start
slice, and a like-for-like comparison of the live recommender against
chronological and popularity baselines. It is PURE PYTHON (stdlib
only — no Django, numpy, sklearn or torch) so the metric math is
unit-testable without a database and cannot drift into ORM laziness
bugs; the Django shell around it is
``backend/app/management/commands/eval_feed.py``.

EVALUATION UNIVERSE (decisions, not accidents)
----------------------------------------------
* Catalog = ``AudioClip`` rows with ``status='ready'`` AND
  ``moderation_approved=True``. The audit directive for the eval
  catalog is stricter than the production builder, which filters
  ``status='ready'`` only (recommendation.py:254/266/316) and never
  checks moderation. Baselines rank inside this catalog; the
  recommender adapter post-filters its output to it, so no ranker can
  score points on a clip another ranker is not allowed to serve.
* Relevant (positive) held-out items = test-window interactions whose
  ``interaction_type`` is in ``DEFAULT_POSITIVE_TYPES``
  (like/share/view). ``skip`` is deliberately NOT a positive: the
  repo's own scoring treats a low-completion skip as an explicit
  negative signal (intent weight -0.5, recommendation.py:165).
  Skipped rows still land in train (so their clips are excluded from
  every ranker's candidates) — they just never count as ground truth.
* Timestamps: naive datetimes are assumed UTC (Django with
  ``USE_TZ=True`` returns aware datetimes, so this only matters for
  hand-built rows in tests/scripts). Missing/garbage timestamps raise
  ValueError — silence here would silently mis-split the holdout.

METRIC FORMULAS (binary relevance; K = list cutoff)
---------------------------------------------------
Recall@K  = |topK(recommended) ∩ Relevant| / |Relevant|
            (0.0 when Relevant is empty; callers skip such users in
            the means and count them separately instead).
DCG@K     = Σ_{i=1..K} rel_i / log2(i + 1),  rel_i ∈ {0, 1}
IDCG@K    = Σ_{i=1..min(|Relevant|, K)} 1 / log2(i + 1)
NDCG@K    = DCG@K / IDCG@K  (∈ [0, 1]; 0.0 when IDCG@K == 0)
            IDCG is the best possible ranking of
            min(|Relevant|, K) hits — i.e. a perfect list of length
            min(|Relevant|, K) — which is the standard binary-relevance
            normalization (per-call, not per-dataset).
Coverage@K = |⋃_u topK_u| / |catalog| — the fraction of the catalog
            any ranker actually serves. Items outside the catalog in
            the input lists still enter the numerator; callers that
            care pre-filter (``evaluate`` does).

TOLERANCE POLICY
----------------
Missing vectors, empty candidate lists, users with no positives, and
rankers that crash are all non-fatal: they degrade to empty lists /
skipped recall contributions and are COUNTED (``n_ranker_errors``,
``n_users_skipped_no_positives``) rather than swallowed, because a
silent empty list and a measured zero look identical in a table but
mean opposite things.

RANKER PROTOCOL
---------------
A ranker is a callable ``rank(user_id, k, train_interactions) ->
list[clip_id]`` where ``train_interactions`` is THAT user's train
rows (``InteractionRow`` tuples or dict/model rows with ``clip_id``).
The three shipped rankers are factories bound to the eval catalog:
``chronological_ranker(catalog)``, ``popularity_ranker(catalog)`` and
``recommendation_ranker(catalog)`` — see their docstrings. The
catalog argument is an iterable of dict/model rows carrying
``clip_id`` (or ``id``), ``creator_id``, ``created_at`` and ``likes``
(Django ``.values(...)`` output works directly).

Companion: ``backend/app/tests/test_feed_metrics_eval.py`` (hand-
computed metric cases + one ``django_db`` end-to-end run of the
management command) and ``backend/app/management/commands/eval_feed.py``.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, NamedTuple, Optional

logger = logging.getLogger(__name__)

__all__ = [
    'InteractionRow',
    'DEFAULT_POSITIVE_TYPES',
    'COLD_START_MIN_TRAIN',
    'time_holdout',
    'recall_at_k',
    'ndcg_at_k',
    'catalog_coverage',
    'chronological_ranker',
    'popularity_ranker',
    'recommendation_ranker',
    'evaluate',
]

#: Interaction types that count as ground-truth positives. ``skip`` is
#: excluded on purpose — see the module docstring and
#: ai_ml/pipelines/recommendation.py:165 (skip intent weight -0.5).
DEFAULT_POSITIVE_TYPES = frozenset({'like', 'share', 'view'})

#: "Cold start" slice = ranked users with fewer than this many train
#: interactions. Users with ZERO train rows are excluded from ranking
#: metrics entirely (see ``evaluate``), so the slice effectively spans
#: 1..4 train interactions — the "has a little history, not enough to
#: trust" band the 80/20 exploit/explore design worries about.
COLD_START_MIN_TRAIN = 5

#: How far past ``k`` the ``recommendation_ranker`` over-fetches from
#: ``build_feed_candidates``. Over-fetch = k + |exclusions| guarantees
#: k eligible items survive the post-filter when they exist; the cap
#: bounds the SQL for hoarders with thousands of train interactions
#: (their lists may then fall short of k — reported via mean_list_length).
_OVERFETCH_CAP = 2000

_MISSING = object()


class InteractionRow(NamedTuple):
    """One normalized interaction — the currency of this module.

    ``clip_id`` is always ``str`` (UUIDs normalized) so exclusion sets
    and recommendation lists are directly comparable;
    ``user_id`` keeps its raw type (Django int pk, or whatever the
    caller uses). Naive ``created_at`` values are assumed UTC.
    """

    user_id: Any
    clip_id: str
    created_at: datetime
    interaction_type: Optional[str]


# ---------------------------------------------------------------------------
# Row access / normalization — tolerant to dicts, .values() rows, models
# ---------------------------------------------------------------------------

def _field(row: Any, name: str, *aliases: str) -> Any:
    """Read ``name`` (then ``aliases``) from a dict OR an attribute
    object. Returns ``_MISSING`` when nothing matches, so callers can
    distinguish "field absent" from "field is None"."""
    for key in (name, *aliases):
        if isinstance(row, dict):
            if key in row:
                return row[key]
        else:
            value = getattr(row, key, _MISSING)
            if value is not _MISSING:
                return value
    return _MISSING


def _normalize_interaction(row: Any) -> InteractionRow:
    """Coerce any interaction-shaped row into an ``InteractionRow``.

    Accepts Django ``UserInteraction`` instances, ``.values(...)``
    dicts, and plain dicts. ``clip_id`` (alias ``id``) is stringified;
    naive datetimes get UTC attached. A row without a usable datetime
    is a data bug, not a tolerable gap — it would silently distort the
    split — so it raises ValueError naming the problem.
    """
    user_id = _field(row, 'user_id')
    clip_id = _field(row, 'clip_id', 'id')
    created_at = _field(row, 'created_at')
    interaction_type = _field(row, 'interaction_type')
    if user_id is _MISSING or clip_id is _MISSING or created_at is _MISSING:
        raise ValueError(
            f'interaction row {row!r} is missing user_id / clip_id / created_at'
        )
    if not isinstance(created_at, datetime):
        raise ValueError(
            f'interaction created_at must be a datetime, got '
            f'{type(created_at).__name__!r} for row {row!r}'
        )
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return InteractionRow(
        user_id=user_id,
        clip_id=str(clip_id),
        created_at=created_at,
        interaction_type=(
            None if interaction_type is _MISSING else interaction_type
        ),
    )


def _ts(value: Any) -> float:
    """Ordering key for created_at values; missing → -inf so 'never'
    sorts as oldest. ``.timestamp()`` maps naive and aware datetimes
    onto one monotonic float axis, which is all ordering needs."""
    if isinstance(value, datetime):
        return value.timestamp()
    return float('-inf')


# ---------------------------------------------------------------------------
# time_holdout — temporal split
# ---------------------------------------------------------------------------

def time_holdout(
    interactions: Iterable[Any],
    holdout_days: float,
) -> tuple[list[InteractionRow], list[InteractionRow]]:
    """Split interactions into (train, test) by timestamp.

    WHY temporal: a random split leaks the future into the training
    signal (the recommender would "know" about clips the user only
    touched later) and inflates every ranker equally, which hides the
    exact failure mode the audit cares about. The split is DATA-
    relative: ``cutoff = max(created_at) - holdout_days``, so a dump of
    year-old interactions evaluates identically no matter when the eval
    runs (splitting against wall-clock ``now`` on stale data would put
    100% of rows in train and measure nothing).

    test = interactions with ``created_at >= cutoff`` (the last
    ``holdout_days`` window, closed on the left — a row exactly AT the
    cutoff is test); train = everything strictly older.

    Users ending up with an empty train or empty test side are NOT
    special-cased here — both lists are returned whole and
    ``evaluate`` excludes + counts them (they would either give a
    ranker no exclusion history or nothing to score against).
    """
    if holdout_days <= 0:
        raise ValueError(f'holdout_days must be > 0, got {holdout_days!r}')
    rows = [_normalize_interaction(row) for row in interactions]
    if not rows:
        return [], []
    cutoff = max(row.created_at for row in rows) - timedelta(days=holdout_days)
    train = [row for row in rows if row.created_at < cutoff]
    test = [row for row in rows if row.created_at >= cutoff]
    return train, test


# ---------------------------------------------------------------------------
# Core metrics — pure, hand-verifiable
# ---------------------------------------------------------------------------

def _dedupe(items: Iterable[Any]) -> list[str]:
    """Stringify + dedupe preserving first-occurrence order. Duplicates
    in a recommended list would otherwise double-count a hit inside the
    DCG sum (position-weighted), which is a metric bug, not a ranking
    judgment — real lists are expected unique, this defends the math."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = str(item)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def recall_at_k(recommended: Iterable[Any], relevant: Iterable[Any], k: int) -> float:
    """Recall@K = |topK(recommended) ∩ relevant| / |relevant|.

    Binary relevance. The denominator is ALL relevant items — not
    min(K, |relevant|) — so "recall@3 with 2 hits of 3 relevant" is
    exactly 2/3 regardless of list length. Returns 0.0 for an empty
    relevant set or k <= 0 (the caller decides whether an empty
    relevant set means "skip this user" — ``evaluate`` does).
    """
    if k <= 0:
        return 0.0
    rel = set(str(item) for item in relevant)
    if not rel:
        return 0.0
    hits = 0
    for index, item in enumerate(_dedupe(recommended)):
        if index >= k:
            break
        if item in rel:
            hits += 1
    return hits / len(rel)


def ndcg_at_k(recommended: Iterable[Any], relevant: Iterable[Any], k: int) -> float:
    """Normalized Discounted Cumulative Gain at K, binary relevance.

    DCG@K  = Σ_{i=1..K} rel_i / log2(i + 1)      (i is 1-based rank)
    IDCG@K = Σ_{i=1..min(|relevant|, K)} 1 / log2(i + 1)
    NDCG@K = DCG@K / IDCG@K, and 0.0 when IDCG@K == 0.

    The log2 discount encodes "a hit at rank r is worth
    1/log2(r+1) of a hit at rank 1" (rank 1 → 1.0, rank 2 → ~0.63,
    rank 3 → 0.5, ...). IDCG uses min(|relevant|, K) — the best a list
    of K slots can score given only |relevant| positives — so a perfect
    ranking scores exactly 1.0 even when |relevant| < K, and
    unreachable slots never penalize a ranker. Recommended lists are
    deduped first (see ``_dedupe``) so a repeated hit cannot inflate
    DCG. Returns 0.0 for empty relevant / k <= 0.
    """
    if k <= 0:
        return 0.0
    rel = set(str(item) for item in relevant)
    if not rel:
        return 0.0
    dcg = 0.0
    for rank, item in enumerate(_dedupe(recommended)[:k], start=1):
        if item in rel:
            dcg += 1.0 / math.log2(rank + 1)
    ideal = min(len(rel), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def catalog_coverage(
    recommendations_by_user: Any,
    catalog_size: int,
) -> float:
    """Coverage = |distinct items recommended across all users| / catalog_size.

    ``recommendations_by_user`` is a dict user→list or any iterable of
    lists (the finalized top-K lists). Coverage complements accuracy:
    a ranker can win NDCG by serving the same 20 viral clips to
    everyone, and this ratio is how that failure becomes visible.
    Returns 0.0 when ``catalog_size`` <= 0 (nothing measurable — an
    empty catalog cannot be 'covered', and dividing by zero would be
    worse). Items outside the catalog are NOT filtered here; callers
    that restrict the universe pre-filter their lists (``evaluate``
    does) so the denominator and numerator describe the same space.
    """
    if catalog_size <= 0:
        return 0.0
    sequences = (
        recommendations_by_user.values()
        if isinstance(recommendations_by_user, dict)
        else recommendations_by_user
    )
    distinct: set[str] = set()
    for sequence in sequences:
        if sequence:
            distinct.update(str(item) for item in sequence)
    return len(distinct) / catalog_size


# ---------------------------------------------------------------------------
# Ranker factories — the three comparable strategies
# ---------------------------------------------------------------------------

def _catalog_entries(catalog: Iterable[Any]) -> list[tuple[str, Optional[str], float, float]]:
    """Normalize catalog rows to (clip_id, creator_id, created_ts, likes).

    clip_id/creator_id are stringified (UUID/int safe); creator None
    means "row did not say" — own-clip exclusion then never fires for
    it, which is the correct degradation (never exclude on a guess).
    """
    entries: list[tuple[str, Optional[str], float, float]] = []
    for entry in catalog:
        clip_id = _field(entry, 'clip_id', 'id')
        if clip_id is _MISSING:
            raise ValueError(f'catalog row {entry!r} is missing clip_id/id')
        creator = _field(entry, 'creator_id')
        likes = _field(entry, 'likes')
        entries.append((
            str(clip_id),
            None if creator is _MISSING or creator is None else str(creator),
            _ts(_field(entry, 'created_at')),
            0.0 if likes is _MISSING or likes is None else float(likes),
        ))
    return entries


def _train_exclusions(train_interactions: Iterable[Any]) -> set[str]:
    """The clip ids the user already interacted with in train. These
    are removed from EVERY ranker's candidates — a ranker that gets to
    recommend a train clip competes with inside knowledge the
    baselines do not have, and the comparison would be theatre."""
    out: set[str] = set()
    for row in train_interactions or ():
        clip_id = _field(row, 'clip_id', 'id')
        if clip_id is not _MISSING and clip_id is not None:
            out.add(str(clip_id))
    return out


def chronological_ranker(catalog: Iterable[Any]) -> Callable:
    """Factory: baseline ranking "most recent ready clips first".

    Orders the eval catalog by ``created_at`` DESC (ties → clip_id ASC
    for deterministic, reproducible runs), then per user returns the
    first k clips that are neither the user's own nor in their train
    interactions. This is what the feed degenerates to without any
    personalization (and what the production cold-start ordering
    approximates, recommendation.py:315-328) — the floor the
    recommender must beat to justify its vector infrastructure.
    """
    entries = sorted(
        _catalog_entries(catalog),
        key=lambda entry: (-entry[2], entry[0]),
    )
    clip_ids = [entry[0] for entry in entries]
    creator_ids = [entry[1] for entry in entries]

    def rank(user_id, k, train_interactions):
        if k <= 0:
            return []
        exclude = _train_exclusions(train_interactions)
        user_key = str(user_id)
        out: list[str] = []
        for clip_id, creator_id in zip(clip_ids, creator_ids):
            if clip_id in exclude or creator_id == user_key:
                continue
            out.append(clip_id)
            if len(out) >= k:
                break
        return out

    rank.__name__ = 'chronological_ranker'
    rank.__qualname__ = 'chronological_ranker.<locals>.rank'
    return rank


def popularity_ranker(catalog: Iterable[Any]) -> Callable:
    """Factory: baseline ranking "(likes DESC, created_at DESC)".

    Same exclusions and tie-break rule as ``chronological_ranker``; the
    difference is purely the ordering key. This isolates HOW MUCH of
    the recommender's value is just "serve what everyone else liked" —
    if the recommender cannot beat popularity, its personalization is
    not paying for the pool rebuilds.
    """
    entries = sorted(
        _catalog_entries(catalog),
        key=lambda entry: (-entry[3], -entry[2], entry[0]),
    )
    clip_ids = [entry[0] for entry in entries]
    creator_ids = [entry[1] for entry in entries]

    def rank(user_id, k, train_interactions):
        if k <= 0:
            return []
        exclude = _train_exclusions(train_interactions)
        user_key = str(user_id)
        out: list[str] = []
        for clip_id, creator_id in zip(clip_ids, creator_ids):
            if clip_id in exclude or creator_id == user_key:
                continue
            out.append(clip_id)
            if len(out) >= k:
                break
        return out

    rank.__name__ = 'popularity_ranker'
    rank.__qualname__ = 'popularity_ranker.<locals>.rank'
    return rank


def recommendation_ranker(catalog: Iterable[Any]) -> Callable:
    """Factory: ranker that adapts the LIVE recommender.

    Each call runs ``ai_ml.pipelines.recommendation.build_feed_candidates
    (user_id, count, pool_first=False, now=as_of, created_before=as_of)``
    — the real production ranking code, not a re-implementation — then
    applies the SAME exclusions as the baselines (own clips + train
    clips) and the catalog restriction, so the comparison is
    like-for-like. ``count = k + |exclusions|`` (capped at
    ``_OVERFETCH_CAP``) over-fetches so that filtering down to k
    eligible items is possible whenever k eligible items exist.

    TEMPORAL SIMULATION (leak-free by construction): ``as_of`` is the
    instant of the user's most recent TRAIN interaction. The builder's
    temporal-simulation parameters (added for this evaluator; defaults
    preserve production behavior exactly) scope its inputs to that
    moment:
      * ``created_before=as_of`` — the seen-exclusion and the
        preference vectors are computed from the user's train
        interactions ONLY. Without it, ``calculate_time_decayed_vectors``
        reads the user's latest 50 interactions straight from the DB,
        which on a temporal holdout means the TEST window informs the
        very vector that then ranks the test positives — a leak that
        would inflate this ranker's recall/ndcg relative to the
        baselines. (A user's test rows are always strictly later than
        their latest train row, so ``created_before=max(train ts)``
        excludes exactly the test rows.)
      * ``now=as_of`` — the 30-day seen window and the decay weights
        are anchored at the simulated serving instant instead of wall
        clock, so "recently seen" and "recently liked" mean what they
        meant AT the split, not at eval-run time.
    What the ranker may still legitimately recommend: test-window
    clips (they are candidates like any other — that is what recall
    measures); they just cannot influence its inputs.

    WHAT IS EXERCISED (and what it costs):
      * SQL composite fallback (recommendation.py:310-361) — the
        45/30/25 vector+completion+velocity blend via pgvector
        CosineDistance — plus ``calculate_time_decayed_vectors``
        (recommendation.py:120) and the cold-start ordering
        (recommendation.py:365-376) when the user has no usable
        vectors. Needs Postgres with pgvector; NO Redis.
      * ``_recovery_candidates`` (recommendation.py:70) if the
        eligible catalog ever runs dry.

    WHAT IS *NOT* EXERCISED:
      * The Redis pool path. ``pool_first=False`` skips
        ``feed_pool.get_user_candidates`` (feed_pool.py:258, a
        ZREVRANGE against ZSETs built by ``rebuild_global_exploit_pool``
        / ``rebuild_user_explore_pool``, feed_pool.py:129/184, which
        need Redis + Celery beat). The eval therefore measures the
        on-demand ranking math, NOT pool freshness or the Redis-side
        80/20 exploit/explore mix (feed_tasks.refill_user_feed). Run
        the eval against populated pools if you want the pool path
        too — a deliberate scope line, documented here rather than
        hidden.
      * The pending-feed dedup the Celery refill applies on write.

    CATALOG MISMATCH: the production builder filters ``status='ready'``
    only (recommendation.py:316/368) and never checks
    ``moderation_approved``; the eval catalog is ready+approved. This
    adapter post-filters the builder's output to the eval catalog, so
    non-approved clips it proposes are dropped before any metric —
    otherwise it could score on clips the baselines may not serve.
    """
    entries = _catalog_entries(catalog)
    catalog_ids = {entry[0] for entry in entries}
    clips_by_creator: dict[str, set[str]] = defaultdict(set)
    for clip_id, creator_id, _ts_value, _likes in entries:
        if creator_id is not None:
            clips_by_creator[creator_id].add(clip_id)

    def rank(user_id, k, train_interactions):
        if k <= 0:
            return []
        train_rows = list(train_interactions or ())
        exclude = _train_exclusions(train_rows)
        exclude |= clips_by_creator.get(str(user_id), set())
        count = k + min(len(exclude), _OVERFETCH_CAP)
        # As-of serving instant: the user's most recent train
        # interaction. All train rows carry datetimes after evaluate's
        # normalization; a direct caller passing something else simply
        # gets wall-clock behavior (documented degradation, not a
        # crash).
        timestamps = [
            row.created_at for row in train_rows
            if getattr(row, 'created_at', None) is not None
        ]
        as_of = max(timestamps) if timestamps else None
        # Late import mirrors the repo convention (recommendation.py
        # does the same to dodge import cycles) and keeps THIS module
        # import-light for the boundary guard.
        from ai_ml.pipelines.recommendation import build_feed_candidates

        try:
            raw = build_feed_candidates(
                user_id,
                count,
                pool_first=False,
                now=as_of,
                created_before=as_of,
            )
        except Exception:
            # A single broken user must not kill a 500-user eval run;
            # the failure is counted (n_ranker_errors) and logged, not
            # swallowed into a deceptively normal-looking score.
            logger.warning(
                'recommendation_ranker: build_feed_candidates failed for '
                'user %s; scoring as an empty list',
                user_id,
                exc_info=True,
            )
            return []
        out: list[str] = []
        for item in raw or ():
            clip_id = str(item)
            if clip_id in exclude or clip_id not in catalog_ids:
                continue
            out.append(clip_id)
            if len(out) >= k:
                break
        return out

    rank.__name__ = 'recommendation_ranker'
    rank.__qualname__ = 'recommendation_ranker.<locals>.rank'
    return rank


# ---------------------------------------------------------------------------
# evaluate — the harness around the metrics
# ---------------------------------------------------------------------------

def _finalize(
    recommended: Any,
    k: int,
    exclude: set[str],
    catalog_set: Optional[set[str]],
) -> list[str]:
    """Uniform last-mile filter applied to EVERY ranker's output:
    dedupe → drop excluded (train/own) → drop off-catalog → truncate k.

    The shipped rankers already self-filter, so for them this is a
    no-op; it exists so a custom ranker that ignores the exclusion
    contract still cannot win the comparison on leakage. Truncation
    happens AFTER filtering — rankers that want k clean slots should
    over-fetch (the recommendation adapter does).
    """
    if not recommended:
        return []
    out: list[str] = []
    for item in _dedupe(recommended):
        if item in exclude:
            continue
        if catalog_set is not None and item not in catalog_set:
            continue
        out.append(item)
        if len(out) >= k:
            break
    return out


def _named_rankers(rankers: Any) -> list[tuple[str, Callable]]:
    """Accept {name: callable}, [(name, callable), ...] or bare
    callables (→ ``__name__``); reject duplicate/empty names and
    non-callables loudly instead of silently dropping a competitor."""
    if isinstance(rankers, dict):
        items = list(rankers.items())
    else:
        items = []
        for entry in rankers:
            if (
                isinstance(entry, (tuple, list))
                and len(entry) == 2
                and callable(entry[1])
            ):
                items.append((entry[0], entry[1]))
            elif callable(entry):
                items.append(
                    (getattr(entry, '__name__', f'ranker_{len(items)}'), entry)
                )
            else:
                raise TypeError(f'not a ranker callable or (name, callable): {entry!r}')
    named: list[tuple[str, Callable]] = []
    seen: set[str] = set()
    for name, fn in items:
        name = str(name)
        if not callable(fn):
            raise TypeError(f'ranker {name!r} is not callable')
        if name in seen:
            raise ValueError(f'duplicate ranker name {name!r}')
        seen.add(name)
        named.append((name, fn))
    return named


def _mean(values: list[float]) -> Optional[float]:
    """Mean, or None for an empty population. None (not 0.0) is the
    honest value: 'no users could be scored' and 'every user scored 0'
    are different findings, and JSON null prints as n/a instead of
    masquerading as a measured zero."""
    return sum(values) / len(values) if values else None


def evaluate(
    rankers: Any,
    train: Iterable[Any],
    test: Iterable[Any],
    k: int,
    catalog_size: int,
    *,
    catalog: Optional[Iterable[Any]] = None,
    positive_types: Iterable[str] = DEFAULT_POSITIVE_TYPES,
    cold_start_threshold: int = COLD_START_MIN_TRAIN,
) -> dict:
    """Run every ranker over the temporal holdout and return per-ranker
    metrics — the offline counterpart of the P2 audit finding.

    Protocol: each ranker is called as ``rank(user_id, k,
    user_train_rows)`` for every RANKED user (non-empty train AND
    test; users failing that are counted in
    ``n_users_excluded_empty_train`` / ``..._empty_test`` — they can
    contribute neither a fair candidate history nor a scoreable
    ground truth). Its output passes through ``_finalize`` (uniform
    dedupe / exclusion / catalog restriction / k-truncation), then:

      * recall@k / ndcg@k  — averaged over users with >= 1 positive
        (catalog-restricted) held-out interaction; users with none are
        counted in ``n_users_skipped_no_positives`` and still
        contribute to list length + coverage (their recommendations
        get served in production too).
      * coverage@k         — ``catalog_coverage`` over all finalized
        lists, against ``catalog_size``.
      * mean_list_length   — mean finalized list size over all ranked
        users (short lists are a real defect: fewer chances to hit).
      * cold_start         — the same recall/ndcg restricted to ranked
        users with < ``cold_start_threshold`` train rows, plus
        ``n_users_in_slice`` / ``n_users_evaluated`` for that slice.
        This is where personalization must prove it adds anything over
        the chronological floor.

    ``catalog`` (optional) supplies the clip universe: when given, it
    restricts both relevance (test rows on off-catalog clips are
    unservable by every ranker, so they are excluded from ground truth
    rather than allowed to depress all metrics equally) and the
    uniform own-clip exclusion; when omitted, only train-based
    exclusions apply. ``positive_types`` defaults to like/share/view
    (see ``DEFAULT_POSITIVE_TYPES``).

    Returns ``{'meta': {...}, 'rankers': {name: {...}}}`` — fully
    JSON-serializable; metric values are None when their population is
    empty. Never invents a number: everything here is measured from
    the (train, test) you pass in.
    """
    if k <= 0:
        raise ValueError(f'k must be >= 1, got {k!r}')
    if cold_start_threshold < 1:
        raise ValueError(
            f'cold_start_threshold must be >= 1, got {cold_start_threshold!r}'
        )
    positive = frozenset(positive_types)
    named = _named_rankers(rankers)

    train_by_user: dict[Any, list[InteractionRow]] = defaultdict(list)
    test_by_user: dict[Any, list[InteractionRow]] = defaultdict(list)
    # Normalize on the way IN so both the rankers (train rows) and the
    # relevance computation (test rows) always see InteractionRow
    # attributes, whatever row shape the caller passed.
    for row in train:
        normalized = _normalize_interaction(row)
        train_by_user[normalized.user_id].append(normalized)
    for row in test:
        normalized = _normalize_interaction(row)
        test_by_user[normalized.user_id].append(normalized)

    catalog_set: Optional[set[str]] = None
    clips_by_creator: dict[str, set[str]] = defaultdict(set)
    if catalog is not None:
        catalog_set = set()
        for clip_id, creator_id, _ts_value, _likes in _catalog_entries(catalog):
            catalog_set.add(clip_id)
            if creator_id is not None:
                clips_by_creator[creator_id].add(clip_id)

    all_users = set(train_by_user) | set(test_by_user)
    # str() key: user ids may be int (Django pks) or str — sort must not
    # compare mixed types, and determinism of ranker call order matters
    # for reproducible runs.
    ranked_users = sorted(
        (
            user
            for user in all_users
            if train_by_user.get(user) and test_by_user.get(user)
        ),
        key=str,
    )
    meta = {
        'k': k,
        'catalog_size': catalog_size,
        'cold_start_threshold': cold_start_threshold,
        'positive_types': sorted(positive),
        'n_users_total': len(all_users),
        'n_users_ranked': len(ranked_users),
        'n_users_excluded_empty_train': sum(
            1 for user in all_users if not train_by_user.get(user)
        ),
        'n_users_excluded_empty_test': sum(
            1 for user in all_users if not test_by_user.get(user)
        ),
        'n_users_skipped_no_positives': sum(
            1
            for user in ranked_users
            if not _relevant_for(
                test_by_user[user], positive, catalog_set
            )
        ),
    }

    ranker_results: dict[str, dict] = {}
    for name, rank_fn in named:
        recalls: list[float] = []
        ndcgs: list[float] = []
        lengths: list[float] = []
        rec_lists: list[list[str]] = []
        cold_recalls: list[float] = []
        cold_ndcgs: list[float] = []
        n_eval = 0
        n_cold_slice = 0
        n_cold_eval = 0
        n_skipped = 0
        n_errors = 0
        for user_id in ranked_users:
            user_train = train_by_user[user_id]
            is_cold = len(user_train) < cold_start_threshold
            exclude = _train_exclusions(user_train)
            if catalog_set is not None:
                exclude |= clips_by_creator.get(str(user_id), set())
            try:
                raw = rank_fn(user_id, k, user_train)
            except Exception:
                n_errors += 1
                logger.warning(
                    'evaluate: ranker %s raised for user %s; scoring as '
                    'an empty list',
                    name,
                    user_id,
                    exc_info=True,
                )
                raw = []
            final = _finalize(raw, k, exclude, catalog_set)
            lengths.append(float(len(final)))
            rec_lists.append(final)
            # Slice membership is a property of the user's TRAIN depth,
            # not of whether they had positives — counted before the
            # no-positive skip so the slice size is ranker-independent.
            if is_cold:
                n_cold_slice += 1
            relevant = _relevant_for(test_by_user[user_id], positive, catalog_set)
            if not relevant:
                n_skipped += 1
                continue
            recalls.append(recall_at_k(final, relevant, k))
            ndcgs.append(ndcg_at_k(final, relevant, k))
            n_eval += 1
            if is_cold:
                cold_recalls.append(recalls[-1])
                cold_ndcgs.append(ndcgs[-1])
                n_cold_eval += 1

        ranker_results[name] = {
            'recall_at_k': _mean(recalls),
            'ndcg_at_k': _mean(ndcgs),
            'coverage_at_k': catalog_coverage(rec_lists, catalog_size),
            'mean_list_length': _mean(lengths) if lengths else 0.0,
            'n_users_evaluated': n_eval,
            'n_users_ranked': len(ranked_users),
            'n_users_skipped_no_positives': n_skipped,
            'n_ranker_errors': n_errors,
            'cold_start': {
                'recall_at_k': _mean(cold_recalls),
                'ndcg_at_k': _mean(cold_ndcgs),
                'n_users_evaluated': n_cold_eval,
                'n_users_in_slice': n_cold_slice,
            },
        }

    return {'meta': meta, 'rankers': ranker_results}


def _relevant_for(
    test_rows: Iterable[InteractionRow],
    positive: frozenset,
    catalog_set: Optional[set[str]],
) -> set[str]:
    """Held-out positives for one user: test rows of a positive type,
    restricted to the catalog when one is in force (off-catalog items
    are unservable by every ranker — counting them as relevant would
    uniformly depress all metrics without discriminating anything)."""
    return {
        row.clip_id
        for row in test_rows
        if row.interaction_type in positive
        and (catalog_set is None or row.clip_id in catalog_set)
    }
