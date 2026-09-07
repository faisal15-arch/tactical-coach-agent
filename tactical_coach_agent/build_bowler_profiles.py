"""
Build bowler profiles from a Cricsheet archive.

Run this once. It reads every match JSON in a folder, adds up each
bowler's career balls, runs and wickets, and turns those into a
base_skill rating on the same 0-100 scale the rest of the project uses.

The point is to retire the hand-written BOWLER_PROFILES dict. Those five
ratings were typed in by hand and happened to cover one team in one
match; every other team and every other match produced an empty ranking.

SMALL SAMPLES
-------------
A hard cutoff forced a bad choice. Set it high and real bowlers
disappeared: S Badree bowled around 30 overs across this archive and fell
off the list entirely, so the agent could not rate a man who was actually
bowling in the match being replayed. Set it low and bowlers with 22 career
overs landed level with front-line bowlers who had 150 behind them.

So the cutoff is now low, and confidence does the work instead. A rating
is a blend of the bowler's own figures and the pool average, weighted by
how much he has bowled:

    10 overs  -> mostly the pool average, barely his own numbers
    30 overs  -> roughly half and half, flagged provisional
    50+ overs -> entirely his own figures

This is shrinkage, the same idea already used for in-match form, where a
two-over spell is pulled back toward 50. A thin sample should move a
rating a little, not a lot.

Usage:
    python build_bowler_profiles.py "C:\\Users\\user\\Downloads\\psl_json"

Writes tools/analytics/bowler_profiles.json.

Known limit: Cricsheet ball-by-ball data does not record bowling style,
and it cannot be inferred from deliveries. Style is written as null.
"""

import json
import os
import sys
from collections import defaultdict

# Absolute floor. Below this there is nothing to work with at all, and a
# rating would be the pool average wearing someone's name.
MIN_CAREER_BALLS = 60  # 10 overs

# The sample at which a bowler's own figures are trusted completely.
# Between the floor and this, the rating is pulled toward the pool mean
# in proportion to how little he has bowled.
FULL_CONFIDENCE_BALLS = 300  # 50 overs

# Below this, the rating is marked provisional so the UI can say so.
PROVISIONAL_BELOW_BALLS = 300  # 50 overs

# Reference points for the rating curve, in T20 terms.
GOOD_ECONOMY = 5.5       # scores 100 on the economy component
POOR_ECONOMY = 11.0      # scores near 0
GOOD_STRIKE_RATE = 12.0  # balls per wicket, scores 100
POOR_STRIKE_RATE = 42.0  # scores near 0

# Economy matters more than strike rate in T20: containing is the job.
ECONOMY_WEIGHT = 0.6
STRIKE_RATE_WEIGHT = 0.4

# Keep ratings inside a band the rest of the pipeline is calibrated for.
MIN_SKILL, MAX_SKILL = 45, 95

# Optional. Cricsheet has no style field, so fill these in by hand if you
# want the pitch bonuses in bowler_effectiveness.py to fire. Anything not
# listed stays null and gets no bonus, which is what happens today.
STYLE_OVERRIDES = {
    # "S Badree": "spin",
    # "Zulfiqar Babar": "spin",
    # "Mohammad Irfan": "pace",
    # "Rumman Raees": "pace_swing",
}

OUTPUT_PATH = os.path.join("tools", "analytics", "bowler_profiles.json")


def _scale(value, good, poor):
    """Map a value onto 0-100, where `good` is 100 and `poor` is 0."""
    if value <= good:
        return 100.0
    if value >= poor:
        return 0.0
    return 100.0 * (poor - value) / (poor - good)


def collect_career_stats(folder: str) -> dict:
    stats = defaultdict(lambda: {"balls": 0, "runs": 0, "wickets": 0, "matches": set()})

    files = [f for f in os.listdir(folder) if f.lower().endswith(".json")]
    if not files:
        raise FileNotFoundError(f"No .json files found in {folder}")

    skipped = 0

    for filename in files:
        path = os.path.join(folder, filename)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                match = json.load(handle)
        except Exception:
            skipped += 1
            continue

        if not isinstance(match, dict) or "innings" not in match:
            skipped += 1
            continue

        for innings in match["innings"]:
            for over_data in innings.get("overs", []):
                for delivery in over_data.get("deliveries", []):
                    bowler = delivery.get("bowler")
                    if not bowler:
                        continue

                    runs = delivery.get("runs", {})
                    extras = delivery.get("extras", {})

                    entry = stats[bowler]
                    entry["matches"].add(filename)

                    # Wides and no-balls are not legal deliveries, so they
                    # do not count toward balls bowled, but the runs are
                    # still charged to the bowler.
                    if not ("wides" in extras or "noballs" in extras):
                        entry["balls"] += 1

                    # Byes and leg byes are not the bowler's fault.
                    conceded = runs.get("total", 0)
                    conceded -= extras.get("byes", 0)
                    conceded -= extras.get("legbyes", 0)
                    entry["runs"] += max(conceded, 0)

                    for wicket in delivery.get("wickets", []) or []:
                        # Run outs are not credited to the bowler.
                        if wicket.get("kind") not in (
                            "run out", "retired hurt", "obstructing the field"
                        ):
                            entry["wickets"] += 1

    print(f"[build] read {len(files) - skipped} match files, skipped {skipped}")
    return stats


