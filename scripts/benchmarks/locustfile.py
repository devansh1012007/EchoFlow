"""EchoFlow baseline load profile (locust).

DEVELOPMENT-ONLY DEPENDENCY: locust is deliberately NOT in requirements*.txt.
Install it on the bench machine only::

    pip install locust

DISCOVERED API SURFACE (encoded below — these are read from the code, not
guessed; file:line evidence in each section):

  POST /auth/login/                      -> {"access": ..., "refresh": ...}
        ThrottledTokenObtainPairView, backend/app/urls.py:123.
        simplejwt default body {"username","password"}. Throttled HARD:
        scope 'login' 10/min per IP (TrustedProxyRateThrottle, fail-closed
        503) plus 10/hour per username (LoginUsernameRateThrottle) — see
        backend/EchoFlow/settings.py:966 and backend/app/urls.py:27-67.
        => For multi-user runs use a PRE-MINTED token (BENCH_TOKEN) or the
           10/min IP bucket will 429 much of the ramp-up. The harness
           retries 429s with backoff but cannot beat a deliberate 10/min
           cap; mint the token once with curl instead.

  GET  /feed/                            -> FastFeedViewSet.list
        backend/app/views/feed.py:62. IsAuthenticated. NOTE: this endpoint
        is destructive by design — it lpop()s up to 10 clip ids from the
        per-user Redis queue `user_feed:{id}` (feed.py:74) and publishes
        refill_user_feed to the 'fast_feed' Celery queue when drained
        (feed.py:77). Cold queue -> 202 with {"results": [], "degraded":
        true, "retry_after_ms": 1500} (feed.py:90-99). Hot -> 200 envelope
        {"next": "auto_trigger", "queue_health": N, "results": [...]}
        (feed.py:164-168). BOTH are success for the benchmark: the server
        answered within its designed behaviour. (CursorPagination for the
        sibling /suggestions/ endpoints uses {"next","previous","results"}
        — backend/app/views/_pagination.py:5-8 — but /feed/ itself is the
        custom envelope above and has no query params.)

  GET  /profile/me/                      -> ProfileViewSet 'me' action
        backend/app/views/profile.py:29. Light, authed, DB-only — the
        non-feed authed endpoint in the ~20% mix.

  GET  /health/                          -> liveness, no auth, no DB
        backend/EchoFlow/urls.py:10, backend/EchoFlow/health.py:9.
  GET  /ready/                           -> readiness, no auth, one DB
        roundtrip; 503 when DB is unreachable
        backend/EchoFlow/urls.py:11, backend/EchoFlow/health.py:20-41.

  POST /media/playback-token/<uuid:clip_id>/
        PlaybackTokenView, backend/app/urls.py:134,
        backend/app/views/media.py:133+. POST only (GET answers 405 with a
        hint, media.py:183-193). Throttle 'playback_token' 300/min
        (settings.py:1000). DISABLED in the baseline profile (weight 0)
        because it needs a real clip uuid and its throttle would bind
        before the feed target does; opt in with BENCH_PLAYBACK_WEIGHT>0
        (clip uuids are harvested from live feed responses).

  Auth header: "Authorization: Bearer <access>" — simplejwt default (no
  AUTH_HEADER_TYPES override in settings.py); matches
  mobile/src/api/client.ts:255 and frontend/src/api/client.ts:430.

PACING / TARGET RELATION (documented, not fudged):

  wait_time = constant_pacing(BENCH_PACE_SECONDS, default 1.0) means each
  virtual user starts one task per second, so

      nominal RPS ~= users / BENCH_PACE_SECONDS

  With the shipped profile (-u 50, pace 1.0s) that is ~50 RPS nominal.
  constant_pacing (not between()) is deliberate: when latency rises, the
  OFFERED load stays constant and the shortfall appears as achieved RPS
  dropping below 50 in the report — instead of the load generator stacking
  its own queueing latency on top of the server's. Caveat, stated plainly:
  if responses take longer than the pace window, the user simply starts
  the next request immediately, so achieved RPS converges to
  users/mean_response_time. That is the honest failure mode and the report
  shows it.

FAILURE HANDLING:
  - 5xx   -> failure, labelled "5xx <code>"
  - 429   -> failure, labelled "429 throttled" (a throttled benchmark
             client is a benchmark problem and must be visible, not hidden)
  - 401   -> re-auth ONCE, retry the request ONCE; with BENCH_TOKEN mode a
             re-mint is impossible, so it fails with explicit guidance.
  - 2xx incl. 202 -> success (202 is the feed's designed cold-start reply).
  - everything else -> locust defaults (non-2xx fails).

NO INVENTED NUMBERS: nothing in this file assumes a latency or RPS result.
Whatever the server does is what gets recorded.
"""

