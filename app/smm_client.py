"""
Thin async wrapper around the SMM panel API (dnoxsmm / any Perfect-Panel clone).

All actions are POST form-encoded to a single endpoint, distinguished by `action`.
"""
import httpx

from .config import get_settings


class SmmError(Exception):
    pass


class SmmClient:
    def __init__(self):
        s = get_settings()
        self.url = s.smm_api_url
        self.key = s.smm_api_key

    async def _post(self, data: dict) -> dict | list:
        payload = {"key": self.key, **data}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self.url, data=payload)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception as e:
                raise SmmError(f"Non-JSON response from panel: {resp.text[:300]}") from e

    async def services(self) -> list:
        return await self._post({"action": "services"})

    async def balance(self) -> dict:
        return await self._post({"action": "balance"})

    async def add_order(self, service_id: int, link: str, quantity: int) -> dict:
        """
        Returns {"order": <id>} on success, or {"error": "..."} on failure.
        """
        res = await self._post(
            {
                "action": "add",
                "service": service_id,
                "link": link,
                "quantity": quantity,
            }
        )
        if isinstance(res, dict) and res.get("error"):
            raise SmmError(str(res["error"]))
        if not isinstance(res, dict) or "order" not in res:
            raise SmmError(f"Unexpected add-order response: {res}")
        return res

    async def order_status(self, order_id) -> dict:
        return await self._post({"action": "status", "order": order_id})

    async def multi_status(self, order_ids: list) -> dict:
        return await self._post({"action": "status", "orders": ",".join(map(str, order_ids))})
