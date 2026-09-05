"""Configuration tests for backend.EchoFlow.settings.

A1 regression guard: per-session safety timeouts on the default
DATABASES connection. These timeouts are critical for operating
behind PgBouncer (a single stuck transaction can hold a backend
connection indefinitely; 25 such connections exhaust the pool).

DECISION: the GUCs are applied via the psycopg2 `options='-c ...'`
connection string. This requires pgbouncer to whitelist these
startup parameters via `ignore_startup_parameters` in its config
(docker-compose.yml: IGNORE_STARTUP_PARAMETERS=...). Without that
whitelist, pgbouncer transaction mode rejects the connection at
startup with
   FATAL: unsupported startup parameter in options: statement_timeout
and every web/celery container crashes before its first query.

`connect_timeout` is a libpq *connection* parameter (not a GUC)
and must be a top-level psycopg2 kwarg. Putting it in the
`-c ...` string raises
   `unrecognized configuration parameter "connect_timeout"`.

The conftest forces SQLite + LocMem for tests by overwriting
settings.DATABASES at module import. The production options on the
default connection are therefore NOT visible at runtime. We test
the static config: parse settings.py and confirm the options are
present in the source. This catches accidental deletion during
refactor without depending on the runtime override.
"""
import re
from pathlib import Path


SETTINGS_PATH = Path(__file__).resolve().parents[3] / 'backend' / 'EchoFlow' / 'settings.py'
COMPOSE_PATH = Path(__file__).resolve().parents[3] / 'docker-compose.yml'


def _read_settings_source() -> str:
    return SETTINGS_PATH.read_text(encoding='utf-8')


def _read_compose_source() -> str:
    return COMPOSE_PATH.read_text(encoding='utf-8')


# The conftest overrides settings.DATABASES at import time to a
# SQLite dict without the OPTIONS key. To validate the production
# config, we parse the source and assert the static configuration
# is what the audit requires.
class TestDefaultDatabaseOptions:
    """A1: per-session timeouts on the default DB connection."""

    def _default_block(self) -> str:
        """Extract the DATABASES['default'] assignment from settings.py source."""
        src = _read_settings_source()
        match = re.search(
            r"DATABASES\s*=\s*\{[^}]*'default'\s*:\s*dj_database_url\.config\((.*?)\)\s*\}",
            src,
            re.DOTALL,
        )
        assert match, "DATABASES['default'] = dj_database_url.config(...) not found"
        return match.group(0)

    def test_default_connection_uses_postgres_options_string(self):
        # The audit fix uses psycopg2's `options` connection parameter
        # (a single string) to set server-side timeouts. This is the
        # documented mechanism per libpq docs. We assert the settings
        # source contains an explicit assignment to OPTIONS['options']
        # with the required timeout values.
        src = _read_settings_source()
        match = re.search(
            r"DATABASES\['default'\]\['OPTIONS'\]\['options'\]\s*=\s*\((.*?)\)",
            src,
            re.DOTALL,
        )
        assert match, (
            "DATABASES['default']['OPTIONS']['options'] = (...) assignment not found. "
            "psycopg2 requires the libpq options string to set server-side timeouts."
        )
        options_str = match.group(1)
        assert 'statement_timeout=30s' in options_str, (
            f"statement_timeout=30s missing from options string. Got: {options_str}"
        )
        assert 'idle_in_transaction_session_timeout=60s' in options_str, (
            f"idle_in_transaction_session_timeout=60s missing from options string. "
            f"Got: {options_str}"
        )
        assert 'lock_timeout=10s' in options_str, (
            f"lock_timeout=10s missing from options string. Got: {options_str}"
        )

    def test_connect_timeout_is_libpq_connection_param_not_guc(self):
        # connect_timeout is a libpq *connection* parameter (passed to
        # PQconnectdb), NOT a GUC server variable. Putting it in the
        # psycopg2 `options` string (`-c connect_timeout=10s`) makes
        # Postgres raise `unrecognized configuration parameter
        # "connect_timeout"` the moment a worker opens its first
        # connection — every web/celery container crashes in
        # wait_for_db. Assert it's set as a top-level OPTIONS kwarg
        # instead.
        src = _read_settings_source()
        options_str_match = re.search(
            r"DATABASES\['default'\]\['OPTIONS'\]\['options'\]\s*=\s*\((.*?)\)",
            src,
            re.DOTALL,
        )
        options_str = options_str_match.group(1) if options_str_match else ""
        assert 'connect_timeout' not in options_str, (
            "connect_timeout leaked into the libpq `options` string. "
            "It is a connection parameter, not a GUC. Move it to "
            "DATABASES['default']['OPTIONS']['connect_timeout']."
        )
        assert "DATABASES['default']['OPTIONS']['connect_timeout']" in src, (
            "connect_timeout not set as a top-level psycopg2 kwarg. "
            "Add: DATABASES['default']['OPTIONS']['connect_timeout'] = 10"
        )

    def test_default_block_passes_required_kwarg_conn_max_age(self):
        # conn_max_age=600 (10 min) is the connection-recycle window.
        # Without it, every request opens a new connection (slow).
        src = self._default_block()
        assert 'conn_max_age=600' in src, (
            "conn_max_age=600 removed from DATABASES['default'] — "
            "this would force a reconnect every request."
        )

    def test_default_block_passes_required_kwarg_conn_health_checks(self):
        # conn_health_checks=True makes Django ping the connection
        # before each request. Without it, a stale connection after
        # a Postgres restart causes every request to fail until the
        # conn_max_age window expires (10 min).
        src = self._default_block()
        assert 'conn_health_checks=True' in src, (
            "conn_health_checks=True removed from DATABASES['default'] — "
            "stale connections after Postgres restart would not be detected."
        )

    def test_statement_timeout_uses_libpq_c_prefix(self):
        # psycopg2's `options` parameter must contain `-c name=value`
        # entries (one per server-side GUC). Without the `-c` prefix,
        # Postgres raises "invalid command-line argument". The
        # connection-side timeouts (connect_timeout) are NOT in this
        # string — they're a top-level psycopg2 kwarg.
        src = _read_settings_source()
        match = re.search(
            r"DATABASES\['default'\]\['OPTIONS'\]\['options'\]\s*=\s*\((.*?)\)",
            src,
            re.DOTALL,
        )
        assert match
        options_str = match.group(1)
        assert '-c statement_timeout' in options_str
        assert '-c lock_timeout' in options_str
        assert '-c idle_in_transaction_session_timeout' in options_str
        # Negative assertion: connect_timeout must NOT be in the
        # GUC options string (see test_connect_timeout_is_libpq_connection_param_not_guc).
        assert '-c connect_timeout' not in options_str


