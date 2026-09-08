"""
Tactical Coach Agent - LangGraph routing layer.

Flow:

User question
    |
    v
classify_intent
    |
    +--> bowling_recommendation
    |       |
    |       v
    |   analytics_tool
    |       |
    |       v
    |   gemini_reasoning
    |
    +--> score_info --> score_node
    |
    +--> follow_up --> follow_up_node
    |
    +--> what_if --> what_if_node
    |
    +--> ranking --> ranking_node
    |
    +--> unknown --> open_question_node

Redis is NOT required.
All conversation state is carried through LangGraph's state.
"""

import glob
import importlib.util
import json
import os
import re
from typing import Any, Optional, Tuple

from langgraph.graph import END, StateGraph

from state import MatchState

from tools.analytics.pressure import calculate_pressure
from tools.analytics.bowler_effectiveness import (
    BOWLER_PROFILES,
    calculate_bowler_effectiveness,
)
from tools.analytics.momentum import calculate_momentum
from tools.analytics.form_score import calculate_form_score

from tools.decision_simulation import simulate_strategies

from llm.gemini_reasoner import (
    answer_open_question,
    generate_followup_answer,
    generate_recommendation,
)


# ============================================================
# CONFIG
# ============================================================

MATCH_FILE = None

PITCH = "neutral"
WEATHER_CONDITION = "clear"
HUMIDITY = 50

MAX_OVERS_PER_BOWLER = 4
TOTAL_OVERS = 20

USE_IN_MATCH_FORM = True

_PAR_PATH = os.path.join(
    os.path.dirname(__file__),
    "tools",
    "analytics",
    "venue_par.json",
)


# ============================================================
# GLOBAL DATA
# ============================================================

_replay_match = None
_snapshot_index = None
_match_info = None
_par_data = None


RATED_BOWLERS = set(BOWLER_PROFILES.keys())


# ============================================================
# REPLAY ENGINE
# ============================================================

def _load_replay_engine():
    global _replay_match

    if _replay_match is not None:
        return _replay_match

    module_names = (
        "replay_engine",
        "tools.replay_engine",
        "tools.historical.replay_engine",
        "core.replay_engine",
    )

    for module_path in module_names:
        try:
            module = __import__(
                module_path,
                fromlist=["replay_match"],
            )

            _replay_match = module.replay_match

            print(
                f"[data] using replay engine from {module_path}"
            )

            return _replay_match

        except (ImportError, AttributeError):
            continue

    for path in glob.glob(
        "**/replay_engine.py",
        recursive=True,
    ):
        if any(
            ignored in path
            for ignored in (
                "venv",
                "site-packages",
                "node_modules",
            )
        ):
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                "replay_engine",
                path,
            )

            if spec is None or spec.loader is None:
                continue

            module = importlib.util.module_from_spec(spec)

            spec.loader.exec_module(module)

            _replay_match = module.replay_match

            print(
                f"[data] using replay engine from file {path}"
            )

            return _replay_match

        except Exception:
            continue

    raise ImportError(
        "replay_engine.py not found. "
        "Please make sure replay_engine.py exists."
    )


# ============================================================
# MATCH FILE
# ============================================================

def _find_match_file() -> str:
    if MATCH_FILE:
        if not os.path.exists(MATCH_FILE):
            raise FileNotFoundError(
                f"MATCH_FILE does not exist: {MATCH_FILE}"
            )

        return MATCH_FILE

    for path in glob.glob(
        "**/*.json",
        recursive=True,
    ):
        if any(
            ignored in path
            for ignored in (
                "venv",
                "node_modules",
                "site-packages",
            )
        ):
            continue

        try:
            with open(
                path,
                "r",
                encoding="utf-8",
            ) as handle:
                data = json.load(handle)

            if (
                isinstance(data, dict)
                and "innings" in data
                and "info" in data
            ):
                return path

        except Exception:
            continue

    raise FileNotFoundError(
        "No Cricsheet JSON file found."
    )


# ============================================================
# DELIVERY ANNOTATION
# ============================================================

def _annotate_deliveries(match_data: dict) -> dict:
    for innings in match_data.get("innings", []):
        for over_data in innings.get("overs", []):
            over_number = over_data.get("over", 0)

            legal_balls = 0

            for delivery in over_data.get(
                "deliveries",
                [],
            ):
                extras = delivery.get(
                    "extras",
                    {},
                )

                if not (
                    "wides" in extras
                    or "noballs" in extras
                ):
                    legal_balls += 1

                delivery["actual_delivery"] = (
                    f"{over_number}.{legal_balls or 1}"
                )

    return match_data


# ============================================================
# SNAPSHOTS
# ============================================================

def _load_snapshots():
    global _snapshot_index
    global _match_info

    if _snapshot_index is not None:
        return _snapshot_index

    replay_match = _load_replay_engine()

    path = _find_match_file()

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as handle:
        match_data = json.load(handle)

    match_data = _annotate_deliveries(match_data)

    snapshots = replay_match(match_data)

    squads = (
        match_data
        .get("info", {})
        .get("players", {})
    )

    first = [
        snapshot
        for snapshot in snapshots
        if not snapshot.get("target")
    ]

    chase = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("target")
    ]

    first_index = {}
    chase_index = {}

    for snapshot in first:
        snapshot["bowling_squad"] = list(
            squads.get(
                snapshot.get("bowling_team"),
                [],
            )
        )

        first_index[
            str(snapshot.get("overs"))
        ] = snapshot

    for snapshot in chase:
        snapshot["bowling_squad"] = list(
            squads.get(
                snapshot.get("bowling_team"),
                [],
            )
        )

        chase_index[
            str(snapshot.get("overs"))
        ] = snapshot

    _snapshot_index = (
        first_index,
        chase_index,
    )

    info = match_data.get(
        "info",
        {},
    )

    event = info.get(
        "event",
        {},
    ) or {}

    _match_info = {
        "teams": info.get("teams", []),
        "batting_first": (
            first[0].get("batting_team")
            if first
            else None
        ),
        "chasing": (
            chase[0].get("batting_team")
            if chase
            else None
        ),
        "venue": info.get("venue"),
        "city": info.get("city"),
        "dates": info.get("dates", []),
        "season": info.get("season"),
        "match_type": info.get("match_type"),
        "event_name": event.get("name"),
        "match_number": (
            event.get("match_number")
            or event.get("stage")
        ),
        "toss": info.get("toss", {}),
        "outcome": info.get("outcome", {}),
        "player_of_match": info.get(
            "player_of_match",
            [],
        ),
        "target": (
            chase[0].get("target")
            if chase
            else None
        ),
        "total_overs": TOTAL_OVERS,
        "squads": {
            team: list(players)
            for team, players in squads.items()
        },
        "rated_bowlers": sorted(
            RATED_BOWLERS
        ),
    }

    return _snapshot_index


def get_match_info(
    reveal_result: bool = False,
) -> dict:
    if _match_info is None:
        _load_snapshots()

    info = dict(_match_info)

    if not reveal_result:
        info.pop("outcome", None)
        info.pop(
            "player_of_match",
            None,
        )

        info["result_hidden"] = True

    return info


# ============================================================
# OVER HELPERS
# ============================================================

