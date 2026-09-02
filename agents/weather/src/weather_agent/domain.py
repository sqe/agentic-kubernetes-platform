"""Location parsing and Open-Meteo integration."""

import re
from typing import Any

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def extract_location(prompt: str) -> str | None:
    prompt = prompt.strip().rstrip("?.!")
    patterns = (
        r"(?i)\bweather\s+(?:forecast\s+)?(?:for|in|at)\s+(.+)$",
        r"(?i)\bforecast\s+(?:for|in|at)\s+(.+)$",
        r"(?i)^(.+?)\s+weather(?:\s+forecast)?$",
    )
    for pattern in patterns:
        if match := re.search(pattern, prompt):
            return match.group(1).strip()
    return None


class OpenMeteoClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=10)
        self._owns_client = client is None

    async def current(self, location: str) -> dict[str, Any]:
        place = await self._geocode(location)
        response = await self.client.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        data = response.json()
        return {
            "location": place["name"],
            "country": place.get("country"),
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "timezone": data.get("timezone"),
            "current": data["current"],
            "units": data.get("current_units", {}),
        }

    async def forecast(self, location: str, days: int = 7) -> dict[str, Any]:
        place = await self._geocode(location)
        response = await self.client.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "forecast_days": min(max(days, 1), 16),
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        data = response.json()
        daily = data["daily"]
        return {
            "location": place["name"],
            "country": place.get("country"),
            "timezone": data.get("timezone"),
            "days": [
                {
                    "date": date,
                    "high_c": daily["temperature_2m_max"][index],
                    "low_c": daily["temperature_2m_min"][index],
                    "precipitation_mm": daily["precipitation_sum"][index],
                    "weather_code": daily["weather_code"][index],
                }
                for index, date in enumerate(daily["time"])
            ],
        }

    async def _geocode(self, location: str) -> dict[str, Any]:
        response = await self.client.get(
            GEOCODING_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            raise ValueError(f"Location not found: {location}")
        return results[0]

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
