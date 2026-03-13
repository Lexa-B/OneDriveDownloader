# OneDrive Downloader — Design Spec

## Purpose

One-time tool to download an entire personal OneDrive (100+ GB) to local disk before account deletion. Terminal-based TUI with interactive folder browsing, metadata preservation, and optional remote deletion after verified download.

## Authentication

- **Azure AD app registration** required (free, ~3 min setup). User provides client ID.
- **Device code flow** via MSAL: tool displays a code, user pastes it at microsoft.com/devicelogin.
- Token cached locally by MSAL for session reuse.
- Auth scope: `Files.ReadWrite.All` (full drive access for reads, downloads, and deletion).
- Client ID stored in `config.json` at project root (gitignored).
- On first run with no config, tool prints step-by-step Azure app registration instructions.

### config.json

```json
{
  "client_id": "your-azure-app-client-id"
}
```

## TUI Layout (Textual)

Single-screen, two-panel layout:

```
┌─ OneDrive Downloader ──────────────────────────────────┐
│ ┌─ Folders ─────────────────┐ ┌─ Status ─────────────┐ │
│ │ ▶ ☐ Documents             │ │ Selected: 3          │ │
│ │ ▼ ☑ Photos                │ │ Total size: ~24 GB   │ │
│ │   ├── ☑ 2024              │ │                      │ │
│ │   ├── ☑ 2025              │ │ Delete remote: ON    │ │
│ │   └── ☐ Screenshots       │ │                      │ │
│ │ ▶ ☐ Music                 │ │ ── Download ──       │ │
│ │ ▶ ☑ Work                  │ │ file_034.jpg         │ │
│ │                           │ │ ████████░░ 80%       │ │
│ │                           │ │                      │ │
│ │                           │ │ Overall:             │ │
│ │                           │ │ 142 / 1893 files     │ │
│ │                           │ │ 12.4 / 98.2 GB       │ │
│ └───────────────────────────┘ └──────────────────────┘ │
│ [Space] Toggle  [Enter] Expand  [D] Download  [R] Del  │
│ toggle  [Q] Quit                                       │
└────────────────────────────────────────────────────────┘
```

### Left panel — Folder tree

- Lazy-loaded: children fetched from API only when a folder is expanded.
- Space to check/uncheck (checking a parent checks all children).
- Enter to expand/collapse.
- Check states: ☐ unchecked, ☑ checked, ☒ partial (some children selected).

### Right panel — Status

- Count of selected folders, estimated total size (from driveItem `size` property — approximate, eventually consistent).
- Delete remote toggle indicator.
- During download: current file name, per-file progress bar, overall progress (file count + bytes).

### Key bindings

| Key     | Action                         |
|---------|--------------------------------|
| Space   | Toggle folder selection        |
| Enter   | Expand/collapse folder         |
| D       | Start download                 |
| R       | Toggle remote deletion (on/off)|
| Q       | Quit                           |

## Download Engine

### File downloading

- Async HTTP via `httpx`.
- Chunked streaming in 4 MB chunks (handles large files without memory pressure).
- Files written to `./outputs/` preserving full OneDrive path structure.
  - Example: `Photos/2024/trip.jpg` → `./outputs/Photos/2024/trip.jpg`
- Empty folders are created locally to preserve directory structure, and deleted remotely if deletion is enabled.
- 3–5 concurrent downloads to balance throughput against rate limits.

### Hash verification (mandatory)

- OneDrive Personal uses `quickXorHash` (not SHA1). This is a proprietary Microsoft hash.
- The `/children` listing endpoint may omit `quickXorHash` for some files (server-side optimization). When the hash is missing from the listing response, fetch it via a per-item `GET /me/drive/items/{id}` call before download. This adds one API call per affected file but guarantees the hash is always available.
- Compute quickXorHash while streaming chunks (no extra file re-read).
- Compare local hash against the API-provided `quickXorHash`.
- **If the per-item GET still does not return a hash: hard-fail the pipeline.** Stop all concurrent downloads, surface the error in the TUI with the file path, and exit. No fallback, no skip, no silent continuation.
- Only proceed (and optionally delete remote) if hashes match.

### Remote deletion (default: on)

- After hash-verified download, delete remote file via `DELETE /me/drive/items/{id}`.
- Toggle-able in the TUI via `R` key (on by default).
- **Confirmation prompt** before starting any download batch with deletion enabled: "You are about to download N files and DELETE them from OneDrive. Press Y to confirm." This prevents accidental destructive operations from a stray `D` keypress.
- `.metadata.json` sidecar written before any remote deletion.
- Failed deletions logged but do not stop the batch (file is safely local).

### Metadata preservation

- After writing each file, set filesystem timestamps:
  - `mtime` from `fileSystemInfo.lastModifiedDateTime`
  - `atime` set equal to `mtime` (access time is not meaningful to preserve; it gets overwritten on first read anyway)
- `createdDateTime` (original creation date) is preserved only in the `.metadata.json` sidecar — Python's `os.utime` cannot set birth time on Linux.
- Per-folder `.metadata.json` sidecar with full API metadata for every file — ensures all original dates are preserved regardless of filesystem limitations.

### Resume logic

- Before downloading, check if file already exists locally with matching size (from API metadata).
- If size matches: skip (already downloaded and hash-verified on the initial pass).
- Size-only is sufficient for resume because the initial download already hash-verified the file. A partial/corrupt file matching the exact expected byte count is astronomically unlikely with chunked writes.
- Re-running the tool picks up where it left off — no state database needed.

### Rate limiting

- Exponential backoff on HTTP 429 responses.
- Concurrent download cap (3–5 parallel files).

### Error handling

- Hash mismatch or missing hash: **hard stop the pipeline** — cancel all in-flight downloads and exit.
- Download failure (network, timeout): logged, does not stop batch — file will be retried on next run.
- Deletion failure: logged, does not stop batch.
- Summary at end: X downloaded, Y skipped (existed), Z failed (with file list).

## Project Structure

```
├── pyproject.toml
├── config.json              # user's client_id (gitignored)
├── .gitignore
├── outputs/                 # download destination
│   └── .metadata.json       # per-folder metadata sidecars
├── src/
│   ├── __init__.py
│   ├── app.py               # Textual app, layout, key bindings
│   ├── auth.py              # MSAL device code flow, token cache
│   ├── graph.py             # Microsoft Graph API client
│   ├── downloader.py        # async download engine, hash verification, metadata
│   └── models.py            # dataclasses for drive items, folder tree nodes
```

### Dependencies

- `msal` — Microsoft authentication (device code flow, token caching)
- `httpx` — async HTTP client (API calls + file downloads)
- `textual` — TUI framework

## Explicitly Out of Scope

- Upload / sync capability
- Database or state file (resume is file-existence-based)
- Config UI (edit config.json manually)
- OneNote / Notebook support (not regular files in the API)
- Shared-with-me files (own OneDrive root only)