def over_to_tuple(
    over_str: Any,
) -> Tuple[int, int]:
    value = str(over_str).strip()

    parts = value.split(".")

    over = int(parts[0])

    if (
        len(parts) > 1
        and parts[1].isdigit()
    ):
        ball = int(parts[1])
    else:
        ball = 6

    return over, ball


def over_to_balls(
    over_str: Any,
) -> int:
    value = str(over_str).strip()

    if value.isdigit():
        # User-facing whole overs are boundaries: 11 means the end of
        # over 11, represented by Cricsheet's zero-based 10.6 snapshot.
        return int(value) * 6

    over, ball = over_to_tuple(value)

    return over * 6 + ball


def tuple_to_overs_float(
    over: int,
    ball: int,
) -> float:
    return round(
        over + ball / 6.0,
        2,
    )


def parse_over(
    text: str,
) -> Optional[str]:
    if not text:
        return None

    lowered = text.lower()

    match = re.search(
        r"\b(\d{1,2}\.\d)\b",
        lowered,
    )

    if match:
        return match.group(1)

    match = re.search(
        r"\bover\s+(?:to\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
        lowered,
    )

    if match:
        return match.group(1)

    match = re.search(
        r"\b(\d{1,2})\s*overs?\b",
        lowered,
    )

    if match:
        return match.group(1)

    match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)\s*over\b",
        lowered,
    )

    if match:
        return match.group(1)

    return None


# ============================================================
# INTENT KEYWORDS
# ============================================================

FINAL_SCORE_KEYWORDS = [
    "final score",
    "final total",
    "end of the match",
    "end of match",
    "overall score",
]


FIRST_INNINGS_WORDS = [
    "first innings",
    "1st innings",
    "innings 1",
    "batting first",
]


CHASE_WORDS = [
    "second innings",
    "2nd innings",
    "innings 2",
    "the chase",
    "chasing side",
]


CONCEPT_KEYWORDS = [
    "pressure",
    "momentum",
    "par ",
    "par?",
    "conditions",
    "condition",
    "career rating",
    "rating",
    "effectiveness",
    "in-match form",
    "form",
    "confidence",
    "ranking score",
    "provisional",
    "favour",
    "favor",
    "suit",
    "matchup",
    "match up",
    "mean",
    "means",
]


# ============================================================
# PHASE
# ============================================================

def _phase_from_words(
    lowered: str,
) -> Optional[str]:

    if any(
        word in lowered
        for word in FIRST_INNINGS_WORDS
    ):
        return "first"

    if any(
        word in lowered
        for word in CHASE_WORDS
    ):
        return "chase"

    if _match_info:
        for phase, team in (
            (
                "first",
                _match_info.get(
                    "batting_first"
                ),
            ),
            (
                "chase",
                _match_info.get(
                    "chasing"
                ),
            ),
        ):
            if not team:
                continue

            for word in team.lower().split():
                if len(word) > 3 and word in lowered:
                    return phase

    return None


# ============================================================
# RESOLVE OVER
# ============================================================

def _resolve_over(
    question: str,
    previous_context: dict,
) -> Tuple[
    Optional[str],
    str,
    bool,
]:

    lowered = (question or "").lower()

    previous = previous_context or {}

    if any(
        keyword in lowered
        for keyword in FINAL_SCORE_KEYWORDS
    ):
        return None, "chase", True

    named_phase = _phase_from_words(
        lowered
    )

    explicit_over = parse_over(
        question
    )

    active_phase = (
        named_phase
        or previous.get("current_phase")
        or "first"
    )

    if explicit_over is not None:
        return (
            explicit_over,
            active_phase,
            False,
        )

    active_over = (
        previous.get("current_over")
        or previous.get("requested_over")
    )

    return (
        active_over,
        active_phase,
        False,
    )


# ============================================================
# PICK SNAPSHOT
# ============================================================

def _pick_over(
    index: dict,
    over: Optional[str],
) -> str:

    if not index:
        raise ValueError(
            "Snapshot index is empty."
        )

    if over is not None:
        over_str = str(over)

        if over_str in index:
            return over_str

        try:
            target_balls = over_to_balls(
                over_str
            )

            return min(
                index.keys(),
                key=lambda key: abs(
                    over_to_balls(key)
                    - target_balls
                ),
            )

        except (
            ValueError,
            IndexError,
        ):
            pass

    return sorted(
        index.keys(),
        key=lambda key: over_to_balls(key),
    )[0]


def get_snapshot(
    over: Optional[str] = None,
    phase: str = "first",
) -> dict:

    first_index, chase_index = (
        _load_snapshots()
    )

    index = (
        first_index
        if phase == "first"
        else chase_index
    )

    if not index:
        index = (
            chase_index
            if phase == "first"
            else first_index
        )

    snapshot_over = _pick_over(
        index,
        over,
    )

    snapshot = dict(
        index[snapshot_over]
    )

    snapshot["requested_over"] = (
        over or snapshot_over
    )

    snapshot["snapshot_over"] = (
        snapshot_over
    )

    snapshot["phase"] = phase

    return snapshot


# ============================================================
# LEGAL BOWLERS
# ============================================================

def legal_bowlers(
    snapshot: dict,
) -> dict:

    stats = (
        snapshot.get(
            "bowling_stats",
            {},
        )
        or {}
    )

    candidates = list(
        snapshot.get(
            "bowling_squad",
            [],
        )
        or []
    )

    for name in stats:
        if name not in candidates:
            candidates.append(name)

    current_bowler = snapshot.get(
        "current_bowler"
    )

    eligible = []
    excluded = {}

    for name in candidates:
        figures = stats.get(
            name,
            {},
        ) or {}

        balls = figures.get(
            "balls",
            0,
        ) or 0

        overs_bowled = balls // 6

        if name == current_bowler:
            excluded[name] = (
                "Currently bowling / just "
                "bowled this over"
            )

        elif overs_bowled >= MAX_OVERS_PER_BOWLER:
            excluded[name] = (
                f"quota used "
                f"({overs_bowled} overs)"
            )

        else:
            eligible.append(name)

    return {
        "eligible": eligible,
        "excluded": excluded,
        "current_bowler": current_bowler,

        "current_batter": snapshot.get(
            "striker"
        ),

        "current_batsmen": [
            name
            for name in (
                snapshot.get("striker"),
                snapshot.get("non_striker"),
            )
            if name
        ],
    }


# ============================================================
# FORM
# ============================================================

def _form_from_bowling_stats(
    bowling_stats: dict,
    bowlers: list,
) -> dict:

    form_scores = {}

    for bowler in bowlers:
        figures = (
            bowling_stats.get(bowler)
            or {}
        )

        balls = figures.get(
            "balls",
            0,
        ) or 0

        if balls < 12:
            continue

        overs = balls / 6.0

        runs = figures.get(
            "runs",
            0,
        ) or 0

        wickets = figures.get(
            "wickets",
            0,
        ) or 0

        economy = runs / max(
            overs,
            0.1,
        )

        raw = (
            50
            + wickets * 15
            - ((economy - 7.5) * 4)
        )

        confidence = min(
            overs / float(
                MAX_OVERS_PER_BOWLER
            ),
            1.0,
        )

        score = (
            50
            + (raw - 50) * confidence
        )

        form_scores[bowler] = round(
            min(
                max(score, 0),
                100,
            )
        )

    return {
        "form_scores": form_scores
    }


