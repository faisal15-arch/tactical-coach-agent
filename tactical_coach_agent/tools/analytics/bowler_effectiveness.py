"""
Bowler Effectiveness Tool

Loads generated bowler profiles from:
    tools/analytics/bowler_profiles.json

A bowler with a thin career sample is marked as provisional.
The actual recommendation confidence is capped later in graph.py.
"""

import json
import os
from typing import Any


_PROFILES_PATH = os.path.join(
    os.path.dirname(__file__),
    "bowler_profiles.json",
)


# Fallback only.
# These are used if bowler_profiles.json does not exist.
_FALLBACK_PROFILES = {
    "Mohammad Irfan": {
        "base_skill": 82,
        "style": "pace",
    },
    "S Badree": {
        "base_skill": 78,
        "style": "spin",
    },
    "AD Russell": {
        "base_skill": 85,
        "style": "pace",
    },
    "Rumman Raees": {
        "base_skill": 80,
        "style": "pace_swing",
    },
    "SR Watson": {
        "base_skill": 76,
        "style": "pace",
    },
}


def _load_profiles() -> dict:
    try:
        with open(_PROFILES_PATH, "r", encoding="utf-8") as handle:
            profiles = json.load(handle)

        if not isinstance(profiles, dict):
            raise ValueError("bowler_profiles.json must contain a JSON object")

        provisional_count = sum(
            1
            for profile in profiles.values()
            if isinstance(profile, dict) and profile.get("provisional")
        )

        print(
            f"[bowler_effectiveness] loaded {len(profiles)} profiles "
            f"({provisional_count} provisional)"
        )

        return profiles

    except FileNotFoundError:
        print(
            "[bowler_effectiveness] WARNING: bowler_profiles.json not found. "
            "Using fallback profiles."
        )
        return dict(_FALLBACK_PROFILES)

    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(
            f"[bowler_effectiveness] WARNING: could not load profiles: {exc}. "
            "Using fallback profiles."
        )
        return dict(_FALLBACK_PROFILES)


BOWLER_PROFILES = _load_profiles()


def get_career(bowler: str) -> dict:
    """
    Return career data for a bowler.
    """
    profile = BOWLER_PROFILES.get(bowler) or {}
    return profile.get("career", {}) or {}


def is_provisional(bowler: str) -> bool:
    """
    Return True if bowler has a thin/provisional career sample.
    """
    profile = BOWLER_PROFILES.get(bowler) or {}
    return bool(profile.get("provisional"))


def calculate_bowler_effectiveness(
    available_bowlers: list[str],
    pitch: str = "neutral",
    weather_condition: str = "clear",
    humidity: int = 50,
) -> dict:
    """
    Score every available bowler for the current conditions.

    Returns:
        bowler_scores
        top_bowler
        unrated_bowlers
        provisional_bowlers
    """

    scores = {}
    unrated = []
    provisional = {}

    pitch = (pitch or "neutral").lower()
    weather_condition = (weather_condition or "clear").lower()

    for bowler in available_bowlers:
        profile = BOWLER_PROFILES.get(bowler)

        if not profile:
            unrated.append(bowler)
            continue

        try:
            score = float(profile.get("base_skill", 50))
        except (TypeError, ValueError):
            score = 50

        style = profile.get("style")

        # Conditions bonuses only when the profile actually contains
        # a known style.
        if style:
            if pitch == "green" and style in ("pace", "pace_swing"):
                score += 5

            if pitch == "dry" and style == "spin":
                score += 6

            if style == "pace_swing":
                if weather_condition == "cloudy":
                    score += 4

                if humidity > 70:
                    score += 3

        score = min(max(round(score), 0), 100)
        scores[bowler] = score

        # Preserve provisional metadata.
        if profile.get("provisional"):
            career = profile.get("career", {}) or {}

            provisional[bowler] = {
                "overs": career.get("overs"),
                "own_figures": career.get(
                    "raw_skill",
                    profile.get("base_skill"),
                ),
                "confidence": profile.get("confidence"),
            }

    ranked = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    return {
        "bowler_scores": scores,
        "top_bowler": ranked[0][0] if ranked else None,
        "unrated_bowlers": unrated,
        "provisional_bowlers": provisional,
    }


if __name__ == "__main__":
    print(
        f"[bowler_effectiveness] profiles loaded: "
        f"{len(BOWLER_PROFILES)}"
    )

    test_bowlers = list(BOWLER_PROFILES.keys())[:10]

    result = calculate_bowler_effectiveness(
        available_bowlers=test_bowlers,
        pitch="neutral",
        weather_condition="clear",
        humidity=50,
    )

    print("\nScores:")

    for name, score in sorted(
        result["bowler_scores"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        career = get_career(name)

        marker = ""

        if name in result["provisional_bowlers"]:
            marker = " [PROVISIONAL]"

        print(
            f"{name}: {score} | "
            f"overs={career.get('overs', 'n/a')} "
            f"economy={career.get('economy', 'n/a')}"
            f"{marker}"
        )

    print("\nUnrated:")
    print(result["unrated_bowlers"])