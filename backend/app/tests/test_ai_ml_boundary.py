"""Boundary guards for the ai_ml/ package migration — audit P2 (2026-11-21).

The audit requires the ai_ml/ migration to be *complete or clearly
deferred*, and the deferral to hold even when nobody is looking. The
decision recorded in ``ai_ml/README.md`` ("Migration Status") is DEFER:
the working pipeline stays in ``backend.app.tasks`` by design, and the
stub modules remain ``NotImplementedError`` documentation-of-intent.
This file turns that documentation into something enforced — three
properties, three tests:

1. Runtime code may only import the ai_ml modules that are actually
   implemented. An accidental runtime import of a stub would either
   crash a request path mid-flight or silently advertise an entry
   point that does not work; the AST scan below makes it fail the
   suite instead, naming the offending ``file:line``.

2. Stub modules must stay import-light. They are documentation, not
   dead weight: importing a stub must never drag in a model runtime
   (torch, faster-whisper, librosa, ...), so cold imports,
   ``django check``, and test collection stay fast.

3. Every public callable in a stub still raises ``NotImplementedError``
   with a message pointing at where the working implementation lives,
   so the first person to hit one learns the deferral policy instead
   of filing a bug. Each stub's docstring must also mark the DEFERRED
   state and point back at ``ai_ml/README.md``.

This module is CONFIG/AST validation: it touches no database, no model,
no view, and therefore carries no ``django_db`` marker. The heaviest
thing it does is import five tiny stub modules (numpy at most).

Escalation path — if one of these tests goes red because a stub *became
real*, the fix is deliberate, not a revert: implement + test the module,
move it out of ``STUB_FILES`` / into ``ALLOWED_AI_ML_RUNTIME_IMPORTS``,
and update the Migration Status table in ``ai_ml/README.md``.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/app/tests -> repo root


# ---------------------------------------------------------------------------
# Test 1 — runtime imports only implemented ai_ml modules
# ---------------------------------------------------------------------------

#: The ONLY ai_ml modules backend runtime code may import. Everything
#: else in the package is a stub (NotImplementedError) whose migration is
#: deliberately deferred — see ai_ml/README.md ("Migration Status").
#:
#: Why three entries and not the two named in the original audit brief:
#: the brief predates the cold-start pass, which implemented
#: ``pipelines.cold_start`` for real (``initialize_user_vectors``) and
#: wired it into the onboarding endpoint (``backend/app/views/feed.py``,
#: covered end-to-end by ``test_tags_initialize_bounds.py``). Adding a
#: module to this set therefore requires all three of: a real
#: implementation, tests, and an updated Migration Status row.
ALLOWED_AI_ML_RUNTIME_IMPORTS = frozenset({
    'ai_ml.pipelines.recommendation',
    'ai_ml.pipelines.feed_tasks',
    'ai_ml.pipelines.cold_start',
    # Implemented 2026-11 (audit P2 follow-up: measure recommendation
    # QUALITY, not just ranking fidelity). Its only runtime importer is
    # the read-only eval command
    # backend/app/management/commands/eval_feed.py; the pure module is
    # stdlib-only and its tests live in
    # backend/app/tests/test_feed_metrics_eval.py. ai_ml/README.md
    # "Migration Status" row must be updated in the same pass.
    'ai_ml.eval.feed_metrics',
})

#: Runtime roots scanned for absolute ``ai_ml`` imports. ``backend/app``
#: is walked recursively; ``backend/app/tests`` (test code, which may
#: import ai_ml modules to verify them) and ``backend/app/migrations``
#: (generated, frozen) are excluded — neither is runtime code.
_RUNTIME_ROOTS = ('app', 'EchoFlow', 'scripts')
_EXCLUDED_DIRS = frozenset({'tests', 'migrations'})


def _iter_runtime_python_files():
    """Yield every backend runtime .py file the boundary polices."""
    backend_dir = REPO_ROOT / 'backend'
    for root_name in _RUNTIME_ROOTS:
        root = backend_dir / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*.py')):
            parts = path.relative_to(backend_dir).parts[:-1]  # drop filename
            if _EXCLUDED_DIRS.intersection(parts):
                continue
            yield path


def _ai_ml_imports(path):
    """Return ``[(lineno, dotted_module), ...]`` for absolute ai_ml imports.

    ``backend/app`` uses relative imports elsewhere (``from .models
    import ...``); only level==0 statements whose module is ``ai_ml`` or
    an ``ai_ml.*`` submodule are what this boundary polices.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'ai_ml' or alias.name.startswith('ai_ml.'):
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and (
                node.module == 'ai_ml' or node.module.startswith('ai_ml.')
            ):
                found.append((node.lineno, node.module))
    return found