# ============================================================
# ENRICHED CONTEXT
# ============================================================

def build_enriched_context(
    snapshot: dict,
    previous_context: Optional[dict] = None,
    top_strategy: Optional[str] = None,
    extra_context: Optional[dict] = None,
) -> dict:

    previous = previous_context or {}

    availability = legal_bowlers(
        snapshot
    )

    current_bowler = snapshot.get(
        "current_bowler"
    )

    exclusions = dict(
        availability.get(
            "excluded",
            {},
        )
    )

    if current_bowler:
        exclusions[current_bowler] = (
            "Currently bowling / just "
            "bowled this over"
        )

    context = {
        **previous,

        "current_over": snapshot.get(
            "snapshot_over"
        ),

        "requested_over": snapshot.get(
            "requested_over",
            snapshot.get(
                "snapshot_over"
            ),
        ),

        "current_phase": snapshot.get(
            "phase",
            "first",
        ),

        "score": (
            f"{snapshot.get('runs', 0)}"
            f"/"
            f"{snapshot.get('wickets', 0)}"
        ),

        "batting_team": snapshot.get(
            "batting_team"
        ),

        "bowling_team": snapshot.get(
            "bowling_team"
        ),

        "matchup_stats": snapshot.get(
            "matchup_stats",
            {},
        ),

        "bowling_stats": snapshot.get(
            "bowling_stats",
            {},
        ),

        "batter_stats": snapshot.get(
            "batter_stats",
            {},
        ),

        "current_batter": snapshot.get(
            "striker"
        ),

        "current_bowler": current_bowler,

        "available_bowlers": availability.get(
            "eligible",
            [],
        ),

        "excluded_bowlers": exclusions,

        "top_strategy": (
            top_strategy
            or previous.get(
                "top_strategy"
            )
        ),
    }

    if extra_context:
        context.update(
            extra_context
        )

    return context


# ============================================================
# INTENT CLASSIFICATION
# ============================================================

def classify_intent(
    state: MatchState,
) -> MatchState:

    question = (
        state.get("question")
        or ""
    )

    lowered = question.lower()

    whatif_keywords = [
        "what if",
        "suppose",
        "assume",
        "remove",
        "without",
        "unavailable",
    ]

    ranking_keywords = [
        "ranking",
        "rank",
        "list all",
        "all bowlers",
        "order",
    ]

    followup_keywords = [
        "why",
        "what about",
        "instead",
        "explain",
        "reason",
        "compare",
    ]

    bowling_keywords = [
        "bowl",
        "bowler",
        "pace",
        "spin",
        "next up",
        "who should",
        "option",
    ]

    score_keywords = [
        "score",
        "runs",
        "total",
        "wickets",
        "chasing",
        "innings",
        "situation",
        "go to",
    ]

    info_keywords = [
        "in-form",
        "in form",
        "who is batting",
        "who's batting",
        "batting now",
        "batsmen",
        "batter",
        "on strike",
        "at strike",
        "crease",
        "batting at",
        "current bowler",
        "bowling figures",
        "matchup",
        "against",
        "stats",
        "statistics",
        "score of",
        "figures for",
        "record against",
        "previous over",
        "last over",
    ]

    recommendation_phrases = [
        "who should bowl",
        "which bowler should",
        "what bowler should",
        "give next over",
        "bowl next",
        "next bowler",
    ]

    has_previous_context = bool(
        state.get("previous_context")
    )

    is_bowling_question = any(
        word in lowered
        for word in bowling_keywords
    )

    is_info_question = any(
        word in lowered
        for word in info_keywords
    )

    is_recommendation_question = any(
        phrase in lowered
        for phrase in recommendation_phrases
    )

    mentions_over = (
        parse_over(question) is not None
    )

    if (
        has_previous_context
        and any(
            word in lowered
            for word in whatif_keywords
        )
    ):
        intent = "what_if"

    elif (
        has_previous_context
        and any(
            word in lowered
            for word in ranking_keywords
        )
    ):
        intent = "ranking"

    elif is_info_question and has_previous_context and not is_recommendation_question:
        intent = "match_info"

    elif is_bowling_question:
        intent = "bowling_recommendation"

    elif (
        has_previous_context
        and any(
            word in lowered
            for word in followup_keywords
        )
    ):
        intent = "follow_up"

    elif (
        not is_bowling_question
        and (
            any(
                word in lowered
                for word in score_keywords
            )
            or mentions_over
        )
    ):
        intent = "score_info"

    elif (
        has_previous_context
        and not is_bowling_question
    ):
        intent = "follow_up"

    else:
        intent = "unknown"

    print(
        f"[classify_intent] "
        f"'{question}' -> {intent}"
    )

    return {
        **state,
        "intent": intent,
    }


def classify_live_intent(
    state: MatchState,
) -> MatchState:
    """Classify a live/completed-match question without running analytics."""
    question = (state.get("question") or "").lower()

    if any(phrase in question for phrase in ("go live", "current situation", "back to live")):
        intent = "go_live"
    elif any(phrase in question for phrase in ("toss", "won the toss", "win the toss")):
        intent = "match_info"
    elif any(phrase in question for phrase in ("live score", "latest score", "score now")):
        intent = "live_score"
    elif any(phrase in question for phrase in ("line and length", "line-length", "what line", "what length")):
        intent = "line_length"
    elif any(phrase in question for phrase in ("bowling plan", "plan for", "next overs")):
        intent = "bowling_plan"
    elif any(phrase in question for phrase in ("ranking", "rank the bowlers", "ranked")):
        intent = "bowling_ranking"
    elif any(phrase in question for phrase in ("who should bowl", "bowl next", "next bowler", "recommend")):
        intent = "bowling_recommendation"
    elif any(phrase in question for phrase in ("why", "explain", "how is", "formula")):
        intent = "explanation"
    elif "pressure" in question:
        intent = "pressure"
    elif "momentum" in question:
        intent = "momentum"
    elif any(term in question for term in ("matchup", "versus", " vs ", "against")):
        intent = "matchup"
    elif any(term in question for term in ("score", "runs", "over", "wickets")):
        intent = "score_info"
    else:
        intent = "general"

    return {
        **state,
        "live_intent": intent,
    }


# ============================================================
# ANALYTICS NODE
# ============================================================

