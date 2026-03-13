# OneDrive Downloader

A terminal UI for bulk-downloading files from OneDrive Personal with hash verification and optional remote deletion.

Built for one-time migrations off OneDrive — browse folders, select what you want, download with integrity checks, optionally delete the originals.

## Setup

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### Azure App Registration

The app uses Microsoft's device code OAuth flow. You need a free Azure app registration:

1. Go to [Azure App Registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
   - Name: anything (e.g. "OneDrive Downloader")
   - Account type: **Personal Microsoft accounts only**
   - Redirect URI: leave blank
3. Copy the **Application (client) ID**
4. Go to **Authentication** in the sidebar, set **Allow public client flows** to **Yes**, save
5. Create `config.json` in the project root:

```json
{"client_id": "YOUR-CLIENT-ID-HERE"}
```

## Usage

```bash
uv run python -m src
```

On first run, you'll be prompted to visit a URL and enter a device code to sign in.

### Controls

| Key     | Action                              |
|---------|-------------------------------------|
| Arrow keys | Navigate folder tree             |
| Space   | Toggle folder selection             |
| Enter   | Expand/collapse folder              |
| D       | Start download                      |
| R       | Toggle remote deletion on/off       |
| Q       | Quit                                |

### What it does

- Browses your OneDrive folder tree (lazy-loaded)
- Downloads selected folders to `./outputs/`, preserving directory structure
- Computes QuickXorHash inline during download and verifies against OneDrive's hash
- Skips files already downloaded (by size match)
- Writes `.metadata.json` sidecars with file IDs, timestamps, and hashes
- Preserves `lastModifiedDateTime` as the local file's mtime
- Optionally deletes remote files after verified download (with confirmation prompt)
- Hard-stops on any hash mismatch — no silent corruption

### Remote deletion

Deletion is **on by default**. Press R to toggle it off before downloading. When on, the app will prompt for confirmation before proceeding.

## Project Structure

```
src/
  app.py              # Textual app — wires everything together
  app.tcss            # Textual CSS layout
  auth.py             # MSAL device code flow
  downloader.py       # Chunked download with hash verification
  graph.py            # Async Microsoft Graph API client
  models.py           # DriveItem and FolderNode dataclasses
  quickxor.py         # QuickXorHash (Microsoft's OneDrive hash)
  widgets/
    folder_tree.py    # Tree widget with checkboxes and lazy loading
    status_panel.py   # Progress and status display
tests/
  test_auth.py
  test_downloader.py
  test_graph.py
  test_models.py
  test_quickxor.py
```

## Testing

```bash
uv run pytest -v
```
