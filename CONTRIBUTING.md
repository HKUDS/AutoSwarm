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

Run `python -m unittest discover -s tests -v` and `node --test tests/test_opencode_plugin.mjs`. Tests use controlled native CLI peers and local HTTP/WebSocket servers; they require local socket and subprocess access. CI installs the built wheel and tests Linux, macOS and Windows with Python 3.10 and 3.12. Python runtime dependencies are empty; Node is needed only for the OpenCode plugin tests.

The `scripts/live_*` checks create isolated native conversations and spend model calls using existing credentials. They are optional integration checks, not part of ordinary CI. `scripts/build_release.py` creates a standalone executable on the target operating system using PyInstaller.

For archived validation, install the v1 `dev` extra in its own environment, change into `autoswarm-v1.0.0/`, and run `python -m pytest tests`. Historical CI checks are preserved in its `.github/` directory.

## Pull requests

Keep changes focused, describe the resulting behavior, and include relevant validation. Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