def analytics_tool(
    state: MatchState,
) -> MatchState:

    previous = (
        state.get(
            "previous_context"
        )
        or {}
    )

    requested, phase, _ = _resolve_over(
        state.get("question", ""),
        previous,
    )

    snapshot = get_snapshot(
        requested,
        phase,
    )

    availability = legal_bowlers(
        snapshot
    )

    eligible = (
        availability.get("eligible")
        or list(
            (
                snapshot.get(
                    "bowling_stats",
                    {},
                )
                or {}
            ).keys()
        )
    )

    venue = (
        snapshot.get("venue")
        or (
            _match_info or {}
        ).get("venue")
    )

    # --------------------------------------------------------
    # Situation
    # --------------------------------------------------------

    situational = _situational_metrics(
        snapshot,
        phase,
        venue,
    )

    form_scores = _form_from_bowling_stats(
        snapshot.get("bowling_stats", {}) or {},
        list((snapshot.get("bowling_stats", {}) or {}).keys()),
    ).get("form_scores", {})

    situational.pop(
        "overs_bowled",
        None,
    )

    par_note = situational.pop(
        "par_note",
        None,
    )

    momentum_score = situational.pop(
        "momentum_score",
        0,
    )

    momentum_label = situational.pop(
        "momentum_label",
        "",
    )

    momentum_level = situational.pop(
        "momentum_level",
        "Neutral",
    )

    pressure_data = situational

    # --------------------------------------------------------
    # Bowler effectiveness
    # --------------------------------------------------------

    bowler_data = calculate_bowler_effectiveness(
        available_bowlers=eligible,
        pitch=PITCH,
        weather_condition=WEATHER_CONDITION,
        humidity=HUMIDITY,
    )

    bowler_scores = (
        bowler_data.get(
            "bowler_scores",
            {},
        )
        or {}
    )

    unrated = [
        name
        for name in (
            bowler_data.get(
                "unrated_bowlers",
                [],
            )
            or []
        )
        if name in (
            snapshot.get(
                "bowling_stats",
                {},
            )
            or {}
        )
    ]

    provisional = (
        bowler_data.get(
            "provisional_bowlers",
            {},
        )
        or {}
    )

    # --------------------------------------------------------
    # Form
    # --------------------------------------------------------

    if USE_IN_MATCH_FORM:
        form_data = _form_from_bowling_stats(
            snapshot.get(
                "bowling_stats",
                {},
            )
            or {},
            list(
                bowler_scores.keys()
            ),
        )
    else:
        form_data = calculate_form_score(
            bowlers=eligible
        )

    form_scores = (
        form_data.get(
            "form_scores",
            {},
        )
        or {}
    )

    # --------------------------------------------------------
    # Combined ranking
    # --------------------------------------------------------

    combined_scores = {}

    for bowler, career_score in bowler_scores.items():
        form_score = form_scores.get(
            bowler,
            50,
        )

        combined_scores[bowler] = round(
            0.7 * career_score
            + 0.3 * form_score
        )

    # --------------------------------------------------------
    # Simulation
    # --------------------------------------------------------

    simulation_data = simulate_strategies(
        bowler_scores=combined_scores,
        pressure=pressure_data.get(
            "pressure",
            50,
        ),
    )

    strategies = (
        simulation_data.get(
            "strategies",
            [],
        )
        or []
    )

    # Normalize strategy names.
    for strategy in strategies:
        bowler = strategy.get(
            "bowler"
        )

        strategy["strategy"] = (
            f"Bring on {bowler}"
        )

    # --------------------------------------------------------
    # CONFIDENCE CAPS
    # --------------------------------------------------------

    choice_note = None

    if len(strategies) == 1:
        strategies[0]["confidence"] = min(
            strategies[0].get(
                "confidence",
                0,
            ),
            60,
        )

        choice_note = (
            "only one rated bowler was "
            "available, so there was no "
            "real choice to weigh"
        )

    elif len(strategies) == 2:
        choice_note = (
            "only two rated bowlers were "
            "available at this point"
        )

    # Provisional top-pick cap.
    if strategies:
        initial_top = strategies[0].get(
            "bowler"
        )

        if initial_top in provisional:
            detail = provisional[
                initial_top
            ]

            strategies[0]["confidence"] = min(
                strategies[0].get(
                    "confidence",
                    0,
                ),
                70,
            )

            overs = detail.get(
                "overs",
                "unknown",
            )

            note = (
                f"{initial_top}'s rating is "
                f"provisional, built on "
                f"{overs} career overs"
            )

            if choice_note:
                choice_note = (
                    f"{choice_note}; {note}"
                )
            else:
                choice_note = note

    # --------------------------------------------------------
    # IMPORTANT:
    # Confidence caps can change the order.
    # Re-sort after capping.
    # --------------------------------------------------------

    strategies.sort(
        key=lambda item: item.get(
            "confidence",
            0,
        ),
        reverse=True,
    )

    top_strategy = (
        strategies[0].get("strategy")
        if strategies
        else ""
    )

    top_bowler = (
        strategies[0].get("bowler")
        if strategies
        else ""
    )

    # --------------------------------------------------------
    # Save context
    # --------------------------------------------------------

    updated_context = build_enriched_context(
        snapshot=snapshot,
        previous_context=previous,
        top_strategy=top_strategy,
        extra_context={
            "ranking_team": snapshot.get(
                "bowling_team"
            ),

            "ranking_phase": phase,

            "pressure": pressure_data.get(
                "pressure",
                50,
            ),

            "pressure_label": pressure_data.get(
                "pressure_label"
            ),

            "momentum_score": momentum_score,

            "momentum_level": momentum_level,

            "momentum_label": momentum_label,

            "bowler_scores": bowler_scores,

            "form_scores": form_scores,

            "combined_scores": combined_scores,

            "strategies": strategies,

            "unrated_bowlers": unrated,

            "provisional_bowlers": provisional,

            "par_note": par_note,

            "choice_note": choice_note,

            "recommendation": top_strategy,

            "top_bowler": top_bowler,
        },
    )

    return {
        **state,

        "score": (
            f"{snapshot.get('runs', 0)}"
            f"/"
            f"{snapshot.get('wickets', 0)}"
        ),

        "pressure": pressure_data.get(
            "pressure",
            50,
        ),

        "pressure_label": pressure_data.get(
            "pressure_label"
        ),

        "momentum_score": momentum_score,

        "momentum_level": momentum_level,

        "momentum_label": momentum_label,

        "strategies": strategies,

        "choice_note": choice_note,

        "provisional_bowlers": provisional,

        "unrated_bowlers": unrated,

        "matchup_stats": snapshot.get(
            "matchup_stats",
            {},
        ),

        "previous_context": updated_context,
    }


# ============================================================
# GEMINI NODE
# ============================================================

def gemini_reasoning_node(
    state: MatchState,
) -> MatchState:

    result = generate_recommendation(
        state
    )

    recommendation = (
        result.get(
            "recommendation"
        )
        or "No recommendation"
    )

    confidence = result.get(
        "confidence",
        0,
    )

    explanation = (
        result.get(
            "explanation"
        )
        or ""
    ).strip()

    recommendation_name = recommendation
    if recommendation_name.lower().startswith("bring on "):
        recommendation_name = recommendation_name[9:]

    answer = (
        f"Recommendation: "
        f"{recommendation_name} "
        f"(confidence {confidence}%)."
    )

    if explanation:
        answer += f" {explanation[:65].rsplit(' ', 1)[0]}."

    previous_context = dict(
        state.get(
            "previous_context",
            {},
        )
        or {}
    )

    previous_context[
        "recommendation"
    ] = recommendation

    previous_context[
        "confidence"
    ] = confidence

    previous_context[
        "explanation"
    ] = explanation

    return {
        **state,
        "answer": answer,
        "recommendation": recommendation,
        "confidence": confidence,
        "explanation": explanation,
        "previous_context": previous_context,
    }


# ============================================================
# SCORE NODE
# ============================================================