import logging
import os
import time

from locust import HttpUser, constant_pacing, events, task

logger = logging.getLogger("bench.locustfile")

# ---------------------------------------------------------------------------
# Environment (all knobs env-driven; defaults match the baseline profile)
# ---------------------------------------------------------------------------
BENCH_BASE_URL = os.getenv("BENCH_BASE_URL", "").rstrip("/")
BENCH_USERNAME = os.getenv("BENCH_USERNAME", "")
BENCH_PASSWORD = os.getenv("BENCH_PASSWORD", "")
# Pre-minted JWT access token (mint once via curl against /auth/login/).
# Recommended for 50-user runs because of the 10/min-per-IP login throttle.
BENCH_TOKEN = os.getenv("BENCH_TOKEN", "")

# Task mix weights. Defaults give ~80% feed, ~20% health/ready/profile.
BENCH_FEED_WEIGHT = int(os.getenv("BENCH_FEED_WEIGHT", "80"))
BENCH_HEALTH_WEIGHT = int(os.getenv("BENCH_HEALTH_WEIGHT", "6"))
BENCH_READY_WEIGHT = int(os.getenv("BENCH_READY_WEIGHT", "6"))
BENCH_PROFILE_WEIGHT = int(os.getenv("BENCH_PROFILE_WEIGHT", "8"))
# Playback token is opt-in (0 = excluded from the baseline profile).
BENCH_PLAYBACK_WEIGHT = int(os.getenv("BENCH_PLAYBACK_WEIGHT", "0"))

# Seconds between task starts per user. 50 users @ 1.0s -> ~50 RPS nominal.
BENCH_PACE_SECONDS = float(os.getenv("BENCH_PACE_SECONDS", "1.0"))

# Login retry policy: the login endpoint is throttled 10/min per IP.
BENCH_LOGIN_MAX_ATTEMPTS = int(os.getenv("BENCH_LOGIN_MAX_ATTEMPTS", "12"))
BENCH_LOGIN_BACKOFF_SECONDS = float(os.getenv("BENCH_LOGIN_BACKOFF_SECONDS", "5.0"))


@events.init.add_listener
def _validate_env(environment, **kwargs):
    """Fail fast (before any user spawns) if required env is missing."""
    if not BENCH_BASE_URL:
        logger.error("BENCH_BASE_URL is required, e.g. https://echoflow.example")
        raise SystemExit("BENCH_BASE_URL is required")
    if not BENCH_TOKEN and not (BENCH_USERNAME and BENCH_PASSWORD):
        logger.error(
            "Set BENCH_TOKEN (pre-minted access JWT) or BENCH_USERNAME + "
            "BENCH_PASSWORD. Login is throttled to 10/min per IP "
            "(settings.py:966); token mode is strongly recommended for the "
            "50-user profile."
        )
        raise SystemExit("BENCH_TOKEN or BENCH_USERNAME/BENCH_PASSWORD required")


