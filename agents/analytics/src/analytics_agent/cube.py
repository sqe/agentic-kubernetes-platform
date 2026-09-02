from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt


class CubeClient:
    def __init__(
        self,
        url: str,
        secret: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.secret = secret
        self.client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None

    async def query(self, query: dict[str, Any]) -> dict[str, Any]:
        if not self.secret:
            raise ValueError("CUBE_API_SECRET is required")
        now = datetime.now(UTC)
        token = jwt.encode(
            {"iat": now, "exp": now + timedelta(minutes=5), "sub": "analytics-agent"},
            self.secret,
            algorithm="HS256",
        )
        response = await self.client.post(
            f"{self.url}/cubejs-api/v1/load",
            headers={"Authorization": token},
            json={"query": query},
        )
        response.raise_for_status()
        payload = response.json()
        if error := payload.get("error"):
            raise ValueError(str(error))
        return payload

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
