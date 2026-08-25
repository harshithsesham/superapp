"""Weather for outfit suggestions — open-meteo (keyless). Stub without coords.
The result is written as a wardrobe fact with expires_at so it decays instead
of going stale (architecture §6.2 decay hygiene, first real use)."""
import httpx

from ..config import get_settings

STUB_WEATHER = {"condition": "mild", "high_c": 24, "low_c": 16, "precip_prob": 10}

_WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 51: "drizzle", 61: "rain", 63: "rain", 65: "heavy rain",
        71: "snow", 80: "showers", 95: "thunderstorm"}


def todays_weather() -> dict:
    settings = get_settings()
    if settings.home_lat is None or settings.home_lon is None:
        return dict(STUB_WEATHER)
    try:
        data = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": settings.home_lat, "longitude": settings.home_lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "forecast_days": 1, "timezone": "auto",
            },
            timeout=10,
        ).json()["daily"]
        return {
            "condition": _WMO.get(data["weather_code"][0], "mixed"),
            "high_c": round(data["temperature_2m_max"][0]),
            "low_c": round(data["temperature_2m_min"][0]),
            "precip_prob": data["precipitation_probability_max"][0] or 0,
        }
    except (httpx.HTTPError, KeyError, IndexError, TypeError):
        return dict(STUB_WEATHER)