class EchoFlowUser(HttpUser):
    """One virtual user holding one JWT identity.

    DOCUMENTED LIMITATION: every virtual user shares ONE identity, so the
    server-side per-user feed queue `user_feed:{id}` is shared and drained
    concurrently by all of them. This exercises the refill path harder
    than N distinct users would — representative of the queue/refill
    mechanics, but NOT a claim about per-user fan-out. See README.md.
    """

    host = BENCH_BASE_URL
    wait_time = constant_pacing(BENCH_PACE_SECONDS)

    # Access tokens live 15 min (settings.py:1059) and the baseline run is
    # 2 min, so no mid-run refresh is needed; refresh_token is stored for
    # future longer soaks.
    access_token = ""
    refresh_token = ""
    # Clip uuid harvested from the last feed response; used only when
    # BENCH_PLAYBACK_WEIGHT > 0.
    _last_clip_id = ""

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def on_start(self):
        if BENCH_TOKEN:
            self.access_token = BENCH_TOKEN
        else:
            self._login_with_retry()

    def _login_with_retry(self):
        """POST /auth/login/ and store access+refresh.

        Retries only 429/5xx with linear backoff — the 10/min-per-IP
        throttle makes some 429s unavoidable during ramp-up in
        username/password mode. A 4xx other than 429 is bad credentials;
        retrying cannot help and is not attempted.
        """
        attempt = 0
        while attempt < BENCH_LOGIN_MAX_ATTEMPTS:
            attempt += 1
            with self.client.post(
                "/auth/login/",
                json={"username": BENCH_USERNAME, "password": BENCH_PASSWORD},
                name="POST /auth/login/ [setup]",
                catch_response=True,
            ) as resp:
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                        self.access_token = body.get("access", "")
                        self.refresh_token = body.get("refresh", "")
                    except ValueError:
                        resp.failure("login 200 with non-JSON body")
                        return
                    if not self.access_token:
                        resp.failure("login 200 without an access token")
                        return
                    resp.success()
                    return
                if resp.status_code == 429 or resp.status_code >= 500:
                    resp.failure(
                        f"login attempt {attempt}/{BENCH_LOGIN_MAX_ATTEMPTS} "
                        f"got {resp.status_code} (throttle/server error — "
                        "backing off)"
                    )
                else:
                    resp.failure(f"login rejected with {resp.status_code}")
                    return  # e.g. 401 bad credentials — do not hammer
            time.sleep(BENCH_LOGIN_BACKOFF_SECONDS * attempt)
        logger.error(
            "login did not succeed after %d attempts; every authed request "
            "from this user will fail as 401 until the run ends",
            BENCH_LOGIN_MAX_ATTEMPTS,
        )

    def _try_reauth(self):
        """Re-mint credentials once. Returns True if a retry can proceed."""
        if BENCH_TOKEN:
            logger.warning(
                "401 with static BENCH_TOKEN: token expired and cannot be "
                "re-minted by this process; mint a fresh token and rerun."
            )
            return False
        self._login_with_retry()
        return bool(self.access_token)

    def _authed(self, method, path, name, **kwargs):
        """Authed request with ONE 401 re-auth + retry, per request.

        Marking: 5xx/429 labelled explicitly; 2xx (incl. 202) and other
        4xx fall through to locust's default ok-based marking when the
        response context closes.
        """
        for attempt in range(2):
            with self.client.request(
                method, path, headers={"Authorization": f"Bearer {self.access_token}"},
                name=name, catch_response=True, **kwargs,
            ) as resp:
                if resp.status_code == 401 and attempt == 0 and self._try_reauth():
                    resp.failure("401 — re-authenticating and retrying once")
                    continue
                if resp.status_code >= 500:
                    resp.failure(f"5xx {resp.status_code}")
                elif resp.status_code == 429:
                    resp.failure("429 throttled")
            return resp
        return resp  # pragma: no cover — loop always returns on attempt 0/1

    # ------------------------------------------------------------------
    # Tasks (weights env-driven; defaults ~= 80% feed / 20% light mix)
    # ------------------------------------------------------------------
    @task(BENCH_FEED_WEIGHT)
    def feed(self):
        resp = self._authed("GET", "/feed/", "GET /feed/ [auth]")
        if resp.status_code in (200, 202):
            try:
                body = resp.json()
            except ValueError:
                logger.warning("feed returned non-JSON 2xx body")
                return
            results = body.get("results") or []
            if results and isinstance(results[0], dict) and results[0].get("id"):
                self._last_clip_id = results[0]["id"]

    @task(BENCH_PROFILE_WEIGHT)
    def profile_me(self):
        self._authed("GET", "/profile/me/", "GET /profile/me/ [auth]")

    @task(BENCH_HEALTH_WEIGHT)
    def health(self):
        # Unauthenticated liveness probe (health.py:9). Counts toward the
        # measured RPS on purpose: it is part of the declared profile.
        self.client.get("/health/", name="GET /health/")

    @task(BENCH_READY_WEIGHT)
    def ready(self):
        # Unauthenticated readiness probe; one DB roundtrip per hit
        # (health.py:20-34), so it contributes to measured DB pressure.
        self.client.get("/ready/", name="GET /ready/")

    @task(BENCH_PLAYBACK_WEIGHT)
    def playback_token(self):
        if not self._last_clip_id:
            return  # no clip harvested yet; skip this cycle
        self._authed(
            "POST",
            f"/media/playback-token/{self._last_clip_id}/",
            "POST /media/playback-token/<uuid>/ [auth]",
        )
