"""Offline feed-quality evaluation: the live recommender vs baselines.

WHY THIS EXISTS
---------------
The P2 audit finding: nothing in the repo measured recommendation
*quality* — only formula fidelity. This command wraps the pure
evaluator ``ai_ml/eval/feed_metrics.py`` with the Django ORM so an
operator can run the audit's requested comparison on a real dump:

    python manage.py eval_feed
    python manage.py eval_feed --k 10 --holdout-days 14 --max-users 500
    python manage.py eval_feed --rankers chronological,popularity --json-out /tmp/feed_eval.json

What it does: loads ``UserInteraction`` rows (restricted to catalog
clips), splits them with a temporal holdout (test = the last
``--holdout-days`` window, data-relative — see
``ai_ml.eval.feed_metrics.time_holdout``), then runs each requested
ranker and reports measured Recall@K / NDCG@K / coverage@K /
mean list length / cold-start slice. It prints measured values ONLY —
there are no target numbers, no invented benchmarks, and no silent
defaults: an empty population prints ``n/a``.

Eval universe (mirrors the audit directive):
  * catalog = ``AudioClip`` ``status='ready'`` AND
    ``moderation_approved=True``. NOTE: the production builder filters
    ``status='ready'`` only (ai_ml/pipelines/recommendation.py:254/266/316);
    the eval is deliberately stricter, and the recommendation adapter
    post-filters its output to this catalog so no ranker can score on
    clips the others may not serve.
  * positives = like / share / view interactions (``skip`` is the
    repo's own negative signal — recommendation.py:165).
  * user sample = the first ``--max-users`` users (ordered by pk) who
    have at least one catalog-clip interaction. Deterministic by
    design: two runs on the same data must produce the same table, or
    the comparison between them is noise.

REDIS: NOT REQUIRED. The ``recommendation`` ranker calls
``build_feed_candidates(..., pool_first=False, now=as_of,
created_before=as_of)``, which skips the Redis pool read
(``feed_pool.get_user_candidates``, feed_pool.py:258, gated by
``if pool_first:`` at recommendation.py:289) and exercises the
on-demand SQL composite path + cold-start ordering against Postgres
only. The ``now``/``created_before`` pair simulates serving at the
user's last train interaction so held-out (test) interactions cannot
leak into the ranking inputs. The Redis pool path
(``rebuild_global_exploit_pool`` / ``rebuild_user_explore_pool``) is
deliberately OUT of scope — see the adapter docstring in
``ai_ml/eval/feed_metrics.py`` for exactly what that means.

The command is read-only: it never writes interactions, counters or
caches. ``UserInteraction.save()`` side effects don't apply because
rows are only ever read here.
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

#: Ranker registry — names accepted by ``--rankers``. The factories
#: bind the eval catalog; the evaluator then treats them uniformly.
RANKER_FACTORIES = {
    'chronological': 'chronological_ranker',
    'popularity': 'popularity_ranker',
    'recommendation': 'recommendation_ranker',
}

DEFAULT_K = 10
DEFAULT_HOLDOUT_DAYS = 14
DEFAULT_MAX_USERS = 500


class Command(BaseCommand):
    help = (
        "Offline evaluation of feed recommendation quality: temporal "
        "holdout, Recall@K / NDCG@K / coverage@K / cold-start for the "
        "live recommender vs chronological and popularity baselines. "
        "Read-only; prints measured values only. No Redis required."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--k', type=int, default=DEFAULT_K,
            help=f'Cutoff for the ranked list (default: {DEFAULT_K}). '
                 'Metrics are recall@K / ndcg@K / coverage@K.',
        )
        parser.add_argument(
            '--holdout-days', type=float, default=DEFAULT_HOLDOUT_DAYS,
            dest='holdout_days',
            help=f'Width of the test window in days, measured back from '
                 f'the most recent interaction in the data '
                 f'(default: {DEFAULT_HOLDOUT_DAYS}). Data-relative, so '
                 f'old dumps evaluate identically whenever they are run.',
        )
        parser.add_argument(
            '--max-users', type=int, default=DEFAULT_MAX_USERS,
            dest='max_users',
            help=f'Sample cap: evaluate the first N users (ordered by pk) '
                 f'who have >= 1 catalog-clip interaction '
                 f'(default: {DEFAULT_MAX_USERS}). Deterministic, not '
                 f'random, for reproducible comparisons.',
        )
        parser.add_argument(
            '--rankers', type=str,
            default=','.join(RANKER_FACTORIES),
            help='Comma-separated ranker names '
                 f"(default: all of {', '.join(RANKER_FACTORIES)}).",
        )
        parser.add_argument(
            '--json-out', type=str, default=None, dest='json_out',
            help='Optional path: also write the full results dict as JSON '
                 '(same numbers as the table, plus per-slice counts).',
        )

    def handle(self, *args, **options):
        # Late import: keeps the command import-light (same convention
        # as the ai_ml package) and resolves only once the eval runs.
        import ai_ml.eval.feed_metrics as feed_metrics

        from backend.app.models import AudioClip, UserInteraction

        k = options['k']
        holdout_days = options['holdout_days']
        max_users = options['max_users']
        if k <= 0:
            raise CommandError('--k must be >= 1')
        if holdout_days <= 0:
            raise CommandError('--holdout-days must be > 0')
        if max_users <= 0:
            raise CommandError('--max-users must be >= 1')

        # The registry IS the source of truth: names, their factory
        # functions and the --rankers default all derive from it, so a
        # new baseline cannot be half-registered.
        factories = {
            name: getattr(feed_metrics, func_name)
            for name, func_name in RANKER_FACTORIES.items()
        }
        requested = [
            name.strip().lower()
            for name in (options['rankers'] or '').split(',')
            if name.strip()
        ]
        if not requested:
            raise CommandError(
                f'--rankers is empty; valid names: '
                f"{', '.join(RANKER_FACTORIES)}"
            )
        unknown = [name for name in requested if name not in factories]
        if unknown:
            raise CommandError(
                f"unknown ranker(s): {', '.join(unknown)}; valid names: "
                f"{', '.join(RANKER_FACTORIES)}"
            )

        # The catalog is the complete ready+approved clip set, not the
        # sample's clips: baselines and the recommender must draw from
        # the same universe for coverage and for the own-clip exclusion.
        catalog_rows = AudioClip.objects.filter(
            status='ready', moderation_approved=True,
        ).values('id', 'creator_id', 'created_at', 'likes')
        catalog = [
            {
                'clip_id': str(row['id']),
                'creator_id': row['creator_id'],
                'created_at': row['created_at'],
                'likes': row['likes'],
            }
            for row in catalog_rows
        ]

        # Eval users = anyone with >= 1 interaction on a catalog clip.
        # Interactions with non-catalog clips are excluded throughout:
        # their clips cannot be recommended by any ranker under eval,
        # so keeping the rows would pollute the exclusion sets with
        # items no ranker could have served anyway.
        user_rows = (
            UserInteraction.objects
            .filter(clip__status='ready', clip__moderation_approved=True)
            .values('user_id')
            .annotate(n=Count('id'))
            .order_by('user_id')
        )
        user_ids = [row['user_id'] for row in user_rows]
        n_users_found = len(user_ids)
        selected_user_ids = user_ids[:max_users]

        interaction_rows = (
            UserInteraction.objects
            .filter(
                user_id__in=selected_user_ids,
                clip__status='ready',
                clip__moderation_approved=True,
            )
            .values('user_id', 'clip_id', 'created_at', 'interaction_type')
        )
        train, test = feed_metrics.time_holdout(interaction_rows, holdout_days)

        rankers = {name: factories[name](catalog) for name in requested}

        results = feed_metrics.evaluate(
            rankers, train, test, k, len(catalog), catalog=catalog,
        )
        results['meta'].update({
            'generated_at': timezone.now().isoformat(),
            'holdout_days': holdout_days,
            'max_users': max_users,
            'n_users_found': n_users_found,
            'n_users_selected': len(selected_user_ids),
            'requested_rankers': requested,
            'catalog_filter': "status='ready' AND moderation_approved=True",
        })

        self._print_report(results)
        if options['json_out']:
            with open(options['json_out'], 'w', encoding='utf-8') as fh:
                json.dump(results, fh, indent=2, default=str)
                fh.write('\n')
            self.stdout.write(self.style.SUCCESS(
                f'JSON written to {options["json_out"]}'
            ))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _fmt(self, value, spec: str = '%.4f') -> str:
        """Format a metric, or 'n/a' when the population was empty.
        None and 0.0 must look different in the table: the first is
        'nothing measured', the second is 'measured, all misses'."""
        return 'n/a' if value is None else spec % value

    def _print_report(self, results: dict) -> None:
        meta = results['meta']
        self.stdout.write('EchoFeed offline feed evaluation (measured values only)')
        self.stdout.write(
            f"k={meta['k']} holdout_days={meta.get('holdout_days', 'n/a')} "
            f"max_users={meta.get('max_users', 'n/a')} "
            f"catalog_size={meta['catalog_size']} [{meta.get('catalog_filter', '')}]"
        )
        self.stdout.write(
            f"users: found={meta.get('n_users_found', 'n/a')} "
            f"selected={meta.get('n_users_selected', 'n/a')} "
            f"ranked={meta['n_users_ranked']} "
            f"excluded_empty_train={meta['n_users_excluded_empty_train']} "
            f"excluded_empty_test={meta['n_users_excluded_empty_test']} "
            f"skipped_no_positives={meta['n_users_skipped_no_positives']}"
        )
        self.stdout.write(
            'positive interaction types: '
            f"{', '.join(meta['positive_types'])} "
            '(other held-out types never count as ground truth)'
        )
        k = meta['k']
        header = (
            f"{'ranker':<16} {'recall@%s' % k:>9} {'ndcg@%s' % k:>9} "
            f"{'coverage@%s' % k:>12} {'mean_len':>9} {'n_eval':>7} "
            f"{'errors':>7} {'cs_recall':>10} {'cs_ndcg':>9} {'cs_eval':>8}"
        )
        self.stdout.write(header)
        if not results['rankers']:
            self.stdout.write('(no rankers selected)')
            return
        for name, metrics in results['rankers'].items():
            cold = metrics['cold_start']
            self.stdout.write(
                f"{name:<16} "
                f"{self._fmt(metrics['recall_at_k']):>9} "
                f"{self._fmt(metrics['ndcg_at_k']):>9} "
                f"{self._fmt(metrics['coverage_at_k']):>12} "
                f"{self._fmt(metrics['mean_list_length'], '%.2f'):>9} "
                f"{metrics['n_users_evaluated']:>7} "
                f"{metrics['n_ranker_errors']:>7} "
                f"{self._fmt(cold['recall_at_k']):>10} "
                f"{self._fmt(cold['ndcg_at_k']):>9} "
                f"{cold['n_users_evaluated']:>8}"
            )
        self.stdout.write(
            'cs_* = cold-start slice (users with < '
            f"{meta['cold_start_threshold']} train interactions); "
            'n_eval = users contributing to recall/ndcg'
        )
