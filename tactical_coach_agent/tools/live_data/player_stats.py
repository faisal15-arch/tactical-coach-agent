"""Fetch the T20 column from a Cricbuzz player profile."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote

import requests


PROFILE_URL = "https://www.cricbuzz.com/profiles/{player_id}/{player_slug}"
PLAYER_SEARCH_URL = "https://www.cricbuzz.com/api/player-search/{query}"
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_T20_BOWLING_CACHE: dict[str, dict[str, Any] | None] = {}
_PROFILE_CACHE: dict[str, dict[str, Any]] = {}
_PLAYER_QUERY_CACHE: dict[str, tuple[str, str]] = {}


def _clean_player_query(player_query: str) -> str:
    # Keep digits so format tokens such as ``t20`` stay intact and can be
    # removed as one noise word.  The old letters-only expression turned
    # "in T20" into "in t", which broke otherwise valid player searches.
    words = re.findall(r"[a-zA-Z0-9'-]+", player_query.lower())
    words = [word[:-2] if word.endswith("'s") else word for word in words]
    noise = {
        "what", "is", "are", "was", "were", "the", "a", "an", "of", "for",
        "how", "many", "total", "number", "does", "has", "have", "show", "tell",
        "me", "player", "career", "t20", "stat", "stats", "profile", "batting",
        "bowling", "average", "economy", "econ", "strike", "rate", "runs",
        "run", "wickets", "wicket", "matches", "match", "hundreds", "hundred",
        "centuries", "century", "fifties", "fifty", "highest", "score", "sixes",
        "six", "fours", "four", "ranking", "rank", "role", "style", "born",
        "age", "date", "birth", "teams", "team", "cricbuzz", "in",
        "about", "please", "strikerate", "strike-rate",
        "played", "appearances", "appearance", "innings", "balls", "ball",
        "faced", "not", "out", "outs", "ducks", "duck", "maidens",
        "maiden", "best", "figures", "figure", "bbi", "bbm", "conceded",
        "haul", "hauls", "five", "ten", "double", "triple", "t20i",
        "t20s", "time", "times", "on", "at", "dismissed", "zero",
        "nought", "nil", "without", "scoring", "been", "games", "game",
        "over", "overs", "previous", "last", "who", "bowl", "bowls", "bowled",
        "bowler", "and", "or",
    }
    return " ".join(
        word for word in words
        if word not in noise and not word.isdigit()
    )


def _flight_values(page: str) -> list[Any]:
    pattern = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)
    values = []
    for raw in pattern.findall(page):
        try:
            decoded = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            continue
        match = re.match(r"^[0-9a-fA-F]+:(.*)$", decoded, re.DOTALL)
        if not match:
            continue
        try:
            values.append(json.loads(match.group(1).strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return values


def _find_profile(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if "playerData" in node and "bowlingStats" in node:
            return node
        for value in node.values():
            found = _find_profile(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_profile(value)
            if found:
                return found
    return None


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _t20_column(profile: dict[str, Any]) -> dict[str, Any] | None:
    table = profile.get("bowlingStats") or {}
    headers = table.get("headers") or []
    try:
        t20_index = next(
            index for index, header in enumerate(headers)
            if str(header).upper() == "T20"
        )
    except StopIteration:
        return None

    stats = {}
    for row in table.get("values") or []:
        values = row.get("values") or []
        if len(values) > t20_index:
            stats[str(values[0]).lower()] = values[t20_index]

    matches = int(_number(stats.get("matches")))
    balls = int(_number(stats.get("balls")))
    wickets = int(_number(stats.get("wickets")))
    if matches == 0 and balls == 0:
        return None

    player = profile.get("playerData") or {}
    return {
        "player_id": int(_number(player.get("id"))),
        "bowling_style": player.get("bowl"),
        "matches": matches,
        "balls": balls,
        "wickets": wickets,
        "economy": round(_number(stats.get("eco")), 2),
        "strike_rate": round(_number(stats.get("sr")), 2),
        "average": round(_number(stats.get("avg")), 2),
    }


def _format_column(table: dict[str, Any], format_name: str = "T20") -> dict[str, Any]:
    headers = table.get("headers") or []
    try:
        format_index = next(
            index for index, header in enumerate(headers)
            if str(header).upper() == format_name.upper()
        )
    except StopIteration:
        return {}

    return {
        str(values[0]).lower(): values[format_index]
        for row in table.get("values") or []
        if (values := row.get("values") or []) and len(values) > format_index
    }


def _get_profile(
    player_id: int | str,
    player_slug: str,
    client: Any,
    timeout: float,
) -> dict[str, Any]:
    cache_key = str(player_id)
    if cache_key in _PROFILE_CACHE:
        return _PROFILE_CACHE[cache_key]

    response = client.get(
        PROFILE_URL.format(player_id=player_id, player_slug=player_slug),
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    for value in _flight_values(response.text):
        profile = _find_profile(value)
        if profile:
            _PROFILE_CACHE[cache_key] = profile
            return profile
    raise ValueError("Could not locate player data in the Cricbuzz profile")


def _resolve_player(
    player_query: str,
    client: Any,
    timeout: float,
) -> tuple[str, str]:
    cache_key = " ".join(player_query.lower().split())
    if cache_key in _PLAYER_QUERY_CACHE:
        return _PLAYER_QUERY_CACHE[cache_key]

    cleaned_query = _clean_player_query(player_query)
    def search(query: str) -> list[dict[str, Any]]:
        response = client.get(
            PLAYER_SEARCH_URL.format(query=quote(query)),
            headers=DEFAULT_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("player") or []

    players = search(cleaned_query)
    if not players and len(cleaned_query.split()) > 1:
        # Cricbuzz search requires close-to-exact spelling.  Broaden to the
        # first name, then accept only a strong full-name similarity so a
        # small typo such as "Colin Muno" can resolve without guessing.
        players = search(cleaned_query.split()[0])
    if not players:
        raise ValueError("Could not resolve that player to a Cricbuzz profile")

    normalized_query = re.sub(r"[^a-z0-9]", "", cleaned_query.lower())
    scored_players = [
        (
            SequenceMatcher(
                None,
                normalized_query,
                re.sub(r"[^a-z0-9]", "", str(item.get("name") or "").lower()),
            ).ratio(),
            item,
        )
        for item in players
    ]
    similarity, player = max(
        scored_players,
        key=lambda candidate: candidate[0],
    )
    if similarity < 0.85:
        raise ValueError("Could not confidently resolve that player")
    player_name = str(player.get("name") or cleaned_query)
    player_slug = re.sub(r"[^a-z0-9]+", "-", player_name.lower()).strip("-")
    result = (str(player["id"]), player_slug)
    _PLAYER_QUERY_CACHE[cache_key] = result
    return result


def get_player_t20_profile(
    player_query: str,
    client: Any = requests,
    timeout: float = 10,
) -> dict[str, Any]:
    """Resolve any player name/question and return Cricbuzz's T20 columns."""
    player_id, player_slug = _resolve_player(player_query, client, timeout)
    profile = _get_profile(player_id, player_slug, client, timeout)
    player = profile.get("playerData") or {}
    return {
        "player_id": int(player_id),
        "name": player.get("name") or player_slug.replace("-", " ").title(),
        "role": player.get("role"),
        "date_of_birth": player.get("DoB"),
        "batting_style": player.get("bat"),
        "bowling_style": player.get("bowl"),
        "teams": player.get("teams"),
        "rankings": player.get("rankings") or {},
        "batting": _format_column(profile.get("battingStats") or {}, "T20"),
        "bowling": _format_column(profile.get("bowlingStats") or {}, "T20"),
    }


def get_t20_bowling_stats(
    player_id: int | str,
    player_name: str,
    client: Any = requests,
    timeout: float = 10,
) -> dict[str, Any] | None:
    """Return cached career T20 bowling stats for one scorecard player."""
    cache_key = str(player_id)
    if cache_key in _T20_BOWLING_CACHE:
        return _T20_BOWLING_CACHE[cache_key]

    slug = re.sub(r"[^a-z0-9]+", "-", player_name.lower()).strip("-")
    profile = _get_profile(player_id, slug, client, timeout)
    result = _t20_column(profile)

    _T20_BOWLING_CACHE[cache_key] = result
    return result
