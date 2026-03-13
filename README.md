# OneDrive Downloader

A terminal UI for bulk-downloading files from OneDrive Personal with hash verification, metadata preservation, and optional remote deletion.

Built for one-time migrations off OneDrive — browse your folder tree, select what you want, download with integrity checks, and optionally delete the originals.

## Features

- **Interactive folder tree** — lazy-loaded from OneDrive, browse and select folders or individual files
- **Checkbox selection** — toggle entire folders or pick specific files; partial-selection indicators for mixed states
- **Chunked streaming downloads** — 4 MB chunks, up to 4 files in parallel
- **Hash verification** — computes Microsoft's QuickXorHash inline during download and verifies against OneDrive's hash; hard-stops on any mismatch. Already-downloaded files are re-verified by hash before any remote deletion
- **Resume support** — re-running picks up where you left off; existing files are hash-verified locally instead of re-downloaded
- **Metadata preservation** — restores `lastModifiedDateTime` as local file mtime; writes `.metadata.json` sidecars with full file metadata (IDs, timestamps, hashes)
- **Remote deletion (on by default)** — deletes originals from OneDrive only after the local copy is verified on disk (exists + correct size + hash match); toggle off with `R` before downloading
- **Rate limit handling** — automatic retry on HTTP 429 (respects Retry-After header, exponential fallback); automatic token refresh on 401
- **Directory structure preserved** — local `outputs/` mirrors your OneDrive folder hierarchy

> **Warning:** Remote deletion is **enabled by default**. Press `R` to toggle it off before pressing `D` to download. When enabled, a confirmation prompt appears before any files are deleted.

## Prerequisites

