# Contributing to AutoSwarm v2

The repository root contains v2. Make implementation changes under `src/autoswarm/`, documentation under `docs/`, and regression tests under `tests/`. See [the architecture](docs/architecture.zh-CN.md) for responsibilities and extension points.

The complete v1 implementation is frozen in [`autoswarm-v1.0.0/`](autoswarm-v1.0.0/ARCHIVE.md). Keep the archive unchanged during v2 development. Its package, tests, runtime data, and frontend dependencies belong to v1.

## Development setup

```bash
uv venv .venv-v2 --python 3.12
uv pip install --python .venv-v2/bin/python -e .
.venv-v2/bin/autoswarm --version
.venv-v2/bin/autoswarm --help
```

Requires Python 3.10 or later. On Windows the environment executables are under `Scripts/`. Keep any pre-existing v1 environment separate.

## Validation

Run the complete CI checks before pushing:

```bash
python scripts/check.py
```

Use a working Python 3.10+ interpreter (preferably 3.12) and install Node.js 20. On Windows, use `.venv-v2\Scripts\python.exe` if `python` resolves to the Microsoft Store alias, or run `uv run --no-project --python 3.12 python scripts/check.py`. The script builds and installs the wheel in a temporary virtual environment, checks entry points, runs the Python and Node suites, installs a relocatable Skill and checks the installed distribution outside the checkout. The first wheel build may download build dependencies. GitHub Actions uses this same script for each OS/Python combination.

Enable automatic checks for this clone once:

```bash
python scripts/check.py --install-hook
```

The repository-local `pre-push` hook uses the Python interpreter that installed it. A failed or cancelled check stops the push before uploading commits. Commit or stash outstanding changes first so the hook validates the exact commit being pushed; use the manual command above while editing. Push each checked-out branch separately. Deletion-only pushes need no checks. Existing custom hooks or `core.hooksPath` configurations are preserved; integrate `scripts/check.py --pre-push` into those hooks instead. Reinstall the hook if its Python interpreter moves. Local checks cover the current OS/Python only, so the remote matrix still runs after pushing.

For focused debugging, run the individual suites:

Run `python -m unittest discover -s tests -v` and `node --test tests/test_opencode_plugin.mjs`. Tests use controlled native CLI peers and local HTTP/WebSocket servers; they require local socket and subprocess access. CI installs the built wheel and tests Linux, macOS and Windows with Python 3.10 and 3.12. Python runtime dependencies are empty; Node is needed only for the OpenCode plugin tests.

The `scripts/live_*` checks create isolated native conversations and spend model calls using existing credentials. They are optional integration checks, not part of ordinary CI.

For archived validation, install the v1 `dev` extra in its own environment, change into `autoswarm-v1.0.0/`, and run `python -m pytest tests`. Historical CI checks are preserved in its `.github/` directory.

## Pull requests

Keep changes focused, describe the resulting behavior, and include relevant validation. Follow the [Code of Conduct](CODE_OF_CONDUCT.md).

Keep changes local unless the user explicitly requests a push. Completing an implementation or test does not authorize publishing it.
