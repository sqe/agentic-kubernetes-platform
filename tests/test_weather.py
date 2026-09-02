import httpx
import pytest
from weather_agent.app import WeatherHandler, agent_card, create_app
from weather_agent.domain import (
    FORECAST_URL,
    GEOCODING_URL,
    PHOTON_URL,
    OpenMeteoClient,
    extract_location,
)

from platform_runtime.cache import Cache


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("weather in New York?", "New York"),
        ("Forecast for Reykjavík", "Reykjavík"),
        ("Tokyo weather", "Tokyo"),
        (
            "what's the weather in djalalabat Kyrgyzstan, also give me forecast",
            "Manas Jalal-Abad Kyrgyzstan",
        ),
        ("what's the weather in vancouwer wa", "vancouwer wa"),
        ("hello", None),
    ],
)
def test_extract_location(prompt, expected):
    assert extract_location(prompt) == expected


@pytest.mark.asyncio
async def test_current_weather():
    async def transport(request: httpx.Request):
        if str(request.url).startswith(GEOCODING_URL):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"name": "Oslo", "country": "Norway", "latitude": 59.9, "longitude": 10.7}
                    ]
                },
            )
        assert str(request.url).startswith(FORECAST_URL)
        return httpx.Response(
            200,
            json={
                "timezone": "Europe/Oslo",
                "current": {"temperature_2m": 7},
                "current_units": {"temperature_2m": "°C"},
            },
        )

    client = OpenMeteoClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    result = await client.current("Oslo")
    assert result["current"]["temperature_2m"] == 7
    assert result["country"] == "Norway"
    await client.close()


@pytest.mark.asyncio
async def test_forecast_and_missing_location():
    calls = 0

    async def transport(request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, json={"results": [{"name": "Paris", "latitude": 1, "longitude": 2}]}
            )
        return httpx.Response(
            200,
            json={
                "timezone": "UTC",
                "daily": {
                    "time": ["2026-01-01"],
                    "temperature_2m_max": [12],
                    "temperature_2m_min": [4],
                    "precipitation_sum": [1],
                    "weather_code": [3],
                },
            },
        )

    client = OpenMeteoClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    assert (await client.forecast("Paris", 99))["days"][0]["high_c"] == 12
    missing = OpenMeteoClient(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    )
    with pytest.raises(ValueError, match="Location not found"):
        await missing.current("Nowhere")


@pytest.mark.asyncio
async def test_fuzzy_geocoding_fallback():
    async def transport(request: httpx.Request):
        if str(request.url).startswith(GEOCODING_URL):
            return httpx.Response(200, json={})
        if str(request.url).startswith(PHOTON_URL):
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "name": "Vancouver",
                                "state": "Washington",
                                "country": "United States",
                                "osm_key": "place",
                            },
                            "geometry": {"coordinates": [-122.67, 45.63]},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "timezone": "America/Los_Angeles",
                "current": {"temperature_2m": 12},
                "current_units": {"temperature_2m": "°C"},
            },
        )

    client = OpenMeteoClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    result = await client.current("vancouwer wa")
    assert result["location"] == "Vancouver"
    assert result["latitude"] == 45.63


class FakeWeather:
    calls = 0
    forecast_calls = 0

    async def current(self, location):
        self.calls += 1
        return {"location": location, "temperature": 20}

    async def forecast(self, location, days):
        self.forecast_calls += 1
        return {"location": location, "days": days}


@pytest.mark.asyncio
async def test_weather_handler_methods():
    weather = FakeWeather()
    handler = WeatherHandler(weather, Cache(None, 60))
    result = await handler(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "weather.current",
            "params": {"prompt": "weather in Lima"},
        }
    )
    assert result["result"]["location"] == "Lima"
    combined = await handler(
        {
            "jsonrpc": "2.0",
            "id": "combined",
            "method": "weather.current",
            "params": {"prompt": "weather in Lima, also give me forecast", "days": 4},
        }
    )
    assert combined["result"]["forecast"]["days"] == 4
    forecast = await handler(
        {
            "jsonrpc": "2.0",
            "id": "2",
            "method": "weather.forecast",
            "params": {"location": "Lima", "days": 3},
        }
    )
    assert forecast["result"]["days"] == 3
    routed = await handler(
        {
            "jsonrpc": "2.0",
            "id": "model-fleet-1",
            "method": "tasks.execute",
            "params": {
                "skill": "weather.current",
                "prompt": "weather in Madrid",
                "user_id": "U1",
            },
        }
    )
    assert routed["result"]["location"] == "Madrid"
    invalid = await handler(
        {"jsonrpc": "2.0", "id": "3", "method": "other", "params": {"location": "Lima"}}
    )
    assert invalid["error"]["code"] == -32601
    missing = await handler(
        {"jsonrpc": "2.0", "id": "4", "method": "weather.current", "params": {}}
    )
    assert missing["error"]["code"] == -32602


def test_weather_card_and_routes():
    assert {skill.id for skill in agent_card().skills} == {"weather.current", "weather.forecast"}
    paths = {route.path for route in create_app(FakeWeather(), register=False).routes}
    assert "/.well-known/agent.json" in paths
    assert "/tasks" not in paths
