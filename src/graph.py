from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from src.models import DriveItem

if TYPE_CHECKING:
    pass

BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None, access_token: str = "") -> None:
        if http_client is not None:
            self._client = http_client
        else:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            )
        self._max_retries = 5

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self._max_retries):
            response = await self._client.request(method, url, **kwargs)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"Request to {url} failed after {self._max_retries} retries (429 throttled)")

    async def list_children(self, item_id: str) -> list[DriveItem]:
        items: list[DriveItem] = []
        if item_id == "root":
            url = "/me/drive/root/children"
        else:
            url = f"/me/drive/items/{item_id}/children"

        while url:
            response = await self._request("GET", url)
            data = response.json()
            for raw in data.get("value", []):
                items.append(DriveItem.from_api(raw))
            url = data.get("@odata.nextLink")

        return items

    async def get_item(self, item_id: str) -> DriveItem:
        response = await self._request("GET", f"/me/drive/items/{item_id}")
        return DriveItem.from_api(response.json())

    async def delete_item(self, item_id: str) -> None:
        await self._request("DELETE", f"/me/drive/items/{item_id}")

    async def close(self) -> None:
        await self._client.aclose()
