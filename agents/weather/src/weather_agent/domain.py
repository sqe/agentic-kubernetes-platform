"""Location parsing and Open-Meteo integration."""

import re
from typing import Any

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
PHOTON_URL = "https://photon.komoot.io/api/"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def clean_location(location: str) -> str:
    location = re.split(
        r"(?i)\s*,?\s+(?:and|also)\s+(?:give|show|include|tell)\b",
        location,
        maxsplit=1,
    )[0]
    location = re.sub(
        r"(?i)\b(?:d?jalal)[-\s]?abat\b", "Manas Jalal-Abad", location
    )
    return location.strip(" ,?.!")


def extract_location(prompt: str) -> str | None:
    prompt = prompt.strip().rstrip("?.!")
    patterns = (
        r"(?i)\bweather\s+(?:forecast\s+)?(?:for|in|at)\s+(.+)$",
        r"(?i)\bforecast\s+(?:for|in|at)\s+(.+)$",
        r"(?i)^(.+?)\s+weather(?:\s+forecast)?$",
    )
    for pattern in patterns:
        if match := re.search(pattern, prompt):
            return clean_location(match.group(1))
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
        location = clean_location(location)
        response = await self.client.get(
            GEOCODING_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if results:
            return results[0]

        response = await self.client.get(
            PHOTON_URL,
            params={"q": location, "limit": 5},
            headers={"User-Agent": "agentic-kubernetes-platform/0.1"},
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            raise ValueError(f"Location not found: {location}")
        feature = next(
            (
                item
                for item in features
                if item.get("properties", {}).get("type") in {"city", "town", "village"}
            ),
            next(
                (
                    item
                    for item in features
                    if item.get("properties", {}).get("osm_key") == "place"
                ),
                features[0],
            ),
        )
        properties = feature.get("properties", {})
        longitude, latitude = feature["geometry"]["coordinates"]
        return {
            "name": properties.get("name") or properties.get("city") or location,
            "country": properties.get("country"),
            "admin1": properties.get("state"),
            "latitude": latitude,
            "longitude": longitude,
        }

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
