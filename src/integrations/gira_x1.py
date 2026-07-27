from __future__ import annotations

# URL prefixes (from official Gira IoT REST API v2 docs):
# /api/v2/  — for uiconfig, values, licenses
# /api/clients/  — for client registration, callback management (NO /v2/)

import logging
from typing import Any

import httpx  # type: ignore[import-not-found]


logger = logging.getLogger(__name__)


class GiraX1Adapter:
    """Async HTTP client for Gira X1 REST API.

    Uses verify=False for self-signed cert.
    Token passed as query parameter to all /api/v2/ endpoints.

    Two URL prefixes:
    - /api/v2/{path}?token={token}  — values, uiconfig, licenses
    - /api/clients/{path}           — client/callback management (no v2)
    """

    name: str = "gira_x1"

    def __init__(
        self,
        host: str,
        token: str,
        *,
        verify_tls: bool = True,
        ca_bundle: str = "",
    ) -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._verify_tls = verify_tls
        self._ca_bundle = ca_bundle
        self._client: httpx.AsyncClient | None = None

    def _api_v2_url(self, path: str) -> str:
        """URL for /api/v2/ endpoints with token."""
        path = path.lstrip("/")
        return f"https://{self._host}/api/v2/{path}?token={self._token}"

    def _api_clients_url(self, path: str) -> str:
        """URL for /api/clients/ endpoints (no v2, no token in URL)."""
        path = path.lstrip("/")
        return f"https://{self._host}/api/clients/{path}"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GiraX1Adapter not started. Call await adapter.start() first.")
        return self._client

    async def start(self) -> None:
        verification: bool | str = self._ca_bundle or self._verify_tls
        if isinstance(verification, bool) and not verification:
            logger.warning("Gira X1 TLS verification is explicitly disabled")
        self._client = httpx.AsyncClient(verify=verification, timeout=10.0)
        logger.info("GiraX1Adapter started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("GiraX1Adapter stopped")

    async def health_check(self) -> bool:
        return await self.check_api()

    async def close(self) -> None:
        await self.stop()

    async def check_api(self) -> bool:
        """Check X1 API availability."""
        try:
            client = self._get_client()
            url = f"https://{self._host}/api/v2/?token={self._token}"
            resp = await client.get(url)
            return resp.status_code < 400
        except Exception:
            logger.warning("X1 API check failed")
            return False

    async def fetch_uiconfig(self) -> dict[str, Any]:
        """Fetch full expanded uiconfig from X1."""
        client = self._get_client()
        url = self._api_v2_url("uiconfig") + "&expand=parameters,dataPointFlags,locations,trades"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_value(self, uid: str) -> dict[str, str]:
        """Get values for a function UID or datapoint UID.

        Returns dict of {datapoint_uid: value} for all datapoints.
        The X1 response is {"values": [{"uid": "dp-dimmer-1-on", "value": "1"}, ...]}
        — parse ALL items, not just values[0].
        """
        client = self._get_client()
        url = self._api_v2_url(f"values/{uid}")
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        result: dict[str, str] = {}
        for item in data.get("values", []):
            result[item["uid"]] = item["value"]
        return result

    async def set_value(self, uid: str, value: Any) -> None:
        """Set a single datapoint value."""
        client = self._get_client()
        url = self._api_v2_url(f"values/{uid}")
        resp = await client.put(url, json={"value": value})
        resp.raise_for_status()

    async def set_values(self, uid_values: dict[str, Any]) -> None:
        """Set multiple datapoint values in a single request."""
        client = self._get_client()
        url = self._api_v2_url("values")
        body = {"values": [{"uid": uid, "value": val} for uid, val in uid_values.items()]}
        resp = await client.put(url, json=body)
        resp.raise_for_status()

    async def register_callbacks(
        self,
        service_url: str | None = None,
        value_url: str | None = None,
        test: bool = True,
    ) -> bool:
        """Register value/service callbacks with X1.

        POST /api/clients/{token}/callbacks
        Body: {"serviceCallback": url, "valueCallback": url, "testCallbacks": bool}

        If testCallbacks=True, X1 immediately POSTs test events to both URLs.
        They must respond 200 or registration fails (400 callbackTestFailed).
        HTTPS only (422 if not).
        """
        client = self._get_client()
        url = self._api_clients_url(f"{self._token}/callbacks")
        body: dict[str, Any] = {}
        if service_url:
            body["serviceCallback"] = service_url
        if value_url:
            body["valueCallback"] = value_url
        body["testCallbacks"] = test

        try:
            resp = await client.post(url, json=body)
            if resp.status_code == 200:
                logger.info("X1 callback registration successful")
                return True
            else:
                logger.warning("X1 callback registration failed: %s", resp.status_code)
                return False
        except Exception:
            logger.exception("X1 callback registration error")
            return False

    async def unregister_callbacks(self) -> bool:
        """Unregister callbacks. DELETE /api/clients/{token}/callbacks"""
        try:
            client = self._get_client()
            url = self._api_clients_url(f"{self._token}/callbacks")
            resp = await client.delete(url)
            if resp.status_code < 400:
                logger.info("X1 callbacks unregistered")
                return True
            else:
                logger.warning("X1 callback unregister failed: %s", resp.status_code)
                return False
        except Exception:
            logger.exception("X1 callback unregister error")
            return False
