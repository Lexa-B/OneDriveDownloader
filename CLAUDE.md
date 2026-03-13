# CLAUDE.md

## Project

One-time OneDrive downloader TUI. Python 3.14, uv, Textual.

## Commands

- Install deps: `uv sync`
- Run app: `uv run python -m src`
- Run tests: `uv run pytest -v`
- Run single test file: `uv run pytest tests/test_models.py -v`

## Architecture

- `src/models.py` — `DriveItem` (parsed from Graph API JSON) and `FolderNode` (tree selection state)
- `src/quickxor.py` — `QuickXorHash`, port of Microsoft's C# reference implementation
- `src/auth.py` — MSAL device code flow, `TokenProvider` for silent refresh, config loading from `config.json`
- `src/graph.py` — `GraphClient` wrapping httpx with pagination, 429 retry, and 401 token refresh
- `src/downloader.py` — chunked streaming download, inline hash verification, metadata sidecars
- `src/widgets/folder_tree.py` — Textual `Tree` subclass with lazy loading and checkbox selection
- `src/widgets/status_panel.py` — reactive progress display
- `src/app.py` — main Textual app wiring everything together

## Conventions

- All source code under `src/`, tests under `tests/`
- Async I/O via httpx and anyio
- Tests use pytest with pytest-anyio for async tests
- Graph API tests use `httpx.MockTransport`
- No mocking of internal modules — tests hit real code paths
- Sensitive files (`config.json`, `.msal_cache.json`) are gitignored
