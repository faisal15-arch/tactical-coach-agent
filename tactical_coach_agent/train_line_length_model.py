"""Train the compact T20 line-and-length outcome model from a Kaggle ZIP.

The source CSV is streamed directly from the archive because its expanded size is
several gigabytes.  The resulting model contains smoothed outcome aggregates, so
runtime inference has no pandas/scikit-learn dependency.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile


MODEL_VERSION = 2
VALIDATION_START = "2024-01-01"
MIN_GROUP_BALLS = 20
MIN_BATTER_BALLS = 12


def _number(value: str) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clean(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _player_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value)).strip()


def _batting_hand(value: str) -> str | None:
    text = _clean(value)
    if "left" in text:
        return "left"
    if "right" in text:
        return "right"
    return None


def _phase(ball: str) -> str | None:
    try:
        over = float(ball)
    except (TypeError, ValueError):
        return None
    if over <= 6:
        return "powerplay"
    if over >= 16:
        return "death overs"
    return "middle overs"


def _bowler_type(style: str) -> str | None:
    text = _clean(style).replace("-", " ")
    if ("right arm" in text or "left arm" not in text) and (
        "offbreak" in text or "off spin" in text
    ):
        return "right_arm_off_spin"
    if "left arm" in text and ("orthodox" in text or "finger" in text):
        return "left_arm_finger_spin"
    if ("right arm" in text or "left arm" not in text) and (
        "legbreak" in text or "leg spin" in text
    ):
        return "right_arm_leg_spin"
    if "left arm" in text and any(
        word in text for word in ("chinaman", "wrist", "unorthodox")
    ):
        return "left_arm_wrist_spin"
    if "left arm" in text and any(
        word in text for word in ("fast", "medium", "pace")
    ):
        return "left_arm_pace"
    if "right arm" in text and any(
        word in text for word in ("fast", "medium", "pace")
    ):
        return "right_arm_pace"
    return None


def _length(value: str) -> str | None:
    text = _clean(value).replace("-", " ")
    if not text:
        return None
    if "full toss" in text:
        return "full toss"
    if "yorker" in text or "block hole" in text:
        return "yorker"
    if any(
        word in text
        for word in ("half volley", "overpitched", "very full", "slot")
    ):
        return "full"
    if text == "full" or "full length" in text:
        return "full"
    if any(word in text for word in ("back of length", "back of a length")):
        return "back of length"
    if any(word in text for word in ("short of", "short length")):
        return "short"
    if any(word in text for word in ("bouncer", "short")):
        return "short"
    if "good length" in text or text == "length ball":
        return "good length"
    return None


def _line(value: str) -> str | None:
    text = _clean(value).replace("-", " ")
    if not text:
        return None
    if "wide" in text and "off" in text:
        return "wide outside off"
    if "outside off" in text or "channel" in text:
        return "outside off"
    if "off stump" in text:
        return "off stump"
    if "middle" in text or "on the stumps" in text:
        return "middle stump"
    if "leg stump" in text or text == "leg":
        return "leg stump"
    if "down leg" in text or "down the leg" in text or "wide leg" in text:
        return "down leg"
    if "body" in text:
        return "body"
    return None


def _is_bowler_wicket(wicket_type: str) -> bool:
    dismissal = _clean(wicket_type)
    if not dismissal:
        return False
    return dismissal not in {
        "run out",
        "retired hurt",
        "retired out",
        "obstructing the field",
        "timed out",
    }


def _add(bucket: dict[tuple, list[float]], key: tuple, outcome: tuple) -> None:
    row = bucket[key]
    row[0] += 1
    row[1] += outcome[0]
    row[2] += outcome[1]
    row[3] += outcome[2]


def _new_bucket() -> defaultdict[tuple, list[float]]:
    return defaultdict(lambda: [0, 0.0, 0, 0])


def _record(entries: Iterable[tuple[tuple, list[float]]], minimum: int) -> list[dict]:
    return [
        {
            "key": list(key),
            "balls": int(values[0]),
            "runs": round(values[1], 3),
            "boundaries": int(values[2]),
            "wickets": int(values[3]),
        }
        for key, values in entries
        if values[0] >= minimum
    ]


def _rates(values: list[float]) -> tuple[float, float, float]:
    balls = max(float(values[0]), 1.0)
    return values[1] / balls, values[2] / balls, values[3] / balls


def _validation_metrics(snapshot: dict) -> dict:
    overall = snapshot["overall"]
    overall_rates = _rates(overall)
    actions = {
        tuple(row["key"]): [
            row["balls"], row["runs"], row["boundaries"], row["wickets"]
        ]
        for row in snapshot["actions"]
    }
    groups = {
        tuple(row["key"]): [
            row["balls"], row["runs"], row["boundaries"], row["wickets"]
        ]
        for row in snapshot["groups"]
    }
    handed_groups = {
        tuple(row["key"]): [
            row["balls"], row["runs"], row["boundaries"], row["wickets"]
        ]
        for row in snapshot["handed_groups"]
    }
    totals = {
        "balls": 0,
        "runs_calibration_error": 0.0,
        "boundary_brier": 0.0,
        "wicket_brier": 0.0,
        "baseline_boundary_brier": 0.0,
        "baseline_wicket_brier": 0.0,
    }

    for row in snapshot["validation_handed_groups"]:
        key = tuple(row["key"])
        balls = row["balls"]
        phase, style, hand, line, length = key
        action = actions.get((phase, line, length))
        action_rates = _rates(action) if action else overall_rates
        group = groups.get((phase, style, line, length))
        if group:
            group_rates = _rates(group)
            weight = group[0] / (group[0] + 100)
            group_prediction = tuple(
                weight * group_rates[i] + (1 - weight) * action_rates[i]
                for i in range(3)
            )
        else:
            group_prediction = action_rates
        handed = handed_groups.get(key)
        if handed:
            handed_rates = _rates(handed)
            weight = handed[0] / (handed[0] + 80)
            predicted = tuple(
                weight * handed_rates[i] + (1 - weight) * group_prediction[i]
                for i in range(3)
            )
        else:
            predicted = group_prediction

        actual_runs = row["runs"] / balls
        totals["balls"] += balls
        totals["runs_calibration_error"] += abs(predicted[0] - actual_runs) * balls
        for metric, successes, probability in (
            ("boundary_brier", row["boundaries"], predicted[1]),
            ("wicket_brier", row["wickets"], predicted[2]),
            ("baseline_boundary_brier", row["boundaries"], overall_rates[1]),
            ("baseline_wicket_brier", row["wickets"], overall_rates[2]),
        ):
            totals[metric] += (
                successes * (1 - probability) ** 2
                + (balls - successes) * probability ** 2
            )

    balls = max(totals["balls"], 1)
    return {
        "holdout_balls": totals["balls"],
        "runs_per_ball_group_calibration_mae": round(
            totals["runs_calibration_error"] / balls, 4
        ),
        "boundary_brier": round(totals["boundary_brier"] / balls, 4),
        "boundary_baseline_brier": round(
            totals["baseline_boundary_brier"] / balls, 4
        ),
        "wicket_brier": round(totals["wicket_brier"] / balls, 4),
        "wicket_baseline_brier": round(
            totals["baseline_wicket_brier"] / balls, 4
        ),
    }


def train(archive: Path, output: Path) -> dict:
    all_groups = _new_bucket()
    all_handed_groups = _new_bucket()
    all_actions = _new_bucket()
    all_batters = _new_bucket()
    train_groups = _new_bucket()
    train_handed_groups = _new_bucket()
    train_actions = _new_bucket()
    train_batters = _new_bucket()
    validation_groups = _new_bucket()
    validation_handed_groups = _new_bucket()
    batter_hand_counts: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"left": 0, "right": 0}
    )
    totals = {
        "rows_seen": 0,
        "t20_rows": 0,
        "usable_rows": 0,
        "validation_rows": 0,
    }
    overall = [0, 0.0, 0, 0]
    train_overall = [0, 0.0, 0, 0]

    with ZipFile(archive) as zipped:
        csv_names = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError("No CSV file was found inside the archive")
        with zipped.open(csv_names[0]) as raw:
            import io

            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text)
            header = next(reader)
            index = {name: position for position, name in enumerate(header)}
            required = {
                "match_id", "start_date", "ball", "striker", "ball_length",
                "ball_line", "runs_off_bat", "extras", "wicket_type", "format",
                "bowling style_bowler", "full name_striker",
            }
            missing = sorted(required - index.keys())
            if missing:
                raise ValueError(f"Required columns are missing: {', '.join(missing)}")

            for values in reader:
                totals["rows_seen"] += 1
                if len(values) < len(header) or values[index["format"]] != "T20":
                    continue
                totals["t20_rows"] += 1
                phase = _phase(values[index["ball"]])
                style = _bowler_type(values[index["bowling style_bowler"]])
                line = _line(values[index["ball_line"]])
                length = _length(values[index["ball_length"]])
                if not all((phase, style, line, length)):
                    continue

                batter = _player_key(
                    values[index["full name_striker"]]
                    or values[index["striker"]]
                )
                batting_hand = _batting_hand(
                    values[index["batting style_striker"]]
                )
                runs = _number(values[index["runs_off_bat"]]) + _number(
                    values[index["extras"]]
                )
                boundary = int(_number(values[index["runs_off_bat"]]) >= 4)
                wicket = int(_is_bowler_wicket(values[index["wicket_type"]]))
                outcome = (runs, boundary, wicket)
                group_key = (phase, style, line, length)
                handed_key = (phase, style, batting_hand, line, length)
                action_key = (phase, line, length)
                batter_key = (batter, phase, style, line, length)

                _add(all_groups, group_key, outcome)
                if batting_hand:
                    _add(all_handed_groups, handed_key, outcome)
                _add(all_actions, action_key, outcome)
                if batter:
                    _add(all_batters, batter_key, outcome)
                    if batting_hand:
                        batter_hand_counts[batter][batting_hand] += 1
                for position, amount in enumerate((1, runs, boundary, wicket)):
                    overall[position] += amount
                totals["usable_rows"] += 1

                start_date = values[index["start_date"]]
                if start_date and start_date >= VALIDATION_START:
                    _add(validation_groups, group_key, outcome)
                    if batting_hand:
                        _add(validation_handed_groups, handed_key, outcome)
                    totals["validation_rows"] += 1
                else:
                    _add(train_groups, group_key, outcome)
                    if batting_hand:
                        _add(train_handed_groups, handed_key, outcome)
                    _add(train_actions, action_key, outcome)
                    if batter:
                        _add(train_batters, batter_key, outcome)
                    for position, amount in enumerate((1, runs, boundary, wicket)):
                        train_overall[position] += amount

                if totals["rows_seen"] % 500_000 == 0:
                    print(
                        f"rows={totals['rows_seen']:,} "
                        f"t20={totals['t20_rows']:,} usable={totals['usable_rows']:,}",
                        flush=True,
                    )

    payload = {
        "version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "source_archive": archive.name,
        "scope": "T20",
        "validation_start": VALIDATION_START,
        "totals": totals,
        "overall": overall,
        "groups": _record(all_groups.items(), MIN_GROUP_BALLS),
        "handed_groups": _record(all_handed_groups.items(), MIN_GROUP_BALLS),
        "actions": _record(all_actions.items(), MIN_GROUP_BALLS),
        "batters": _record(all_batters.items(), MIN_BATTER_BALLS),
        "batter_hands": {
            batter: max(counts, key=counts.get)
            for batter, counts in batter_hand_counts.items()
            if sum(counts.values()) >= MIN_BATTER_BALLS
        },
        "training_snapshot": {
            "overall": train_overall,
            "groups": _record(train_groups.items(), MIN_GROUP_BALLS),
            "handed_groups": _record(
                train_handed_groups.items(), MIN_GROUP_BALLS
            ),
            "actions": _record(train_actions.items(), MIN_GROUP_BALLS),
            "validation_groups": _record(validation_groups.items(), 1),
            "validation_handed_groups": _record(
                validation_handed_groups.items(), 1
            ),
        },
    }
    payload["validation_metrics"] = _validation_metrics(
        payload["training_snapshot"]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8", compresslevel=9) as model_file:
        json.dump(payload, model_file, separators=(",", ":"))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/line_length/line_length_model.json.gz"),
    )
    args = parser.parse_args()
    result = train(args.archive, args.output)
    print(json.dumps(result["totals"], indent=2))
    print(json.dumps(result["validation_metrics"], indent=2))
    print(f"Model written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
