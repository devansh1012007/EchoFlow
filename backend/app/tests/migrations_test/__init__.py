"""Test-only migration stub for the `app` app.

WHY THIS EXISTS:
  The real 0001_initial.py runs `CREATE EXTENSION IF NOT EXISTS vector;`
  which is PostgreSQL-only. In tests we use SQLite, which doesn't
  understand that SQL and the migration fails with:
    django.db.utils.OperationalError: near "EXTENSION": syntax error

  This stub's `Migration.initial = True` with no operations tells
  Django "the app has no tables; just create them from the current
  models." This works because the test DB is `:memory:` and we don't
  care about preserving data.

  In production (postgresql), the real 0001_initial.py is used.
"""