- **Python 3.14+** — if you don't have it, [uv](https://docs.astral.sh/uv/) can install it for you: `uv python install 3.14`
- **uv** — install from [docs.astral.sh/uv](https://docs.astral.sh/uv/)
- **A Microsoft account** with OneDrive Personal (free accounts work fine)

## Azure App Registration

This app uses the [device code OAuth flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code) to authenticate with your Microsoft account. You need a free Azure app registration — this takes about 3 minutes and costs nothing.

### Why these settings?

- **Device code flow** lets you authenticate without a redirect URI or local web server — you just paste a code into your browser.
- **"Personal Microsoft accounts only"** is required because OneDrive Personal uses Microsoft's `/consumers` authentication endpoint. Work/school accounts use a different endpoint.
- **"Allow public client flows"** tells Azure this app authenticates without a client secret. Device code flow is a public client flow — the app never holds a secret, only a user-granted token.
- **`Files.ReadWrite.All` scope** grants read access (to browse and download) and write access (to delete originals after verified download). If you don't plan to use remote deletion, the app still requires this scope because it's a single permission that covers both.

### Steps

1. Go to [Azure App Registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) (sign in with any Microsoft account)
2. Click **New registration**
   - **Name:** anything you like (e.g., "OneDrive Downloader")
   - **Supported account types:** select **Personal Microsoft accounts only**
   - **Redirect URI:** leave blank
3. After creation, you'll land on the app's overview page. Copy the **Application (client) ID** — this is the only value you need.
4. In the left sidebar, click **Authentication**
   - Scroll to **Advanced settings**
   - Set **Allow public client flows** to **Yes**
   - Click **Save**

No client secret is needed. No API permissions need to be configured — the app requests `Files.ReadWrite.All` at runtime via the device code prompt, and the user consents interactively.

## Installation

```bash
git clone <repo-url>
cd OneDriveDownloader
uv sync
```

### Configuration

Create `config.json` in the project root with your Application (client) ID from the Azure registration:

```json
{"client_id": "YOUR-CLIENT-ID-HERE"}
```

This file is gitignored and will not be committed.

## Usage

```bash
uv run python -m src
```

Or using the installed script alias:

```bash
uv run onedrive-dl
```

### First run

On first launch, the app will display a URL and a device code:

```
To sign in, visit: https://microsoft.com/devicelogin
Enter code: ABCD1234
```

Open the URL in any browser, enter the code, and sign in with your Microsoft account. The app will cache your tokens in `.msal_cache.json` (gitignored) for future sessions — you won't need to sign in again unless the refresh token expires.

### Controls

| Key        | Action                            |
|------------|-----------------------------------|
| Arrow keys | Navigate the folder tree          |
| Space      | Toggle selection (file or folder) |
| Enter      | Expand / collapse folder          |
| D          | Start download                    |
| R          | Toggle remote deletion on/off     |
| Q          | Quit                              |

### Download behavior

1. Select folders or individual files in the tree (Space to toggle)
2. Check the status panel on the right — it shows selection count, total size, and deletion toggle state
3. Press D to start downloading
4. If remote deletion is on, a confirmation dialog appears first
5. Files download to `./outputs/`, preserving the OneDrive directory structure
6. After download completes, the folder tree reloads from OneDrive to reflect any deletions

## How It Works

```
Sign in (device code flow)
        │
        ▼
Browse folder tree (lazy-loaded from Graph API)
        │
        ▼
Select folders / files (Space to toggle)
        │
        ▼
Press D → collect all files from selected folders
        │
        ▼
For each file (up to 4 in parallel):
  ├─ Already local with matching size?
  │   ├─ Yes → verify hash of local copy
  │   └─ No  → download in 4 MB chunks, compute hash inline
  ├─ Hash match → write metadata sidecar
  │   └─ (If deletion enabled) verify local file on disk, then delete remote
  ├─ Hash mismatch → HARD STOP all downloads
  └─ Download failure → skip, don't delete remote
        │
        ▼
Reload folder tree from OneDrive
```

### Hash verification

OneDrive Personal uses [QuickXorHash](https://learn.microsoft.com/en-us/onedrive/developer/code-snippets/quickxorhash), a proprietary Microsoft hash algorithm. For fresh downloads, the hash is computed inline while streaming each chunk. For already-downloaded files, the local copy is read and hashed. In both cases, if the hash doesn't match OneDrive's hash, the pipeline hard-stops: all in-flight downloads are cancelled and no corrupt file is kept. Remote files are never deleted unless the local copy has been verified on disk.

If the OneDrive API omits the hash in folder listings (which happens occasionally), the app fetches it via a per-item API call before downloading.

### Resume

Re-running the app after an interrupted session automatically picks up where you left off. Files that already exist locally with the correct size are hash-verified against OneDrive's QuickXorHash instead of re-downloaded. If the hash matches, the metadata sidecar is refreshed and the remote is deleted (if deletion is enabled). If the hash doesn't match, the pipeline hard-stops just like a failed download.

## Architecture

```
src/
├── app.py              Main Textual app — wires auth, API, UI, and download together
├── app.tcss            Textual CSS layout (two-panel grid)
├── auth.py             MSAL device code flow, silent token refresh, config loading
├── downloader.py       Chunked streaming download, inline hash verification, metadata sidecars
├── graph.py            Async Microsoft Graph API client with pagination, 429 backoff, 401 refresh
├── models.py           DriveItem (parsed API response) and FolderNode (tree selection state)
├── quickxor.py         QuickXorHash — port of Microsoft's C# reference implementation
└── widgets/
    ├── folder_tree.py  Textual Tree subclass with lazy loading, checkboxes, file/folder selection
    └── status_panel.py Reactive progress display (selection count, download progress, ETA)
```

### Data flow

**Authentication:** `auth.py` handles the MSAL device code flow and provides a `TokenProvider` that `graph.py` calls when it receives a 401 response. Tokens are cached locally in `.msal_cache.json`.

**Browsing:** `folder_tree.py` calls `graph.py` to lazily load folder contents when the user expands a tree node. Each API response is parsed into `DriveItem` objects via `models.py`.

**Downloading:** When the user presses D, `app.py` collects all files from selected folders (recursively via `graph.py`), then dispatches concurrent download tasks through `downloader.py`. Each task streams the file, computes the hash via `quickxor.py`, verifies it, writes the file and metadata sidecar, and optionally deletes the remote via `graph.py`.

**Progress:** `status_panel.py` uses Textual's reactive properties — `app.py` updates counters and the panel re-renders automatically.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `AADSTS` error codes during sign-in | Azure app misconfigured | Verify: account type is "Personal Microsoft accounts only", public client flows enabled, no redirect URI set |
| "No cached account — re-run the app" | Token cache expired or corrupted | Delete `.msal_cache.json` and re-run — you'll be prompted to sign in again |
| "PIPELINE STOPPED: HASH_MISMATCH" | Downloaded bytes don't match OneDrive's hash | This is a safety feature. Re-run the app — the file will be retried. If it persists, the file may be corrupted on OneDrive's side |
| "PIPELINE STOPPED: MISSING_HASH" | OneDrive API returned no hash even after per-item fetch | Rare server-side issue. Wait and retry later |
| Downloads seem slow / pausing | HTTP 429 rate limiting from Microsoft | The app retries automatically (respects the server's Retry-After header). Just wait — it will resume |
| `config.json not found` | Missing configuration file | Create `config.json` in the project root per the Installation section |

## Testing

```bash
uv run pytest -v
```

Tests use `httpx.MockTransport` to simulate Graph API responses — no real Microsoft account needed. Test coverage includes models, auth config loading, Graph API client (pagination, retry, token refresh), download engine (hash verification, skip logic, metadata), and QuickXorHash (reference vectors from Microsoft's spec).

## License

[MIT](LICENSE)

## Citation

If you use this software in research, see [CITATION.cff](CITATION.cff) for citation metadata.
