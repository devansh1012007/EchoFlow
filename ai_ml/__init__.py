"""EchoFlow ML package.

Pure-Python ranking logic and the thin Celery task wiring for the
feed recommendation engine. See README.md for the migration plan
and current scope.

The on-disk directory was previously `/ai_ml/`; it was renamed to
`/ai_ml/` in the feed-engine separation pass so it can be imported
under a valid Python identifier. The `conftest.py` at the repo
root inserts `/` on `sys.path` so both `import backend.app...` and
`import ai_ml.pipelines...` resolve. Celery workers run with
cwd=/app which is on sys.path by default.
"""