def score_node(
    state: MatchState,
) -> MatchState:

    previous = (
        state.get(
            "previous_context"
        )
        or {}
    )

    requested, phase, _ = _resolve_over(
        state.get("question", ""),
        previous,
    )

    snapshot = get_snapshot(
        requested,
        phase,
    )

    venue = (
        snapshot.get("venue")
        or (
            _match_info or {}
        ).get("venue")
    )

    situational = _situational_metrics(
        snapshot,
        phase,
        venue,
    )

    form_scores = _form_from_bowling_stats(
        snapshot.get("bowling_stats", {}) or {},
        list((snapshot.get("bowling_stats", {}) or {}).keys()),
    ).get("form_scores", {})

    par, par_source = par_at_over(
        venue,
        max(
            1,
            int(situational.get("overs_bowled", 0)),
        ),
    )

    answer = (
        f"Score is "
        f"{snapshot.get('runs', 0)}"
        f"/"
        f"{snapshot.get('wickets', 0)} "
        f"at "
        f"{('the end of over ' if str(requested).isdigit() else 'over ')}"
        f"{requested or snapshot.get('snapshot_over')}."
    )

    if par is not None:
        par_gap = round(
            par - snapshot.get("runs", 0),
            1,
        )

        if par_gap >= 0:
            answer += (
                f" That is {par_gap} runs behind "
                f"the venue par of {par} "
                f"({par_source})."
            )
        else:
            answer += (
                f" That is {abs(par_gap)} runs ahead "
                f"of the venue par of {par} "
                f"({par_source})."
            )

    updated_context = build_enriched_context(
        snapshot=snapshot,
        previous_context=previous,
        extra_context={
            "pressure": situational.get(
                "pressure",
                50,
            ),

            "pressure_label": situational.get(
                "pressure_label"
            ),

            "momentum_score": situational.get(
                "momentum_score",
                0,
            ),

            "momentum_level": situational.get(
                "momentum_level",
                "Neutral",
            ),

            "momentum_label": situational.get(
                "momentum_label"
            ),

            "par_note": situational.get(
                "par_note"
            ),

            "matchup_stats": snapshot.get(
                "matchup_stats",
                {},
            ),

            "batter_stats": snapshot.get(
                "batter_stats",
                {},
            ),

            "current_batsmen": [
                name
                for name in (
                    snapshot.get("striker"),
                    snapshot.get("non_striker"),
                )
                if name
            ],

            "form_scores": form_scores,
        },
    )

    return {
        **state,

        "answer": answer,

        "score": (
            f"{snapshot.get('runs', 0)}"
            f"/"
            f"{snapshot.get('wickets', 0)}"
        ),

        "pressure": situational.get(
            "pressure",
            50,
        ),

        "pressure_label": situational.get(
            "pressure_label"
        ),

        "momentum_score": situational.get(
            "momentum_score",
            0,
        ),

        "momentum_level": situational.get(
            "momentum_level",
            "Neutral",
        ),

        "momentum_label": situational.get(
            "momentum_label"
        ),

        "matchup_stats": snapshot.get(
            "matchup_stats",
            {},
        ),

        "batter_stats": snapshot.get(
            "batter_stats",
            {},
        ),

        "form_scores": form_scores,

        "previous_context": updated_context,
    }


# ============================================================
# FOLLOW-UP NODE
# ============================================================

def follow_up_node(
    state: MatchState,
) -> MatchState:

    previous = (
        state.get(
            "previous_context"
        )
        or {}
    )

    question = (
        state.get("question")
        or ""
    )

    lowered = question.lower()

    def mentioned(name: str) -> bool:
        name_lower = name.lower()

        if name_lower in lowered:
            return True

        parts = name_lower.split()

        return bool(
            parts
            and parts[-1] in lowered
        )

    # Concept questions should NOT be hijacked
    # by bowler name shortcuts.
    asks_about_concept = any(
        keyword in lowered
        for keyword in CONCEPT_KEYWORDS
    )

    if not asks_about_concept:

        # ----------------------------------------------------
        # EXCLUDED
        # ----------------------------------------------------

        excluded = (
            previous.get(
                "excluded_bowlers",
                {},
            )
            or {}
        )

        for name, reason in excluded.items():
            if mentioned(name):
                return {
                    **state,
                    "answer": (
                        f"{name} is not an option "
                        f"here: {reason}."
                    ),
                }

        # ----------------------------------------------------
        # UNRATED
        # ----------------------------------------------------

        unrated = (
            previous.get(
                "unrated_bowlers",
                [],
            )
            or []
        )

        for name in unrated:
            if mentioned(name):
                return {
                    **state,
                    "answer": (
                        f"{name} has no career "
                        f"profile in the archive, "
                        f"so he cannot be scored or "
                        f"ranked. He may be eligible "
                        f"to bowl, but this system "
                        f"has no rating basis for him."
                    ),
                }

        # ----------------------------------------------------
        # PROVISIONAL
        # ----------------------------------------------------

        provisional = (
            previous.get(
                "provisional_bowlers",
                {},
            )
            or {}
        )

        for name, detail in provisional.items():

            if not mentioned(name):
                continue

            combined_score = (
                previous.get(
                    "combined_scores",
                    {},
                )
                or {}
            ).get(name)

            recommendation = (
                previous.get(
                    "recommendation"
                )
                or previous.get(
                    "top_strategy"
                )
                or ""
            )

            is_current_pick = (
                bool(recommendation)
                and name.lower()
                in recommendation.lower()
            )

            if is_current_pick:
                lead = (
                    f"{name} is the current pick"
                )
            else:
                lead = (
                    f"{name} is a viable option, "
                    f"but {recommendation or 'the current pick'} "
                    f"ranks higher right now"
                )

            overs = detail.get(
                "overs",
                "unknown",
            )

            own_figures = detail.get(
                "own_figures",
                "unknown",
            )

            score_text = (
                f" His combined score is "
                f"{combined_score}."
                if combined_score is not None
                else ""
            )

            return {
                **state,
                "answer": (
                    f"{lead}; his rating is provisional "
                    f"because it is based on only "
                    f"{overs} career overs in the archive."
                    f"{score_text} "
                    f"His underlying figures give an "
                    f"own-sample rating of {own_figures}."
                ),
            }

    # --------------------------------------------------------
    # No context
    # --------------------------------------------------------

    if not previous:
        return {
            **state,
            "answer": (
                "Nothing has been computed yet. "
                "Move to an over and ask who "
                "should bowl next, then follow up."
            ),
        }

    # --------------------------------------------------------
    # Gemini follow-up
    # --------------------------------------------------------

    answer = generate_followup_answer(
        question=question,
        previous_context=previous,
    )

    return {
        **state,
        "answer": answer,
    }


# ============================================================
# MATCH INFORMATION NODE
# ============================================================

