import os
import sys
import time
import psycopg2

MAX_RETRIES = 120
RETRY_DELAY = 1
BACKOFF_FACTOR = 2


def wait_for_db(max_retries=MAX_RETRIES, initial_delay=RETRY_DELAY):
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Waiting for database at {url}...")
    delay = initial_delay
    attempt = 0

    while attempt < max_retries:
        attempt += 1
        try:
            conn = psycopg2.connect(url)
            conn.close()
            print(f"Database is ready after {attempt} attempt(s).")
            return True
        except psycopg2.OperationalError as e:
            print(
                f"Attempt {attempt}/{max_retries} failed: {e}. "
                f"Retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay = min(delay * BACKOFF_FACTOR, 30)

    print(
        f"ERROR: Database not available after {max_retries} attempts. Giving up.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    wait_for_db()
