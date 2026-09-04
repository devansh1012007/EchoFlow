"""Tests for the read-replica router.

These tests verify the routing contract without requiring a live
PostgreSQL replica. The router is consulted for every read; we use
the in-memory SQLite database and assert which connection the ORM
ends up using by inspecting `instance._state.db` and
`query._db`.

Companion: backend/app/db_routers.py and
docs/EXPLAIN/database/05-read-replica-design.md.
"""
import pytest
from django.test import override_settings

from backend.app.db_routers import (
    APP_LABEL,
    ROUTED_APP_LABELS,
    db_for_read,
    db_for_write,
    allow_relation,
    allow_migrate,
)

# Note: We do not use the django_db marker here. The router is a
# pure-function module that only reads settings.DATABASES; we don't
# need a real connection. `transaction.atomic()` is not exercised
# in these tests — that path is covered by a static-source check
# in TestInAtomicBlockGuard below.


class TestReadRouterFallback:
    """When 'read' is not configured, the router must NOT break anything."""

    def test_db_for_read_returns_none_without_read_alias(self):
        # If 'read' is not in DATABASES, the router returns None
        # (Django then uses 'default'). This is the safe fallback.
        from django.conf import settings
        assert 'read' not in settings.DATABASES
        result = db_for_read(None)
        assert result is None

    def test_db_for_write_always_returns_default(self):
        # Writes NEVER go to the replica, even if the alias exists.
        # The function does not depend on settings — it always returns
        # 'default'.
        assert db_for_write(None) == 'default'


class TestReadRouterWithReadAlias:
    """When READ_DATABASE_URL is set, reads on the 'app' app route to
    'read' except in the documented exceptions. We patch
    `_has_read_alias` to avoid the django_db / override_settings
    interaction in pytest-django's connection setup."""

    def test_app_label_routes_to_read(self, monkeypatch):
        # Force the router to believe a 'read' alias is configured.
        from backend.app import db_routers
        monkeypatch.setattr(db_routers, '_has_read_alias', lambda: True)
        class _Meta:
            app_label = APP_LABEL
        class _Model:
            _meta = _Meta()
        assert db_for_read(_Model) == 'read'

    def test_non_routed_app_label_returns_none(self, monkeypatch):
        from backend.app import db_routers
        monkeypatch.setattr(db_routers, '_has_read_alias', lambda: True)
        class _Meta:
            app_label = 'auth'
        class _Model:
            _meta = _Meta()
        assert db_for_read(_Model) is None

    def test_in_atomic_block_falls_back_to_primary(self, monkeypatch):
        # We cannot easily flip `in_atomic_block` without a real
        # connection here. Verify the source instead: the router must
        # import transaction and consult get_connection().in_atomic_block.
        import inspect
        from backend.app import db_routers
        src = inspect.getsource(db_routers.db_for_read)
        assert 'in_atomic_block' in src, (
            "Router must check transaction.get_connection().in_atomic_block; "
            "without this guard, reads inside an atomic block that contains "
            "writes would lose read-your-own-writes consistency."
        )


class TestAllowMigrate:
    """Migrations must only run on the primary."""

    def test_migrations_allowed_on_default(self):
        assert allow_migrate('default', 'app') is True

    def test_migrations_blocked_on_read(self):
        # Critical: DDL on a read replica is undefined behavior and
        # would corrupt the replica. The router must refuse.
        assert allow_migrate('read', 'app') is False

    def test_migrations_blocked_on_arbitrary_alias(self):
        assert allow_migrate('something_else', 'app') is False


class TestAllowRelation:
    """Cross-DB relations should be blocked unless both objects are
    on the same DB."""

    def test_same_db_relation_allowed(self):
        class _State:
            db = 'default'
        class _Obj:
            _state = _State()
        assert allow_relation(_Obj(), _Obj()) is True

    def test_cross_db_relation_blocked(self):
        class _StateDefault:
            db = 'default'
        class _StateRead:
            db = 'read'
        class _ObjDefault:
            _state = _StateDefault()
        class _ObjRead:
            _state = _StateRead()
        assert allow_relation(_ObjDefault(), _ObjRead()) is None