def match_info_node(
    state: MatchState,
) -> MatchState:
    previous = state.get("previous_context") or {}
    question = (state.get("question") or "").lower()

    batsmen = previous.get("current_batsmen") or []
    striker = previous.get("current_batter") or (batsmen[0] if batsmen else None)
    non_striker = next((name for name in batsmen if name != striker), None)

    if "batting" in question or "batsmen" in question:
        answer = (
            f"Batting at the crease: {striker} and {non_striker}."
            if striker and non_striker
            else f"Batting at the crease: {', '.join(batsmen) or 'not available'}."
        )
        return {**state, "answer": answer}

    if "strike" in question:
        answer = (
            f"{striker} is on strike; {non_striker} is at the non-striker's end."
            if striker and non_striker
            else f"{striker or 'No striker'} is on strike."
        )
        return {**state, "answer": answer}

    if "pressure" in question:
        return {
            **state,
            "answer": (
                f"Batting side pressure: {previous.get('pressure', 'not available')} "
                f"({previous.get('pressure_label', 'not available')})."
            ),
        }

    if "momentum" in question:
        return {
            **state,
            "answer": (
                f"{previous.get('momentum_level', 'Neutral')}: "
                f"{previous.get('momentum_label', 'Momentum neutral')}."
            ),
        }

    figures = previous.get("bowling_stats") or {}
    batter_stats = previous.get("batter_stats") or {}
    requested_batter = next(
        (
            name
            for name in batter_stats
            if name.lower() in question
            or name.split()[-1].lower() in question
        ),
        None,
    )
    requested_bowler = next(
        (
            name
            for name in figures
            if name.lower() in question
            or any(
                token.lower() in question
                for token in re.sub(r"\s*\(\d+\)", "", name).split()
                if len(token) > 2
            )
        ),
        None,
    )

    if requested_batter and ("score" in question or "runs" in question or "stats" in question):
        stats = batter_stats[requested_batter]
        balls = stats.get("balls", 0)
        strike_rate = stats.get("runs", 0) / max(balls, 1) * 100
        return {
            **state,
            "answer": (
                f"{requested_batter}: {stats.get('runs', 0)} runs off {balls} balls, "
                f"SR {strike_rate:.1f}, {stats.get('fours', 0)} fours, "
                f"{stats.get('sixes', 0)} sixes."
            ),
        }

    if (
        requested_bowler
        and not requested_batter
        and ("score" in question or "figure" in question or "bowling" in question)
    ):
        stats = figures[requested_bowler]
        balls = stats.get("balls", 0)
        overs = f"{balls // 6}.{balls % 6}"
        economy = stats.get("runs", 0) / max(balls / 6, 0.1)
        return {
            **state,
            "answer": (
                f"{requested_bowler}: {overs} overs, {stats.get('runs', 0)} runs, "
                f"{stats.get('wickets', 0)} wickets, economy {economy:.2f}."
            ),
        }

    if "previous over" in question or "last over" in question:
        return {
            **state,
            "answer": (
                f"{previous.get('current_bowler', 'No bowler')} bowled the previous delivery/over."
            ),
        }

    if "score" in question or "runs" in question:
        return {
            **state,
            "answer": (
                f"Score: {previous.get('score', 'not available')} at "
                f"over {previous.get('requested_over') or previous.get('current_over', 'unknown')}."
            ),
        }

    if "crease" in question or "batting at" in question:
        answer = (
            f"At the crease: {striker} and {non_striker}."
            if striker and non_striker
            else f"At the crease: {', '.join(batsmen) or 'not available'}."
        )
        return {**state, "answer": answer}

    form_scores = previous.get("form_scores") or {}
    if "in-form" in question or "in form" in question:
        if form_scores:
            bowler, score = max(form_scores.items(), key=lambda item: item[1])
            return {**state, "answer": f"{bowler} is the in-form bowler (form score {score})."}
        return {**state, "answer": "No in-match form sample is available yet."}

    if "bowling figure" in question or "bowling stats" in question:
        if figures:
            rows = []
            for bowler, stats in figures.items():
                balls = stats.get("balls", 0)
                rows.append(
                    f"{bowler}: {balls // 6}.{balls % 6} overs, "
                    f"{stats.get('runs', 0)} runs, {stats.get('wickets', 0)} wickets"
                )
            return {**state, "answer": "Bowling figures: " + "; ".join(rows[:6]) + "."}
        return {**state, "answer": "No bowling figures are available yet."}

    matchups = previous.get("matchup_stats") or {}
    requested_batter = requested_batter or striker

    requested_matchup_bowler = next(
        (
            name
            for name in (matchups.get(requested_batter) or {})
            if name.lower() in question
            or any(
                token.lower() in question
                for token in re.sub(r"\s*\(\d+\)", "", name).split()
                if len(token) > 2
            )
        ),
        None,
    )

    if requested_batter and requested_matchup_bowler and "against" in question:
        figures = matchups[requested_batter][requested_matchup_bowler]
        balls = figures.get("balls", 0)
        runs = figures.get("runs", 0)
        strike_rate = runs / max(balls, 1) * 100
        return {
            **state,
            "answer": (
                f"{requested_batter} vs {requested_matchup_bowler}: "
                f"{runs} runs off {balls} balls, SR {strike_rate:.1f}, "
                f"{figures.get('dismissals', 0)} dismissals."
            ),
        }

    if requested_batter and ("score" in question or "runs" in question or "stats" in question):
        stats = batter_stats.get(requested_batter)
        if stats:
            balls = stats.get("balls", 0)
            strike_rate = stats.get("runs", 0) / max(balls, 1) * 100
            return {
                **state,
                "answer": (
                    f"{requested_batter}: {stats.get('runs', 0)} runs off {balls} balls, "
                    f"SR {strike_rate:.1f}, {stats.get('fours', 0)} fours, "
                    f"{stats.get('sixes', 0)} sixes."
                ),
            }
    batter_stats = matchups.get(requested_batter) or {}

    if "against" in question or "matchup" in question or "stats" in question or "statistics" in question:
        if not batter_stats:
            return {**state, "answer": f"No matchup data is available for {requested_batter or 'that batter'} yet."}

        rows = []
        for bowler, figures in batter_stats.items():
            balls = figures.get("balls", 0)
            runs = figures.get("runs", 0)
            rate = (runs / balls * 100) if balls else 0
            rows.append(f"{bowler}: {runs} runs off {balls}, SR {rate:.1f}, {figures.get('dismissals', 0)} out")
        return {**state, "answer": f"{requested_batter}: " + "; ".join(rows[:4]) + "."}

    if "current bowler" in question:
        return {**state, "answer": f"Current bowler: {previous.get('current_bowler', 'not available')}."}

    return {**state, "answer": generate_followup_answer(question, previous)}


# ============================================================
# WHAT IF NODE
# ============================================================

