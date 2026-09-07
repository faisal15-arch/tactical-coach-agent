"""Resolve and fetch recorded T20 batter-versus-bowler matchup statistics."""

from __future__ import annotations

import html
import time
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urljoin

import requests


BASE_URL = "https://www.cricmetric.com"
SEARCH_URL = f"{BASE_URL}/jscripts/search2.py"
MATCHUP_URL = f"{BASE_URL}/matchup.py"
PLAYER_STATS_URL = f"{BASE_URL}/playerstats.py"
DEFAULT_HEADERS = {
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_MATCHUP_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_TYPE_MATCHUP_CACHE: dict[str, dict[str, Any]] = {}
_PLAYER_RESOLUTION_CACHE: dict[str, tuple[str, str]] = {}

BOWLING_TYPE_GROUPS = {
    "right_arm_off_spin": {
        "label": "Right-arm off spin",
        "styles": ("Right-arm Offbreak",),
    },
    "left_arm_finger_spin": {
        "label": "Left-arm finger spin (orthodox)",
        "styles": ("Left-arm Orthodox",),
    },
    "right_arm_leg_spin": {
        "label": "Right-arm leg spin",
        "styles": ("Right-arm Legbreak",),
    },
    "left_arm_wrist_spin": {
        "label": "Left-arm wrist spin (chinaman)",
        "styles": ("Left-arm Chinaman",),
    },
    "left_arm_pace": {
        "label": "Left-arm pace",
        "styles": ("Left-arm Fast", "Left-arm Medium"),
    },
    "right_arm_pace": {
        "label": "Right-arm pace",
        "styles": ("Right-arm Fast", "Right-arm Medium"),
    },
}


def _get_with_retry(
    client: Any,
    url: str,
    *,
    timeout: float,
    attempts: int = 2,
    **kwargs,
):
    """Retry intermittent Cricmetric timeouts without hiding final failures."""
    last_error = None
    for attempt in range(attempts):
        try:
            response = client.get(url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.35 * (attempt + 1))
    raise last_error


def _resolve_matchup_player(
    query: str,
    client: Any,
    timeout: float,
) -> tuple[str, str]:
    normalized = " ".join(query.lower().split())
    if normalized in _PLAYER_RESOLUTION_CACHE:
        return _PLAYER_RESOLUTION_CACHE[normalized]

    response = _get_with_retry(
        client,
        SEARCH_URL,
        params={"term": query, "category": "player"},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    candidates = (response.json() or {}).get("results") or []
    if not candidates:
        raise ValueError(f"Could not resolve player: {query}")

    best = max(
        candidates,
        key=lambda row: SequenceMatcher(
            None,
            normalized,
            str(row.get("text") or "").lower(),
        ).ratio(),
    )
    resolved = str(best["id"]), str(best["text"])
    _PLAYER_RESOLUTION_CACHE[normalized] = resolved
    return resolved


def _extract_data_url(page: str) -> str:
    marker = 'var url_string = "'
    if marker not in page:
        raise ValueError("Matchup data URL was not present in the page")
    return html.unescape(page.split(marker, 1)[1].split('"', 1)[0])


def _google_table_row(payload: dict) -> dict[str, Any] | None:
    data = payload.get("data") or {}
    columns = [column.get("id") for column in data.get("cols") or []]
    rows = data.get("rows") or []
    if not columns or not rows:
        return None
    values = [cell.get("v") for cell in rows[0].get("c") or []]
    return dict(zip(columns, values))


def _google_table_rows(payload: dict) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    columns = [column.get("id") for column in data.get("cols") or []]
    parsed = []
    for raw_row in data.get("rows") or []:
        values = [cell.get("v") for cell in raw_row.get("c") or []]
        parsed.append(dict(zip(columns, values)))
    return parsed


def _strength_assessment(
    runs: int,
    balls: int,
    dismissals: int,
) -> tuple[int | None, str, str]:
    """Rate attack and wicket resistance without overstating tiny samples."""
    if balls <= 0:
        return None, "No sample", "none"

    strike_rate = runs / balls * 100
    balls_per_dismissal = balls / dismissals if dismissals else balls
    attack_score = min(max((strike_rate - 90) / 70 * 100, 0), 100)
    survival_score = min(max((balls_per_dismissal - 8) / 22 * 100, 0), 100)
    score = round(0.65 * attack_score + 0.35 * survival_score)

    reliability = "high" if balls >= 100 else "medium" if balls >= 40 else "low"
    if balls < 18:
        return score, "Limited sample", reliability
    if score >= 67:
        return score, "Strong", reliability
    if score <= 40:
        return score, "Weak", reliability
    return score, "Balanced", reliability


def _aggregate_bowling_type_rows(rows: list[dict[str, Any]]) -> dict[str, dict]:
    by_style = {str(row.get("key") or ""): row for row in rows}
    result = {}

    for key, definition in BOWLING_TYPE_GROUPS.items():
        selected = [
            by_style[style]
            for style in definition["styles"]
            if style in by_style
        ]
        runs = round(sum(float(row.get("R") or 0) for row in selected))
        balls = round(sum(float(row.get("B") or 0) for row in selected))
        dismissals = round(sum(float(row.get("Outs") or 0) for row in selected))
        innings = round(sum(float(row.get("I") or 0) for row in selected))
        dot_count = sum(
            float(row.get("B") or 0) * float(row.get("Dots") or 0) / 100
            for row in selected
        )
        score, assessment, reliability = _strength_assessment(
            runs,
            balls,
            dismissals,
        )
        result[key] = {
            "label": definition["label"],
            "styles": list(definition["styles"]),
            "innings": innings,
            "runs": runs,
            "balls": balls,
            "dismissals": dismissals,
            "wickets": dismissals,
            "strike_rate": round(runs / balls * 100, 1) if balls else None,
            "average": round(runs / dismissals, 1) if dismissals else None,
            "balls_per_dismissal": (
                round(balls / dismissals, 1) if dismissals else None
            ),
            "dot_percentage": round(dot_count / balls * 100, 1) if balls else None,
            "strength_score": score,
            "assessment": assessment,
            "sample_reliability": reliability,
        }

    return result


def get_t20_batter_type_stats(
    player_query: str,
    *,
    timeout: float = 20,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    """Return All-T20 batting splits against four tactical bowling groups."""
    cache_key = player_query.lower().strip()
    if cache_key in _TYPE_MATCHUP_CACHE:
        return _TYPE_MATCHUP_CACHE[cache_key]

    client = session or requests.Session()
    player_id, player_name = _resolve_matchup_player(
        player_query,
        client,
        timeout,
    )
    params = {
        "player": player_id,
        "role": "batsman",
        "format": "TWENTY20",
        "groupby": "opp_player_type",
        "start_date": "2005-01-01",
        "end_date": date.today().isoformat(),
        "start_over": "0",
        "end_over": "9999",
    }
    page_response = _get_with_retry(
        client,
        PLAYER_STATS_URL,
        params=params,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    if 'var url_string = "' not in page_response.text:
        if "batting stats in TWENTY20 format" in page_response.text:
            return None
        raise ValueError("Cricmetric player statistics page was incomplete")
    data_path = _extract_data_url(page_response.text)
    data_response = _get_with_retry(
        client,
        urljoin(BASE_URL, data_path),
        headers={**DEFAULT_HEADERS, "Referer": page_response.url},
        timeout=timeout,
    )
    rows = _google_table_rows(data_response.json())
    if not rows:
        return None

    result = {
        "player": player_name,
        "schema_version": 2,
        "scope": "All T20",
        "source": "Cricmetric All T20",
        "categories": _aggregate_bowling_type_rows(rows),
    }
    _TYPE_MATCHUP_CACHE[cache_key] = result
    return result


def get_t20_player_matchup(
    batter_query: str,
    bowler_query: str,
    *,
    timeout: float = 15,
    session: requests.Session | None = None,
) -> dict[str, Any] | None:
    """Return recorded All-T20 head-to-head stats from Cricmetric."""
    cache_key = (batter_query.lower().strip(), bowler_query.lower().strip())
    if cache_key in _MATCHUP_CACHE:
        return _MATCHUP_CACHE[cache_key]

    client = session or requests.Session()
    batter_id, batter_name = _resolve_matchup_player(
        batter_query,
        client,
        timeout,
    )
    bowler_id, bowler_name = _resolve_matchup_player(
        bowler_query,
        client,
        timeout,
    )
    params = {
        "batsman": batter_id,
        "bowler": bowler_id,
        "format": "All_T20",
        "groupby": "batter",
        "matchupFilters": "on",
        "start_date": "2005-01-01",
        "end_date": date.today().isoformat(),
        "start_over": "0",
        "end_over": "9999",
    }
    page_response = client.get(
        MATCHUP_URL,
        params=params,
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    page_response.raise_for_status()
    data_path = _extract_data_url(page_response.text)

    data_headers = {**DEFAULT_HEADERS, "Referer": page_response.url}
    data_response = client.get(
        urljoin(BASE_URL, data_path),
        headers=data_headers,
        timeout=timeout,
    )
    data_response.raise_for_status()
    row = _google_table_row(data_response.json())
    if row is None:
        return None

    result = {
        "batter": batter_name,
        "bowler": bowler_name,
        "runs": int(row.get("runs") or 0),
        "balls": int(row.get("balls") or 0),
        "dismissals": int(row.get("outs") or 0),
        "dots": int(row.get("dots") or 0),
        "fours": int(row.get("4s") or 0),
        "sixes": int(row.get("6s") or 0),
        "strike_rate": float(row.get("sr") or 0),
        "average": (
            float(row["avg"])
            if row.get("avg") is not None else None
        ),
        "source": "Cricmetric All T20",
    }
    _MATCHUP_CACHE[cache_key] = result
    return result