class TestPgbouncerIgnoreStartupParameters:
    """PgBouncer transaction mode rejects the psycopg2 `options`
    string unless each GUC name is in its `ignore_startup_parameters`
    whitelist. Without that whitelist, every web/celery container
    fails to start with
       FATAL: unsupported startup parameter in options: statement_timeout
    Regression guard: the docker-compose.yml `IGNORE_STARTUP_PARAMETERS`
    env var must list every GUC the app sets via `options=`.
    """

    def _ignore_env_line(self) -> str:
        src = _read_compose_source()
        match = re.search(
            r"IGNORE_STARTUP_PARAMETERS\s*=\s*([^\n]+)",
            src,
        )
        assert match, (
            "IGNORE_STARTUP_PARAMETERS not set in docker-compose.yml. "
            "PgBouncer will reject every connection that uses "
            "psycopg2 OPTIONS['options'] = '-c ...'."
        )
        return match.group(1)

    def test_ignore_includes_statement_timeout(self):
        line = self._ignore_env_line()
        assert 'statement_timeout' in line, (
            f"statement_timeout missing from IGNORE_STARTUP_PARAMETERS. "
            f"PgBouncer will reject connections with 'options=-c statement_timeout=...'."
        )

    def test_ignore_includes_lock_timeout(self):
        line = self._ignore_env_line()
        assert 'lock_timeout' in line, (
            "lock_timeout missing from IGNORE_STARTUP_PARAMETERS."
        )

    def test_ignore_includes_idle_in_transaction(self):
        line = self._ignore_env_line()
        assert 'idle_in_transaction_session_timeout' in line, (
            "idle_in_transaction_session_timeout missing from "
            "IGNORE_STARTUP_PARAMETERS."
        )


class TestReadDatabaseOptions:
    """Pre-existing bug guard: the read replica alias (when activated)
    must not crash on settings import. The pre-PR code passed
    `options={...}` as a kwarg to `dj_database_url.config()`, which
    this library does not accept. We assert the read block does not
    use that broken pattern."""

    def test_read_block_does_not_pass_options_kwarg(self):
        src = _read_settings_source()
        # Find the read block. It's conditional on READ_DATABASE_URL.
        # If present, the kwarg-list passed to dj_database_url.config()
        # should not include `options=`.
        match = re.search(
            r"DATABASES\['read'\]\s*=\s*dj_database_url\.config\((.*?)\)",
            src,
            re.DOTALL,
        )
        if not match:
            import pytest
            pytest.skip("DATABASES['read'] is conditional; not present in source")
        block = match.group(1)
        assert 'options=' not in block, (
            "DATABASES['read'] still passes `options=` kwarg to "
            "dj_database_url.config() — that kwarg does not exist "
            "in the pinned version. Settings will fail to import "
            "the moment READ_DATABASE_URL is set."
        )
