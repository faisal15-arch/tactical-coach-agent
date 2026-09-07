"""
Live Score Tool.

Uses CricketData.org (formerly CricAPI) — a free cricket data API — for
real match scores when CRICKETDATA_API_KEY is set in .env. Falls back to
mock data if:
  - no API key is set (so the app still runs out of the box)
  - the API has no live matches right now (very common — most of the day,
    no international/major match is actually in progress)
  - the API call fails for any reason (network, rate limit, etc.)

Sign up for a free key (100 requests/day, no expiry) at:
    https://cricketdata.org/member.aspx

NOTE: "available_bowlers" stays mocked even with a real match, since this
demo's bowler-effectiveness analytics (tools/analytics/bowler_effectiveness.py)
is built around named profiles (Shaheen, Naseem, Abrar) rather than a real
squad — matching bowlers to those profiles would need a separate mapping
step, which is a good next task once this basic integration is working.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

CRICKETDATA_API_KEY = os.getenv("CRICKETDATA_API_KEY")
TEAM_FILTER = os.getenv("CRICKET_TEAM_FILTER")  # e.g. "India" — optional


def _mock_score() -> dict:
    return {
        "score": "165/4",
        "overs": 32.0,
        "current_batter": "Root",
        "available_bowlers": ["Shaheen", "Naseem", "Abrar"],
        "source": "mock",
    }


def get_live_score(match_id: str = "mock-match-1") -> dict:
    if not CRICKETDATA_API_KEY:
        print("[live_score_tool] no CRICKETDATA_API_KEY set — using mock data")
        return _mock_score()

    try:
        response = requests.get(
            "https://api.cricapi.com/v1/currentMatches",
            params={"apikey": CRICKETDATA_API_KEY, "offset": 0},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()

        matches = data.get("data", [])
        live_matches = [m for m in matches if m.get("matchStarted") and not m.get("matchEnded")]

        if TEAM_FILTER:
            filtered = [m for m in live_matches if TEAM_FILTER.lower() in m.get("name", "").lower()]
            if filtered:
                live_matches = filtered
            else:
                print(f"[live_score_tool] no live match found matching '{TEAM_FILTER}' — showing first available live match instead")

        if not live_matches:
            print("[live_score_tool] API reachable but no live matches right now — using mock data")
            return _mock_score()

        match = live_matches[0]
        print(f"[live_score_tool] using live match: {match.get('name')}")
        score_entries = match.get("score", [])

        if not score_entries:
            print("[live_score_tool] live match found but no score data yet — using mock data")
            return _mock_score()

        latest = score_entries[-1]  # most recent innings
        runs = latest.get("r", 0)
        wickets = latest.get("w", 0)
        overs = latest.get("o", 0.0)

        return {
            "score": f"{runs}/{wickets}",
            "overs": float(overs),
            "current_batter": None,  # not provided by this endpoint
            "available_bowlers": ["Shaheen", "Naseem", "Abrar"],  # see module docstring
            "source": "live",
            "match_name": match.get("name"),
        }

    except Exception as e:
        print(f"[live_score_tool] API call failed ({e}) — using mock data")
        return _mock_score()
