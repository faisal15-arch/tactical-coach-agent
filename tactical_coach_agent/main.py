import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.conversation_memory import (
    get_recent_messages,
    load_session_context,
    save_conversation_turn,
)
from core.auth import (
    authenticate_student,
    create_auth_session,
    create_student,
    revoke_auth_session,
    student_for_token,
)
from core.db import SessionLocal, create_tables, get_db
from core.player_stats_cache import (
    load_player_type_stats,
    save_player_type_stats,
)

from tools.live_data.cricbuzz import (
    get_live_matches,
    get_live_situation,
    get_match_details,
)
from tools.live_data.player_stats import (
    _clean_player_query,
    get_player_t20_profile,
    get_t20_bowling_stats,
)
from tools.live_data.player_matchups import (
    get_t20_batter_type_stats,
    get_t20_player_matchup,
)
from tools.analytics.line_length_model import recommend_line_length

# -------------------------------------------------------
# Environment
# -------------------------------------------------------

load_dotenv()

SESSION_TTL_SECONDS = int(
    os.getenv("SESSION_TTL_SECONDS", "86400")
)
AUTH_SESSION_HOURS = int(os.getenv("AUTH_SESSION_HOURS", "12"))
AUTH_COOKIE_NAME = "tactical_student_session"
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")

_memory_sessions = {}

# -------------------------------------------------------
# FastAPI
# -------------------------------------------------------

app = FastAPI(
    title="Tactical Coach Agent",
    version="1.0.0",
)


@app.on_event("startup")
def initialize_database():
    """Create conversation tables without making live chat depend on DB uptime."""
    try:
        create_tables()
    except Exception as exc:
        print(f"[main] WARNING: database initialization failed: {exc}")

# -------------------------------------------------------
# CORS
# -------------------------------------------------------

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_authenticated_student(request: Request, call_next):
    path = request.url.path
    protected = path.startswith("/api/") and not path.startswith("/api/auth/")
    if protected and request.method != "OPTIONS":
        db = SessionLocal()
        try:
            student = student_for_token(
                db,
                request.cookies.get(AUTH_COOKIE_NAME),
            )
            student_id = student.student_id if student else None
            student_name = student.full_name if student else None
        finally:
            db.close()
        if student is None:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Student authentication required"},
            )
            origin = request.headers.get("origin")
            if origin in allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response
        request.state.student_id = student_id
        request.state.student_name = student_name
    return await call_next(request)

# -------------------------------------------------------
# Optional project imports
# -------------------------------------------------------
# These imports are kept flexible because the project may
# have these utilities in different modules.
#
# FIX: graph.py exports the compiled graph as `graph`, not
# `coach_agent`. Importing a name that doesn't exist there
# was silently failing (caught below), which meant every
# /api/chat call fell through to "Backend graph is not
# loaded" no matter what. Alias it on import instead.
# -------------------------------------------------------

try:
    from graph import (
        graph as coach_agent,
        classify_live_question,
        get_match_info,
        get_snapshot,
        over_to_balls,
        par_at_over,
    )
except Exception as e:
    coach_agent = None
    classify_live_question = None
    get_match_info = None
    get_snapshot = None
    over_to_balls = None
    par_at_over = None
    print(f"[main] WARNING: could not import coach_agent: {e}")


# -------------------------------------------------------
# Redis helpers
# -------------------------------------------------------

try:
    from core.redis_client import cache_get, cache_set
except Exception:
    try:
        from redis_client import cache_get, cache_set
    except Exception:

        cache_get = None
        cache_set = None

        print(
            "[main] WARNING: Redis client not found. "
            "Session persistence will be disabled."
        )


# -------------------------------------------------------
# Query parser
# -------------------------------------------------------

def parse_query_metadata(
    question: str,
    previous_context: dict,
):
    """
    Detect current phase and requested over.

    Supports examples such as:
        Go to 7.4
        What happened at 18.6?
        Who should bowl next?

    FIX: phase values must match graph.py's canonical
    vocabulary ("first" / "chase"). This previously emitted
    "second" for second-innings phrasing, which only
    happened to look right because analytics_tool/score_node
    re-derive and overwrite current_phase from the question
    text on every turn -- but follow_up_node, ranking_node,
    and what_if_node pass previous_context through untouched,
    so a stray "second" would persist and disagree with the
    "chase" label graph.py uses internally (e.g. in
    ranking_node's stale-ranking check).
    """

    import re

    text = (question or "").strip().lower()

    previous_phase = previous_context.get("current_phase")
    previous_over = None
    if not previous_context.get("live_mode"):
        previous_over = (
            previous_context.get("requested_over")
            or previous_context.get("current_over")
        )

    current_phase = previous_phase or "first"

    # Phase detection
    if any(
        word in text
        for word in (
            "second innings",
            "2nd innings",
            "second inning",
            "2nd inning",
        )
    ):
        current_phase = "chase"

    elif any(
        word in text
        for word in (
            "first innings",
            "1st innings",
            "first inning",
            "1st inning",
        )
    ):
        current_phase = "first"

    # Over detection
    requested_over = None

    patterns = [
        r"\bgo to\s+(?:the\s+)?(\d+(?:\.\d+)?)(?:st|nd|rd|th)?\s*overs?\b",
        r"\bover\s+(\d+(?:\.\d+)?)(?:st|nd|rd|th)?\b",
        r"\bat\s+(\d+(?:\.\d+)?)\s*overs?\b",
        r"\bat\s+(?:the\s+)?(\d+)(?:st|nd|rd|th)\s*over\b",
        r"\b(\d+)(?:st|nd|rd|th)\s*over\b",
        r"\b(\d+(?:\.\d+)?)\s*overs?\b",
        r"\b(\d+)\.(\d+)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            if len(match.groups()) == 2:
                requested_over = f"{match.group(1)}.{match.group(2)}"
            else:
                requested_over = match.group(1)

            break

    if requested_over is None:
        requested_over = previous_over

    return current_phase, requested_over


# -------------------------------------------------------
# Redis safe wrappers
# -------------------------------------------------------

def safe_cache_get(key: str):
    if cache_get is None:
        return dict(_memory_sessions.get(key, {})) or None

    try:
        cached = cache_get(key)
        if cached is not None:
            return cached
    except Exception as e:
        print(f"[main] Redis read failed: {e}")

    return dict(_memory_sessions.get(key, {})) or None


def safe_cache_set(
    key: str,
    value: dict,
    ttl_seconds: int,
):
    _memory_sessions[key] = dict(value)

    if cache_set is None:
        return True

    try:
        cache_set(
            key,
            value,
            ttl_seconds=ttl_seconds,
        )
        return True
    except TypeError:
        # Compatibility with clients using positional TTL
        try:
            cache_set(
                key,
                value,
                ttl_seconds,
            )
            return True
        except Exception as e:
            print(f"[main] Redis write failed: {e}")
            return True
    except Exception as e:
        print(f"[main] Redis write failed: {e}")
        return True


# -------------------------------------------------------
# Request / Response models
# -------------------------------------------------------

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    match_id: Optional[int] = None


class ChatResponse(BaseModel):
    session_id: str

    answer: str = ""

    intent: Optional[str] = None

    current_over: Optional[Any] = None
    current_phase: Optional[str] = None

    score: Optional[Any] = None

    batting_team: Optional[str] = None
    bowling_team: Optional[str] = None

    requested_over: Optional[Any] = None
    live_mode: Optional[bool] = None
    match_finished: Optional[bool] = None
    match_status: Optional[str] = None
    score_projections: Optional[dict] = None

    ranking_team: Optional[str] = None
    ranking_phase: Optional[str] = None

    pressure: Optional[Any] = None
    pressure_label: Optional[str] = None

    momentum_score: Optional[Any] = None
    momentum_level: Optional[str] = None
    momentum_label: Optional[str] = None

    bowler_scores: Optional[dict] = None
    player_t20_stats: Optional[dict] = None
    effectiveness_scores: Optional[dict] = None
    form_scores: Optional[dict] = None
    matchup_scores: Optional[dict] = None
    career_matchup_scores: Optional[dict] = None
    factor_scores: Optional[dict] = None
    combined_scores: Optional[dict] = None
    confidence_scores: Optional[dict] = None
    matchup_stats: Optional[dict] = None
    career_matchup_stats: Optional[dict] = None
    bowling_type_matchups: Optional[dict] = None

    strategies: Optional[list] = None
    top_strategy: Optional[str] = None
    bowling_plan: Optional[list] = None

    recommendation: Optional[str] = None
    confidence: Optional[Any] = None

    available_bowlers: Optional[list] = None
    excluded_bowlers: Optional[dict] = None
    unrated_bowlers: Optional[list] = None
    provisional_bowlers: Optional[dict] = None

    current_bowler: Optional[str] = None
    current_batter: Optional[str] = None
    current_batsmen: Optional[list] = None
    current_batter_weakness: Optional[str] = None
    current_batter_weakness_types: Optional[dict] = None

    par_note: Optional[str] = None
    choice_note: Optional[str] = None

    side_panel: Optional[dict] = None


class StudentLoginRequest(BaseModel):
    student_id: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class StudentRegistrationRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=200)
    student_id: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=10, max_length=200)


# -------------------------------------------------------
# Persistent-memory questions
# -------------------------------------------------------

def _is_memory_question(question: str) -> bool:
    text = re.sub(r"\s+", " ", question.lower()).strip()
    phrases = (
        "what did i ask",
        "what was my question",
        "my previous question",
        "my last question",
        "what did you answer",
        "your previous answer",
        "your last answer",
        "what did you say",
        "what do you remember",
        "what were we discussing",
        "conversation history",
        "chat history",
        "previous questions",
        "earlier questions",
        "last recommendation",
        "previous recommendation",
        "who did you recommend",
    )
    return any(phrase in text for phrase in phrases)


def _answer_memory_question(
    question: str,
    db: Session,
    session_id: str,
    context: dict,
) -> str:
    """Answer common history questions from this session's saved messages."""
    text = re.sub(r"\s+", " ", question.lower()).strip(" ?!.")
    messages = get_recent_messages(db, session_id, limit=40)
    user_messages = [item for item in messages if item.role == "user"]
    assistant_messages = [item for item in messages if item.role == "assistant"]

    if "recommend" in text:
        recommendation = context.get("recommendation")
        confidence = context.get("confidence")
        if recommendation:
            suffix = f" ({confidence}% confidence)" if confidence is not None else ""
            return f"My last recommendation was {recommendation}{suffix}."

    if any(phrase in text for phrase in (
        "what did i ask", "what was my question", "my previous question",
        "my last question",
    )):
        if user_messages:
            return f'Your previous question was: "{user_messages[-1].content}"'
        return "There is no earlier question saved in this chat yet."

    if any(phrase in text for phrase in (
        "what did you answer", "your previous answer", "your last answer",
    )):
        if assistant_messages:
            return f'My previous answer was: "{assistant_messages[-1].content}"'
        return "There is no earlier answer saved in this chat yet."

    if "previous questions" in text or "earlier questions" in text:
        if not user_messages:
            return "There are no earlier questions saved in this chat yet."
        recent = user_messages[-5:]
        lines = [f"{index}. {item.content}" for index, item in enumerate(recent, 1)]
        return "Your recent questions were:\n" + "\n".join(lines)

    topic_match = re.search(r"\b(?:about|regarding)\s+(.+)$", text)
    if topic_match:
        topic = topic_match.group(1).strip(" ?!.")
        matched = [item for item in messages if topic in item.content.lower()]
        if matched:
            return f'The most recent saved message about {topic} was: "{matched[-1].content}"'
        return f"I do not have a saved message about {topic} in this chat."

    if messages:
        recent = messages[-6:]
        summary = "\n".join(
            f"{item.role.title()}: {item.content}" for item in recent
        )
        return "I remember these recent messages from this chat:\n" + summary

    return "There is no earlier conversation saved in this chat yet."


# -------------------------------------------------------
# Health check
# -------------------------------------------------------

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "Tactical Coach Agent",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "graph_loaded": coach_agent is not None,
        "redis_loaded": cache_get is not None,
    }