def what_if_node(
    state: MatchState,
) -> MatchState:

    previous = (
        state.get(
            "previous_context"
        )
        or {}
    )

    question = (
        state.get("question")
        or ""
    )

    lowered = question.lower()

    original = dict(
        previous.get(
            "combined_scores",
            {},
        )
        or {}
    )

    scores = dict(original)

    if not scores:
        return {
            **state,
            "answer": (
                "No previous recommendation "
                "to work from. Ask who should "
                "bowl next first."
            ),
        }

    removed = []

    for name in list(scores.keys()):

        last_name = (
            name.split()[-1].lower()
        )

        if (
            name.lower() in lowered
            or last_name in lowered
        ):
            scores.pop(name)
            removed.append(name)

    if not removed:
        return {
            **state,
            "answer": (
                "I could not tell which bowler "
                "to remove. Try: "
                "What if Umar Gul wasn't available?"
            ),
        }

    removed_text = ", ".join(
        removed
    )

    if not scores:
        return {
            **state,
            "answer": (
                f"If {removed_text} wasn't "
                f"available, no other rated "
                f"bowler would be left."
            ),
        }

    simulation = simulate_strategies(
        bowler_scores=scores,
        pressure=previous.get(
            "pressure",
            50,
        ),
    )

    strategies = (
        simulation.get(
            "strategies",
            [],
        )
        or []
    )

    for strategy in strategies:
        strategy["strategy"] = (
            f"Bring on "
            f"{strategy.get('bowler')}"
        )

    if not strategies:
        return {
            **state,
            "answer": (
                f"If {removed_text} wasn't "
                f"available, no alternative "
                f"could be ranked."
            ),
        }

    best = strategies[0]

    best_name = best.get(
        "bowler",
        "unknown",
    )

    best_score = scores.get(
        best_name
    )

    dropped_score = original.get(
        removed[0]
    )

    answer = (
        f"If {removed_text} wasn't available, "
        f"the best option would be "
        f"{best_name}"
    )

    if best_score is not None:
        answer += (
            f" with a score of {best_score}"
        )

    if dropped_score is not None:
        answer += (
            f", against "
            f"{removed[0]}'s {dropped_score}"
        )

    answer += "."

    provisional = (
        previous.get(
            "provisional_bowlers",
            {},
        )
        or {}
    )

    if best_name in provisional:

        detail = provisional[
            best_name
        ]

        answer += (
            f" That rating is provisional: "
            f"{best_name} has only "
            f"{detail.get('overs', 'unknown')} "
            f"career overs in the archive."
        )

    return {
        **state,
        "strategies": strategies,
        "answer": answer,
    }


# ============================================================
# RANKING NODE
# ============================================================

def ranking_node(
    state: MatchState,
) -> MatchState:

    previous = (
        state.get(
            "previous_context"
        )
        or {}
    )

    scores = (
        previous.get(
            "combined_scores",
            {},
        )
        or {}
    )

    if not scores:
        return {
            **state,
            "answer": (
                "No scored bowlers yet. "
                "Ask who should bowl next first."
            ),
        }

    effectiveness = (
        previous.get(
            "bowler_scores",
            {},
        )
        or {}
    )

    form = (
        previous.get(
            "form_scores",
            {},
        )
        or {}
    )

    provisional = (
        previous.get(
            "provisional_bowlers",
            {},
        )
        or {}
    )

    over = previous.get(
        "requested_over"
    )

    team = previous.get(
        "ranking_team"
    )

    ranking_phase = previous.get(
        "ranking_phase"
    )

    current_phase = previous.get(
        "current_phase"
    )

    header = (
        f"Bowler ranking at {over} overs"
        if over
        else "Bowler ranking"
    )

    if team:
        header += (
            f" ({team} bowling)"
        )

    lines = [
        header + ":"
    ]

    if (
        ranking_phase
        and ranking_phase != current_phase
    ):
        lines.append(
            "This ranking is from the other "
            "innings. Ask who should bowl next "
            "to refresh it."
        )

    strategies = (
        previous.get(
            "strategies",
            [],
        )
        or []
    )

    if strategies:
        ordered = [
            (
                item.get("bowler"),
                scores[item.get("bowler")],
            )
            for item in strategies
            if (
                item.get("bowler")
                in scores
            )
        ]
    else:
        ordered = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

    for position, (
        bowler,
        score,
    ) in enumerate(
        ordered,
        start=1,
    ):

        career = effectiveness.get(
            bowler,
            "n/a",
        )

        detail = (
            f"career {career}"
        )

        if bowler in form:
            detail += (
                f", form {form[bowler]}"
            )

        if bowler in provisional:
            detail += (
                ", provisional on "
                f"{provisional[bowler].get('overs', 'unknown')} "
                "career overs"
            )

        lines.append(
            f"{position}. {bowler} - "
            f"{score} ({detail})"
        )

    unrated = (
        previous.get(
            "unrated_bowlers",
            [],
        )
        or []
    )

    if unrated:
        lines.append("")
        lines.append(
            "Not rated (no career profile): "
            + ", ".join(unrated)
        )

    excluded = (
        previous.get(
            "excluded_bowlers",
            {},
        )
        or {}
    )

    if excluded:
        lines.append("")
        lines.append(
            "Unavailable:"
        )

        for name, reason in excluded.items():
            lines.append(
                f"- {name}: {reason}"
            )

    choice_note = previous.get(
        "choice_note"
    )

    if choice_note:
        lines.append("")
        lines.append(
            f"Note: {choice_note}."
        )

    return {
        **state,
        "answer": "\n".join(lines),
    }


# ============================================================
# OPEN QUESTION NODE
# ============================================================

def open_question_node(
    state: MatchState,
) -> MatchState:

    answer = answer_open_question(
        question=state.get(
            "question",
            "",
        ),
        previous_context=(
            state.get(
                "previous_context",
                {},
            )
            or {}
        ),
    )

    return {
        **state,
        "answer": answer,
    }


# ============================================================
# SITUATIONAL METRICS
# ============================================================

def _load_par_data() -> dict:
    global _par_data

    if _par_data is not None:
        return _par_data

    try:
        with open(
            _PAR_PATH,
            "r",
            encoding="utf-8",
        ) as handle:
            _par_data = json.load(handle)

    except Exception:
        _par_data = {}

    return _par_data


def par_at_over(
    venue: str,
    over_number: int,
):

    data = _load_par_data()

    if not data:
        return None, None

    key = str(
        max(
            1,
            min(
                over_number,
                TOTAL_OVERS,
            ),
        )
    )

    venues = (
        data.get(
            "venues",
            {},
        )
        or {}
    )

    venue_key = venue if venue in venues else None

    if venue and venue_key is None:
        requested = "".join(
            character
            for character in venue.casefold()
            if character.isalnum()
        )
        candidates = []
        for stored_venue in venues:
            stored = "".join(
                character
                for character in stored_venue.casefold()
                if character.isalnum()
            )
            if stored.startswith(requested) or requested.startswith(stored):
                candidates.append(stored_venue)

        if candidates:
            venue_key = max(
                candidates,
                key=lambda name: venues[name].get("matches", 0),
            )

    venue_entry = venues.get(venue_key) if venue_key else None

    if venue_entry:
        value = (
            venue_entry
            .get(
                "par_by_over",
                {},
            )
            .get(key)
        )

        if value is not None:
            return (
                value,
                f"{venue} men's T20I history "
                f"({venue_entry.get('matches')} matches)",
            )

    value = (
        data.get(
            "global",
            {},
        )
        or {}
    ).get(
        "par_by_over",
        {},
    ).get(key)

    if value is not None:
        return (
            value,
            "all venues",
        )

    return None, None


