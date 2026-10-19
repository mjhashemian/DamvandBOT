from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import aiohttp

from .config import Config


class XUIError(RuntimeError):
    pass


class XUIClient:
    def __init__(self, config: Config):
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25),
            headers={"Authorization": f"Bearer {self.config.xui_api_token}"},
        )
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    async def _request(self, method: str, path: str, **kwargs):
        if not self.session:
            raise RuntimeError("XUIClient is not started")
        url = f"{self.config.xui_base_url}/panel/api{path}"
        ssl = None if self.config.xui_verify_ssl else False
        async with self.session.request(method, url, ssl=ssl, allow_redirects=False, **kwargs) as response:
            if response.status != 200:
                body = (await response.text())[:500]
                raise XUIError(f"3x-ui HTTP {response.status}: {body}")
            data = await response.json(content_type=None)
        if not data.get("success"):
            raise XUIError(data.get("msg") or "3x-ui API returned success=false")
        return data.get("obj")

    async def health(self) -> bool:
        await self._request("GET", "/server/status")
        return True

    async def enabled_inbound_ids(self) -> list[int]:
        if self.config.xui_inbound_ids:
            return list(self.config.xui_inbound_ids)
        rows = await self._request("GET", "/inbounds/list") or []
        ids = [
            int(row["id"])
            for row in rows
            if row.get("enable") and row.get("protocol") in {"vless", "vmess", "trojan"}
        ]
        if not ids:
            raise XUIError("No enabled supported inbound exists")
        return ids

    async def clients_for_telegram(self, telegram_id: int) -> list[dict]:
        result = await self._request("GET", f"/clients/get/tgId/{telegram_id}")
        return result or []

    async def get_client(self, email: str) -> dict:
        result = await self._request("GET", f"/clients/get/{quote(email, safe='')}")
        if not result:
            raise XUIError("Client not found")
        return result

    async def create_client(
        self,
        *,
        email: str,
        telegram_id: int,
        traffic_bytes: int,
        duration_days: int,
        comment: str,
    ) -> dict:
        expiry_ms = int(
            (datetime.now(timezone.utc) + timedelta(days=duration_days)).timestamp() * 1000
        )
        payload = {
            "client": {
                "email": email,
                "totalGB": traffic_bytes,
                "expiryTime": expiry_ms,
                "tgId": telegram_id,
                "limitIp": 0,
                "limitHwid": 0,
                "enable": True,
                "comment": comment,
            },
            "inboundIds": await self.enabled_inbound_ids(),
        }
        await self._request("POST", "/clients/add", json=payload)
        return await self.get_client(email)

    async def renew_client(self, email: str, traffic_bytes: int, duration_days: int) -> dict:
        payload = {
            "emails": [email],
            "addDays": duration_days,
            "addBytes": traffic_bytes,
        }
        await self._request("POST", "/clients/bulkAdjust", json=payload)
        return await self.get_client(email)

    async def delete_client(self, email: str) -> None:
        await self._request("POST", f"/clients/del/{quote(email, safe='')}")

    async def links(self, email: str) -> list[str]:
        result = await self._request("GET", f"/clients/links/{quote(email, safe='')}")
        if isinstance(result, list):
            return [str(link) for link in result if link]
        return []

    def subscription_url(self, client: dict) -> str:
        sub_id = client.get("client", client).get("subId")
        if not sub_id:
            raise XUIError("Client has no subscription ID")
        return f"{self.config.xui_subscription_base}/{sub_id}"