def _raw_skill(entry: dict):
    """A bowler's rating from his own figures alone, before shrinkage."""
    balls = entry["balls"]
    overs = balls / 6
    economy = entry["runs"] / overs

    # A wicketless bowler would divide by zero, so treat him as if the
    # next wicket is still coming.
    strike_rate = balls / entry["wickets"] if entry["wickets"] else POOR_STRIKE_RATE

    economy_score = _scale(economy, GOOD_ECONOMY, POOR_ECONOMY)
    strike_score = _scale(strike_rate, GOOD_STRIKE_RATE, POOR_STRIKE_RATE)

    raw = ECONOMY_WEIGHT * economy_score + STRIKE_RATE_WEIGHT * strike_score
    skill = MIN_SKILL + (MAX_SKILL - MIN_SKILL) * raw / 100

    return skill, overs, economy, strike_rate


def build_profiles(stats: dict) -> dict:
    eligible = {
        bowler: entry
        for bowler, entry in stats.items()
        if entry["balls"] >= MIN_CAREER_BALLS
    }
    below_floor = len(stats) - len(eligible)

    if not eligible:
        return {}

    # The pool mean is what a thin sample gets pulled toward. Weighting it
    # by balls bowled stops a crowd of 10-over bowlers from dragging the
    # anchor around.
    total_balls = sum(entry["balls"] for entry in eligible.values())
    pool_mean = sum(
        _raw_skill(entry)[0] * entry["balls"] for entry in eligible.values()
    ) / total_balls

    print(f"[build] pool mean rating {pool_mean:.1f}, weighted by overs bowled")

    profiles = {}
    provisional = 0

    for bowler, entry in eligible.items():
        balls = entry["balls"]
        raw_skill, overs, economy, strike_rate = _raw_skill(entry)

        # Confidence in this bowler's own numbers: 0 at the floor, 1 at
        # full confidence, linear in between.
        span = max(FULL_CONFIDENCE_BALLS - MIN_CAREER_BALLS, 1)
        confidence = min(max((balls - MIN_CAREER_BALLS) / span, 0.0), 1.0)

        base_skill = confidence * raw_skill + (1 - confidence) * pool_mean
        is_provisional = balls < PROVISIONAL_BELOW_BALLS

        if is_provisional:
            provisional += 1

        profiles[bowler] = {
            "base_skill": round(base_skill),
            "style": STYLE_OVERRIDES.get(bowler),
            "provisional": is_provisional,
            "confidence": round(confidence, 2),
            "career": {
                "matches": len(entry["matches"]),
                "overs": round(overs, 1),
                "runs": entry["runs"],
                "wickets": entry["wickets"],
                "economy": round(economy, 2),
                "strike_rate": round(strike_rate, 1),
                "raw_skill": round(raw_skill),
            },
        }

    print(
        f"[build] {len(profiles)} bowlers rated "
        f"({provisional} provisional), "
        f"{below_floor} below the {MIN_CAREER_BALLS}-ball floor"
    )
    return profiles


def main():
    if len(sys.argv) < 2:
        print('Usage: python build_bowler_profiles.py "<path to psl_json folder>"')
        sys.exit(1)

    folder = sys.argv[1]
    stats = collect_career_stats(folder)
    profiles = build_profiles(stats)

    if not profiles:
        print("[build] nothing to write, check the folder path")
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(profiles, handle, indent=2, sort_keys=True)

    print(f"[build] wrote {OUTPUT_PATH}")

    # Only settled ratings belong in a top list. A provisional one is
    # mostly the pool average and would sit there under false pretences.
    settled = {n: d for n, d in profiles.items() if not d["provisional"]}
    ranked = sorted(settled.items(), key=lambda kv: -kv[1]["base_skill"])

    print("\nTop 15 (settled ratings only):")
    for name, data in ranked[:15]:
        career = data["career"]
        print(
            f"  {data['base_skill']:>3}  {name:<26} "
            f"{career['overs']:>7} ov  econ {career['economy']:>5}  "
            f"sr {career['strike_rate']:>5}  ({career['matches']} matches)"
        )

    print("\nBottom 5 (settled ratings only):")
    for name, data in ranked[-5:]:
        career = data["career"]
        print(
            f"  {data['base_skill']:>3}  {name:<26} "
            f"{career['overs']:>7} ov  econ {career['economy']:>5}"
        )

    # Sanity check against the match the project currently runs on.
    check = [
        "AD Russell", "Mohammad Irfan", "Rumman Raees", "S Badree", "SR Watson",
        "Umar Gul", "Zulfiqar Babar", "Mohammad Nabi", "Anwar Ali", "Mohammad Nawaz (3)",
    ]
    print("\nCoverage check for match 959175:")
    for name in check:
        data = profiles.get(name)
        if not data:
            print(f"  MISSING      {name}")
            continue

        tag = "provisional" if data["provisional"] else "settled    "
        career = data["career"]
        print(
            f"  {tag}  {name:<22} skill {data['base_skill']:>3} "
            f"(own figures {career['raw_skill']}, {career['overs']} ov, "
            f"confidence {data['confidence']})"
        )


if __name__ == "__main__":
    main()