"""Runtime inference for the trained T20 line-and-length outcome model."""

from __future__ import annotations

import gzip
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "line_length"
    / "line_length_model.json.gz"
)
_MODEL_CACHE: dict[str, Any] | None = None
_MODEL_MTIME: float | None = None


def _model_path() -> Path:
    configured = os.getenv("LINE_LENGTH_MODEL_PATH")
    return Path(configured) if configured else DEFAULT_MODEL_PATH


def _index(rows: list[dict]) -> dict[tuple[str, ...], dict]:
    return {tuple(row["key"]): row for row in rows}


def _load_model() -> dict[str, Any] | None:
    global _MODEL_CACHE, _MODEL_MTIME

    path = _model_path()
    if not path.exists():
        return None
    modified = path.stat().st_mtime
    if _MODEL_CACHE is not None and _MODEL_MTIME == modified:
        return _MODEL_CACHE

    with gzip.open(path, "rt", encoding="utf-8") as model_file:
        payload = json.load(model_file)
    payload["group_index"] = _index(payload.get("groups") or [])
    payload["action_index"] = _index(payload.get("actions") or [])
    payload["batter_index"] = _index(payload.get("batters") or [])
    payload["batter_names"] = {
        key[0] for key in payload["batter_index"] if key
    }
    _MODEL_CACHE = payload
    _MODEL_MTIME = modified
    return payload


def _rates(row: dict | list | None) -> tuple[float, float, float]:
    if isinstance(row, dict):
        balls = max(float(row.get("balls") or 0), 1.0)
        return (
            float(row.get("runs") or 0) / balls,
            float(row.get("boundaries") or 0) / balls,
            float(row.get("wickets") or 0) / balls,
        )
    values = row or [1, 0, 0, 0]
    balls = max(float(values[0]), 1.0)
    return values[1] / balls, values[2] / balls, values[3] / balls


def _blend(
    row: dict | None,
    parent: tuple[float, float, float],
    prior_balls: int,
) -> tuple[float, float, float]:
    if not row:
        return parent
    balls = float(row.get("balls") or 0)
    observed = _rates(row)
    weight = balls / (balls + prior_balls)
    return tuple(
        weight * observed[index] + (1 - weight) * parent[index]
        for index in range(3)
    )


def _player_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _resolve_batter(query: str, known: set[str]) -> str | None:
    target = _player_key(query)
    if not target:
        return None
    if target in known:
        return target

    target_tokens = set(target.split())
    candidates = []
    for name in known:
        name_tokens = set(name.split())
        if target_tokens.issubset(name_tokens) or name_tokens.issubset(target_tokens):
            candidates.append((1.0, name))
            continue
        if target.split()[-1] == name.split()[-1]:
            candidates.append((SequenceMatcher(None, target, name).ratio(), name))
    if not candidates:
        return None
    score, name = max(candidates)
    return name if score >= 0.55 else None


def _phase(over: str | float | None, total_overs: int) -> str:
    try:
        over_number = float(over or 0)
    except (TypeError, ValueError):
        over_number = 0
    powerplay_end = min(6, total_overs * 0.30)
    death_start = max(16, total_overs * 0.80)
    if over_number <= powerplay_end:
        return "powerplay"
    if over_number >= death_start:
        return "death overs"
    return "middle overs"


def recommend_line_length(
    bowler_type: str | None,
    over: str | float | None,
    batters: list[str] | None = None,
    total_overs: int = 20,
) -> dict[str, Any] | None:
    """Rank historical line-length choices for this T20 context."""
    if not bowler_type:
        return None
    model = _load_model()
    if not model:
        return None

    phase = _phase(over, total_overs)
    groups = model["group_index"]
    actions = model["action_index"]
    batter_rows = model["batter_index"]
    resolved_batters = [
        resolved
        for batter in (batters or [])
        if (resolved := _resolve_batter(batter, model["batter_names"]))
    ]
    resolved_batters = list(dict.fromkeys(resolved_batters))
    overall = _rates(model.get("overall"))
    candidates = []

    for key, group in groups.items():
        group_phase, group_type, line, length = key
        if group_phase != phase or group_type != bowler_type:
            continue
        action = actions.get((phase, line, length))
        action_prediction = _blend(action, overall, 200)
        group_prediction = _blend(group, action_prediction, 100)
        predictions = []
        batter_balls = 0
        matched_batters = []
        for batter in resolved_batters:
            batter_row = batter_rows.get(
                (batter, phase, bowler_type, line, length)
            )
            if batter_row:
                predictions.append(_blend(batter_row, group_prediction, 40))
                batter_balls += int(batter_row.get("balls") or 0)
                matched_batters.append(batter)
            else:
                predictions.append(group_prediction)
        if predictions:
            predicted = tuple(
                sum(row[index] for row in predictions) / len(predictions)
                for index in range(3)
            )
        else:
            predicted = group_prediction

        expected_runs, boundary_probability, wicket_probability = predicted
        risk_score = (
            expected_runs
            + 2.0 * boundary_probability
            - 3.0 * wicket_probability
        )
        group_balls = int(group.get("balls") or 0)
        risk_score += 0.08 if group_balls < 100 else 0.03 if group_balls < 300 else 0
        reliability = (
            "high" if batter_balls >= 100 and group_balls >= 500
            else "medium" if batter_balls >= 30 or group_balls >= 200
            else "low"
        )
        candidates.append({
            "line": line,
            "length": length,
            "expected_runs": round(expected_runs, 2),
            "boundary_probability": round(boundary_probability * 100, 1),
            "wicket_probability": round(wicket_probability * 100, 1),
            "risk_score": round(risk_score, 4),
            "group_balls": group_balls,
            "batter_balls": batter_balls,
            "matched_batters": matched_batters,
            "reliability": reliability,
        })

    if not candidates:
        return None
    candidates.sort(key=lambda row: row["risk_score"])
    return {
        "phase": phase,
        "best": candidates[0],
        "alternative": candidates[1] if len(candidates) > 1 else None,
        "batter_specific": candidates[0]["batter_balls"] > 0,
        "source": "trained historical T20 line-and-length model",
        "training_rows": (model.get("totals") or {}).get("usable_rows"),
        "validation_metrics": model.get("validation_metrics") or {},
    }