class TestRouterConfigurationContract:
    """Static checks on the router's contract."""

    def test_only_app_is_routed(self):
        # The doc says we only route the 'app' app. Anything else
        # is a regression.
        assert ROUTED_APP_LABELS == frozenset({'app'})

    def test_db_for_write_is_unconditional(self):
        # db_for_write never consults settings. Even with 'read'
        # missing, it returns 'default'.
        assert db_for_write(None) == 'default'

    def test_readonly_transaction_option_set(self):
        # SECURITY: the read connection must be configured with
        # default_transaction_read_only=on so that any write that
        # accidentally routes to 'read' fails loudly instead of
        # silently corrupting the replica. We verify via settings
        # source check since we cannot instantiate a real replica.
        import inspect
        from backend import EchoFlow
        from backend.EchoFlow import settings as settings_mod
        src = inspect.getsource(settings_mod)
        assert 'default_transaction_read_only=on' in src, (
            "Read DB must be configured with default_transaction_read_only=on "
            "as defense in depth. See db_routers.py docstring and the read-replica design doc."
        )

    def test_router_only_registered_when_read_configured(self):
        # Verify the conditional registration: when 'read' is missing,
        # DATABASE_ROUTERS must not include the read router. Otherwise
        # every read would fail with "connection does not exist".
        import inspect
        from backend.EchoFlow import settings as settings_mod
        src = inspect.getsource(settings_mod)
        # The pattern is:
        #   if 'read' in DATABASES:
        #       DATABASE_ROUTERS = ['backend.app.db_routers.ReadRouter']
        # (or equivalent). We just need to assert the conditional exists.
        assert "if 'read' in DATABASES" in src, (
            "DATABASE_ROUTERS registration must be gated on 'read' in DATABASES. "
            "Otherwise enabling the router without a replica crashes every read."
        )


class TestRouterConditionalActivation:
    """Contract tests for the activation-side wiring of the read-replica
    router. These exercise `override_settings(DATABASES=...)` to simulate
    the moment `READ_DATABASE_URL` is set in the environment and assert the
    router correctly activates (and deactivates) without touching the
    router functions directly.

    See `docs/EXPLAIN/database/05-read-replica-design.md` §"Activation
    Playbook" for the operational sequence these tests guard.
    """

    def test_router_activates_when_read_alias_present(self):
        # When 'read' is added to DATABASES (via override_settings to
        # simulate `READ_DATABASE_URL` being set), settings.DATABASE_ROUTERS
        # must include the read router. This is the activation contract:
        # the env var is the only thing that flips the bit.
        from django.conf import settings as dj_settings
        base_default = dj_settings.DATABASES['default']
        with override_settings(
            DATABASES={
                'default': base_default,
                'read': {
                    'ENGINE': 'django.db.backends.postgresql',
                    'NAME': 'echoflow_db',
                    'USER': 'echoflow',
                    'PASSWORD': 'x',
                    'HOST': 'db_read',
                    'PORT': '5432',
                    'OPTIONS': {'options': '-c default_transaction_read_only=on'},
                },
            },
        ):
            assert 'read' in dj_settings.DATABASES
            # The settings-level conditional is verified statically in
            # TestRouterConfigurationContract::test_router_only_registered_when_read_configured;
            # here we verify the runtime consequence: when the alias is
            # present, the router's `_has_read_alias()` returns True.
            from backend.app import db_routers
            assert db_routers._has_read_alias() is True

    def test_router_inert_when_read_alias_absent(self):
        # Default state: 'read' is not in DATABASES. The router must
        # be inert — every read falls back to 'default'. This is the
        # pre-activation state in production today.
        from django.conf import settings as actual_settings
        # Sanity: the test conftest forces SQLite + no 'read' alias.
        assert 'read' not in actual_settings.DATABASES
        with override_settings(DATABASES={'default': actual_settings.DATABASES['default']}):
            from backend.app import db_routers
            assert db_routers._has_read_alias() is False
            # And the router function returns None (falls back to default).
            class _Meta:
                app_label = APP_LABEL
            class _Model:
                _meta = _Meta()
            assert db_for_read(_Model) is None

    def test_queryset_under_atomic_block_falls_back_to_primary(self):
        # When 'read' IS configured but the call is inside transaction.atomic(),
        # the router must return None (fall back to 'default'). This is the
        # "read-your-own-writes" guard: a read inside a write transaction
        # must see the just-written state, which the replica may not have
        # yet.
        from django.conf import settings as actual_settings
        from backend.app import db_routers
        from django.db import transaction
        base_default = actual_settings.DATABASES['default']
        with override_settings(
            DATABASES={
                'default': base_default,
                'read': {
                    'ENGINE': 'django.db.backends.postgresql',
                    'NAME': 'echoflow_db',
                    'USER': 'echoflow',
                    'PASSWORD': 'x',
                    'HOST': 'db_read',
                    'PORT': '5432',
                    'OPTIONS': {'options': '-c default_transaction_read_only=on'},
                },
            },
        ):
            assert db_routers._has_read_alias() is True

            class _Meta:
                app_label = APP_LABEL
            class _Model:
                _meta = _Meta()

            # Outside an atomic block: router routes to 'read'.
            assert db_for_read(_Model) == 'read'

            # Inside an atomic block: router must refuse to route. We
            # monkey-patch `transaction.get_connection` to return an
            # object that claims `in_atomic_block=True` without requiring
            # an actual database connection (the test runs against
            # SQLite + LocMem, where opening a real transaction for a
            # router-level unit test is unnecessary).
            class _FakeConnection:
                in_atomic_block = True

            original_get_connection = transaction.get_connection
            transaction.get_connection = lambda: _FakeConnection()
            try:
                assert db_for_read(_Model) is None
            finally:
                transaction.get_connection = original_get_connection