def first_innings_pressure(
    runs: int,
    wickets: int,
    overs_bowled: float,
    venue: str,
) -> dict:

    over_num = max(
        1,
        int(overs_bowled),
    )

    par, source = par_at_over(
        venue,
        over_num,
    )

    current_run_rate = (
        runs
        / max(
            overs_bowled,
            0.1,
        )
    )

    if par is None:
        return {
            "pressure": 50,
            "pressure_label": (
                "No par data for this venue"
            ),
            "current_run_rate": round(
                current_run_rate,
                2,
            ),
            "required_run_rate": 0.0,
        }

    par_gap = (
        par - runs
    ) / max(
        par,
        1,
    )

    expected_wickets = (
        0.3 * overs_bowled
    )

    wicket_gap = (
        wickets
        - expected_wickets
    )

    pressure = round(
        min(
            max(
                50
                + par_gap * 100
                + wicket_gap * 5,
                5,
            ),
            95,
        )
    )

    if pressure >= 70:
        label = (
            "Batting side under pressure"
        )

    elif pressure <= 30:
        label = (
            "Batting side on top"
        )

    else:
        label = "Even"

    par_final, _ = par_at_over(
        venue,
        TOTAL_OVERS,
    )

    overs_left = max(
        TOTAL_OVERS - overs_bowled,
        0.1,
    )

    required = max(
        (par_final or 0) - runs,
        0,
    ) / overs_left

    return {
        "pressure": pressure,
        "pressure_label": label,
        "current_run_rate": round(
            current_run_rate,
            2,
        ),
        "required_run_rate": round(
            required,
            2,
        ),
        "_par": par,
        "_par_source": source,
        "_par_final": par_final,
    }


def _situational_metrics(
    snapshot: dict,
    phase: str,
    venue: str,
) -> dict:

    over_tuple = over_to_tuple(
        snapshot.get(
            "overs"
        )
    )

    overs_bowled = (
        tuple_to_overs_float(
            over_tuple[0],
            over_tuple[1],
        )
    )

    par_note = None

    if phase == "first":

        pressure_data = (
            first_innings_pressure(
                snapshot.get(
                    "runs",
                    0,
                ),
                snapshot.get(
                    "wickets",
                    0,
                ),
                overs_bowled,
                venue,
            )
        )

        par_now = pressure_data.pop(
            "_par",
            None,
        )

        par_source = pressure_data.pop(
            "_par_source",
            None,
        )

        pressure_data.pop(
            "_par_final",
            None,
        )

        if par_now is not None:
            par_note = (
                f"par {par_now} at this over, "
                f"from {par_source}"
            )

        par_then, _ = par_at_over(
            venue,
            max(
                1,
                int(overs_bowled) - 5,
            ),
        )

        if (
            par_now is not None
            and par_then is not None
        ):
            baseline_rate = max(
                (
                    par_now
                    - par_then
                ) / 5,
                0.1,
            )
        else:
            baseline_rate = (
                snapshot.get(
                    "runs",
                    0,
                )
                / max(
                    overs_bowled,
                    0.1,
                )
            )

    else:

        pressure_data = calculate_pressure(
            current_score=snapshot.get(
                "runs",
                0,
            ),
            wickets_lost=snapshot.get(
                "wickets",
                0,
            ),
            overs_bowled=overs_bowled,
            target=snapshot.get(
                "target",
                0,
            ),
            total_overs=TOTAL_OVERS,
        )

        overs_remaining = max(
            TOTAL_OVERS
            - overs_bowled,
            0.1,
        )

        runs_needed = max(
            snapshot.get(
                "target",
                0,
            )
            - snapshot.get(
                "runs",
                0,
            ),
            0,
        )

        baseline_rate = (
            runs_needed
            / overs_remaining
        )

    momentum_data = calculate_momentum(
        recent_runs=snapshot.get(
            "recent_runs",
            0,
        ),
        recent_wickets=snapshot.get(
            "recent_wickets",
            0,
        ),
        recent_overs_span=snapshot.get(
            "recent_overs_span",
            5,
        ),
        current_run_rate=baseline_rate,
    )

    return {
        "overs_bowled": overs_bowled,
        "par_note": par_note,
        **pressure_data,
        **momentum_data,
    }


# ============================================================
# ROUTER
# ============================================================

def route_intent(
    state: MatchState,
) -> str:

    intent = state.get(
        "intent",
        "unknown",
    )

    routes = {
        "bowling_recommendation": (
            "analytics_tool"
        ),
        "what_if": "what_if_node",
        "ranking": "ranking_node",
        "match_info": "match_info_node",
        "score_info": "score_node",
        "follow_up": "follow_up_node",
        "unknown": "open_question_node",
    }

    return routes.get(
        intent,
        "open_question_node",
    )


# ============================================================
# BUILD GRAPH
# ============================================================

def build_graph():

    builder = StateGraph(
        MatchState
    )

    builder.add_node(
        "classify_intent",
        classify_intent,
    )

    builder.add_node(
        "analytics_tool",
        analytics_tool,
    )

    builder.add_node(
        "gemini_reasoning_node",
        gemini_reasoning_node,
    )

    builder.add_node(
        "score_node",
        score_node,
    )

    builder.add_node(
        "follow_up_node",
        follow_up_node,
    )

    builder.add_node(
        "open_question_node",
        open_question_node,
    )

    builder.add_node(
        "what_if_node",
        what_if_node,
    )

    builder.add_node(
        "ranking_node",
        ranking_node,
    )

    builder.add_node(
        "match_info_node",
        match_info_node,
    )

    builder.set_entry_point(
        "classify_intent"
    )

    builder.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "analytics_tool": (
                "analytics_tool"
            ),
            "score_node": "score_node",
            "follow_up_node": (
                "follow_up_node"
            ),
            "open_question_node": (
                "open_question_node"
            ),
            "what_if_node": (
                "what_if_node"
            ),
            "ranking_node": (
                "ranking_node"
            ),
            "match_info_node": "match_info_node",
        },
    )

    builder.add_edge(
        "analytics_tool",
        "gemini_reasoning_node",
    )

    builder.add_edge(
        "gemini_reasoning_node",
        END,
    )

    builder.add_edge(
        "score_node",
        END,
    )

    builder.add_edge(
        "follow_up_node",
        END,
    )

    builder.add_edge(
        "open_question_node",
        END,
    )

    builder.add_edge(
        "what_if_node",
        END,
    )

    builder.add_edge(
        "match_info_node",
        END,
    )

    builder.add_edge(
        "ranking_node",
        END,
    )

    return builder.compile()


def build_live_intent_graph():
    """Build the lightweight LangGraph used by the live chat path."""
    builder = StateGraph(MatchState)
    builder.add_node("classify_live_intent", classify_live_intent)
    builder.set_entry_point("classify_live_intent")
    builder.add_edge("classify_live_intent", END)
    return builder.compile()


# ============================================================
# GRAPH INSTANCE
# ============================================================

graph = build_graph()
live_intent_graph = build_live_intent_graph()


def classify_live_question(question: str, previous_context: Optional[dict] = None) -> str:
    """Return the live intent using the dedicated LangGraph classifier."""
    result = live_intent_graph.invoke({
        "question": question,
        "previous_context": previous_context or {},
    })
    return result.get("live_intent", "general")


# ============================================================
# LOCAL TEST
# ============================================================

if __name__ == "__main__":

    app = graph

    conversation = [
        "First innings, go to over 18",
        "Who should bowl next?",
        "Why not Umar Gul?",
        "Show ranking",
        "What if Umar Gul wasn't available?",
        "What is pressure?",
    ]

    result = {}

    for question in conversation:

        print("\n" + "=" * 60)
        print(f"USER: {question}")
        print("=" * 60)

        try:
            result = app.invoke(
                {
                    **result,
                    "question": question,
                }
            )

            print("\nAI:")
            print(
                result.get(
                    "answer",
                    "No answer",
                )
            )

        except Exception as exc:
            print(
                f"\n[ERROR] {type(exc).__name__}: {exc}"
            )
            raise
