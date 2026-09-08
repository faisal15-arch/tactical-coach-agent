"""Fetch and parse current matches from Cricbuzz's live-scores page."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests


LIVE_SCORES_URL = "https://www.cricbuzz.com/cricket-match/live-scores"
MATCH_SQUADS_URL = "https://m.cricbuzz.com/cricket-match-squads/{match_id}/match"
MATCH_SCORECARD_URL = "https://m.cricbuzz.com/live-cricket-scorecard/{match_id}/match"
MATCH_OVERS_API_URL = "https://m.cricbuzz.com/api/mcenter/over-by-over/{match_id}/{innings_id}"
MATCH_OVER_DETAIL_URL = "https://m.cricbuzz.com/api/mcenter/over-detail/{match_id}/{innings_id}/{timestamp}"
VENUE_PAGE_URL = "https://m.cricbuzz.com/cricket-series/{series_id}/series/venues/{venue_id}/venue"
_OVER_HISTORY_CACHE: dict[tuple[str, int], tuple[float, list[dict[str, Any]]]] = {}
_OVER_DETAIL_CACHE: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
_VENUE_AVERAGE_CACHE: dict[tuple[str, str], int | None] = {}
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def _next_payload_chunks(page: str) -> list[str]:
    """Decode the string chunks emitted by Next.js into the HTML."""
    chunks = []
    pattern = re.compile(r"self\.__next_f\.push\((\[1,\s*\"(?:\\.|[^\"\\])*\"\])\)")
    for match in pattern.finditer(page):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if len(value) == 2 and isinstance(value[1], str):
            chunks.append(value[1])
    return chunks


def _extract_current_matches(page: str) -> list[dict[str, Any]]:
    marker = '"currentMatchesList":'

    for chunk in _next_payload_chunks(page):
        marker_index = chunk.find(marker)
        if marker_index == -1:
            continue

        value_start = marker_index + len(marker)
        try:
            current_matches, _ = json.JSONDecoder().raw_decode(chunk[value_start:])
        except json.JSONDecodeError:
            continue

        matches = []
        for type_group in current_matches.get("typeMatches", []):
            match_type = type_group.get("matchType")
            for series_group in type_group.get("seriesMatches", []):
                series = series_group.get("seriesAdWrapper") or {}
                for raw_match in series.get("matches", []):
                    match_info = raw_match.get("matchInfo") or {}
                    if not match_info:
                        continue
                    matches.append(
                        _normalise_match(
                            match_info,
                            raw_match.get("matchScore") or {},
                            match_type,
                            series,
                        )
                    )
        return matches

    raise ValueError("Cricbuzz match data was not present in the page")


def _normalise_match(
    info: dict[str, Any],
    score: dict[str, Any],
    match_type: str | None,
    series: dict[str, Any],
) -> dict[str, Any]:
    match_id = info.get("matchId")
    venue = info.get("venueInfo") or {}
    return {
        "match_id": match_id,
        "series_id": info.get("seriesId", series.get("seriesId")),
        "series_name": info.get("seriesName", series.get("seriesName")),
        "match_type": match_type,
        "description": info.get("matchDesc"),
        "format": info.get("matchFormat"),
        "state": info.get("state"),
        "status": info.get("status"),
        "start_time_ms": _as_int(info.get("startDate")),
        "end_time_ms": _as_int(info.get("endDate")),
        "team1": _normalise_team(info.get("team1"), score.get("team1Score")),
        "team2": _normalise_team(info.get("team2"), score.get("team2Score")),
        "venue": {
            "ground": venue.get("ground"),
            "city": venue.get("city"),
            "timezone": venue.get("timezone"),
        },
        "url": f"https://www.cricbuzz.com/live-cricket-scores/{match_id}" if match_id else None,
    }


def _normalise_team(team: Any, team_score: Any) -> dict[str, Any]:
    team = team if isinstance(team, dict) else {}
    team_score = team_score if isinstance(team_score, dict) else {}
    innings = []
    for innings_key, value in team_score.items():
        if not isinstance(value, dict):
            continue
        innings.append(
            {
                "innings": innings_key,
                "innings_id": value.get("inningsId"),
                "runs": value.get("runs"),
                "wickets": value.get("wickets"),
                "overs": value.get("overs"),
            }
        )
    return {
        "team_id": team.get("teamId"),
        "name": team.get("teamName"),
        "short_name": team.get("teamSName"),
        "innings": innings,
    }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_squad(raw_team: dict[str, Any]) -> dict[str, Any]:
    team = raw_team.get("team") or {}
    groups = raw_team.get("players") or {}
    playing_xi = groups.get("playing XI") or []

    return {
        "team_id": team.get("teamId"),
        "name": team.get("teamName"),
        "short_name": team.get("teamSName"),
        "playing_xi": [
            {
                "player_id": player.get("id"),
                "name": player.get("fullName") or player.get("name"),
                "role": player.get("role"),
                "captain": bool(player.get("captain")),
                "keeper": bool(player.get("keeper")),
            }
            for player in playing_xi
            if isinstance(player, dict)
        ],
    }


def _extract_squads(page: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()

    for chunk in _next_payload_chunks(page):
        team1_marker = '"team1":'
        team1_start = chunk.find(team1_marker)
        if team1_start == -1:
            continue

        try:
            team1, team1_end = decoder.raw_decode(
                chunk[team1_start + len(team1_marker):]
            )
        except json.JSONDecodeError:
            continue

        if not isinstance(team1, dict) or "players" not in team1:
            continue

        search_from = team1_start + len(team1_marker) + team1_end
        team2_marker = '"team2":'
        team2_start = chunk.find(team2_marker, search_from)
        if team2_start == -1:
            continue

        try:
            team2, _ = decoder.raw_decode(
                chunk[team2_start + len(team2_marker):]
            )
        except json.JSONDecodeError:
            continue

        if isinstance(team2, dict) and "players" in team2:
            return [_normalise_squad(team1), _normalise_squad(team2)]

    raise ValueError("Cricbuzz squad data was not present in the page")


def _extract_scorecard(page: str) -> dict[str, Any]:
    marker = '"scorecardApiData":'

    for chunk in _next_payload_chunks(page):
        marker_index = chunk.find(marker)
        if marker_index == -1:
            continue

        try:
            scorecard, _ = json.JSONDecoder().raw_decode(
                chunk[marker_index + len(marker):]
            )
        except json.JSONDecodeError:
            continue

        if isinstance(scorecard, dict) and scorecard.get("scoreCard"):
            return scorecard

    raise ValueError("Cricbuzz scorecard data was not present in the page")


def _overs_limit(match_format: str | None) -> int | None:
    value = (match_format or "").upper()
    if "T20" in value:
        return 20
    if value in ("ODI", "OD", "LISTA"):
        return 50
    return None


def _get_venue_t20_average(
    series_id: int | str | None,
    venue_id: int | str | None,
    client: Any,
    timeout: float,
) -> int | None:
    if not series_id or not venue_id:
        return None

    cache_key = (str(series_id), str(venue_id))
    if cache_key in _VENUE_AVERAGE_CACHE:
        return _VENUE_AVERAGE_CACHE[cache_key]

    response = client.get(
        VENUE_PAGE_URL.format(series_id=series_id, venue_id=venue_id),
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()

    average = None
    for chunk in _next_payload_chunks(response.text):
        section_start = chunk.find("STATS - T20")
        if section_start == -1:
            continue
        section = chunk[section_start:section_start + 12000]
        label_start = section.find("Average 1st innings Score")
        if label_start == -1:
            continue
        match = re.search(
            r'Average 1st innings Score.{0,300}?children[^0-9]*(\d+)',
            section[label_start:],
            re.DOTALL,
        )
        if match:
            average = int(match.group(1))
            break

    _VENUE_AVERAGE_CACHE[cache_key] = average
    return average


def _normalise_live_situation(scorecard: dict[str, Any]) -> dict[str, Any]:
    innings_list = scorecard.get("scoreCard") or []
    current = innings_list[-1]
    header = scorecard.get("matchHeader") or {}
    score = current.get("scoreDetails") or {}
    batting = current.get("batTeamDetails") or {}
    bowling = current.get("bowlTeamDetails") or {}
    match_format = header.get("matchFormat")
    overs_limit = _overs_limit(match_format)

    toss = header.get("toss") or header.get("tossInfo") or {}
    toss_winner = toss.get("winner") or toss.get("tossWinner")
    toss_decision = toss.get("decision") or toss.get("tossDecision")
    status = str(header.get("status") or scorecard.get("status") or "")
    if not toss_winner:
        toss_match = re.search(
            r"^(.+?)\s+opt(?:ed)?\s+to\s+(bat|bowl)\b",
            status,
            re.IGNORECASE,
        )
        if toss_match:
            toss_winner = toss_match.group(1).strip()
            toss_decision = toss_decision or toss_match.group(2).lower()

    player_of_match = []
    award_keys = {
        "playerofthematch",
        "playerofmatch",
        "manofthematch",
    }

    def collect_award_names(value: Any, key: str = "") -> None:
        normalized_key = re.sub(r"[^a-z]", "", key.lower())
        if normalized_key in award_keys:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str) and item.strip():
                    player_of_match.append(item.strip())
                elif isinstance(item, dict):
                    name = (
                        item.get("name")
                        or item.get("playerName")
                        or item.get("batName")
                    )
                    if name:
                        player_of_match.append(str(name).strip())
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect_award_names(child_value, str(child_key))
        elif isinstance(value, list):
            for child_value in value:
                collect_award_names(child_value, key)

    collect_award_names(scorecard)
    player_of_match = list(dict.fromkeys(player_of_match))

    innings_scores = []
    for innings in innings_list:
        innings_score = innings.get("scoreDetails") or {}
        innings_batting = innings.get("batTeamDetails") or {}
        innings_runs = int(innings_score.get("runs") or 0)
        innings_wickets = int(innings_score.get("wickets") or 0)
        innings_scores.append({
            "innings_id": innings.get("inningsId"),
            "batting_team": innings_batting.get("batTeamName"),
            "runs": innings_runs,
            "wickets": innings_wickets,
            "score": f"{innings_runs}/{innings_wickets}",
            "over": str(innings_score.get("overs") or 0),
            "run_rate": float(innings_score.get("runRate") or 0),
        })

    active_batters = []
    batting_card = []
    for batter in (batting.get("batsmenData") or {}).values():
        batting_card.append({
            "batter": batter.get("batName"),
            "runs": batter.get("runs", 0),
            "balls": batter.get("balls", 0),
            "fours": batter.get("fours", 0),
            "sixes": batter.get("sixes", 0),
            "strike_rate": float(batter.get("strikeRate") or 0),
            "status": batter.get("outDesc") or "",
        })
        if str(batter.get("outDesc") or "").lower() == "batting":
            active_batters.append({
                "name": batter.get("batName"),
                "runs": batter.get("runs", 0),
                "balls": batter.get("balls", 0),
                "fours": batter.get("fours", 0),
                "sixes": batter.get("sixes", 0),
                "strike_rate": batter.get("strikeRate", 0),
            })

    bowler_ids = {}
    for innings in innings_list:
        innings_bowlers = (
            (innings.get("bowlTeamDetails") or {}).get("bowlersData") or {}
        )
        for bowler in innings_bowlers.values():
            if bowler.get("bowlName") and bowler.get("bowlerId"):
                bowler_ids[bowler["bowlName"]] = bowler["bowlerId"]

    bowling_card = []
    quota = 4 if overs_limit == 20 else 10 if overs_limit == 50 else None
    for bowler in (bowling.get("bowlersData") or {}).values():
        balls = int(bowler.get("balls") or 0)
        overs = bowler.get("overs", f"{balls // 6}.{balls % 6}")
        overs_used = balls / 6
        bowling_card.append({
            "bowler": bowler.get("bowlName"),
            "bowler_id": bowler.get("bowlerId"),
            "overs": str(overs),
            "balls": balls,
            "maidens": bowler.get("maidens", 0),
            "runs": bowler.get("runs", 0),
            "wickets": bowler.get("wickets", 0),
            "economy": float(bowler.get("economy") or 0),
            "wides": bowler.get("wides", 0),
            "no_balls": bowler.get("no_balls", 0),
            "overs_left": max(round(quota - overs_used, 1), 0) if quota else None,
            "quota_used": bool(quota and overs_used >= quota),
            "rated": True,
            "provisional": False,
            "is_bowling": False,
        })

    runs = int(score.get("runs") or 0)
    wickets = int(score.get("wickets") or 0)
    over = score.get("overs", 0)
    target = None
    chase = None

    if overs_limit and len(innings_list) > 1:
        previous_score = innings_list[-2].get("scoreDetails") or {}
        target = int(previous_score.get("runs") or 0) + 1
        completed_balls = int(score.get("ballNbr") or 0)
        balls_remaining = max(overs_limit * 6 - completed_balls, 0)
        runs_needed = max(target - runs, 0)
        chase = {
            "target": target,
            "runs_needed": runs_needed,
            "balls_remaining": balls_remaining,
            "required_run_rate": round(
                runs_needed / max(balls_remaining / 6, 0.1), 2
            ),
            "achieved": runs_needed == 0,
        }

    venue = header.get("venue") or {}
    return {
        "match_id": header.get("matchId"),
        "format": match_format,
        "match_status": status,
        "toss": {
            "winner": toss_winner,
            "decision": toss_decision,
        } if toss_winner else None,
        "player_of_match": player_of_match,
        "state": header.get("state"),
        "series_name": header.get("seriesName") or header.get("seriesDesc"),
        "series_id": header.get("seriesId"),
        "venue_id": venue.get("id"),
        "venue": venue.get("name"),
        "city": venue.get("city"),
        "innings_id": current.get("inningsId"),
        "current_phase": "first" if len(innings_list) == 1 else "chase",
        "batting_team": batting.get("batTeamName"),
        "bowling_team": bowling.get("bowlTeamName"),
        "runs": runs,
        "wickets": wickets,
        "score": f"{runs}/{wickets}",
        "over": str(over),
        "current_over": str(over),
        "run_rate": float(score.get("runRate") or 0),
        "batsmen": {
            "striker": active_batters[0]["name"] if active_batters else None,
            "non_striker": active_batters[1]["name"] if len(active_batters) > 1 else None,
            "on_strike_next_ball": active_batters[0]["name"] if active_batters else None,
        },
        "current_batsmen": active_batters,
        "batting_card": batting_card,
        "target": target,
        "chase": chase,
        "bowling_card": bowling_card,
        "bowler_ids": bowler_ids,
        "total_overs": overs_limit,
        "innings_scores": innings_scores,
    }


def _get_over_history(
    match_id: int | str,
    innings_id: int,
    client: Any,
    timeout: float,
) -> list[dict[str, Any]]:
    cache_key = (str(match_id), innings_id)
    cached = _OVER_HISTORY_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 30:
        return list(cached[1])

    url = MATCH_OVERS_API_URL.format(
        match_id=match_id,
        innings_id=innings_id,
    )
    summaries = []
    seen_timestamps = set()

    for _ in range(12):
        response = client.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        response.raise_for_status()
        if not response.content.strip():
            break
        try:
            page = response.json().get("paginatedData") or []
        except requests.exceptions.JSONDecodeError:
            break
        new_rows = [
            row for row in page
            if row.get("timestamp") not in seen_timestamps
            and int(row.get("inningsId") or 0) == innings_id
        ]
        summaries.extend(new_rows)
        seen_timestamps.update(row.get("timestamp") for row in new_rows)

        if not page or not new_rows:
            break

        oldest = page[-1]
        url = "/".join((
            MATCH_OVERS_API_URL.format(
                match_id=match_id,
                innings_id=innings_id,
            ),
            str(oldest.get("timestamp")),
        ))

    summaries.sort(key=lambda row: float(row.get("overs") or 0))
    _OVER_HISTORY_CACHE[cache_key] = (time.time(), summaries)
    return list(summaries)


def _get_over_detail(
    match_id: int | str,
    innings_id: int,
    timestamp: Any,
    client: Any,
    timeout: float,
) -> list[dict[str, Any]]:
    cache_key = (str(match_id), innings_id, str(timestamp))
    if cache_key in _OVER_DETAIL_CACHE:
        return list(_OVER_DETAIL_CACHE[cache_key])

    response = client.get(
        MATCH_OVER_DETAIL_URL.format(
            match_id=match_id,
            innings_id=innings_id,
            timestamp=timestamp,
        ),
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    commentary = response.json().get("commentaryList") or []
    _OVER_DETAIL_CACHE[cache_key] = commentary
    return list(commentary)


def _live_matchups(
    history: list[dict[str, Any]],
    active_batters: list[str],
    max_ball: int,
    innings_id: int,
    match_id: int | str,
    client: Any,
    timeout: float,
) -> dict[str, dict[str, dict[str, int]]]:
    matchups = {batter: {} for batter in active_batters}
    if not active_batters:
        return matchups

    relevant_rows = [
        row for row in history
        if row.get("timestamp")
        and set(row.get("batStrikerNames") or []).intersection(active_batters)
        and int(float(row.get("overs") or 0)) * 6 <= max_ball
    ]
    for row in relevant_rows:
        commentary = _get_over_detail(
            match_id,
            innings_id,
            row["timestamp"],
            client,
            timeout,
        )
        for delivery in commentary:
            if int(delivery.get("inningsId") or 0) != innings_id:
                continue
            if int(delivery.get("ballNbr") or 0) > max_ball:
                continue
            batter = (delivery.get("batsmanStriker") or {}).get("batName")
            bowler = (delivery.get("bowlerStriker") or {}).get("bowlName")
            if batter not in matchups or not bowler:
                continue
            figures = matchups[batter].setdefault(
                bowler,
                {"balls": 0, "runs": 0, "dismissals": 0},
            )
            event = str(delivery.get("event") or "").upper()
            if "WIDE" not in event and "NO_BALL" not in event and "NOBALL" not in event:
                figures["balls"] += 1
            figures["runs"] += int(delivery.get("legalRuns") or 0)
            if "WICKET" in event:
                figures["dismissals"] += 1

    return matchups


def _batting_card_at_over(
    history: list[dict[str, Any]],
    active_batters: list[str],
    max_ball: int,
    innings_id: int,
    match_id: int | str,
    client: Any,
    timeout: float,
) -> list[dict[str, Any]]:
    relevant_rows = [
        row for row in history
        if row.get("timestamp")
        and int(float(row.get("overs") or 0)) * 6 <= max_ball
    ]

    with ThreadPoolExecutor(max_workers=min(6, max(len(relevant_rows), 1))) as pool:
        futures = [
            pool.submit(
                _get_over_detail,
                match_id,
                innings_id,
                row["timestamp"],
                client,
                timeout,
            )
            for row in relevant_rows
        ]
        commentary_groups = [future.result() for future in futures]

    deliveries = sorted(
        (
            delivery
            for commentary in commentary_groups
            for delivery in commentary
            if int(delivery.get("inningsId") or 0) == innings_id
            and int(delivery.get("ballNbr") or 0) <= max_ball
        ),
        key=lambda delivery: int(delivery.get("ballNbr") or 0),
    )
    figures_by_batter = {}
    cumulative_by_batter = {}
    batting_order = []
    for delivery in deliveries:
        batter = delivery.get("batsmanStriker") or {}
        name = batter.get("batName")
        if not name:
            continue
        if name not in batting_order:
            batting_order.append(name)
        figures = figures_by_batter.setdefault(
            name,
            {"runs": 0, "balls": 0, "fours": 0, "sixes": 0},
        )
        cumulative_by_batter[name] = {
            "runs": int(batter.get("batRuns") or 0),
            "balls": int(batter.get("batBalls") or 0),
        }
        event = str(delivery.get("event") or "").upper()
        batter_runs = int(delivery.get("legalRuns") or 0)
        figures["runs"] += batter_runs
        if "WIDE" not in event and "NO_BALL" not in event and "NOBALL" not in event:
            figures["balls"] += 1
        figures["fours"] += int(batter_runs == 4 and "FOUR" in event)
        figures["sixes"] += int(batter_runs == 6 and "SIX" in event)

    for name in active_batters:
        if name and name not in batting_order:
            batting_order.append(name)
            figures_by_batter[name] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}

    card = []
    for name in batting_order:
        figures = figures_by_batter[name]
        cumulative = cumulative_by_batter.get(name)
        if cumulative:
            figures["runs"] = cumulative["runs"]
            figures["balls"] = cumulative["balls"]
        balls = figures["balls"]
        card.append({
            "batter": name,
            **figures,
            "strike_rate": round(figures["runs"] / max(balls, 1) * 100, 2),
            "status": "not out" if name in active_batters else "out",
        })
    return card


def _partial_over_batter_names(
    previous_names: list[str],
    deliveries: list[dict[str, Any]],
) -> list[str]:
    """Keep both active batters and put the next-ball striker first."""
    active = list(dict.fromkeys(name for name in previous_names if name))[-2:]
    next_striker = active[0] if active else None

    for delivery in deliveries:
        striker = (delivery.get("batsmanStriker") or {}).get("batName")
        if not striker:
            continue
        if striker not in active:
            active.append(striker)
            active = active[-2:]

        other = next((name for name in active if name != striker), None)
        if "WICKET" in str(delivery.get("event") or "").upper():
            active = [name for name in active if name != striker]
            next_striker = other
            continue

        runs = int(delivery.get("totalRuns") or 0)
        next_striker = other if runs % 2 and other else striker

    if next_striker in active:
        return [next_striker] + [name for name in active if name != next_striker]
    return active


def _situation_at_over(
    current: dict[str, Any],
    history: list[dict[str, Any]],
    requested_over: str,
    innings_id: int,
    match_id: int | str,
    client: Any,
    timeout: float,
) -> dict[str, Any]:
    try:
        wanted_over = int(float(requested_over))
    except (TypeError, ValueError):
        raise ValueError("Over must be a number") from None

    over_parts = str(requested_over).split(".", 1)
    requested_ball = int(over_parts[1]) if len(over_parts) > 1 else 0
    if requested_ball < 0 or requested_ball > 6:
        raise ValueError("A cricket over can only contain balls 0 to 6")

    available = [
        row for row in history
        if int(float(row.get("overs") or 0)) <= wanted_over
    ]
    if not available:
        raise ValueError(f"Over {requested_over} is not available for this innings")

    selected = available[-1]
    selected_commentary = _get_over_detail(
        match_id,
        innings_id,
        selected.get("timestamp"),
        client,
        timeout,
    )
    bowling_by_name = {}
    for row in available:
        names = row.get("bowlNames") or []
        if not names:
            continue
        name = names[-1]
        balls = int(round(float(row.get("bowlOvers") or 0) * 6))
        overs_text = str(row.get("bowlOvers") or 0)
        if "." in overs_text:
            whole, partial = overs_text.split(".", 1)
            balls = int(whole) * 6 + int(partial[:1] or 0)
        bowling_by_name[name] = {
            "bowler": name,
            "bowler_id": (current.get("bowler_ids") or {}).get(name),
            "overs": overs_text,
            "balls": balls,
            "maidens": row.get("bowlMaidens", 0),
            "runs": row.get("bowlRuns", 0),
            "wickets": row.get("bowlWickets", 0),
            "economy": round(
                float(row.get("bowlRuns") or 0) / max(balls / 6, 0.1), 2
            ),
        }

    quota = 4 if current.get("total_overs") == 20 else 10 if current.get("total_overs") == 50 else None
    bowling_card = []
    for figures in bowling_by_name.values():
        figures["overs_left"] = (
            max(round(quota - figures["balls"] / 6, 1), 0) if quota else None
        )
        figures["quota_used"] = bool(quota and figures["balls"] >= quota * 6)
        figures["rated"] = True
        figures["provisional"] = False
        figures["is_bowling"] = False
        bowling_card.append(figures)

    batter_names = []
    if selected_commentary:
        separator = selected_commentary[0].get("overSeparator") or {}
        batter_names = [
            name for name in (
                *((separator.get("batStrikerNames") or [])[-1:]),
                *((separator.get("batNonStrikerNames") or [])[-1:]),
            )
            if name
        ]
    if not batter_names:
        batter_names = list(dict.fromkeys(selected.get("batStrikerNames") or []))[-2:]

    score = int(selected.get("score") or 0)
    wickets = int(selected.get("wickets") or 0)
    over = str(int(float(selected.get("overs") or wanted_over)))
    result = {
        **current,
        "innings_id": innings_id,
        "current_phase": "first" if innings_id == 1 else "chase",
        "batting_team": selected.get("batTeamName"),
        "runs": score,
        "wickets": wickets,
        "score": f"{score}/{wickets}",
        "over": over,
        "current_over": over,
        "run_rate": round(score / max(float(over), 0.1), 2),
        "batsmen": {
            "striker": batter_names[0] if batter_names else None,
            "non_striker": batter_names[1] if len(batter_names) > 1 else None,
            "on_strike_next_ball": batter_names[0] if batter_names else None,
        },
        "current_batsmen": [{"name": name} for name in batter_names],
        "bowling_card": bowling_card,
        "recent_over_runs": [row.get("runs", 0) for row in available[-3:]],
        "current_bowler": (selected.get("bowlNames") or [None])[-1],
        "historical_point": True,
    }

    if requested_ball:
        previous_batter_names = list(batter_names)
        next_over = next(
            (
                row for row in history
                if int(float(row.get("overs") or 0)) == wanted_over + 1
            ),
            None,
        )
        if next_over is None:
            raise ValueError(
                f"Ball {requested_over} is not available for this innings"
            )

        commentary = _get_over_detail(
            match_id,
            innings_id,
            next_over.get("timestamp"),
            client,
            timeout,
        )
        deliveries = sorted(
            [
                delivery for delivery in commentary
                if int(float(delivery.get("overNumber") or -1)) == wanted_over
                and float(delivery.get("overNumber") or 0) <= float(requested_over)
            ],
            key=lambda delivery: float(delivery.get("overNumber") or 0),
        )
        if not deliveries:
            raise ValueError(
                f"Ball {requested_over} is not available for this innings"
            )

        delivery = deliveries[-1]
        result["runs"] = int(delivery.get("batTeamScore") or result["runs"])
        result["wickets"] += sum(
            1 for item in deliveries if item.get("event") == "WICKET"
        )
        result["score"] = f"{result['runs']}/{result['wickets']}"
        result["over"] = str(requested_over)
        result["current_over"] = str(requested_over)
        balls_played = wanted_over * 6 + requested_ball
        result["run_rate"] = round(
            result["runs"] / max(balls_played / 6, 0.1), 2
        )

        bowler = delivery.get("bowlerStriker") or {}
        bowler_name = bowler.get("bowlName")
        if bowler_name:
            result["current_bowler"] = bowler_name
            figures = next(
                (
                    row for row in result["bowling_card"]
                    if row.get("bowler") == bowler_name
                ),
                None,
            )
            if figures is None:
                figures = {
                    "bowler": bowler_name,
                    "bowler_id": (
                        bowler.get("bowlerId")
                        or bowler.get("bowlId")
                        or (current.get("bowler_ids") or {}).get(bowler_name)
                    ),
                }
                result["bowling_card"].append(figures)
            bowler_overs = str(bowler.get("bowlOvs") or 0)
            bowler_parts = bowler_overs.split(".", 1)
            bowler_balls = int(bowler_parts[0]) * 6 + (
                int(bowler_parts[1][:1]) if len(bowler_parts) > 1 else 0
            )
            figures.update({
                "overs": bowler_overs,
                "balls": bowler_balls,
                "maidens": bowler.get("bowlMaidens", 0),
                "runs": bowler.get("bowlRuns", 0),
                "wickets": bowler.get("bowlWkts", 0),
                "economy": float(bowler.get("bowlEcon") or 0),
                "overs_left": max(round(quota - bowler_balls / 6, 1), 0) if quota else None,
                "quota_used": bool(quota and bowler_balls >= quota * 6),
                "rated": True,
                "provisional": False,
                "is_bowling": True,
            })

        batter_names = _partial_over_batter_names(
            previous_batter_names,
            deliveries,
        )
        result["batsmen"] = {
            "striker": batter_names[0] if batter_names else None,
            "non_striker": batter_names[1] if len(batter_names) > 1 else None,
            "on_strike_next_ball": batter_names[0] if batter_names else None,
        }
        result["current_batsmen"] = [
            {"name": name} for name in batter_names
        ]
        partial_runs = result["runs"] - int(selected.get("score") or 0)
        result["recent_over_runs"] = (
            [row.get("runs", 0) for row in available[-2:]] + [partial_runs]
        )

    displayed = str(result["over"]).split(".", 1)
    max_ball = int(displayed[0]) * 6 + (
        int(displayed[1]) if len(displayed) > 1 else 0
    )
    result["batting_card"] = _batting_card_at_over(
        history,
        [row["name"] for row in result["current_batsmen"]],
        max_ball,
        innings_id,
        match_id,
        client,
        timeout,
    )
    result["matchup_stats"] = _live_matchups(
        history,
        [row["batter"] for row in result["batting_card"]],
        max_ball,
        innings_id,
        match_id,
        client,
        timeout,
    )

    team_info = current.get("match_team_info") or []
    if len(team_info) >= innings_id:
        result["bowling_team"] = team_info[innings_id - 1].get("bowlingTeamShortName")

    if innings_id == 2 and current.get("target"):
        target = current["target"]
        displayed = str(result["over"]).split(".", 1)
        completed_balls = int(displayed[0]) * 6 + (
            int(displayed[1]) if len(displayed) > 1 else 0
        )
        balls_remaining = max(
            (current.get("total_overs") or 0) * 6 - completed_balls,
            0,
        )
        runs_needed = max(target - score, 0)
        result["chase"] = {
            "target": target,
            "runs_needed": runs_needed,
            "balls_remaining": balls_remaining,
            "required_run_rate": round(
                runs_needed / max(balls_remaining / 6, 0.1), 2
            ),
            "achieved": runs_needed == 0,
        }
    else:
        result["chase"] = None

    return result


def get_live_matches(
    match_id: int | str | None = None,
    *,
    timeout: float = 15,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Return current Cricbuzz matches in a JSON-serializable dictionary.

    If ``match_id`` is supplied, the result contains only that match. A missing
    ID is reported with ``status='not_found'`` rather than raising an exception.
    """
    client = session or requests
    try:
        response = client.get(
            LIVE_SCORES_URL,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        matches = _extract_current_matches(response.text)
    except (requests.RequestException, ValueError) as exc:
        return {
            "status": "error",
            "message": "Unable to retrieve live matches from Cricbuzz",
            "matches": [],
            "error": str(exc),
        }

    matches = [
        match for match in matches
        if "T20" in str(match.get("format") or "").upper()
    ]

    if match_id is not None:
        wanted_id = str(match_id)
        matches = [match for match in matches if str(match.get("match_id")) == wanted_id]
        if not matches:
            return {
                "status": "not_found",
                "message": f"Match {match_id} was not found",
                "matches": [],
            }

    if not matches:
        return {
            "status": "no_matches",
            "message": "There are no current matches",
            "matches": [],
        }

    return {
        "status": "ok",
        "count": len(matches),
        "matches": matches,
    }


def get_live_matches_json(match_id: int | str | None = None, **kwargs: Any) -> str:
    """Return :func:`get_live_matches` as a JSON string."""
    return json.dumps(get_live_matches(match_id, **kwargs), ensure_ascii=False)


def get_match_details(
    match_id: int | str,
    *,
    timeout: float = 15,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Return a live-match summary together with both playing XIs."""
    client = session or requests
    match_result = get_live_matches(match_id, timeout=timeout, session=session)

    if match_result.get("status") != "ok":
        return match_result

    try:
        response = client.get(
            MATCH_SQUADS_URL.format(match_id=match_id),
            headers=DEFAULT_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        lineups = _extract_squads(response.text)
    except (requests.RequestException, ValueError) as exc:
        return {
            "status": "error",
            "message": "Unable to retrieve match lineups from Cricbuzz",
            "error": str(exc),
        }

    return {
        "status": "ok",
        "match": match_result["matches"][0],
        "lineups": lineups,
    }


def get_live_situation(
    match_id: int | str,
    *,
    over: str | None = None,
    phase: str = "chase",
    timeout: float = 15,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Return the latest score, batters, and bowling figures for a match."""
    client = session or requests

    try:
        response = client.get(
            MATCH_SCORECARD_URL.format(match_id=match_id),
            headers=DEFAULT_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        scorecard = _extract_scorecard(response.text)
        situation = _normalise_live_situation(scorecard)
        situation["match_team_info"] = (
            scorecard.get("matchHeader", {}).get("matchTeamInfo") or []
        )

        if over is not None:
            innings_id = 1 if phase == "first" else 2
            history = _get_over_history(match_id, innings_id, client, timeout)
            situation = _situation_at_over(
                situation,
                history,
                str(over),
                innings_id,
                match_id,
                client,
                timeout,
            )
        else:
            try:
                history = _get_over_history(
                    match_id,
                    int(situation.get("innings_id") or 1),
                    client,
                    timeout,
                )
                current_over = int(float(situation.get("over") or 0))
                completed = [
                    row for row in history
                    if int(float(row.get("overs") or 0)) <= current_over
                ]
                situation["recent_over_runs"] = [
                    row.get("runs", 0) for row in completed[-3:]
                ]
                if completed:
                    situation["current_bowler"] = (
                        completed[-1].get("bowlNames") or [None]
                    )[-1]
                displayed = str(situation.get("over") or 0).split(".", 1)
                max_ball = int(displayed[0]) * 6 + (
                    int(displayed[1]) if len(displayed) > 1 else 0
                )
                situation["matchup_stats"] = _live_matchups(
                    history,
                    [
                        batter.get("name")
                        for batter in situation.get("current_batsmen", [])
                        if batter.get("name")
                    ],
                    max_ball,
                    int(situation.get("innings_id") or 1),
                    match_id,
                    client,
                    timeout,
                )
            except (requests.RequestException, ValueError, KeyError):
                situation["recent_over_runs"] = []

        if situation.get("format") == "T20":
            try:
                situation["venue_t20_average"] = _get_venue_t20_average(
                    situation.get("series_id"),
                    situation.get("venue_id"),
                    client,
                    timeout,
                )
            except requests.RequestException:
                situation["venue_t20_average"] = None
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "error": str(exc),
        }
    except (requests.RequestException, KeyError) as exc:
        return {
            "status": "error",
            "message": "Unable to retrieve the live scorecard from Cricbuzz",
            "error": str(exc),
        }

    return {"status": "ok", **situation}
