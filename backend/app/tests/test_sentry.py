"""Tests for the Sentry integration (B13).

Five tests covering the init gates and the capture_exception wrapper:

  1. test_sentry_not_initialized_when_dsn_missing
       Empty SENTRY_DSN → init_sentry() is a no-op (no SDK overhead in
       unconfigured environments).
  2. test_sentry_not_initialized_when_debug_true
       DJANGO_DEBUG=True → init_sentry() is a no-op even with a DSN,
       so dev and tests never pay the SDK cost.
  3. test_sentry_initialized_in_production_mode
       DJANGO_DEBUG=False AND SENTRY_DSN set → init_sentry() actually
       initializes the SDK; the current client is non-None.
  4. test_capture_exception_attaches_correlation_id
       Setting a correlation_id via the contextvar propagates as a
       `correlation_id` tag on the captured event.
  5. test_capture_exception_works_when_sentry_uninitialized
       No DSN → capture_exception is a silent no-op (must not raise).

Note on test isolation:
  The Sentry SDK has a process-wide global client; once initialized it
  cannot be re-initialized without a `sentry_sdk.get_client().close()`
  call (which closes the transport). We use a fixture that captures the
  client at test setup, runs the test, then restores the original client
  so tests don't bleed state into each other.
"""
import pytest

import sentry_sdk
from sentry_sdk import capture_message

from backend.EchoFlow import correlation, sentry as echo_sentry
from backend.app.services import sentry as service_sentry


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _restore_sentry_client():
    """Snapshot the Sentry client before each test and restore it after.

    Tests 1-2 call init_sentry() which mutates the global client. Without
    this fixture, test order would matter — later tests would see the
    client from earlier tests.
    """
    original = sentry_sdk.get_client()
    yield
    try:
        sentry_sdk.get_client().close()
    except Exception:
        pass
    if original is not None:
        # Re-bind the original client into the hub scope so subsequent
        # tests see the same starting state.
        sentry_sdk.get_current_scope().set_client(original)


class TestSentryInitGating:
    def test_sentry_not_initialized_when_dsn_missing(self, monkeypatch):
        monkeypatch.setenv('SENTRY_DSN', '')
        monkeypatch.setenv('DJANGO_DEBUG', 'False')
        echo_sentry.init_sentry()
        # When the SDK is not initialized, the current client should be
        # a no-op/None (sentry_sdk lazily creates a non-network client on
        # first access; what matters is that capture_message / capture_
        # exception don't actually send anything to a remote).
        client = sentry_sdk.get_client()
        assert client is None or getattr(client, 'dsn', None) in (None, '')

    def test_sentry_not_initialized_when_debug_true(self, monkeypatch):
        monkeypatch.setenv('SENTRY_DSN', 'https://fake@sentry.io/123')
        monkeypatch.setenv('DJANGO_DEBUG', 'True')
        echo_sentry.init_sentry()
        # DEBUG=True gates the init entirely — the SDK must NOT have been
        # initialized regardless of DSN being set.
        client = sentry_sdk.get_client()
        assert client is None or getattr(client, 'dsn', None) in (None, '')

    def test_sentry_initialized_in_production_mode(self, monkeypatch):
        monkeypatch.setenv('SENTRY_DSN', 'https://fake@sentry.io/123')
        monkeypatch.setenv('DJANGO_DEBUG', 'False')
        monkeypatch.setenv('SENTRY_TRACES_SAMPLE_RATE', '0.0')
        monkeypatch.setenv('SENTRY_PROFILES_SAMPLE_RATE', '0.0')
        echo_sentry.init_sentry()
        # In production with DSN set, the SDK initializes. The client is
        # non-None and the dsn matches.
        client = sentry_sdk.get_client()
        assert client is not None
        assert 'sentry.io' in str(getattr(client, 'dsn', ''))


class TestCaptureExceptionWrapper:
    def test_capture_exception_attaches_correlation_id(self, monkeypatch):
        monkeypatch.setenv('SENTRY_DSN', 'https://fake@sentry.io/123')
        monkeypatch.setenv('DJANGO_DEBUG', 'False')
        monkeypatch.setenv('SENTRY_TRACES_SAMPLE_RATE', '0.0')
        monkeypatch.setenv('SENTRY_PROFILES_SAMPLE_RATE', '0.0')
        echo_sentry.init_sentry()

        from sentry_sdk.transport import Transport

        captured_events = []

        class _MemoryTransport(Transport):
            def capture_envelope(self, envelope):
                for item in envelope.items:
                    if item.type == 'event':
                        captured_events.append(item.payload.json)
                return None

        sentry_sdk.get_client().transport = _MemoryTransport()

        correlation.set_correlation_id('abc-123-test')
        try:
            service_sentry.capture_exception(RuntimeError('test'), op='unit_test')
            assert len(captured_events) == 1, (
                f"Expected 1 event, got {len(captured_events)}"
            )
            event = captured_events[0]
            tag_dict = event.get('tags') or {}
            if isinstance(tag_dict, list):
                tag_dict = {t['key']: t['value'] for t in tag_dict}
            assert tag_dict.get('correlation_id') == 'abc-123-test', (
                f"Expected tag correlation_id='abc-123-test', got tags={tag_dict}"
            )
        finally:
            correlation.clear_correlation_id()

    def test_capture_exception_works_when_sentry_uninitialized(self, monkeypatch):
        monkeypatch.setenv('SENTRY_DSN', '')
        monkeypatch.setenv('DJANGO_DEBUG', 'True')
        echo_sentry.init_sentry()  # no-op

        # Must NOT raise even though the SDK was never initialized.
        service_sentry.capture_exception(RuntimeError('boom'), op='noop')