def test_runtime_imports_only_implemented_ai_ml_modules():
    """No backend runtime file may import an ai_ml stub module.

    Collected from every ``import ai_ml...`` / ``from ai_ml... import``
    statement (AST ``Import`` / level-0 ``ImportFrom``) under
    ``backend/app`` (minus tests/ and migrations/), ``backend/EchoFlow``
    and ``backend/scripts``. On failure the message lists each offending
    ``file:line`` and the module it pulls in.
    """
    offenders = []
    for path in _iter_runtime_python_files():
        for lineno, module in _ai_ml_imports(path):
            if module not in ALLOWED_AI_ML_RUNTIME_IMPORTS:
                offenders.append(
                    f"  {path.relative_to(REPO_ROOT)}:{lineno} -> {module}"
                )
    assert not offenders, (
        "Runtime code imports ai_ml modules outside the allow-list.\n"
        "The remaining ai_ml/ migration is deliberately DEFERRED: stub\n"
        "modules raise NotImplementedError and must never be imported at\n"
        "runtime — see ai_ml/README.md ('Migration Status'). A module joins\n"
        "ALLOWED_AI_ML_RUNTIME_IMPORTS only with a real implementation,\n"
        "tests, and an updated README row.\n"
        "Offending imports:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Test 2 — stub modules import no heavy libraries
# ---------------------------------------------------------------------------

#: The stub modules as of the 2026-11-21 deferral decision, relative to
#: the repo root. NOTE: ``ai_ml/pipelines/cold_start.py`` used to be on
#: this list; it is implemented now, so it is policed by test 1 instead.
STUB_FILES = (
    'ai_ml/pipelines/audio_ingest.py',
    'ai_ml/models/whisper_wrapper.py',
    'ai_ml/models/embedding_wrapper.py',
    'ai_ml/models/kw_wrapper.py',
    'ai_ml/models/acoustic_extractor.py',
)

#: Top-level import roots a stub must never pull in: the model runtimes
#: the eventual real implementations will need. Why this matters: a stub
#: is documentation-of-intent that must stay free to import — the moment
#: it drags in torch/whisper/librosa it becomes dead weight on every cold
#: import, every ``django check``, and every test collection, for zero
#: functionality. (``numpy`` is deliberately NOT blocklisted: it is a
#: light, already-required backend dependency, and one stub uses it in an
#: annotation.)
STUB_HEAVY_IMPORT_BLOCKLIST = frozenset({
    'torch',
    'transformers',
    'sentence_transformers',
    'faster_whisper',
    'whisper',
    'keybert',
    'librosa',
    'onnxruntime',
    'ctranslate2',
    'soundfile',
    'av',
})


def _module_level_import_roots(path):
    """Return ``[(lineno, top_level_root), ...]`` for module-level imports.

    Only statements in the module body (direct children of the ``Module``
    node) count — lazy imports inside functions are a legitimate pattern
    for the eventual real implementations and are not this test's concern.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    roots = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append((node.lineno, alias.name.split('.')[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                roots.append((node.lineno, '<relative-import>'))
            elif node.module:
                roots.append((node.lineno, node.module.split('.')[0]))
    return roots


def test_stub_modules_import_no_heavy_libraries():
    """No stub module may import a model runtime at module level.

    Rationale: importing a stub must never drag in model runtimes, so
    cold imports, the test suite, and ``django check`` stay fast. The
    stubs are documentation-of-intent, not dead weight; the heavy import
    belongs in the real implementation when the deferral ends.
    """
    offenders = []
    for rel in STUB_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), (
            f"Stub file {rel} is missing. If it was implemented and moved, "
            f"update STUB_FILES, ALLOWED_AI_ML_RUNTIME_IMPORTS, and "
            f"ai_ml/README.md together."
        )
        for lineno, root in _module_level_import_roots(path):
            if root in STUB_HEAVY_IMPORT_BLOCKLIST:
                offenders.append(f"  {rel}:{lineno} -> import {root}")
    assert not offenders, (
        "A stub module imports a heavy model runtime. Stubs must stay\n"
        "import-light (see docstring of this test module and\n"
        "ai_ml/README.md 'Boundary enforcement'): a heavy import belongs\n"
        "in the real implementation, lazily, when the deferral ends.\n"
        "Offending imports:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Test 3 — the stub public API raises NotImplementedError
# ---------------------------------------------------------------------------

#: Same modules as STUB_FILES, as importable dotted paths.
STUB_MODULES = tuple(rel[:-len('.py')].replace('/', '.') for rel in STUB_FILES)


def _stub_public_functions(module):
    """Yield ``(name, func)`` for functions defined in ``module`` itself.

    Skips ``_private`` names and anything merely imported into the module
    (``obj.__module__`` check) so a stub that re-exports a helper is not
    mistaken for a stub-defined callable.
    """
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if name.startswith('_'):
            continue
        if obj.__module__ != module.__name__:
            continue
        yield name, obj


def _call_with_placeholder_args(func):
    """Call ``func()`` filling every declared parameter with ``None``.

    The stubs raise ``NotImplementedError`` before touching any argument,
    so placeholders are all the signatures need. ``*args`` / ``**kwargs``
    parameters are left empty, which is valid.
    """
    args, kwargs = [], {}
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD):
            args.append(None)
        elif param.kind is param.KEYWORD_ONLY:
            kwargs[param.name] = None
    return func(*args, **kwargs)


def test_stub_public_api_raises_not_implemented():
    """Every stub's public callables raise NotImplementedError, and every
    stub's docstring documents the deferral.

    For each stub module (imported via ``importlib``): the module
    docstring must mention the DEFERRED state and point at
    ``ai_ml/README.md``; each public function defined in the module must
    raise ``NotImplementedError`` whose message points at the working
    implementation in ``backend.app.tasks``. A module with no public
    callables instead documents the stub state in its docstring.
    """
    # The stubs live at the repo root (importable as the top-level
    # ``ai_ml`` package); conftest.py normally provides this, but make the
    # test self-sufficient when run in isolation.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    problems = []
    for module_name in STUB_MODULES:
        module = importlib.import_module(module_name)

        docstring = module.__doc__ or ''
        if 'DEFERRED' not in docstring:
            problems.append(
                f"  {module_name}: module docstring does not mark the "
                f"DEFERRED state"
            )
        if 'ai_ml/README.md' not in docstring:
            problems.append(
                f"  {module_name}: module docstring does not point at "
                f"ai_ml/README.md"
            )

        public = list(_stub_public_functions(module))
        if not public:
            if 'NotImplementedError' not in docstring and 'stub' not in docstring.lower():
                problems.append(
                    f"  {module_name}: no public callables, and the docstring "
                    f"does not document the stub state either"
                )
            continue

        for func_name, func in public:
            try:
                _call_with_placeholder_args(func)
            except NotImplementedError as exc:
                message = str(exc)
                if not message:
                    problems.append(
                        f"  {module_name}.{func_name}: NotImplementedError "
                        f"has an empty message"
                    )
                elif 'backend.app.tasks' not in message:
                    problems.append(
                        f"  {module_name}.{func_name}: message should point "
                        f"at the working implementation in "
                        f"backend.app.tasks (got: {message!r})"
                    )
            except Exception as exc:
                problems.append(
                    f"  {module_name}.{func_name}: raised "
                    f"{type(exc).__name__} ({exc!r}) instead of "
                    f"NotImplementedError"
                )
            else:
                problems.append(
                    f"  {module_name}.{func_name}: did not raise "
                    f"NotImplementedError — the stub grew a real body "
                    f"without leaving STUB_FILES"
                )

    assert not problems, (
        "Stub modules drifted from the deferral contract: docstring marks\n"
        "the DEFERRED state, points at ai_ml/README.md, and every public\n"
        "callable raises NotImplementedError pointing at the working\n"
        "implementation in backend.app.tasks. If a stub just became real,\n"
        "move it out of STUB_FILES / into ALLOWED_AI_ML_RUNTIME_IMPORTS and\n"
        "update ai_ml/README.md — deliberately, not by editing this test\n"
        "into silence.\n"
        "Problems:\n" + "\n".join(problems)
    )
