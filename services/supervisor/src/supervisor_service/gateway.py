import json

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError


class RouteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str


class LlmGateway:
    """Constrained OpenAI-compatible skill selector."""

    def __init__(
        self,
        url: str | None,
        model: str | None,
        api_key: str | None,
        client: httpx.AsyncClient,
    ) -> None:
        self.url = url.rstrip("/") if url else None
        self.model = model
        self.api_key = api_key
        self.client = client

    async def select(self, prompt: str, available: list[dict[str, str]]) -> str:
        if not self.url or not self.model:
            raise HTTPException(status_code=503, detail="LLM gateway routing is not configured")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = await self.client.post(
            f"{self.url}/v1/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Select exactly one available skill. Return only JSON with the shape "
                            '{"skill":"skill.id"}. Do not invent a skill.'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps({"prompt": prompt, "available": available}),
                    },
                ],
            },
        )
        response.raise_for_status()
        try:
            selection = RouteSelection.model_validate_json(
                response.json()["choices"][0]["message"]["content"]
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=502, detail="Gateway returned an invalid route"
            ) from exc
        if selection.skill not in {item["skill"] for item in available}:
            raise HTTPException(status_code=502, detail="Gateway selected an unavailable skill")
        return selection.skill
