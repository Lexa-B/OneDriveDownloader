from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import msal

SCOPES = ["Files.ReadWrite.All"]
AUTHORITY = "https://login.microsoftonline.com/consumers"

SETUP_INSTRUCTIONS = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                          OneDrive Setup Required                             ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  1. Go to Azure App Registrations:                                            ║
║     https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade
║                                                                               ║
║  2. Click "New registration"                                                  ║
║     - Name: OneDrive Downloader (or anything)                                 ║
║     - Account type: "Personal Microsoft accounts only"                        ║
║     - Redirect URI: leave blank                                               ║
║                                                                               ║
║  3. Copy the "Application (client) ID"                                        ║
║                                                                               ║
║  4. Go to "Authentication" in the left sidebar                                ║
║     - Under "Advanced settings", set "Allow public client flows" to Yes       ║
║     - Save                                                                    ║
║                                                                               ║
║  5. Create config.json in the project root:                                   ║
║     {"client_id": "YOUR-CLIENT-ID-HERE"}                                      ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
""".strip()


@dataclass
class AuthConfig:
    client_id: str


def load_config(config_path: Path) -> AuthConfig | None:
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text())
        client_id = data.get("client_id")
        if not client_id:
            return None
        return AuthConfig(client_id=client_id)
    except (json.JSONDecodeError, KeyError):
        return None


def build_msal_app(config: AuthConfig, cache_path: Path) -> msal.PublicClientApplication:
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())

    app = msal.PublicClientApplication(
        client_id=config.client_id,
        authority=AUTHORITY,
        token_cache=cache,
    )
    return app


def acquire_token(app: msal.PublicClientApplication, cache_path: Path) -> str:
    accounts = app.get_accounts()
    result = None

    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Device flow initiation failed: {flow.get('error_description', 'unknown error')}")
        print(f"\n  To sign in, visit: {flow['verification_uri']}")
        print(f"  Enter code: {flow['user_code']}\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Authentication failed: {result.get('error_description', 'unknown error')}")

    # Persist token cache
    if app.token_cache.has_state_changed:
        cache_path.write_text(app.token_cache.serialize())

    return result["access_token"]
