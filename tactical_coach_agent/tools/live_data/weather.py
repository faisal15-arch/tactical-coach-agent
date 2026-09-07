"""
Weather Tool
Fetches live weather from OpenWeatherMap.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("WEATHER_API_KEY")


def get_weather(city: str = "Lahore") -> dict:
    """
    Fetch live weather for a city.
    """

    url = "https://api.openweathermap.org/data/2.5/weather"

    params = {
        "q": city,
        "appid": API_KEY,
        "units": "metric",
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        response.raise_for_status()

        data = response.json()

        return {
            "condition": data["weather"][0]["main"],
            "humidity": data["main"]["humidity"],
            "temperature_c": data["main"]["temp"],
        }

    except Exception as e:
        print(f"[weather] API Error: {e}")

        return {
            "condition": "Unknown",
            "humidity": 0,
            "temperature_c": 0,
        }


if __name__ == "__main__":
    print(get_weather("Lahore"))