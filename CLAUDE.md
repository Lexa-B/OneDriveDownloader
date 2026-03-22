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
- `src/graph.py` — `GraphClient` wrapping httpx with pagination, 429/transport retry, and 401 token refresh
- `src/downloader.py` — chunked streaming download with retry on transient errors (429/502/503/504/401), mid-stream URL refresh, resumable downloads via HTTP Range, inline hash verification, metadata sidecars
- `src/widgets/folder_tree.py` — Textual `Tree` subclass with lazy loading, checkbox selection, and partial-selection propagation
- `src/widgets/status_panel.py` — reactive progress display with per-file progress bars
- `src/app.py` — main Textual app wiring everything together; sets terminal tab title via OSC escape sequences

## Conventions

- All source code under `src/`, tests under `tests/`
- Async I/O via httpx and anyio
- Tests use pytest with pytest-anyio for async tests
- Graph API tests use `httpx.MockTransport`
- No mocking of internal modules — tests hit real code paths
- Sensitive files (`config.json`, `.msal_cache.json`) are gitignored
