import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get('GUNICORN_WORKERS', 4))
threads = int(os.environ.get('GUNICORN_THREADS', 4))
worker_class = 'gthread'  # Threaded worker class

# Timeout
timeout = 120  # 2 minutes for vector computation requests
graceful_timeout = 30  # Graceful shutdown timeout
keepalive = 5

# Process naming
proc_name = 'echoflow'

# Server mechanics
preload_app = True  # Load app before forking workers (saves memory)
daemon = False
pidfile = None
user = None
group = None

# Logging
accesslog = '-'  # Log to stdout
errorlog = '-'   # Log to stderr
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')

# Worker cleanup - restart workers periodically to prevent memory leaks
max_requests = 1000  # Restart worker after 1000 requests
max_requests_jitter = 50  # Add jitter to prevent all workers restarting at once

# Graceful shutdown handler
def on_exit(server):
    """Called on graceful shutdown."""
    print("EchoFlow: Shutting down gracefully...")
    print("EchoFlow: Allowing workers to finish current requests...")


def post_fork(server, worker):
    """Called just after a worker has been forked."""
    # Reset ALL shared connections after fork.
    # Critical because EchoFlow/__init__.py imports Celery app at module load time.
    # When preload_app=True, the Celery app's Redis connections AND Django's DB
    # connections are established in the master process. Without resetting,
    # forked workers inherit stale connections, which leads to "connection reset"
    # errors and silent failures.
    from django.db import connections
    for conn in connections.all():
        conn.close()

    # Reset Celery/Redis connections
    try:
        from backend.EchoFlow.celery import app as celery_app
        # Close any existing connections in the worker pool
        if hasattr(celery_app.connection, 'pool'):
            celery_app.connection.pool.disconnect()
    except Exception:
        pass

    server.log.info(f"Worker spawned (pid: {worker.pid}) - DB & Redis connections reset")


def pre_exec(server):
    """Called just before a new master process is forked."""
    server.log.info("Forked child, re-executing.")


def when_ready(server):
    """Called just after the server is started."""
    server.log.info("Server is ready. Spawning workers")


def worker_int(worker):
    """Called when a worker receives the INT or QUIT signal."""
    worker.log.info("worker received INT or QUIT signal")


def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info("worker received SIGABRT signal")