@app.post("/api/auth/login")
def student_login(
    request: StudentLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    student = authenticate_student(db, request.student_id, request.password)
    if student is None:
        raise HTTPException(status_code=401, detail="Invalid student ID or password")

    token, _expires_at = create_auth_session(
        db,
        student,
        lifetime_hours=AUTH_SESSION_HOURS,
    )
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_SESSION_HOURS * 3600,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return {
        "status": "ok",
        "student": {
            "student_id": student.student_id,
            "full_name": student.full_name,
        },
    }


@app.get("/api/auth/me")
def current_student(
    request: Request,
    db: Session = Depends(get_db),
):
    student = student_for_token(db, request.cookies.get(AUTH_COOKIE_NAME))
    if student is None:
        raise HTTPException(status_code=401, detail="Student authentication required")
    return {
        "status": "ok",
        "student": {
            "student_id": student.student_id,
            "full_name": student.full_name,
        },
    }


@app.post("/api/auth/register", status_code=201)
def student_register(
    request: StudentRegistrationRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        student = create_student(
            db,
            request.student_id,
            request.full_name,
            request.password,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already exists" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    token, _expires_at = create_auth_session(
        db,
        student,
        lifetime_hours=AUTH_SESSION_HOURS,
    )
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_SESSION_HOURS * 3600,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return {
        "status": "ok",
        "student": {
            "student_id": student.student_id,
            "full_name": student.full_name,
        },
    }


@app.post("/api/auth/logout")
def student_logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    revoke_auth_session(db, request.cookies.get(AUTH_COOKIE_NAME))
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path="/",
        secure=AUTH_COOKIE_SECURE,
        httponly=True,
        samesite=AUTH_COOKIE_SAMESITE,
    )
    return {"status": "ok"}


@app.get("/api/match")
def match_info():
    raise HTTPException(
        status_code=410,
        detail="Historical match mode has been retired. Select a live or completed match.",
    )


@app.get("/api/live-matches")
def live_matches():
    return get_live_matches()


@app.get("/api/live-matches/{match_id}")
def live_match_details(match_id: int):
    return get_match_details(match_id)


def _add_live_par(situation_data: dict) -> dict:
    data = dict(situation_data)
    data["par"] = None

    if data.get("format") != "T20" or par_at_over is None:
        return data

    try:
        over_number = max(1, int(float(data.get("over") or 0)))
        venue_curve, venue_source = par_at_over(
            data.get("venue"),
            over_number,
        )
        venue_average = data.get("venue_t20_average")
        if venue_curve is not None and venue_source != "all venues":
            par_runs = venue_curve
            source = venue_source
        elif venue_average and over_number >= 20:
            par_runs = float(venue_average)
            source = (
                f"{data.get('venue')} international T20 average "
                f"({venue_average} first-innings runs)"
            )
        else:
            return data
    except (TypeError, ValueError):
        return data

    if par_runs is not None:
        data["par"] = {
            "runs": par_runs,
            "difference": round(data.get("runs", 0) - par_runs, 1),
            "source": source,
            "venue_specific": True,
            "method": (
                "Historical men's T20I mean at this over"
                if venue_curve is not None and venue_source != "all venues"
                else "Published venue T20 first-innings average"
            ),
        }

    return data


def _score_projections(situation_data: dict) -> dict:
    over_text = str(situation_data.get("over") or "0")
    over_parts = over_text.split(".", 1)
    try:
        completed_overs = int(over_parts[0])
        balls_in_over = int(over_parts[1][:1]) if len(over_parts) > 1 else 0
        total_overs = int(situation_data.get("total_overs") or 20)
        current_runs = int(situation_data.get("runs") or 0)
    except (TypeError, ValueError):
        return {}

    balls_in_over = min(max(balls_in_over, 0), 6)
    balls_bowled = min(completed_overs * 6 + balls_in_over, total_overs * 6)
    balls_remaining = max(total_overs * 6 - balls_bowled, 0)
    target = None
    if situation_data.get("current_phase") == "chase":
        chase = situation_data.get("chase") or {}
        target = chase.get("target") or situation_data.get("target")

    scenarios = []
    for run_rate in (8, 10, 12):
        projected_score = round(
            current_runs + run_rate * balls_remaining / 6
        )
        scenario = {
            "run_rate": run_rate,
            "projected_score": projected_score,
        }
        if target:
            scenario["target"] = int(target)
            scenario["target_margin"] = projected_score - int(target)
            scenario["reaches_target"] = projected_score >= int(target)
        scenarios.append(scenario)

    return {
        "from_score": situation_data.get("score"),
        "from_over": over_text,
        "finish_over": total_overs,
        "balls_remaining": balls_remaining,
        "assumption": "Run rate applies to remaining balls; wickets are not modelled",
        "scenarios": scenarios,
    }


def _pressure_index(data: dict) -> tuple[int, str]:
    wickets = int(data.get("wickets") or 0)
    wicket_resource_pressure = min(wickets / 7 * 100, 100)
    chase = data.get("chase")

    if chase and not chase.get("achieved"):
        rate_gap = float(chase.get("required_run_rate") or 0) - float(
            data.get("run_rate") or 0
        )
        rate_pressure = min(max(50 + rate_gap * 7, 5), 95)
        pressure = round(
            0.65 * wicket_resource_pressure + 0.35 * rate_pressure
        )
    else:
        over_parts = str(data.get("over") or 0).split(".", 1)
        overs_bowled = int(over_parts[0]) + (
            int(over_parts[1][:1]) / 6 if len(over_parts) > 1 else 0
        )
        total_overs = max(int(data.get("total_overs") or 20), 1)
        innings_phase_pressure = min(overs_bowled / total_overs * 100, 100)
        pressure = round(
            0.75 * wicket_resource_pressure + 0.25 * innings_phase_pressure
        )

    pressure = min(max(pressure, 5), 95)
    label = (
        "Batting side under pressure" if pressure >= 65
        else "Batting side in control" if pressure <= 30
        else "Pressure is balanced"
    )
    return pressure, label


@app.get("/api/live-matches/{match_id}/situation")
def live_match_situation(
    match_id: int,
    over: Optional[str] = None,
    phase: str = "chase",
    include_profiles: bool = False,
    include_type_matchups: bool = False,
    db: Session = Depends(get_db),
):
    result = get_live_situation(
        match_id,
        over=over,
        phase=phase if phase in ("first", "chase") else "chase",
    )
    if result.get("status") != "ok":
        return result
    data = _add_live_par(result)
    if include_profiles:
        data["player_t20_stats"] = _load_live_t20_stats(
            data.get("bowling_card") or []
        )
    if include_type_matchups:
        data["bowling_type_matchups"] = _load_batter_type_matchups(data, db)
        full_bowling_card = _complete_bowling_card(match_id, data)
        data["career_matchup_stats"] = _load_current_career_matchups(
            data,
            full_bowling_card,
        )
    return data


def _get_cached_batter_type_stats(
    player_name: str,
    db: Optional[Session],
) -> dict | None:
    cached = load_player_type_stats(db, player_name)
    if cached and cached.get("schema_version") == 2:
        return cached
    stats = get_t20_batter_type_stats(player_name)
    if stats:
        save_player_type_stats(db, player_name, stats)
        return stats

    no_data = {
        "player": player_name,
        "schema_version": 2,
        "scope": "All T20",
        "source": "Cricmetric All T20",
        "categories": {},
        "error": "No recorded T20 bowling-type sample was found.",
    }
    save_player_type_stats(db, player_name, no_data)
    return no_data


def _load_batter_type_matchups(
    data: dict,
    db: Optional[Session] = None,
) -> dict:
    """Fetch cached All-T20 bowling-type splits for the active batters."""
    names = [
        batter.get("name") if isinstance(batter, dict) else batter
        for batter in (data.get("current_batsmen") or [])
    ]
    if not any(names):
        names = [
            (data.get("batsmen") or {}).get("striker"),
            (data.get("batsmen") or {}).get("non_striker"),
        ]

    results = {}
    for name in dict.fromkeys(name for name in names if name):
        clean_name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
        try:
            stats = _get_cached_batter_type_stats(clean_name, db)
        except Exception as exc:
            print(f"[main] Bowling-type lookup failed for {clean_name}: {exc}")
            results[name] = {
                "player": clean_name,
                "scope": "All T20",
                "source": "Cricmetric All T20",
                "categories": {},
                "error": (
                    "Bowling-type stats are temporarily unavailable. "
                    "Please retry shortly."
                ),
            }
            continue
        results[name] = stats or {
            "player": clean_name,
            "scope": "All T20",
            "source": "Cricmetric All T20",
            "categories": {},
            "error": "No recorded T20 bowling-type sample was found.",
        }
    return results


def _load_current_career_matchups(
    data: dict,
    bowling_card: Optional[list[dict]] = None,
) -> dict:
    """Fetch All-T20 head-to-head records against every known attack bowler."""
    names = [
        batter.get("name") if isinstance(batter, dict) else batter
        for batter in (data.get("current_batsmen") or [])
    ]
    if not any(names):
        names = [
            (data.get("batsmen") or {}).get("striker"),
            (data.get("batsmen") or {}).get("non_striker"),
        ]

    batter_names = list(dict.fromkeys(name for name in names if name))
    bowler_names = list(dict.fromkeys(
        row.get("bowler")
        for row in (bowling_card or data.get("bowling_card") or [])
        if row.get("bowler")
    ))
    if not batter_names or not bowler_names:
        return {}

    results = {batter: {} for batter in batter_names}

    def load_pair(batter: str, bowler: str) -> tuple[str, str, dict]:
        clean_batter = re.sub(r"\s*\(\d+\)\s*$", "", batter).strip()
        clean_bowler = re.sub(r"\s*\(\d+\)\s*$", "", bowler).strip()
        try:
            stats = get_t20_player_matchup(clean_batter, clean_bowler)
        except Exception as exc:
            print(
                f"[main] Career matchup lookup failed for "
                f"{clean_batter} vs {clean_bowler}: {exc}"
            )
            stats = None
        return batter, bowler, stats or {
            "batter": clean_batter,
            "bowler": clean_bowler,
            "source": "Cricmetric All T20",
            "error": "No recorded All-T20 head-to-head sample was found.",
        }

    pairs = [
        (batter, bowler)
        for batter in batter_names
        for bowler in bowler_names
    ]
    with ThreadPoolExecutor(max_workers=min(4, len(pairs))) as executor:
        futures = [executor.submit(load_pair, *pair) for pair in pairs]
        for future in as_completed(futures):
            batter, bowler, stats = future.result()
            results[batter][bowler] = stats
    return results


def _live_matchup_scores(
    bowler_names: list[str],
    matchup_stats: dict,
    current_batsmen: list,
    missing_score: int = 40,
) -> tuple[dict[str, int], dict[str, float]]:
    active_names = [
        batter.get("name") if isinstance(batter, dict) else batter
        for batter in current_batsmen
    ]
    active_names = [name for name in active_names if name]
    scores = {}
    reliabilities = {}

    for bowler in bowler_names:
        balls = 0
        runs = 0
        dismissals = 0
        for batter in active_names:
            figures = (matchup_stats.get(batter) or {}).get(bowler) or {}
            balls += int(figures.get("balls") or 0)
            runs += int(figures.get("runs") or 0)
            dismissals += int(figures.get("dismissals") or 0)

        reliability = min(balls / 24, 1)
        if balls:
            batter_strike_rate = runs / balls * 100
            control = min(max(110 - batter_strike_rate * 0.4, 0), 100)
            observed_score = min(control + dismissals * 25, 100)
            score = 50 + (observed_score - 50) * reliability
        else:
            score = missing_score

        scores[bowler] = round(score)
        reliabilities[bowler] = round(reliability, 3)

    return scores, reliabilities


def _live_analytics(
    bowling_card: list[dict],
    player_t20_stats: dict[str, dict],
    current_bowler: str | None,
    pressure: int | None,
    momentum_score: int | None,
    match_finished: bool,
    current_over_active: bool = False,
    matchup_stats: dict | None = None,
    current_batsmen: list | None = None,
    career_matchup_stats: dict | None = None,
) -> dict:
    available = [
        row for row in bowling_card
        if not row.get("quota_used")
        and row.get("bowler") != current_bowler
    ]
    available_names = [row.get("bowler") for row in available]
    all_bowler_names = [row.get("bowler") for row in bowling_card]
    matchup_scores, matchup_reliabilities = _live_matchup_scores(
        all_bowler_names,
        matchup_stats or {},
        current_batsmen or [],
    )
    career_matchup_scores, career_matchup_reliabilities = _live_matchup_scores(
        all_bowler_names,
        career_matchup_stats or {},
        current_batsmen or [],
    )
    excluded = {
        row.get("bowler"): (
            "currently bowling; cannot also bowl the next over"
            if row.get("bowler") == current_bowler
            and current_over_active
            and not row.get("quota_used")
            else "bowled the previous over; cannot bowl consecutive overs"
            if row.get("bowler") == current_bowler and not row.get("quota_used")
            else "bowling quota already used"
        )
        for row in bowling_card
        if row.get("quota_used") or row.get("bowler") == current_bowler
    }

    form_scores = {}
    form_parts = {}

    for row in bowling_card:
        balls = int(row.get("balls") or 0)
        if balls < 12:
            continue
        economy = float(row.get("economy") or 0)
        wickets = int(row.get("wickets") or 0)
        control_score = min(max(100 - economy * 8, 0), 100)
        wicket_score = min(wickets * 30, 100)
        form_score = round(0.65 * control_score + 0.35 * wicket_score)
        form_scores[row.get("bowler")] = form_score
        form_parts[row.get("bowler")] = (control_score, wicket_score)

    profile_scores = {}
    for bowler, stats in player_t20_stats.items():
        economy = float(stats.get("economy") or 0)
        strike_rate = float(stats.get("strike_rate") or 0)
        matches = int(stats.get("matches") or 0)
        wickets = int(stats.get("wickets") or 0)
        economy_score = min(max(125 - economy * 7.5, 0), 100)
        strike_score = min(max(120 - strike_rate * 3, 0), 100)
        wicket_score = min(wickets / max(matches, 1) * 40, 100)
        experience_score = min(matches / 50, 1) * 100
        profile_scores[bowler] = round(
            0.40 * economy_score
            + 0.30 * strike_score
            + 0.20 * wicket_score
            + 0.10 * experience_score
        )

    effectiveness_scores = {}
    combined_scores = {}
    planning_scores = {}
    factor_scores = {}
    for row in bowling_card:
        bowler = row.get("bowler")
        form_score = form_scores.get(bowler)
        balls = int(row.get("balls") or 0)
        economy = float(row.get("economy") or 0)
        wickets = int(row.get("wickets") or 0)
        if bowler in form_parts:
            control_score, wicket_score = form_parts[bowler]
        elif balls == 0:
            control_score, wicket_score = 40, 40
        else:
            spell_reliability = min(balls / 12, 1)
            observed_control = min(max(100 - economy * 8, 0), 100)
            observed_wickets = min(wickets * 30, 100)
            control_score = round(
                40 + (observed_control - 40) * spell_reliability
            )
            wicket_score = round(
                40 + (observed_wickets - 40) * spell_reliability
            )
        decision_form_score = form_score if form_score is not None else 40

        pressure_weight = min(max((pressure or 50) / 100, 0), 1)
        pressure_fit = (
            pressure_weight * wicket_score
            + (1 - pressure_weight) * control_score
        )
        momentum_fit = (
            40
            if momentum_score is None
            else wicket_score
            if momentum_score > 20
            else control_score
            if momentum_score < -20
            else (control_score + wicket_score) / 2
        )
        profile_score = profile_scores.get(bowler)
        effectiveness_score = (
            0.70 * profile_score + 0.30 * decision_form_score
            if profile_score is not None
            else decision_form_score
        )
        effectiveness_scores[bowler] = round(effectiveness_score)
        factor_scores[bowler] = {
            "in_match_form": round(decision_form_score),
            "innings_matchup": round(matchup_scores.get(bowler, 40)),
            "bowler_profile": round(
                profile_score if profile_score is not None else 40
            ),
            "career_matchup": round(career_matchup_scores.get(bowler, 40)),
            "momentum_fit": round(momentum_fit),
            "pressure_fit": round(pressure_fit),
        }
        score = (
            0.30 * decision_form_score
            + 0.25 * matchup_scores.get(bowler, 40)
            + 0.20 * (profile_score if profile_score is not None else 40)
            + 0.15 * career_matchup_scores.get(bowler, 40)
            + 0.06 * momentum_fit
            + 0.04 * pressure_fit
        )
        score = round(min(max(score, 0), 100))
        if not row.get("quota_used"):
            planning_scores[bowler] = score
        if bowler in available_names:
            combined_scores[bowler] = score

    strategies = []
    confidence_scores = {}
    planning_confidence_scores = {}
    if not match_finished:
        for bowler, score in planning_scores.items():
            confidence = min(max(round(score), 0), 95)
            planning_confidence_scores[bowler] = confidence
            if bowler not in combined_scores:
                continue
            confidence_scores[bowler] = planning_confidence_scores[bowler]
            strategies.append({
                "bowler": bowler,
                "confidence": confidence_scores[bowler],
            })
        strategies.sort(key=lambda item: item["confidence"], reverse=True)
        strategies = strategies[:3]
        for index, strategy in enumerate(strategies):
            bowler = strategy["bowler"]
            strategy["strategy"] = (
                f"Use {bowler} for the next over"
                if index == 0
                else f"Alternative: {bowler}"
            )
    recommendation = strategies[0].get("bowler") if strategies else None

    return {
        "available_bowlers": available_names,
        "excluded_bowlers": excluded,
        "bowler_scores": profile_scores,
        "player_t20_stats": player_t20_stats,
        "effectiveness_scores": effectiveness_scores,
        "form_scores": form_scores,
        "matchup_scores": matchup_scores,
        "matchup_reliabilities": matchup_reliabilities,
        "career_matchup_scores": career_matchup_scores,
        "career_matchup_reliabilities": career_matchup_reliabilities,
        "combined_scores": combined_scores,
        "confidence_scores": confidence_scores,
        "planning_scores": planning_scores,
        "planning_confidence_scores": planning_confidence_scores,
        "factor_scores": factor_scores,
        "strategies": strategies,
        "recommendation": recommendation,
        "top_strategy": strategies[0].get("strategy") if strategies else None,
        "unrated_bowlers": [
            bowler for bowler in available_names if bowler not in profile_scores
        ],
        "provisional_bowlers": {},
        "choice_note": (
            "Confidence: 30% in-match bowling form, 25% current-innings "
            "batter matchup, 20% bowler T20 profile, 15% career head-to-head, "
            "6% momentum fit and 4% pressure fit. A missing sample scores 40. "
            "Quota and consecutive-over rules remain hard eligibility filters."
        ),
    }


def _lineup_bowling_options(match_id: int, data: dict) -> list[dict]:
    """Return unused bowling candidates from the selected team's playing XI."""
    try:
        details = get_match_details(match_id)
    except Exception as error:
        print(f"[main] Playing XI unavailable for recommendation: {error}")
        return []
    if details.get("status") != "ok":
        return []

    bowling_team = re.sub(
        r"[^a-z0-9]",
        "",
        str(data.get("bowling_team") or "").lower(),
    )
    lineup = next(
        (
            team for team in (details.get("lineups") or [])
            if bowling_team in {
                re.sub(r"[^a-z0-9]", "", str(team.get(key) or "").lower())
                for key in ("name", "short_name")
            }
        ),
        None,
    )
    if not lineup:
        return []

    quota = 4 if int(data.get("total_overs") or 20) == 20 else None
    options = []
    for player in lineup.get("playing_xi") or []:
        role = str(player.get("role") or "").lower()
        if "bowl" not in role and "allround" not in role:
            continue
        options.append({
            "bowler": player.get("name"),
            "bowler_id": player.get("player_id"),
            "overs": "0",
            "balls": 0,
            "maidens": 0,
            "runs": 0,
            "wickets": 0,
            "economy": 0.0,
            "overs_left": quota,
            "quota_used": False,
            "rated": True,
            "provisional": True,
            "is_bowling": False,
        })
    return options


def _complete_bowling_card(match_id: int, data: dict) -> list[dict]:
    card = list(data.get("bowling_card") or [])
    known_names = {
        str(row.get("bowler") or "").strip().lower()
        for row in card
    }
    for option in _lineup_bowling_options(match_id, data):
        name_key = str(option.get("bowler") or "").strip().lower()
        if name_key and name_key not in known_names:
            card.append(option)
            known_names.add(name_key)
    return card


def _load_live_t20_stats(bowling_card: list[dict]) -> dict[str, dict]:
    stats_by_bowler = {}
    for row in bowling_card:
        if not row.get("bowler_id"):
            continue
        try:
            stats = get_t20_bowling_stats(row["bowler_id"], row["bowler"])
        except Exception as error:
            print(f"[main] T20 profile unavailable for {row.get('bowler')}: {error}")
            continue
        if stats:
            stats_by_bowler[row["bowler"]] = stats
    return stats_by_bowler


def _normalize_chat_question(question: str) -> str:
    """Canonicalize common cricket phrasings without changing player names."""
    normalized = question.lower().replace("’", "'")
    normalized = re.sub(r"^\s*hat is\b", "what is", normalized)
    normalized = re.sub(r"\bt20s\b", "t20", normalized)
    normalized = re.sub(
        r"\b(?:illegible|inelligible|non eligible)\s+bowlers?\b",
        "ineligible bowler",
        normalized,
    )
    normalized = re.sub(r"\bhow often\b", "how many times", normalized)
    normalized = re.sub(r"\b(number|total) of games\b", r"\1 of matches", normalized)
    normalized = re.sub(r"\bgames played\b|\bappearances\b", "matches played", normalized)
    normalized = re.sub(r"\bhalf[- ]centur(?:y|ies)\b", "fifties", normalized)
    normalized = re.sub(r"\btons?\b", "hundreds", normalized)

    crease_patterns = (
        r"who(?: is|'s)?(?: currently)? (?:at|on) (?:the )?crease",
        r"who(?: is|'s)? batting(?!\s+(?:1st|2nd|first|second|team))"
        r"(?: right now| now| currently)?",
        r"(?:which|what) (?:batters|batsmen) are (?:at|on) (?:the )?crease",
        r"^(?:who are (?:the )?)?(?:current|active) (?:batters|batsmen)"
        r"(?: right now| now)?[?!.]*$",
    )
    if any(re.search(pattern, normalized) for pattern in crease_patterns):
        return "who is batting"

    duck_patterns = (
        r"(?:been )?(?:out|dismissed)\s*(?:on|for|at)?\s*(?:a )?"
        r"(?:0|zero|nought|nil)(?:\s*\.\s*s)?",
        r"(?:out|dismissed) without scoring",
        r"score(?:d)? (?:a )?(?:0|zero|nought|nil)",
    )
    for pattern in duck_patterns:
        normalized = re.sub(pattern, "ducks", normalized)
    normalized = re.sub(r"\bduck\b", "ducks", normalized)
    return " ".join(normalized.split())


def _asks_for_explanation(question: str) -> bool:
    lowered = question.lower()
    return any(
        phrase in lowered
        for phrase in (
            "why",
            "what is meant",
            "what does it mean",
            "what does pressure mean",
            "what does momentum mean",
            "meaning of",
            "how is it made",
            "how is pressure made",
            "how is momentum made",
            "how is it calculated",
            "how is pressure calculated",
            "how is momentum calculated",
            "how do you calculate",
            "formula",
            "explain",
        )
    )


def _asks_what_venue_par_means(question: str) -> bool:
    lowered = question.lower()
    if "par" not in lowered:
        return False
    if any(
        phrase in lowered
        for phrase in ("right now", "currently", "at this point", "how far")
    ):
        return False
    return any(
        phrase in lowered
        for phrase in (
            "what is against par",
            "what is venue par",
            "what does against par",
            "what does venue par",
            "what is meant by par",
            "meaning of par",
            "explain venue par",
        )
    )


def _asks_for_latest_live_score(question: str) -> bool:
    lowered = question.lower()
    return any(
        phrase in lowered
        for phrase in (
            "live score",
            "latest score",
            "score right now",
            "score now",
            "current live score",
        )
    )


def _asks_to_go_live(question: str) -> bool:
    lowered = question.lower()
    return any(
        phrase in lowered
        for phrase in (
            "go live",
            "go to live",
            "go to current situation",
            "current situation",
            "take me live",
            "back to live",
            "return to live",
        )
    )


def _asks_for_bowling_ranking(question: str) -> bool:
    normalized = re.sub(
        r"\bat over\s+\d+(?:\.\d+)?\b",
        " ",
        question.lower(),
    )
    normalized = re.sub(r"[^a-z\s-]", " ", normalized)
    normalized = " ".join(normalized.split())
    return bool(re.fullmatch(
        r"(?:show|display|give me|what is)?(?: the)?(?: final|bowling|bowler|next-over)? ?ranking",
        normalized,
    )) or "rank the bowlers" in normalized


def _asks_why_not_bowler(question: str) -> bool:
    normalized = re.sub(r"[^a-z\s]", " ", question.lower())
    normalized = " ".join(normalized.split())
    if re.search(r"\bwhy\s+not\b", normalized):
        return True
    if re.search(
        r"\bwhy\s+(?:(?:can|could|should|would)\s*t|cannot)\s+"
        r"(?:i|we|you)\s+"
        r"(?:bring|use|pick|bowl)\b",
        normalized,
    ):
        return True
    has_negative = bool(re.search(
        r"\b(?:didn t|wouldn t|(?:did|would)(?: you)? not)\b",
        normalized,
    ))
    has_choice = any(
        word in normalized for word in ("pick", "choose", "recommend")
    )
    return normalized.startswith("why ") and has_negative and has_choice


def _is_bare_recommendation_why(question: str) -> bool:
    normalized = re.sub(
        r"\bat\s+over\s+\d+(?:\.\d+)?\b",
        " ",
        question.lower(),
    )
    normalized = re.sub(r"[^a-z\s]", " ", normalized)
    normalized = " ".join(normalized.split())
    return normalized in {
        "why",
        "why him",
        "why this bowler",
        "why that bowler",
        "why this plan",
        "why the plan",
        "why plan",
        "reason",
        "what is the reason",
    }


def _recommendation_reason(context: dict) -> str:
    plan = context.get("bowling_plan") or []
    if plan:
        return _explain_bowling_plan(plan, context.get("factor_scores"))
    recommendation = context.get("recommendation")
    confidence = context.get("confidence")
    return (
        f"Pick {recommendation} ({confidence}% confidence): he has the strongest "
        "weighted combination of in-match form, current-innings matchup, "
        "career T20 profile, career head-to-head, momentum and pressure fit."
    )


def _live_response_from_context(
    session_id: str,
    answer: str,
    context: dict,
) -> ChatResponse:
    current_batsmen = [
        batter.get("name") if isinstance(batter, dict) else batter
        for batter in (context.get("current_batsmen") or [])
    ]
    return ChatResponse(
        session_id=session_id,
        answer=answer,
        intent=context.get("live_intent") or "live_match",
        current_over=context.get("current_over"),
        current_phase=context.get("current_phase"),
        requested_over=context.get("requested_over"),
        live_mode=context.get("live_mode", False),
        match_finished=context.get("match_finished", False),
        match_status=context.get("match_status"),
        score_projections=context.get("score_projections"),
        score=context.get("score"),
        batting_team=context.get("batting_team"),
        bowling_team=context.get("bowling_team"),
        ranking_team=context.get("bowling_team"),
        ranking_phase=context.get("current_phase"),
        pressure=context.get("pressure"),
        pressure_label=context.get("pressure_label"),
        momentum_score=context.get("momentum_score"),
        momentum_level=context.get("momentum_level"),
        momentum_label=context.get("momentum_label"),
        bowler_scores=context.get("bowler_scores"),
        player_t20_stats=context.get("player_t20_stats"),
        effectiveness_scores=context.get("effectiveness_scores"),
        form_scores=context.get("form_scores"),
        matchup_scores=context.get("matchup_scores"),
        career_matchup_scores=context.get("career_matchup_scores"),
        factor_scores=context.get("factor_scores"),
        combined_scores=context.get("combined_scores"),
        confidence_scores=context.get("confidence_scores"),
        matchup_stats=context.get("matchup_stats"),
        career_matchup_stats=context.get("career_matchup_stats"),
        strategies=context.get("strategies"),
        top_strategy=context.get("top_strategy"),
        bowling_plan=context.get("bowling_plan"),
        recommendation=context.get("recommendation"),
        confidence=context.get("confidence"),
        available_bowlers=context.get("available_bowlers"),
        excluded_bowlers=context.get("excluded_bowlers"),
        unrated_bowlers=context.get("unrated_bowlers"),
        provisional_bowlers=context.get("provisional_bowlers"),
        current_bowler=context.get("current_bowler"),
        current_batsmen=current_batsmen,
        current_batter_weakness=context.get("current_batter_weakness"),
        current_batter_weakness_types=context.get(
            "current_batter_weakness_types"
        ),
        par_note=context.get("par_note"),
        choice_note=context.get("choice_note"),
        side_panel=context,
    )


def _is_previous_over_question(question: str) -> bool:
    lowered = question.lower()
    return any(phrase in lowered for phrase in ("previous over", "last over"))


def _split_compound_questions(question: str) -> list[str]:
    raw_parts = [
        part.strip(" ,.;")
        for part in re.split(r"\?+", question or "")
        if part.strip(" ,.;")
    ]
    parts = []
    for part in raw_parts:
        if parts and re.fullmatch(r"(?:at\s+)?over\s+\d+(?:\.\d+)?", part):
            parts[-1] = f"{parts[-1]} {part}"
        else:
            parts.append(part)
    return parts[:3] or [question]


def _requested_bowling_stat_fields(question: str) -> list[str]:
    """Return only the basic bowling fields explicitly requested by the user."""
    lowered = _normalize_chat_question(question)
    fields = []
    if re.search(r"\b(?:matches?|appearances?)\b", lowered):
        fields.append("matches")
    if re.search(r"\bwickets?\b", lowered):
        fields.append("wickets")
    if "bowling average" in lowered:
        fields.append("average")
    if "economy" in lowered or re.search(r"\becon\b", lowered):
        fields.append("economy")
    compact = re.sub(r"[\s-]+", "", lowered)
    if "bowling" in lowered and ("strikerate" in compact or re.search(r"\bsr\b", lowered)):
        fields.append("strike_rate")
    return fields


def _format_bowling_stat_selection(
    name: str,
    stats: dict,
    fields: list[str],
    scope: str,
) -> str:
    labels = {
        "matches": "matches",
        "wickets": "wickets",
        "average": "bowling average",
        "economy": "economy",
        "strike_rate": "bowling strike rate",
    }
    values = []
    for field in fields:
        value = stats.get(field)
        if isinstance(value, float):
            value = f"{value:.2f}"
        values.append(f"{labels[field]} {value if value is not None else 'unavailable'}")
    if not values:
        return ""
    if len(values) == 1:
        detail = values[0]
    else:
        detail = ", ".join(values[:-1]) + f" and {values[-1]}"
    return f"{name}'s {scope}: {detail}."


def _match_team_names(data: dict) -> list[str]:
    names = []
    for row in data.get("match_team_info") or []:
        for key in ("battingTeamShortName", "bowlingTeamShortName"):
            name = row.get(key)
            if name and name not in names:
                names.append(name)
    for key in ("batting_team", "bowling_team"):
        name = data.get(key)
        if name and name not in names:
            names.append(name)
    for row in data.get("innings_scores") or []:
        name = row.get("batting_team")
        if name and name not in names:
            names.append(name)
    return names


def _requested_match_team(
    question: str,
    data: dict,
    previous_context: dict,
) -> str | None:
    lowered = question.lower()
    for name in sorted(_match_team_names(data), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", lowered):
            return name
    if re.search(r"\b(?:its|their|that team|this team)\b", lowered):
        return previous_context.get("last_team")
    return None


def _team_score_at_context(data: dict, team: str) -> dict | None:
    if str(data.get("batting_team") or "").lower() == team.lower():
        return {
            "batting_team": data.get("batting_team"),
            "score": data.get("score"),
            "over": data.get("over"),
            "run_rate": data.get("run_rate"),
        }
    direct_match = next(
        (
            row for row in (data.get("innings_scores") or [])
            if str(row.get("batting_team") or "").lower() == team.lower()
        ),
        None,
    )
    if direct_match:
        return direct_match

    for index, info in enumerate(data.get("match_team_info") or [], start=1):
        if str(info.get("battingTeamShortName") or "").lower() != team.lower():
            continue
        return next(
            (
                row for row in (data.get("innings_scores") or [])
                if int(row.get("innings_id") or 0) == index
            ),
            None,
        )
    return None


def _named_matchup_players(question: str) -> tuple[str, str] | None:
    parts = re.split(r"\s+(?:against|versus|vs\.?)(?:\s+|$)", question, maxsplit=1)
    if len(parts) != 2:
        return None
    batter = _clean_player_query(parts[0])
    bowler = _clean_player_query(parts[1])
    if not batter or not bowler:
        return None
    return batter, bowler


def _requested_bowling_type_keys(question: str) -> list[str]:
    text = question.lower().replace("-", " ")
    requested = []
    if re.search(r"\bright\s*arm\s+off\s*spin", text):
        requested.append("right_arm_off_spin")
    elif re.search(r"\bleft\s*arm\s+(?:off|finger)\s*spin|\borthodox", text):
        requested.append("left_arm_finger_spin")
    elif re.search(r"\boff\s*spin(?:ner)?s?\b|\bfinger\s*spin", text):
        requested.extend(("right_arm_off_spin", "left_arm_finger_spin"))

    if re.search(r"\bright\s*arm\s+leg\s*spin", text):
        requested.append("right_arm_leg_spin")
    elif re.search(
        r"\bleft\s*arm\s+(?:leg|wrist)\s*spin|\bchinaman",
        text,
    ):
        requested.append("left_arm_wrist_spin")
    elif re.search(r"\bleg\s*spin(?:ner)?s?\b|\bwrist\s*spin", text):
        requested.extend(("right_arm_leg_spin", "left_arm_wrist_spin"))
    if re.search(r"\bleft\s*arm\s+(?:fast|pace|medium)", text):
        requested.append("left_arm_pace")
    if re.search(r"\bright\s*arm\s+(?:fast|pace|medium)", text):
        requested.append("right_arm_pace")
    if not requested and re.search(r"\bagainst\s+(?:the\s+)?spin(?:ners?)?\b", text):
        requested.extend((
            "right_arm_off_spin",
            "left_arm_finger_spin",
            "right_arm_leg_spin",
            "left_arm_wrist_spin",
        ))
    if not requested and re.search(r"\bagainst\s+(?:the\s+)?(?:pace|fast\s+bowler)", text):
        requested.extend(("left_arm_pace", "right_arm_pace"))
    return requested


def _is_bowling_type_question(question: str) -> bool:
    text = question.lower().replace("-", " ")
    return bool(
        _requested_bowling_type_keys(text)
        or "bowling type" in text
        or "type of bowler" in text
        or "types of bowler" in text
    )


def _format_batter_type_stats(
    stats: dict,
    requested_keys: list[str],
) -> str:
    categories = stats.get("categories") or {}
    keys = requested_keys or list(categories)
    rows = []
    for key in keys:
        row = categories.get(key)
        if not row:
            continue
        rate = row.get("strike_rate")
        rate_text = f"SR {rate:.1f}" if rate is not None else "SR unavailable"
        assessment = row.get("assessment") or "No sample"
        reliability = row.get("sample_reliability") or "none"
        rows.append(
            f"{row['label']}: {row['runs']} runs from {row['balls']} balls, "
            f"{row['dismissals']} dismissals, {rate_text}; {assessment.lower()} "
            f"({reliability} reliability)"
        )
    if not rows:
        return f"No recorded All-T20 bowling-type sample was found for {stats['player']}."
    return f"{stats['player']} in All T20s - " + "; ".join(rows) + "."


def _bowler_type_key(bowling_style: str | None) -> str | None:
    style = str(bowling_style or "").lower().replace("-", " ")
    if ("right arm" in style or "left arm" not in style) and (
        "offbreak" in style or "off spin" in style
    ):
        return "right_arm_off_spin"
    if "left arm" in style and ("orthodox" in style or "finger" in style):
        return "left_arm_finger_spin"
    if ("right arm" in style or "left arm" not in style) and (
        "legbreak" in style or "leg spin" in style
    ):
        return "right_arm_leg_spin"
    if "left arm" in style and (
        "chinaman" in style or "wrist" in style or "unorthodox" in style
    ):
        return "left_arm_wrist_spin"
    if "left arm" in style and any(
        term in style for term in ("fast", "medium", "pace")
    ):
        return "left_arm_pace"
    if "right arm" in style and any(
        term in style for term in ("fast", "medium", "pace")
    ):
        return "right_arm_pace"
    return None


def _is_line_length_question(question: str) -> bool:
    """Detect a request for an actionable bowling line-and-length plan."""
    text = re.sub(r"\s+", " ", question.lower()).strip()
    return bool(
        re.search(r"\bline\s*(?:and|&)?\s*length\b", text)
        or re.search(r"\b(?:what|hat|which|kis)\s+line\b", text)
        or re.search(r"\b(?:what|hat|which|kis)\s+length\b", text)
        or re.search(r"\b(?:where|kahan)\s+should\s+.+\s+bowl\b", text)
        or re.search(r"\bhow\s+should\s+.+\s+bowl\b", text)
    )


def _line_length_advice(
    bowler: str,
    bowling_style: str | None,
    over: str | float | None,
    total_overs: int = 20,
) -> str:
    """Return concise, rule-based T20 guidance without claiming pitch-map data."""
    try:
        over_number = float(over or 0)
    except (TypeError, ValueError):
        over_number = 0

    powerplay_end = min(6, total_overs * 0.30)
    death_start = max(16, total_overs * 0.80)
    phase = (
        "powerplay" if over_number <= powerplay_end
        else "death overs" if over_number >= death_start
        else "middle overs"
    )
    bowler_type = _bowler_type_key(bowling_style)

    if bowler_type in {"left_arm_pace", "right_arm_pace"}:
        if phase == "powerplay":
            primary = "a fourth-stump good length, making the batter play"
            variation = "one fuller attacking ball at the stumps"
            avoid = "easy width or a predictable short ball"
        elif phase == "death overs":
            primary = "the base of off stump with yorkers, mixing in the wide yorker"
            variation = "an occasional slower hard-length ball into the pitch"
            avoid = "slot length"
        else:
            primary = "a hard back-of-a-length line around off stump"
            variation = "a slower ball into the pitch"
            avoid = "repeated full balls in the hitting arc"
    elif bowler_type in {
        "right_arm_off_spin",
        "left_arm_finger_spin",
        "right_arm_leg_spin",
        "left_arm_wrist_spin",
    }:
        if phase == "death overs":
            primary = "a quick, full length at the stumps"
            variation = "a wider ball outside off with a change of pace"
            avoid = "dragging the ball short or feeding the slot"
        elif phase == "powerplay":
            primary = "a full good length on off stump, attacking the stumps"
            variation = "a slightly wider line with a change of pace"
            avoid = "short balls that let the batter play off the back foot"
        else:
            primary = "a good length just outside off stump"
            variation = "changes of pace and trajectory while keeping the stumps in play"
            avoid = "a predictable short length"
    else:
        primary = "a good length around the top of off stump"
        variation = "change pace only after establishing that line"
        avoid = "slot length and unnecessary width"

    style_note = f" ({bowling_style})" if bowling_style else ""
    return (
        f"{bowler}{style_note}, {phase}: bowl {primary}. "
        f"Variation: {variation}. Avoid {avoid}."
    )


def _describe_line_length(line: str, length: str) -> str:
    """Turn model category labels into a natural coaching instruction."""
    article = "an" if length[:1].lower() in "aeiou" else "a"
    if line == "wide outside off":
        return (
            f"{article} wide {length} outside off stump"
            if length == "yorker" else
            f"{article} {length} on a wide line outside off stump"
        )
    line_text = {
        "outside off": "outside off stump",
        "off stump": "on off stump",
        "middle stump": "at middle stump",
        "leg stump": "on leg stump",
        "down leg": "down the leg side",
        "body": "at the batter's body",
    }.get(line, f"on {line}")
    return f"{article} {length} {line_text}"


def _line_length_reason(context: dict) -> str:
    plan = context.get("line_length_plan") or {}
    if plan.get("source") == "model":
        evidence = (
            f"{plan.get('batter_balls', 0)} current-batter-specific and "
            f"{plan.get('group_balls', 0)} comparable T20 balls"
            if plan.get("batter_specific") else
            f"{plan.get('group_balls', 0)} comparable T20 balls; no reliable "
            "current-batter-specific sample"
        )
        return (
            "Why: this option had the best historical run-control and wicket "
            f"balance for this bowler type and phase: {plan.get('expected_runs', 0):.2f} "
            f"runs per ball, about {plan.get('boundaries_per_hundred', 0)} boundaries "
            f"and {plan.get('wickets_per_hundred', 0)} wickets per 100 annotated "
            f"balls. Evidence: {plan.get('reliability', 'low')} ({evidence})."
        )
    reason = plan.get("reason")
    return (
        f"Why: {reason}"
        if reason else
        "No saved line-and-length explanation is available yet."
    )


def _bowling_type_request_label(keys: list[str]) -> str:
    key_set = set(keys)
    if key_set == {"right_arm_off_spin", "left_arm_finger_spin"}:
        return "off-spinner"
    if key_set == {"right_arm_leg_spin", "left_arm_wrist_spin"}:
        return "leg-spinner"
    labels = {
        "right_arm_off_spin": "right-arm off-spinner",
        "left_arm_finger_spin": "left-arm finger spinner",
        "right_arm_leg_spin": "right-arm leg-spinner",
        "left_arm_wrist_spin": "left-arm wrist spinner",
        "left_arm_pace": "left-arm pacer",
        "right_arm_pace": "right-arm pacer",
    }
    return labels.get(keys[0], "requested-type bowler") if keys else "bowler"


def _current_batter_weakness(
    data: dict,
    db: Optional[Session],
) -> tuple[str, dict[str, str]]:
    type_matchups = data.get("bowling_type_matchups") or (
        _load_batter_type_matchups(data, db)
    )
    data["bowling_type_matchups"] = type_matchups
    weakness_keys = {}
    rows = []

    for batter in data.get("current_batsmen") or []:
        name = batter.get("name") if isinstance(batter, dict) else batter
        if not name:
            continue
        categories = (type_matchups.get(name) or {}).get("categories") or {}
        reliable = [
            (key, stats)
            for key, stats in categories.items()
            if int(stats.get("balls") or 0) >= 18
            and stats.get("strength_score") is not None
        ]
        if not reliable:
            continue
        key, weakest = min(
            reliable,
            key=lambda item: item[1]["strength_score"],
        )
        weakness_keys[name] = key
        rows.append(
            f"{name}: {weakest['label']} (SR {weakest['strike_rate']:.1f}, "
            f"{weakest['dismissals']} dismissals)"
        )

    return (
        "Current-batter weakness - " + "; ".join(rows) + "."
        if rows else
        ""
    ), weakness_keys


def _best_ranked_bowler_for_types(
    analytics: dict,
    player_t20_stats: dict,
    requested_keys: list[str],
) -> tuple[str, int, str] | None:
    candidates = []
    for bowler, confidence in analytics.get("confidence_scores", {}).items():
        profile = player_t20_stats.get(bowler) or {}
        style = profile.get("bowling_style")
        if _bowler_type_key(style) in requested_keys:
            candidates.append((bowler, confidence, style))
    return max(candidates, key=lambda row: row[1]) if candidates else None


def _requested_plan_length(question: str) -> int:
    text = question.lower()
    return 4 if re.search(r"\b(?:4|four)\s*overs?\b", text) else 3


def _asks_for_bowling_plan(question: str) -> bool:
    text = question.lower()
    return bool(
        "who should bowl" in text
        or "bowl next" in text
        or re.search(
            r"\b(?:next\s+)?(?:3|three|4|four)[- ]over\s+plan\b"
            r"|\bbowling\s+plan\b",
            text,
        )
    )


CONFIDENCE_FACTOR_META = {
    "in_match_form": ("in-match form", 0.30),
    "innings_matchup": ("current-innings matchup", 0.25),
    "bowler_profile": ("T20 bowler profile", 0.20),
    "career_matchup": ("career head-to-head", 0.15),
    "momentum_fit": ("momentum fit", 0.06),
    "pressure_fit": ("pressure fit", 0.04),
}


def _confidence_x_factor_key(scores: dict | None) -> str | None:
    if not scores:
        return None
    key = max(
        CONFIDENCE_FACTOR_META,
        key=lambda factor: (
            CONFIDENCE_FACTOR_META[factor][1]
            * (float(scores.get(factor, 40)) - 40)
        ),
    )
    label, weight = CONFIDENCE_FACTOR_META[key]
    score = round(float(scores.get(key, 40)))
    if weight * (score - 40) <= 0:
        return None
    return key


def _confidence_x_factor(scores: dict | None) -> str:
    key = _confidence_x_factor_key(scores)
    if key is None:
        return "no clear data edge yet"
    label, _weight = CONFIDENCE_FACTOR_META[key]
    score = round(float(scores.get(key, 40)))
    return f"{label} {score}/100"


def _matchup_evidence(
    matchup_stats: dict,
    bowler: str,
    current_batsmen: list,
) -> tuple[int, int, int]:
    runs = balls = dismissals = 0
    for batter in current_batsmen:
        name = batter.get("name") if isinstance(batter, dict) else batter
        row = (matchup_stats.get(name) or {}).get(bowler) or {}
        runs += int(row.get("runs") or 0)
        balls += int(row.get("balls") or 0)
        dismissals += int(row.get("dismissals") or 0)
    return runs, balls, dismissals


def _factor_evidence_reason(
    bowler: str,
    factor_key: str | None,
    data: dict,
    analytics: dict,
) -> str:
    if factor_key in {"innings_matchup", "career_matchup"}:
        source_key = (
            "matchup_stats"
            if factor_key == "innings_matchup"
            else "career_matchup_stats"
        )
        runs, balls, dismissals = _matchup_evidence(
            data.get(source_key) or {},
            bowler,
            data.get("current_batsmen") or [],
        )
        scope = "this innings" if factor_key == "innings_matchup" else "career T20s"
        if balls:
            dismissal_word = "time" if dismissals == 1 else "times"
            return (
                f"he has conceded {runs} runs from {balls} balls and dismissed "
                f"the current batters {dismissals} {dismissal_word} in {scope}"
            )

    if factor_key == "in_match_form":
        row = next(
            (
                item for item in (data.get("bowling_card") or [])
                if item.get("bowler") == bowler
            ),
            {},
        )
        return (
            f"his current spell is {row.get('overs', '0')}-"
            f"{row.get('runs', 0)}-{row.get('wickets', 0)} at economy "
            f"{float(row.get('economy') or 0):.2f}"
        )

    if factor_key == "bowler_profile":
        profile = (analytics.get("player_t20_stats") or {}).get(bowler) or {}
        return (
            f"his T20 profile has economy {profile.get('economy', '-')}, "
            f"{profile.get('wickets', '-')} wickets in "
            f"{profile.get('matches', '-')} matches"
        )

    factor_scores = (analytics.get("factor_scores") or {}).get(bowler) or {}
    if factor_key in {"momentum_fit", "pressure_fit"}:
        label, _weight = CONFIDENCE_FACTOR_META[factor_key]
        return f"his {label} is {factor_scores.get(factor_key, 40)}/100"
    return "he has the highest available confidence for that over"


def _build_bowling_plan(
    data: dict,
    analytics: dict,
    requested_length: int = 3,
) -> list[dict]:
    """Find the best legal 3/4-over sequence from current evidence."""
    over_parts = str(data.get("over") or "0").split(".", 1)
    completed_overs = int(over_parts[0] or 0)
    balls_in_current = (
        int(over_parts[1][:1] or 0) if len(over_parts) > 1 else 0
    )
    total_overs = int(data.get("total_overs") or 20)
    current_over_in_progress = balls_in_current > 0
    remaining_full_overs = max(
        total_overs - completed_overs - (1 if current_over_in_progress else 0),
        0,
    )
    plan_length = min(requested_length, remaining_full_overs)
    if plan_length <= 0:
        return []

    confidence_scores = analytics.get("planning_confidence_scores") or {}
    factor_scores = analytics.get("factor_scores") or {}
    quota = {
        row.get("bowler"): max(int(float(row.get("overs_left") or 0)), 0)
        for row in (data.get("bowling_card") or [])
        if row.get("bowler") in confidence_scores
    }
    previous_bowler = data.get("current_bowler")

    best_sequence = []
    best_score = float("-inf")

    def search(sequence: list[str], remaining: dict, last: str | None, score: float):
        nonlocal best_sequence, best_score
        if len(sequence) == plan_length:
            if score > best_score:
                best_sequence = list(sequence)
                best_score = score
            return

        candidates = [
            bowler for bowler, overs_left in remaining.items()
            if overs_left > 0 and bowler != last
        ]
        for bowler in candidates:
            next_remaining = dict(remaining)
            next_remaining[bowler] -= 1
            search(
                sequence + [bowler],
                next_remaining,
                bowler,
                score + confidence_scores[bowler],
            )

    search([], quota, previous_bowler, 0)
    if not best_sequence:
        return []

    first_over = completed_overs + (2 if current_over_in_progress else 1)
    remaining = dict(quota)
    plan = []
    for index, bowler in enumerate(best_sequence):
        remaining[bowler] -= 1
        factor_key = _confidence_x_factor_key(factor_scores.get(bowler))
        plan.append({
            "over": first_over + index,
            "bowler": bowler,
            "confidence": confidence_scores[bowler],
            "reason": "highest weighted confidence for this rotation",
            "x_factor": _confidence_x_factor(factor_scores.get(bowler)),
            "x_factor_key": factor_key,
            "x_factor_reason": _factor_evidence_reason(
                bowler,
                factor_key,
                data,
                analytics,
            ),
            "quota_after": remaining[bowler],
        })
    return plan


def _format_bowling_plan(plan: list[dict]) -> str:
    if not plan:
        return "A legal multi-over bowling plan is not available at this point."
    rows = [
        f"Over {row['over']}: {row['bowler']} ({row['confidence']}%)"
        for row in plan
    ]
    return (
        f"Next {len(plan)}-over plan - " + "; ".join(rows)
        + ". Reassess after every over as wickets and form change."
    )


def _explain_bowling_plan(
    plan: list[dict],
    factor_scores: Optional[dict] = None,
) -> str:
    if not plan:
        return "No saved multi-over bowling plan is available yet."
    rows = []
    for row in plan:
        bowler = row["bowler"]
        detailed_reason = row.get("x_factor_reason")
        if detailed_reason:
            rows.append(f"Over {row['over']}: {bowler} because {detailed_reason}")
            continue
        x_factor = row.get("x_factor") or _confidence_x_factor(
            (factor_scores or {}).get(bowler)
        )
        if x_factor == "no clear data edge yet":
            reason = "he has the highest available confidence for that over"
        else:
            label, score = x_factor.rsplit(" ", 1)
            reason = f"{label} is his strongest factor ({score})"
        rows.append(f"Over {row['over']}: {bowler} because {reason}")
    return ". ".join(rows) + "."


def _referenced_batter_name(
    question: str,
    requested_batter: dict | None,
    data: dict,
    previous_context: dict,
) -> str | None:
    """Resolve conversational batter references from the current session."""
    if requested_batter:
        return requested_batter.get("batter")

    lowered = _normalize_chat_question(question)
    refers_to_batter = bool(
        re.search(
            r"\b(?:that|this|current|the|his)\s+(?:batter|batsman|batsmen)\b"
            r"|\b(?:he|him|his)\b",
            lowered,
        )
    )
    if not refers_to_batter:
        return None

    last_batter = previous_context.get("last_batter")
    if last_batter:
        return last_batter

    current_batter = data.get("current_batter") or previous_context.get(
        "current_batter"
    )
    if current_batter:
        return current_batter

    current_batsmen = (
        data.get("current_batsmen")
        or previous_context.get("current_batsmen")
        or []
    )
    if not current_batsmen:
        return None
    first = current_batsmen[0]
    return first.get("name") if isinstance(first, dict) else first


def _answer_player_t20_question(question: str, profile: dict) -> str:
    lowered = _normalize_chat_question(question)
    compact_question = re.sub(r"[\s-]+", "", lowered)
    name = profile["name"]
    batting = profile.get("batting") or {}
    bowling = profile.get("bowling") or {}

    requested_bowling_fields = _requested_bowling_stat_fields(lowered)
    if len(requested_bowling_fields) > 1:
        selected_stats = {
            "matches": batting.get("matches", bowling.get("matches")),
            "wickets": bowling.get("wickets"),
            "average": bowling.get("avg"),
            "economy": bowling.get("eco"),
            "strike_rate": bowling.get("sr"),
        }
        return _format_bowling_stat_selection(
            name,
            selected_stats,
            requested_bowling_fields,
            "T20 bowling stats",
        )

    if "rank" in lowered:
        rankings = profile.get("rankings") or {}
        batting_rank = (rankings.get("bat") or {}).get("t20Rank", "unranked")
        bowling_rank = (rankings.get("bowl") or {}).get("t20Rank", "unranked")
        return (
            f"{name}'s current Cricbuzz T20 rankings: batting {batting_rank}, "
            f"bowling {bowling_rank}."
        )
    if (
        "born" in lowered
        or "date of birth" in lowered
        or "dob" in lowered
        or " age " in f" {lowered} "
    ):
        return f"{name}'s profile lists date of birth/age as {profile.get('date_of_birth') or 'unavailable'}."
    if "role" in lowered:
        return f"{name}'s role is {profile.get('role') or 'unavailable'}."
    if "style" in lowered:
        return (
            f"{name} bats {profile.get('batting_style') or 'style unavailable'} "
            f"and bowls {profile.get('bowling_style') or 'style unavailable'}."
        )
    if "team" in lowered:
        return f"Teams listed for {name}: {profile.get('teams') or 'unavailable'}."
    if "hundred" in lowered or "centur" in lowered or "100s" in lowered:
        if "double" in lowered or "200" in lowered or "200s" in lowered:
            return f"{name} has {batting.get('200s', 'no recorded')} T20 double hundreds."
        if "triple" in lowered or "300" in lowered or "300s" in lowered:
            return f"{name} has {batting.get('300s', 'no recorded')} T20 triple hundreds."
        return (
            f"{name} has {batting.get('100s', 'no recorded')} hundreds in "
            f"Cricbuzz's T20 column, from {batting.get('matches', 'unknown')} matches."
        )
    if "fift" in lowered or "half centur" in lowered or "50s" in lowered:
        return f"{name} has {batting.get('50s', 'no recorded')} T20 fifties."
    if "highest" in lowered or "top score" in lowered:
        return f"{name}'s highest T20 score is {batting.get('highest', 'unavailable')}."
    if any(term in lowered for term in ("five wicket", "5 wicket", "5w")):
        return f"{name} has {bowling.get('5w', 'no recorded')} T20 five-wicket hauls."
    if any(term in lowered for term in ("four wicket", "4 wicket", "4w")):
        return f"{name} has {bowling.get('4w', 'no recorded')} T20 four-wicket hauls."
    if any(term in lowered for term in ("ten wicket", "10 wicket", "10w")):
        return f"{name} has {bowling.get('10w', 'no recorded')} T20 ten-wicket hauls."
    if "six" in lowered:
        return f"{name} has hit {batting.get('sixes', 'an unavailable number of')} T20 sixes."
    if "four" in lowered:
        return f"{name} has hit {batting.get('fours', 'an unavailable number of')} T20 fours."
    if "bowling" in lowered and "average" in lowered:
        return f"{name}'s T20 bowling average is {bowling.get('avg', 'unavailable')}."
    if "average" in lowered:
        return f"{name}'s T20 batting average is {batting.get('average', 'unavailable')}."
    if "economy" in lowered or "econ" in lowered:
        return f"{name}'s T20 bowling economy is {bowling.get('eco', 'unavailable')}."
    if "strikerate" in compact_question or "sr" in lowered:
        table = bowling if "bowl" in lowered else batting
        discipline = "bowling" if "bowl" in lowered else "batting"
        return f"{name}'s T20 {discipline} strike rate is {table.get('sr', 'unavailable')}."
    if "wicket" in lowered:
        return f"{name} has {bowling.get('wickets', 'an unavailable number of')} T20 wickets."
    if "maiden" in lowered:
        return f"{name} has bowled {bowling.get('maidens', 'an unavailable number of')} T20 maidens."
    if any(term in lowered for term in ("best bowling", "bowling figure", "bbi", "bbm")):
        if "match" in lowered or "bbm" in lowered:
            return f"{name}'s best T20 bowling in a match is {bowling.get('bbm', 'unavailable')}."
        return f"{name}'s best T20 bowling in an innings is {bowling.get('bbi', 'unavailable')}."
    if "not out" in lowered or "not-out" in lowered:
        return f"{name} has {batting.get('not out', 'an unavailable number of')} T20 not-outs."
    if "duck" in lowered:
        return f"{name} has {batting.get('ducks', 'an unavailable number of')} T20 ducks."
    if "innings" in lowered:
        table = bowling if "bowl" in lowered else batting
        discipline = "bowling" if "bowl" in lowered else "batting"
        return f"{name} has {table.get('innings', 'an unavailable number of')} T20 {discipline} innings."
    if "ball" in lowered:
        if "bowl" in lowered:
            return f"{name} has bowled {bowling.get('balls', 'an unavailable number of')} balls in T20s."
        return f"{name} has faced {batting.get('balls', 'an unavailable number of')} balls in T20s."
    if "conced" in lowered or ("bowling" in lowered and "run" in lowered):
        return f"{name} has conceded {bowling.get('runs', 'an unavailable number of')} T20 runs."
    if "run" in lowered:
        return f"{name} has scored {batting.get('runs', 'an unavailable number of')} T20 runs."
    if "match" in lowered or "appear" in lowered or "played" in lowered:
        return f"{name} has played {batting.get('matches', 'an unavailable number of')} T20 matches."

    return (
        f"{name}'s T20 profile: {batting.get('runs', 0)} runs in "
        f"{batting.get('matches', 0)} matches, batting average "
        f"{batting.get('average', 0)}, strike rate {batting.get('sr', 0)}; "
        f"{bowling.get('wickets', 0)} wickets, bowling average "
        f"{bowling.get('avg', 0)} and economy {bowling.get('eco', 0)}."
    )


def _live_chat_response(
    question: str,
    session_id: str,
    match_id: int,
    previous_context: dict,
    db: Optional[Session] = None,
) -> ChatResponse:
    lowered = _normalize_chat_question(question)
    live_intent = (
        classify_live_question(lowered, previous_context)
        if classify_live_question is not None
        else "general"
    )
    if (
        _is_bare_recommendation_why(lowered)
        and previous_context.get("last_answer_type") == "line_length_advice"
        and previous_context.get("line_length_plan")
    ):
        context = {
            **previous_context,
            "last_answer_type": "line_length_explanation",
        }
        safe_cache_set(f"session:{session_id}", context, SESSION_TTL_SECONDS)
        return _live_response_from_context(
            session_id,
            _line_length_reason(context),
            context,
        )
    if (
        _is_bare_recommendation_why(lowered)
        and previous_context.get("recommendation")
        and previous_context.get("last_answer_type") in {
            "bowling_recommendation",
            "recommendation_explanation",
        }
    ):
        context = {
            **previous_context,
            "last_answer_type": "recommendation_explanation",
        }
        safe_cache_set(f"session:{session_id}", context, SESSION_TTL_SECONDS)
        return _live_response_from_context(
            session_id,
            _recommendation_reason(context),
            context,
        )

    latest_score_requested = (
        live_intent == "live_score"
        or _asks_for_latest_live_score(lowered)
    )
    go_live_requested = (
        live_intent == "go_live"
        or _asks_to_go_live(lowered)
    )
    current_phase, requested_over = parse_query_metadata(
        question,
        previous_context,
    )
    if latest_score_requested or go_live_requested:
        requested_over = None

    live_mode = go_live_requested or (
        bool(previous_context.get("live_mode"))
        and requested_over is None
    )

    situation_data = get_live_situation(
        match_id,
        over=str(requested_over) if requested_over is not None else None,
        phase=current_phase,
    )
    if situation_data.get("status") != "ok":
        return ChatResponse(
            session_id=session_id,
            answer=situation_data.get("message", "The live score is unavailable."),
        )

    data = _add_live_par(situation_data)
    data["score_projections"] = _score_projections(data)
    if latest_score_requested and previous_context.get("requested_over") is not None:
        answer = (
            f"Live score now: {data['batting_team']} {data['score']} after "
            f"{data['over']} overs (run rate {data['run_rate']:.2f})."
        )
        context = {
            **previous_context,
            "latest_live_score": data.get("score"),
            "latest_live_over": data.get("over"),
            "last_answer_type": "live_score",
        }
        safe_cache_set(f"session:{session_id}", context, SESSION_TTL_SECONDS)
        return _live_response_from_context(session_id, answer, context)

    bowling_card = data.get("bowling_card") or []
    match_state = str(data.get("state") or "").strip().lower()
    match_finished = not data.get("historical_point") and match_state in {
        "complete",
        "completed",
        "result",
        "abandon",
        "abandoned",
    }
    match_not_live = match_finished or match_state in {"preview", "upcoming"}
    if match_finished:
        live_mode = False
    needs_full_bowling_attack = (
        live_intent in {"bowling_plan", "bowling_ranking", "line_length"}
        or _asks_for_bowling_plan(lowered)
        or _asks_for_bowling_ranking(lowered)
        or _is_line_length_question(lowered)
        or "confidence" in lowered
        or _asks_why_not_bowler(lowered)
        or (
            "recommend" in lowered
            and any(word in lowered for word in ("spin", "pace", "fast"))
        )
    )
    if needs_full_bowling_attack and not match_not_live:
        bowling_card = _complete_bowling_card(match_id, data)
        data["bowling_card"] = bowling_card
        data["career_matchup_stats"] = _load_current_career_matchups(
            data,
            bowling_card,
        )
    par = data.get("par")
    chase = data.get("chase")
    pressure, pressure_label = _pressure_index(data)

    recent_runs = data.get("recent_over_runs") or []
    momentum_score = None
    momentum_level = None
    momentum_label = None
    if recent_runs:
        momentum_score = round(
            min(
                max(
                    (
                        sum(recent_runs) / len(recent_runs)
                        - data.get("run_rate", 0)
                    ) * 10,
                    -100,
                ),
                100,
            )
        )
        momentum_level = (
            "High" if momentum_score > 20
            else "Low" if momentum_score < -20
            else "Neutral"
        )
        momentum_label = (
            "Recent overs are accelerating" if momentum_score > 20
            else "Recent scoring has slowed" if momentum_score < -20
            else "Recent scoring matches the innings rate"
        )
    player_t20_stats = _load_live_t20_stats(bowling_card)
    analytics = _live_analytics(
        bowling_card,
        player_t20_stats,
        data.get("current_bowler"),
        pressure,
        momentum_score,
        match_finished,
        current_over_active=(
            "." in str(data.get("over") or "")
            and int(str(data.get("over")).split(".", 1)[1][:1] or 0) > 0
        ),
        matchup_stats=data.get("matchup_stats") or {},
        current_batsmen=data.get("current_batsmen") or [],
        career_matchup_stats=data.get("career_matchup_stats") or {},
    )
    recommendation = analytics["recommendation"]
    confidence = (
        analytics["strategies"][0].get("confidence")
        if analytics["strategies"] else None
    )
    combined_scores = analytics["combined_scores"]
    requested_bowler = next(
        (
            row
            for row in bowling_card
            if str(row.get("bowler") or "").lower() in lowered
            or any(
                len(part) > 3 and part.lower() in lowered
                for part in str(row.get("bowler") or "").split()
            )
        ),
        None,
    )
    if requested_bowler is None and any(
        phrase in lowered
        for phrase in (
            "that bowler",
            "this bowler",
            "his bowling",
            "his economy",
            "its economy",
            "his wickets",
            "its wickets",
            "his stats",
            "its stats",
            "why not this",
            "why not that",
            "bring this bowler",
            "bring that bowler",
        )
    ):
        previous_bowler = previous_context.get("last_bowler")
        requested_bowler = next(
            (
                row for row in bowling_card
                if row.get("bowler") == previous_bowler
            ),
            None,
        )
    requested_batter = next(
        (
            row for row in (data.get("batting_card") or [])
            if str(row.get("batter") or "").lower() in lowered
            or any(
                len(part) > 3 and part.lower() in lowered
                for part in str(row.get("batter") or "").split()
            )
        ),
        None,
    )
    referenced_batter = _referenced_batter_name(
        lowered,
        requested_batter,
        data,
        previous_context,
    )
    if requested_batter is None and referenced_batter:
        requested_batter = next(
            (
                row for row in (data.get("batting_card") or [])
                if row.get("batter") == referenced_batter
            ),
            {"batter": referenced_batter},
        )
    form_ranking = sorted(
        analytics["form_scores"].items(),
        key=lambda item: item[1],
        reverse=True,
    )

    explanation_requested = (
        live_intent == "explanation"
        or _asks_for_explanation(lowered)
    )
    bowling_ranking_requested = (
        live_intent == "bowling_ranking"
        or _asks_for_bowling_ranking(lowered)
    )
    requested_type_keys = _requested_bowling_type_keys(lowered)
    type_recommendation_requested = bool(
        requested_type_keys
        and re.search(
            r"\brecommend\b|\bwho should bowl\b|\bbowl next\b|\bbest\s+"
            r"(?:off|leg|left|right)",
            lowered,
        )
    )
    bowling_type_requested = (
        _is_bowling_type_question(lowered)
        and not type_recommendation_requested
    )
    matchup_requested = not bowling_type_requested and "par" not in lowered and any(
        term in lowered
        for term in ("matchup", "versus", " vs ", "against")
    )
    named_matchup = _named_matchup_players(lowered) if matchup_requested else None
    current_figures_requested = (
        any(
            phrase in lowered
            for phrase in ("bowling figure", "bowling figures")
        )
        and not any(
            scope in lowered for scope in ("best", "career", "t20")
        )
    )
    current_spell_requested = current_figures_requested or any(
        phrase in lowered
        for phrase in (
            "current spell",
            "this spell",
            "this match",
            "current match",
            "that match",
            "current inning",
            "current innings",
            "this inning",
            "this innings",
            "that inning",
            "that innings",
        )
    )
    requested_team = _requested_match_team(lowered, data, previous_context)
    batting_order = [
        row.get("battingTeamShortName")
        for row in (data.get("match_team_info") or [])
        if row.get("battingTeamShortName")
    ]
    if not batting_order:
        batting_order = [
            row.get("batting_team")
            for row in (data.get("innings_scores") or [])
            if row.get("batting_team")
        ]
    answer_type = "general"
    weakness_text = ""
    weakness_keys = {}
    line_length_requested = (
        live_intent == "line_length"
        or _is_line_length_question(lowered)
    )
    line_length_bowler = requested_bowler
    if line_length_requested and line_length_bowler is None:
        remembered_bowler = (
            previous_context.get("last_bowler")
            or previous_context.get("recommendation")
            or recommendation
        )
        if remembered_bowler:
            line_length_bowler = next(
                (
                    row for row in bowling_card
                    if row.get("bowler") == remembered_bowler
                ),
                {"bowler": remembered_bowler},
            )
    plan_explanation_requested = "why" in lowered and "plan" in lowered
    plan_requested = (
        live_intent == "bowling_plan"
        or _asks_for_bowling_plan(lowered)
        and not type_recommendation_requested
        and not plan_explanation_requested
    )
    if plan_requested:
        weakness_text, weakness_keys = _current_batter_weakness(data, db)
    bowling_plan = previous_context.get("bowling_plan") or []
    line_length_plan = previous_context.get("line_length_plan")
    if plan_requested:
        bowling_plan = _build_bowling_plan(
            data,
            analytics,
            _requested_plan_length(lowered),
        )
        if bowling_plan:
            recommendation = bowling_plan[0]["bowler"]
            confidence = bowling_plan[0]["confidence"]

    if line_length_requested:
        answer_type = "line_length_advice"
        bowler_name = (
            line_length_bowler.get("bowler")
            if line_length_bowler else None
        )
        if match_not_live:
            line_length_plan = None
            answer = (
                "Line-and-length advice is available while the match is live."
            )
        elif not bowler_name:
            line_length_plan = None
            answer = (
                "Ask for a bowling recommendation first, or include the bowler's name."
            )
        else:
            profile = (
                player_t20_stats.get(bowler_name)
                or (previous_context.get("player_t20_stats") or {}).get(bowler_name)
                or {}
            )
            bowling_style = profile.get("bowling_style")
            batter_names = [
                batter.get("name") if isinstance(batter, dict) else batter
                for batter in (data.get("current_batsmen") or [])
            ]
            model_plan = recommend_line_length(
                _bowler_type_key(bowling_style),
                data.get("over"),
                [name for name in batter_names if name],
                int(data.get("total_overs") or 20),
            )
            if model_plan:
                best = model_plan["best"]
                style_note = f" ({bowling_style})" if bowling_style else ""
                delivery_plan = _describe_line_length(
                    best["line"],
                    best["length"],
                )
                boundaries_per_hundred = round(best["boundary_probability"])
                wickets_per_hundred = round(best["wicket_probability"])
                line_length_plan = {
                    "source": "model",
                    "bowler": bowler_name,
                    "bowling_style": bowling_style,
                    "phase": model_plan["phase"],
                    "delivery_plan": delivery_plan,
                    "expected_runs": best["expected_runs"],
                    "boundaries_per_hundred": boundaries_per_hundred,
                    "wickets_per_hundred": wickets_per_hundred,
                    "reliability": best["reliability"],
                    "group_balls": best["group_balls"],
                    "batter_balls": best["batter_balls"],
                    "batter_specific": model_plan["batter_specific"],
                }
                answer = (
                    f"{model_plan['phase'].title()}: {bowler_name}{style_note} "
                    f"should bowl {delivery_plan}."
                )
            else:
                fallback_advice = _line_length_advice(
                    bowler_name,
                    bowling_style,
                    data.get("over"),
                    int(data.get("total_overs") or 20),
                )
                first_instruction = fallback_advice.split(". ", 1)[0]
                answer = f"Rule-based fallback: {first_instruction}."
                line_length_plan = {
                    "source": "rule",
                    "bowler": bowler_name,
                    "bowling_style": bowling_style,
                    "reason": fallback_advice,
                }
    elif "pressure" in lowered:
        if not explanation_requested:
            answer = f"Pressure: {pressure}/100 - {pressure_label}."
        elif chase and not chase.get("achieved"):
            rate_gap = chase.get("required_run_rate", 0) - data.get("run_rate", 0)
            answer = (
                f"Pressure index measures chase difficulty now: {pressure}/100. "
                f"It is 65% wicket-resource pressure + 35% required-rate versus "
                f"current-rate pressure (rate gap {rate_gap:.2f}); venue par is excluded."
            )
        else:
            answer = (
                f"Pressure index measures batting risk now: {pressure}/100. "
                "It is 75% wicket-resource pressure + 25% innings stage; venue "
                "par is excluded."
            )
    elif "momentum" in lowered:
        if recent_runs:
            if explanation_requested:
                answer = (
                    f"Momentum shows if recent scoring is speeding up or slowing down: "
                    f"{momentum_score} ({momentum_level}). It compares recent-over rate "
                    "with innings rate; venue par is excluded."
                )
            else:
                answer = (
                    f"Momentum: {momentum_score} ({momentum_level}) - "
                    f"{momentum_label}."
                )
        else:
            answer = "There are not enough completed live overs to calculate momentum yet."
    elif "confidence" in lowered:
        if requested_bowler:
            bowler = requested_bowler["bowler"]
            bowler_confidence = analytics["confidence_scores"].get(bowler)
            if bowler_confidence is None:
                reason = analytics["excluded_bowlers"].get(bowler)
                answer = (
                    f"{bowler} has no next-over confidence score: {reason}."
                    if reason else
                    f"No next-over confidence score is available for {bowler}."
                )
            else:
                answer = (
                    f"{bowler}'s next-over confidence is {bowler_confidence}%. "
                    "It combines in-match form, current-innings matchup, T20 "
                    "profile, career head-to-head, momentum and pressure fit."
                )
        elif explanation_requested:
            answer = (
                "Confidence is evidence strength for the next-bowler recommendation, "
                "not match-win probability. It uses 30% in-match bowling form, "
                "25% current-innings batter matchup, 20% bowler T20 profile, "
                "15% career head-to-head, 6% momentum fit and 4% pressure fit. "
                "A missing sample scores 40, and the result is capped at 95%. "
                "Quota and consecutive-over rules are eligibility filters, not weights."
            )
            if recommendation and confidence is not None:
                answer += f" Current pick: {recommendation}, {confidence}%."
        elif recommendation and confidence is not None:
            answer = f"Recommendation confidence: {recommendation}, {confidence}%."
        else:
            answer = "No recommendation confidence is available at this point."
    elif any(
        term in lowered
        for term in (
            "in form",
            "inform",
            "in-match form",
            "in match form",
            "current form",
            "form bowler",
            "best form",
        )
    ):
        if requested_bowler:
            bowler = requested_bowler["bowler"]
            form_score = analytics["form_scores"].get(bowler)
            answer = (
                f"{bowler}'s in-match form is {form_score}/100, based on 65% "
                "run control and 35% wickets in this spell."
                if form_score is not None else
                f"{bowler} has not completed the two-over sample needed for an "
                "in-match form score."
            )
        elif form_ranking:
            bowler, form_score = form_ranking[0]
            figures = next(row for row in bowling_card if row.get("bowler") == bowler)
            answer = (
                f"{bowler} is the in-form bowler with a form score of "
                f"{form_score}/100. His current spell is {figures['overs']} overs, "
                f"{figures['runs']} runs and {figures['wickets']} wickets at economy "
                f"{figures['economy']:.2f}. The form score gives 65% weight to "
                "run control and 35% to wickets, after at least two overs."
            )
        else:
            answer = "No bowler has completed the two-over sample needed for an in-match form score."
    elif bowling_type_requested:
        asks_for_all_current_batters = bool(re.search(
            r"\b(?:current|active|both)\s+(?:batters|batsmen)\b",
            lowered,
        ))
        batter_names = (
            [
                row.get("name") if isinstance(row, dict) else row
                for row in (data.get("current_batsmen") or [])
            ]
            if asks_for_all_current_batters
            else [requested_batter.get("batter")]
            if requested_batter and requested_batter.get("batter")
            else [
                row.get("name") if isinstance(row, dict) else row
                for row in (data.get("current_batsmen") or [])
            ]
        )
        type_matchups = {}
        answers = []
        for batter_name in dict.fromkeys(name for name in batter_names if name):
            clean_name = re.sub(r"\s*\(\d+\)\s*$", "", batter_name).strip()
            try:
                stats = _get_cached_batter_type_stats(clean_name, db)
            except Exception as error:
                print(f"[main] Bowling-type lookup failed for {clean_name}: {error}")
                answers.append(
                    f"{clean_name}'s All-T20 bowling-type stats are temporarily "
                    "unavailable. Please retry shortly."
                )
                continue
            if stats:
                type_matchups[batter_name] = stats
                answers.append(
                    _format_batter_type_stats(stats, requested_type_keys)
                )
            else:
                answers.append(
                    f"No recorded All-T20 bowling-type sample was found for {clean_name}."
                )
        data["bowling_type_matchups"] = type_matchups
        answer = "\n".join(answers) or (
            "The live scorecard does not identify the current batters yet."
        )
    elif requested_bowler and any(
        term in lowered
        for term in (
            "average",
            "economy",
            "strike rate",
            "wicket",
            "match",
            "bowling figure",
            "bowling figures",
            "career",
            "profile",
            "t20 stat",
        )
    ) or requested_bowler and current_spell_requested:
        bowler = requested_bowler["bowler"]
        profile = player_t20_stats.get(bowler)
        requested_fields = _requested_bowling_stat_fields(lowered)
        if current_spell_requested:
            live_fields = [
                field for field in requested_fields
                if field in {"wickets", "economy"}
            ]
            if live_fields:
                answer = _format_bowling_stat_selection(
                    bowler,
                    {
                        "wickets": requested_bowler["wickets"],
                        "economy": requested_bowler["economy"],
                    },
                    live_fields,
                    "figures in this innings",
                )
            elif current_figures_requested:
                answer = (
                    f"{bowler}'s bowling figures in this match: "
                    f"{requested_bowler['overs']}-"
                    f"{requested_bowler.get('maidens', 0)}-"
                    f"{requested_bowler['runs']}-"
                    f"{requested_bowler['wickets']} (O-M-R-W), economy "
                    f"{requested_bowler['economy']:.2f}."
                )
            else:
                answer = (
                    f"{bowler}'s current spell: {requested_bowler['overs']} overs, "
                    f"{requested_bowler['runs']} runs, {requested_bowler['wickets']} "
                    f"wickets, economy {requested_bowler['economy']:.2f}."
                )
        elif profile:
            if requested_fields:
                answer = _format_bowling_stat_selection(
                    bowler,
                    profile,
                    requested_fields,
                    "career T20 bowling stats",
                )
            else:
                answer = (
                    f"{bowler}'s T20 bowling stats: {profile['matches']} matches, "
                    f"{profile['wickets']} wickets, average {profile['average']:.2f}, "
                    f"economy {profile['economy']:.2f}, and strike rate "
                    f"{profile['strike_rate']:.2f}. These are Cricbuzz T20 profile "
                    "figures, not PSL-only data."
                )
        else:
            answer = f"No usable Cricbuzz T20 bowling profile is available for {bowler}."
    elif (
        not current_spell_requested
        and "live stats" not in lowered
        and not bowling_ranking_requested
        and not matchup_requested
        and any(
        term in lowered
        for term in (
            "hundred",
            "century",
            "centuries",
            "fifties",
            "half century",
            "highest score",
            "top score",
            "batting average",
            "bowling average",
            "average",
            "strike rate",
            "strikerate",
            "strike-rate",
            "economy",
            "career stats",
            "player stats",
            "t20 stats",
            "total runs",
            "how many runs",
            "how many wickets",
            "how many matches",
            "number of matches",
            "total matches",
            "matches played",
            "t20 matches",
            "how many sixes",
            "how many fours",
            "how many innings",
            "number of innings",
            "batting innings",
            "bowling innings",
            "innings played",
            "balls faced",
            "balls bowled",
            "not out",
            "not-out",
            "ducks",
            "maidens",
            "best bowling",
            "career bowling figures",
            "bbi",
            "bbm",
            "four wicket",
            "five wicket",
            "ten wicket",
            "4w",
            "5w",
            "10w",
            "runs conceded",
            "double hundred",
            "triple hundred",
            " stats",
            "ranking",
            "rank",
            "role",
            "batting style",
            "bowling style",
            "date of birth",
            "born",
            "age",
        )
        )
    ):
        try:
            profile_query = referenced_batter or lowered
            profile = get_player_t20_profile(profile_query)
            answer = _answer_player_t20_question(lowered, profile)
            # Remember a successfully resolved profile even when the player is
            # not present in the selected innings scorecard. This makes the
            # next turn (for example, "what is his strike rate?") refer to the
            # same player.
            requested_batter = {"batter": profile.get("name")}
        except Exception as error:
            print(f"[main] Player profile lookup failed: {error}")
            answer = (
                "I could not resolve that player to a Cricbuzz profile. "
                "Please include the player's full name."
            )
    elif "effectiveness" in lowered:
        if requested_bowler:
            bowler = requested_bowler["bowler"]
            score = analytics["effectiveness_scores"].get(bowler)
            if score is None:
                answer = f"No bowling-effectiveness score is available for {bowler} yet."
            elif bowler in analytics["bowler_scores"]:
                answer = (
                    f"{bowler}'s bowling effectiveness is {score}/100. It combines "
                    "70% career T20 profile rating with 30% current-spell form."
                )
            else:
                answer = (
                    f"{bowler}'s bowling effectiveness is {score}/100, based on "
                    "current-spell form because no usable career T20 profile sample "
                    "is available; reliability is therefore lower."
                )
        else:
            effectiveness = sorted(
                analytics["effectiveness_scores"].items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if explanation_requested:
                answer = (
                    "Bowling effectiveness means the bowler's overall suitability "
                    "now: 70% career T20 profile rating + 30% current-spell form. "
                    "Without a usable profile, current form is used with lower "
                    "reliability."
                )
            else:
                answer = "Bowling effectiveness: " + (
                    ", ".join(f"{name} {score}" for name, score in effectiveness)
                    or "none yet"
                ) + "."
    elif bowling_ranking_requested or "ranking" in lowered or "ranked" in lowered:
        ranking = sorted(
            analytics["confidence_scores"].items(),
            key=lambda item: item[1],
            reverse=True,
        )
        answer = (
            "Next-over ranking by confidence: "
            + (", ".join(f"{name} {score}%" for name, score in ranking) or "none")
            + ". Only legally eligible bowlers are included."
        )
    elif any(
        phrase in lowered
        for phrase in (
            "which teams are playing",
            "what teams are playing",
            "who is playing",
            "teams in this match",
            "teams are in this match",
        )
    ):
        teams = _match_team_names(data)
        answer = (
            f"This match is {teams[0]} vs {teams[1]}."
            if len(teams) >= 2 else
            "The two team names are not available in the live scorecard yet."
        )
    elif re.search(r"\b(?:batting|batted|bat)\s+(?:1st|first)\b", lowered):
        requested_team = batting_order[0] if batting_order else None
        answer = (
            f"{requested_team} batted first."
            if requested_team else
            "The first batting team is not available yet."
        )
    elif re.search(r"\b(?:batting|batted|bat)\s+(?:2nd|second)\b", lowered):
        requested_team = batting_order[1] if len(batting_order) > 1 else None
        answer = (
            f"{requested_team} batted second."
            if requested_team else
            "The second batting team is not available yet."
        )
    elif "run rate" in lowered or "runrate" in lowered:
        requested_team = requested_team or data.get("batting_team")
        team_score = (
            _team_score_at_context(data, requested_team)
            if requested_team else None
        )
        answer = (
            f"{requested_team}'s run rate is {float(team_score['run_rate']):.2f} "
            f"at {team_score['over']} overs."
            if team_score and team_score.get("run_rate") is not None else
            f"The run rate for {requested_team or 'that team'} is not available yet."
        )
    elif requested_team and any(word in lowered for word in ("score", "runs")):
        team_score = _team_score_at_context(data, requested_team)
        answer = (
            f"{requested_team} are {team_score['score']} after "
            f"{team_score['over']} overs (run rate "
            f"{float(team_score['run_rate']):.2f})."
            if team_score else
            f"{requested_team} have not started their innings yet."
        )
    elif any(
        term in lowered
        for term in (
            "at crease",
            "at the crease",
            "on crease",
            "on the crease",
            "who is batting",
            "who's batting",
        )
    ):
        active_names = [
            row.get("name") for row in data.get("current_batsmen", [])
            if row.get("name")
        ]
        active_rows = [
            row for row in (data.get("batting_card") or [])
            if row.get("batter") in active_names
        ]
        if active_rows:
            answer = "At the crease: " + "; ".join(
                f"{row['batter']} {row['runs']} off {row['balls']} "
                f"(SR {row['strike_rate']:.2f})"
                for row in active_rows
            ) + "."
        else:
            answer = "The live scorecard does not identify the current batters yet."
    elif _asks_what_venue_par_means(lowered):
        definition = (
            "Against venue par means batting score minus the historical men's "
            "T20I average at the same venue and over. Positive is above par; "
            "negative is behind."
        )
        if par:
            position = "above" if par["difference"] >= 0 else "behind"
            answer = (
                f"{definition} Here: {data['score']} versus {par['runs']} par, "
                f"so {abs(par['difference'])} runs {position}. It is a scoring-pace "
                "benchmark and is excluded from pressure and momentum."
            )
        else:
            answer = (
                f"{definition} No verified venue-specific benchmark is available "
                "for this point."
            )
    elif (
        matchup_requested
        and named_matchup
        and not (requested_batter and requested_bowler)
    ):
        batter_query, bowler_query = named_matchup
        try:
            career_matchup = get_t20_player_matchup(
                batter_query,
                bowler_query,
            )
        except Exception as error:
            print(f"[main] T20 player matchup lookup failed: {error}")
            career_matchup = None

        if career_matchup:
            average = career_matchup.get("average")
            average_text = (
                f", average {average:.2f}" if average is not None else ""
            )
            answer = (
                f"In recorded T20 matches, {career_matchup['batter']} has scored "
                f"{career_matchup['runs']} runs from {career_matchup['balls']} balls "
                f"against {career_matchup['bowler']} (strike rate "
                f"{career_matchup['strike_rate']:.2f}{average_text}), with "
                f"{career_matchup['dismissals']} dismissals. Source: "
                f"{career_matchup['source']}."
            )
        else:
            answer = (
                "No recorded T20 batter-bowler sample was found for those two "
                "players. Please give the batter first and bowler second."
            )
    elif matchup_requested:
        all_matchups = data.get("matchup_stats") or {}
        matchups = all_matchups.get(
            requested_batter.get("batter") if requested_batter else "",
            {},
        )
        if requested_batter and requested_bowler:
            stats = matchups.get(requested_bowler["bowler"])
            if stats:
                strike_rate = (
                    stats["runs"] / max(stats["balls"], 1) * 100
                )
                answer = (
                    f"By {data.get('over')} overs, {requested_batter['batter']} "
                    f"has scored {stats['runs']} runs from {stats['balls']} balls "
                    f"against {requested_bowler['bowler']} (strike rate "
                    f"{strike_rate:.2f}), with {stats['dismissals']} dismissals."
                )
            else:
                answer = (
                    f"By {data.get('over')} overs, {requested_batter['batter']} "
                    f"has not faced {requested_bowler['bowler']} yet: 0 balls, "
                    "0 runs and 0 dismissals."
                )
        elif requested_bowler:
            active_names = [
                batter.get("name") if isinstance(batter, dict) else batter
                for batter in (data.get("current_batsmen") or [])
            ]
            active_names = [name for name in active_names if name]
            rows = []
            for batter in active_names:
                stats = (all_matchups.get(batter) or {}).get(
                    requested_bowler["bowler"],
                ) or {"balls": 0, "runs": 0, "dismissals": 0}
                rows.append(
                    f"{batter}: {stats['runs']} runs from {stats['balls']} balls, "
                    f"{stats['dismissals']} dismissals"
                )
            answer = (
                f"Current-innings matchups vs {requested_bowler['bowler']}: "
                + ("; ".join(rows) if rows else "current batters unavailable")
                + "."
            )
        elif requested_batter and matchups:
            answer = f"{requested_batter['batter']} matchups: " + "; ".join(
                f"vs {bowler}: {stats['runs']} runs from {stats['balls']} balls, "
                f"{stats['dismissals']} dismissals"
                for bowler, stats in matchups.items()
            ) + "."
        elif all_matchups:
            answer = "Live batter-bowler matchups: " + "; ".join(
                f"{batter} vs {bowler}: {stats['runs']} from {stats['balls']} balls, "
                f"{stats['dismissals']} outs"
                for batter, bowlers in all_matchups.items()
                for bowler, stats in bowlers.items()
            ) + "."
        else:
            answer = "No live ball-by-ball matchup sample is available for that batter at this point."
    elif requested_batter and (
        current_spell_requested
        or any(
            term in lowered
            for term in (
                "batting",
                "runs",
                "balls",
                "strike rate",
                "score",
                "stats",
            )
        )
    ):
        answer = (
            f"{requested_batter['batter']}: {requested_batter['runs']} runs from "
            f"{requested_batter['balls']} balls, {requested_batter['fours']} fours, "
            f"{requested_batter['sixes']} sixes, strike rate "
            f"{requested_batter['strike_rate']:.2f}, {requested_batter['status']}."
        )
    elif requested_bowler and any(term in lowered for term in ("quota", "available", "eligible")):
        bowler = requested_bowler["bowler"]
        reason = analytics["excluded_bowlers"].get(bowler)
        answer = (
            f"{bowler} is unavailable: {reason}."
            if reason else
            f"{bowler} is eligible to bowl next with {requested_bowler.get('overs_left')} "
            "overs left in the four-over T20 quota."
        )
    elif any(
        term in lowered
        for term in (
            "ineligible bowler",
            "unavailable bowler",
            "who cannot bowl",
            "who can not bowl",
            "who can't bowl",
        )
    ):
        excluded_rows = [
            f"{name}: {reason}"
            for name, reason in analytics["excluded_bowlers"].items()
        ]
        answer = (
            "Ineligible for the next over: " + "; ".join(excluded_rows) + "."
            if excluded_rows else
            "No bowler is currently excluded from the next over."
        )
    elif any(term in lowered for term in ("who can bowl", "available bowler", "eligible bowler")):
        available_text = ", ".join(analytics["available_bowlers"]) or "none"
        excluded_text = "; ".join(
            f"{name}: {reason}"
            for name, reason in analytics["excluded_bowlers"].items()
        ) or "none"
        answer = (
            f"Eligible next-over bowlers: {available_text}. Excluded: {excluded_text}."
        )
    elif _is_previous_over_question(lowered):
        if data.get("current_bowler"):
            answer = f"Previous over: {data['current_bowler']}."
        else:
            answer = "Previous-over bowler is not available in the live scorecard yet."
    elif plan_explanation_requested:
        answer_type = "recommendation_explanation"
        answer = _explain_bowling_plan(
            bowling_plan,
            analytics.get("factor_scores"),
        )
    elif type_recommendation_requested:
        requested_label = _bowling_type_request_label(requested_type_keys)
        ranked_pick = _best_ranked_bowler_for_types(
            analytics,
            player_t20_stats,
            requested_type_keys,
        )
        if ranked_pick:
            recommendation, confidence, bowling_style = ranked_pick
            answer_type = "bowling_recommendation"
            answer = (
                f"Best eligible {requested_label}: {recommendation} - "
                f"{confidence}% confidence ({bowling_style})."
            )
        else:
            answer = (
                f"No legally eligible {requested_label} with a verified bowling "
                "style is available in the current ranking."
            )
    elif requested_bowler and recommendation and _asks_why_not_bowler(lowered):
        alternative = requested_bowler["bowler"]
        exclusion = analytics["excluded_bowlers"].get(alternative)
        answer_type = "recommendation_comparison"
        scheduled = next(
            (
                row for row in bowling_plan
                if row.get("bowler") == alternative
            ),
            None,
        )

        if scheduled:
            detailed_reason = scheduled.get("x_factor_reason")
            scheduled_x_factor = scheduled.get("x_factor") or _confidence_x_factor(
                analytics.get("factor_scores", {}).get(alternative)
            )
            if detailed_reason:
                scheduled_reason = detailed_reason
            elif scheduled_x_factor == "no clear data edge yet":
                scheduled_reason = "it has the highest available confidence for that over"
            else:
                factor_label, factor_score = scheduled_x_factor.rsplit(" ", 1)
                scheduled_reason = (
                    f"{factor_label} is its strongest factor ({factor_score})"
                )
            answer = (
                f"{alternative} is already in the plan for over "
                f"{scheduled['over']} ({scheduled['confidence']}%). "
                f"Main reason: {scheduled_reason}."
            )
        elif alternative == recommendation:
            answer = (
                f"{alternative} is already the recommended next bowler at "
                f"{confidence}% confidence."
            )
        elif exclusion:
            answer = (
                f"Not {alternative}: {exclusion}. Pick: {recommendation} "
                f"at {confidence}% confidence."
            )
        else:
            alternative_confidence = analytics[
                "planning_confidence_scores"
            ].get(alternative)
            if bowling_plan and alternative_confidence is not None:
                answer = (
                    f"{alternative} is legal but not in this plan: his current "
                    f"confidence is {alternative_confidence}%, below the selected "
                    "rotation after form, innings matchup, T20 profile, career "
                    "head-to-head, momentum and pressure are combined."
                )
            else:
                recommended_form = analytics["form_scores"].get(
                    recommendation,
                )
                alternative_form = analytics["form_scores"].get(alternative)
                reason = (
                    "stronger current form"
                    if recommended_form is not None
                    and alternative_form is not None
                    and recommended_form > alternative_form
                    else "stronger overall tactical evidence"
                )
                answer = (
                    f"Prefer {recommendation} over {alternative}: {reason}. "
                    f"Pick: {recommendation} ({confidence}% confidence)."
                )
    elif "why" in lowered and recommendation:
        answer_type = "recommendation_explanation"
        answer = (
            f"Pick {recommendation} ({confidence}% confidence): strongest weighted "
            "combination of in-match form, current-innings matchup, bowler T20 "
            "profile, career head-to-head, momentum and pressure fit."
        )
    elif plan_requested:
        if bowling_plan:
            answer_type = "bowling_recommendation"
            answer = _format_bowling_plan(bowling_plan)
        elif match_not_live:
            answer = (
                f"This match is not currently in progress: "
                f"{data.get('match_status') or data.get('state')}."
            )
        else:
            answer = "There is not enough live bowling data to recommend a bowler yet."
    elif requested_bowler and "bowl" in lowered:
        answer = (
            f"{requested_bowler['bowler']}: {requested_bowler['overs']} overs, "
            f"{requested_bowler['runs']} runs, {requested_bowler['wickets']} wickets, "
            f"economy {requested_bowler['economy']:.2f}."
        )
    elif "bowling" in lowered and any(
        word in lowered for word in ("figure", "figures", "stats", "card")
    ):
        rows = [
            f"{bowler['bowler']}: {bowler['overs']}-{bowler['maidens']}-"
            f"{bowler['runs']}-{bowler['wickets']} (econ {bowler['economy']:.2f})"
            for bowler in bowling_card
        ]
        answer = "Bowling figures: " + ("; ".join(rows) if rows else "none yet") + "."
    elif "par" in lowered or "ahead" in lowered or "behind" in lowered:
        if par:
            position = "above" if par["difference"] >= 0 else "behind"
            answer = (
                f"{data['batting_team']} are {abs(par['difference'])} runs {position} "
                f"par. The venue benchmark is {par['runs']} at {data['over']} overs "
                f"({par['source']})."
            )
        elif data.get("format") != "T20":
            answer = "Venue par is currently available only for T20 matches."
        else:
            answer = "No venue-par benchmark is available for this match yet."
    elif "venue" in lowered or "ground" in lowered:
        answer = f"The match is at {data.get('venue')}, {data.get('city')}."
    elif any(
        phrase in lowered
        for phrase in (
            "projected score",
            "score projection",
            "project score",
            "projection",
            "finish score",
            "score at 8",
            "score at 10",
            "score at 12",
        )
    ):
        answer_type = "score_projection"
        projections = data.get("score_projections") or {}
        scenarios = projections.get("scenarios") or []
        scenario_text = []
        for scenario in scenarios:
            text = (
                f"RR {scenario['run_rate']} -> "
                f"{scenario['projected_score']}"
            )
            if scenario.get("target"):
                margin = scenario.get("target_margin", 0)
                text += (
                    f" ({abs(margin)} {'above' if margin >= 0 else 'short of'} "
                    "target)"
                )
            scenario_text.append(text)
        answer = (
            f"Projected at {projections.get('finish_over', 20)} overs from "
            f"{data['score']} after {data['over']}: "
            + "; ".join(scenario_text)
            + ". Rates apply only to remaining balls; wickets are not modelled."
        )
    elif go_live_requested and match_finished:
        answer_type = "final_result"
        final_result = data.get("match_status") or data.get("state") or "Result unavailable"
        answer = f"This match has been completed. Final result: {final_result}."
    elif go_live_requested:
        answer_type = "go_live"
        answer = (
            f"Live now: {data['batting_team']} {data['score']} after "
            f"{data['over']} overs. The console is following the current match."
        )
    elif latest_score_requested:
        answer_type = "live_score"
        answer = (
            f"Live score now: {data['batting_team']} {data['score']} after "
            f"{data['over']} overs (run rate {data['run_rate']:.2f})."
        )
    elif "score" in lowered or "runs" in lowered or "current" in lowered:
        answer_type = "point_score"
        answer = (
            f"At this point: {data['batting_team']} are {data['score']} after "
            f"{data['over']} overs "
            f"(run rate {data['run_rate']:.2f}). {data.get('match_status') or ''}"
        ).strip()
    elif requested_over is not None and "over" in lowered:
        innings_label = "first innings" if data.get("current_phase") == "first" else "second innings"
        par_text = ""
        if par:
            position = "above" if par["difference"] >= 0 else "behind"
            par_text = f" They were {abs(par['difference'])} runs {position} par."
        answer = (
            f"At the end of {innings_label} over {data['over']}, "
            f"{data['batting_team']} were {data['score']}.{par_text}"
        )
    elif any(term in lowered for term in ("feature", "what can you", "live data", "live stats")):
        answer = (
            "I can answer the live score and batting card; bowling figures and "
            "T20 profile average, economy, strike rate, wickets and matches; "
            "venue par; pressure and momentum with their formulas; in-match form; "
            "bowling effectiveness and final ranking; four-over quota and previous-over "
            "eligibility; batter-bowler matchups; line-and-length guidance; and who "
            "should bowl next with confidence and reasons."
        )
    else:
        answer = (
            f"Live update: {data['batting_team']} {data['score']} after "
            f"{data['over']} overs. Ask me for the score, venue par, bowling "
            f"figures, or who should bowl next."
        )

    if answer_type in {
        "bowling_recommendation",
        "recommendation_comparison",
        "recommendation_explanation",
    } and weakness_text:
        answer = f"{answer} {weakness_text}"

    par_note = None
    if par:
        position = "above" if par["difference"] >= 0 else "behind"
        par_note = (
            f"{abs(par['difference'])} runs {position} venue par "
            f"({par['runs']} at this over)."
        )

    choice_note = analytics["choice_note"]

    context = {
        **previous_context,
        **data,
        "match_id": match_id,
        "requested_over": requested_over,
        "recommendation": recommendation,
        "confidence": confidence,
        "combined_scores": combined_scores,
        "confidence_scores": analytics["confidence_scores"],
        "bowler_scores": analytics["bowler_scores"],
        "player_t20_stats": analytics["player_t20_stats"],
        "effectiveness_scores": analytics["effectiveness_scores"],
        "matchup_stats": data.get("matchup_stats") or {},
        "career_matchup_stats": data.get("career_matchup_stats") or {},
        "form_scores": analytics["form_scores"],
        "matchup_scores": analytics["matchup_scores"],
        "career_matchup_scores": analytics.get("career_matchup_scores", {}),
        "factor_scores": analytics.get("factor_scores", {}),
        "strategies": analytics["strategies"],
        "available_bowlers": analytics["available_bowlers"],
        "excluded_bowlers": analytics["excluded_bowlers"],
        "unrated_bowlers": analytics["unrated_bowlers"],
        "provisional_bowlers": analytics["provisional_bowlers"],
        "choice_note": choice_note,
        "par_note": par_note,
        "top_strategy": analytics["top_strategy"],
        "bowling_plan": bowling_plan,
        "line_length_plan": line_length_plan,
        "last_answer_type": answer_type,
        "live_mode": live_mode,
        "match_finished": match_finished,
        "match_status": data.get("match_status"),
        "pressure": pressure,
        "pressure_label": pressure_label,
        "momentum_score": momentum_score,
        "momentum_level": momentum_level,
        "momentum_label": momentum_label,
        "last_bowler": (
            requested_bowler.get("bowler")
            if requested_bowler else
            recommendation
            if answer_type == "bowling_recommendation" else
            previous_context.get("last_bowler")
        ),
        "last_batter": (
            requested_batter.get("batter") if requested_batter else previous_context.get("last_batter")
        ),
        "last_team": requested_team or previous_context.get("last_team"),
        "current_batter_weakness": weakness_text,
        "current_batter_weakness_types": weakness_keys,
        "live_intent": live_intent,
    }
    safe_cache_set(f"session:{session_id}", context, SESSION_TTL_SECONDS)

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        intent=live_intent,
        current_over=data.get("current_over"),
        current_phase=data.get("current_phase"),
        requested_over=requested_over,
        live_mode=live_mode,
        match_finished=match_finished,
        match_status=data.get("match_status"),
        score_projections=data.get("score_projections"),
        score=data.get("score"),
        batting_team=data.get("batting_team"),
        bowling_team=data.get("bowling_team"),
        recommendation=recommendation,
        confidence=confidence,
        pressure=pressure,
        pressure_label=pressure_label,
        momentum_score=momentum_score,
        momentum_level=momentum_level,
        momentum_label=momentum_label,
        current_batsmen=[
            batter.get("name") for batter in data.get("current_batsmen", [])
        ],
        current_bowler=data.get("current_bowler"),
        current_batter_weakness=weakness_text or None,
        current_batter_weakness_types=weakness_keys or None,
        available_bowlers=analytics["available_bowlers"],
        excluded_bowlers=analytics["excluded_bowlers"],
        unrated_bowlers=analytics["unrated_bowlers"],
        provisional_bowlers=analytics["provisional_bowlers"],
        bowler_scores=analytics["bowler_scores"],
        player_t20_stats=analytics["player_t20_stats"],
        effectiveness_scores=analytics["effectiveness_scores"],
        matchup_stats=data.get("matchup_stats") or {},
        career_matchup_stats=data.get("career_matchup_stats") or {},
        bowling_type_matchups=data.get("bowling_type_matchups") or {},
        form_scores=analytics["form_scores"],
        matchup_scores=analytics["matchup_scores"],
        career_matchup_scores=analytics.get("career_matchup_scores", {}),
        factor_scores=analytics.get("factor_scores", {}),
        combined_scores=combined_scores,
        confidence_scores=analytics["confidence_scores"],
        strategies=analytics["strategies"],
        top_strategy=analytics["top_strategy"],
        bowling_plan=bowling_plan,
        ranking_team=data.get("bowling_team"),
        ranking_phase=data.get("current_phase"),
        par_note=par_note,
        choice_note=choice_note,
        side_panel=data,
    )


@app.get("/api/situation")
def situation(
    over: Optional[str] = None,
    phase: str = "chase",
    include_type_matchups: bool = False,
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=410,
        detail="Historical situation mode has been retired. Select a live or completed match.",
    )

    snapshot = get_snapshot(over, phase if phase in ("first", "chase") else "chase")
    balls = over_to_balls(snapshot.get("snapshot_over", "0.1"))
    overs_played = max(balls / 6, 0.1)
    display_over = (
        str(over)
        if over is not None and str(over).isdigit()
        else snapshot.get("snapshot_over")
    )
    target = snapshot.get("target")
    runs_needed = max((target or 0) - snapshot.get("runs", 0), 0)
    balls_remaining = max(20 * 6 - balls, 0)
    bowling_card = []
    batting_card = []
    par = None

    if par_at_over is not None:
        par_runs, par_source = par_at_over(
            snapshot.get("venue"),
            max(1, int(balls / 6)),
        )

        if par_runs is not None:
            par = {
                "runs": par_runs,
                "difference": round(snapshot.get("runs", 0) - par_runs, 1),
                "source": par_source,
            }

    for name, figures in (snapshot.get("bowling_stats") or {}).items():
        legal_balls = figures.get("balls", 0)
        bowling_card.append({
            "bowler": name,
            "overs": f"{legal_balls // 6}.{legal_balls % 6}",
            "balls": legal_balls,
            "runs": figures.get("runs", 0),
            "wickets": figures.get("wickets", 0),
            "economy": round(figures.get("runs", 0) / max(legal_balls / 6, 0.1), 2),
            "overs_left": max(4 - legal_balls // 6, 0),
            "quota_used": legal_balls // 6 >= 4,
        })

    active_batters = {
        snapshot.get("striker"),
        snapshot.get("non_striker"),
    }
    for name, figures in (snapshot.get("batter_stats") or {}).items():
        batting_card.append({
            "batter": name,
            "runs": figures.get("runs", 0),
            "balls": figures.get("balls", 0),
            "fours": figures.get("fours", 0),
            "sixes": figures.get("sixes", 0),
            "strike_rate": round(
                figures.get("runs", 0) / max(figures.get("balls", 0), 1) * 100,
                2,
            ),
            "status": "not out" if name in active_batters else "out",
        })

    response = {
        "over": display_over,
        "batting_team": snapshot.get("batting_team"),
        "bowling_team": snapshot.get("bowling_team"),
        "runs": snapshot.get("runs", 0),
        "wickets": snapshot.get("wickets", 0),
        "score": f"{snapshot.get('runs', 0)}/{snapshot.get('wickets', 0)}",
        "run_rate": round(snapshot.get("runs", 0) / overs_played, 2),
        "current_bowler": snapshot.get("current_bowler"),
        "batsmen": {
            "striker": snapshot.get("striker"),
            "non_striker": snapshot.get("non_striker"),
            "on_strike_next_ball": snapshot.get("striker"),
        },
        "chase": ({
            "target": target,
            "runs_needed": runs_needed,
            "balls_remaining": balls_remaining,
            "required_run_rate": round(runs_needed / max(balls_remaining / 6, 0.1), 2),
            "achieved": runs_needed == 0,
        } if target is not None else None),
        "par": par,
        "bowling_card": bowling_card,
        "batting_card": batting_card,
    }
    if include_type_matchups:
        response["current_batsmen"] = [
            {"name": name}
            for name in (
                snapshot.get("striker"),
                snapshot.get("non_striker"),
            )
            if name
        ]
        response["bowling_type_matchups"] = _load_batter_type_matchups(
            response,
            db,
        )
        response["career_matchup_stats"] = _load_current_career_matchups(
            response,
        )
    return response


# -------------------------------------------------------
# Chat endpoint
# -------------------------------------------------------

@app.post(
    "/api/chat",
    response_model=ChatResponse,
)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
):
    session_id = request.session_id or str(uuid.uuid4())

    session_key = f"session:{session_id}"

    # ---------------------------------------------------
    # Load previous conversation state
    # ---------------------------------------------------

    previous_context = safe_cache_get(session_key) or {}
    if not previous_context:
        previous_context = load_session_context(db, session_id)
        if previous_context:
            safe_cache_set(
                session_key,
                previous_context,
                ttl_seconds=SESSION_TTL_SECONDS,
            )

    def finish(
        response: ChatResponse,
        fallback_context: Optional[dict] = None,
    ) -> ChatResponse:
        durable_context = (
            safe_cache_get(session_key)
            or fallback_context
            or previous_context
        )
        save_conversation_turn(
            db=db,
            session_id=session_id,
            match_id=request.match_id,
            question=request.question,
            answer=response.answer,
            context=durable_context,
        )
        return response

    if request.match_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Please select a live or completed match before using chat. "
                "Historical no-match-id chat is disabled."
            ),
        )

    if _is_memory_question(request.question):
        answer = _answer_memory_question(
            request.question,
            db,
            session_id,
            previous_context,
        )
        return finish(ChatResponse(
            session_id=session_id,
            answer=answer,
            intent="conversation_memory",
            current_over=previous_context.get("current_over"),
            current_phase=previous_context.get("current_phase"),
            requested_over=previous_context.get("requested_over"),
            score=previous_context.get("score"),
            batting_team=previous_context.get("batting_team"),
            bowling_team=previous_context.get("bowling_team"),
        ))

    if request.match_id is not None:
        questions = _split_compound_questions(request.question)
        if len(questions) > 1:
            answers = []
            response = None
            live_context = previous_context
            for question in questions:
                response = _live_chat_response(
                    question,
                    session_id,
                    request.match_id,
                    live_context,
                    db,
                )
                answers.append(response.answer)
                live_context = safe_cache_get(session_key) or live_context
            return finish(response.model_copy(update={"answer": "\n".join(answers)}))

        return finish(_live_chat_response(
            request.question,
            session_id,
            request.match_id,
            previous_context,
            db,
        ))

    # ---------------------------------------------------
    # Detect phase + requested over
    # ---------------------------------------------------

    current_phase, requested_over = parse_query_metadata(
        request.question,
        previous_context,
    )

    # ---------------------------------------------------
    # Preserve previous context
    # ---------------------------------------------------

    updated_context = {
        **previous_context,
        "current_phase": current_phase,
        "requested_over": (
            requested_over
            if requested_over is not None
            else previous_context.get("requested_over")
        ),
    }

    # ---------------------------------------------------
    # Graph must exist
    # ---------------------------------------------------

    if coach_agent is None:
        return finish(ChatResponse(
            session_id=session_id,
            answer=(
                "Backend graph is not loaded. "
                "Please check the graph.py imports."
            ),
            current_over=updated_context.get("current_over"),
            current_phase=updated_context.get("current_phase"),
            requested_over=updated_context.get("requested_over"),
        ), updated_context)

    # ---------------------------------------------------
    # Run graph
    # ---------------------------------------------------

    try:
        result = coach_agent.invoke(
            {
                "question": request.question,
                "previous_context": updated_context,
            }
        )

    except Exception as e:
        print(f"[chat] Graph execution failed: {e}")

        return finish(ChatResponse(
            session_id=session_id,
            answer=f"Coach agent error: {e}",
            current_over=updated_context.get("current_over"),
            current_phase=updated_context.get("current_phase"),
            requested_over=updated_context.get("requested_over"),
        ), updated_context)

    if not isinstance(result, dict):
        result = {}

    # ---------------------------------------------------
    # Graph returned context
    # ---------------------------------------------------

    context = result.get("previous_context")

    if not isinstance(context, dict):
        context = dict(updated_context)
    else:
        context = dict(context)

    # ---------------------------------------------------
    # Synchronize current_over
    # ---------------------------------------------------

    result_current_over = result.get("current_over")

    if result_current_over is not None:

        context["current_over"] = result_current_over

    elif context.get("current_over") is None:

        if context.get("requested_over") is not None:
            context["current_over"] = context["requested_over"]

    # ---------------------------------------------------
    # Preserve requested_over
    # ---------------------------------------------------

    if result.get("requested_over") is not None:
        context["requested_over"] = result["requested_over"]

    # ---------------------------------------------------
    # Preserve current phase
    # ---------------------------------------------------

    if result.get("current_phase") is not None:
        context["current_phase"] = result["current_phase"]

    # ---------------------------------------------------
    # Helper
    # ---------------------------------------------------

    def result_or_context(key: str):
        value = result.get(key)

        if value is not None:
            return value

        return context.get(key)

    requested_for_display = str(
        context.get("requested_over") or ""
    )
    current_over_for_display = (
        requested_for_display
        if requested_for_display.isdigit()
        else context.get("current_over")
    )

    # ---------------------------------------------------
    # Build response
    # ---------------------------------------------------

    response_payload = ChatResponse(
        session_id=session_id,

        answer=result.get("answer", ""),

        intent=result.get("intent"),

        current_over=(
            current_over_for_display
            if current_over_for_display is not None
            else result.get("current_over")
        ),

        current_phase=(
            context.get("current_phase")
            if context.get("current_phase") is not None
            else result.get("current_phase")
        ),

        score=result_or_context("score"),

        batting_team=(
            context.get("batting_team")
            if context.get("batting_team") is not None
            else result.get("batting_team")
        ),

        bowling_team=(
            context.get("bowling_team")
            if context.get("bowling_team") is not None
            else result.get("bowling_team")
        ),

        requested_over=context.get("requested_over"),

        ranking_team=result_or_context("ranking_team"),
        ranking_phase=result_or_context("ranking_phase"),

        pressure=result_or_context("pressure"),
        pressure_label=result_or_context("pressure_label"),

        momentum_score=result_or_context("momentum_score"),
        momentum_level=result_or_context("momentum_level"),
        momentum_label=result_or_context("momentum_label"),

        bowler_scores=context.get("bowler_scores"),
        form_scores=context.get("form_scores"),
        combined_scores=context.get("combined_scores"),
        matchup_stats=context.get("matchup_stats"),

        strategies=context.get("strategies"),
        top_strategy=context.get("top_strategy"),

        recommendation=result_or_context("recommendation"),

        confidence=result_or_context("confidence"),

        available_bowlers=context.get("available_bowlers"),
        excluded_bowlers=context.get("excluded_bowlers"),
        unrated_bowlers=context.get("unrated_bowlers"),
        provisional_bowlers=context.get("provisional_bowlers"),

        current_bowler=context.get("current_bowler"),
        current_batter=context.get("current_batter"),
        current_batsmen=context.get("current_batsmen"),

        par_note=context.get("par_note"),
        choice_note=context.get("choice_note"),

        side_panel=context.get("side_panel"),
    )

    # ---------------------------------------------------
    # Save session
    #
    # FIX: previously did `context["previous_context"] =
    # context.copy()` here, which nests a full copy of the
    # context inside itself before every save. Each turn
    # then reloads that nested copy, merges it back in, and
    # re-nests it again -- the cached blob in Redis grows
    # every single turn for no functional reason (nothing
    # downstream reads context["previous_context"]; it isn't
    # even in ChatResponse). Just cache the flat context.
    # ---------------------------------------------------

    safe_cache_set(
        session_key,
        context,
        ttl_seconds=SESSION_TTL_SECONDS,
    )

    print(
        f"[chat] Session saved: "
        f"{session_id} | "
        f"over={context.get('current_over')} | "
        f"phase={context.get('current_phase')}"
    )

    return finish(response_payload, context)


# -------------------------------------------------------
# Local development
# -------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8001,
        reload=True,
    )
