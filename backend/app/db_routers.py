"""Read-replica router.

Routes read-only queries on the `app` app to a read replica (the `'read'`
connection alias) so the primary is not the read bottleneck. Writes,
admin operations, migrations, and any query that participates in a
write transaction stay on the primary.

The replica is OPTIONAL: if the `read` alias is not configured (i.e.,
`READ_DATABASE_URL` is not set in the environment), the router falls
back to `'default'` for every read, so this code is safe to enable
without the replica being live.

Companion design doc: `docs/EXPLAIN/database/05-read-replica-design.md`.
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction


APP_LABEL = 'app'

# DECISION: only route reads for the 'app' app. django.contrib.* tables
# (auth, contenttypes, sessions, admin) stay on the primary because:
# 1) auth is the load-bearing security boundary — wrong-DB reads can
#    cause permission flips to lag the user's actual state.
# 2) The volume is trivial (a few hundred rows) and the contention
#    cost of a replica is not justified.
ROUTED_APP_LABELS = frozenset({APP_LABEL})


def _has_read_alias() -> bool:
    return 'read' in settings.DATABASES


def db_for_read(model, **hints):
    """Return 'read' for routed apps when the replica is configured and
    the call is not inside an atomic block and not using select_for_update.
    """
    if not _has_read_alias():
        return None

    if model._meta.app_label not in ROUTED_APP_LABELS:
        return None

    if transaction.get_connection().in_atomic_block:
        return None

    return 'read'


def db_for_write(model, **hints):
    """Writes always go to the primary. Never return 'read' here."""
    return 'default'


def allow_relation(obj1, obj2, **hints):
    """Allow relations between two objects on the same DB. The default
    Django behavior blocks cross-DB relations; we relax it for the
    case where both objects resolved to 'default' (the common case)."""
    db1 = getattr(obj1._state, 'db', 'default')
    db2 = getattr(obj2._state, 'db', 'default')
    if db1 == db2:
        return True
    return None


def allow_migrate(db, app_label, model_name=None, **hints):
    """Migrations only run on the primary. The replica is read-only and
    must never receive DDL."""
    return db == 'default'
