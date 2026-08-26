import time
import logging
from django.http import JsonResponse
from django.db import connection

logger = logging.getLogger(__name__)


def health_check(request):
    """
    Liveness probe - is the Django process alive?
    GET /health/
    """
    return JsonResponse({
        "status": "healthy",
        "timestamp": time.time(),
    })


def readiness_check(request):
    """
    Readiness probe - is the app ready to serve traffic?
    Checks database connectivity.
    GET /ready/
    """
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({
            "status": "ready",
            "database": "connected",
            "timestamp": time.time(),
        })
    except Exception:
        logger.exception("Readiness check failed during database connectivity validation.")
        return JsonResponse({
            "status": "not_ready",
            "database": "error",
            "timestamp": time.time(),
        }, status=503)
