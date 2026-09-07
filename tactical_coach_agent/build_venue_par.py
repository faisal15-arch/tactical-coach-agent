"""
Build first-innings par scores from a Cricsheet archive.

Run this once, like build_bowler_profiles.py.

Why this exists: pressure in a chase is obvious, the batting side has a
target and a required rate. In the first innings there is no target, so
"pressure" has to be measured against something else. The honest
benchmark is what a first innings normally looks like at that point:
how many runs a side usually has after 5 overs, after 10, after 15.

So this does not just record final totals. It records the average score
at the end of every over, which gives a par curve. A side 62/1 after 10
is ahead of par; 38/4 after 10 is behind it, and the bowling side is on
top. That comparison is what makes a first-innings pressure number mean
anything.

Usage:
    python build_venue_par.py "C:\\Users\\user\\Downloads\\t20i_json"

Writes tools/analytics/venue_par.json.
"""

import json
import os
import sys
from collections import defaultdict

# Innings shorter than this are rain-hit or otherwise irregular and would
# drag the curve down. A side bowled out in 16 overs is fine to include,
# a 12-over-a-side match is not.
EXPECTED_OVERS = 20

# A venue needs this many first innings before its own curve is used.
# Below it, the global curve is the safer answer.
MIN_MATCHES_PER_VENUE = 3

OUTPUT_PATH = os.path.join("tools", "analytics", "venue_par.json")


def _first_innings_curve(match: dict):
    """
    Runs at the end of each over of the first innings, as a dict of
    over number to cumulative score. Returns None if the innings is not
    usable as a par sample.
    """
    info = match.get("info", {})

    # Rain-shortened games and D/L results distort the curve.
    if info.get("overs") != EXPECTED_OVERS:
        return None
    if (info.get("outcome", {}) or {}).get("method"):
        return None

    innings = match.get("innings") or []
    if not innings:
        return None

    first = innings[0]
    runs = 0
    curve = {}

    for over_data in first.get("overs", []):
        for delivery in over_data.get("deliveries", []):
            runs += delivery.get("runs", {}).get("total", 0)
        # over is 0-indexed in Cricsheet, so over 0 is the first over.
        curve[over_data["over"] + 1] = runs

    if not curve:
        return None

    # An all-out first innings has genuinely finished, but its final total
    # still belongs in the later-over venue average. Without carrying that
    # score forward, over 20 only contains teams that survived all 20 overs
    # and the benchmark is biased upwards.
    final_score = runs
    for over_number in range(max(curve) + 1, EXPECTED_OVERS + 1):
        curve[over_number] = final_score

    return curve


def collect(folder: str):
    files = [f for f in os.listdir(folder) if f.lower().endswith(".json")]
    if not files:
        raise FileNotFoundError(f"No .json files found in {folder}")

    # venue -> over -> list of scores at that over
    by_venue = defaultdict(lambda: defaultdict(list))
    globally = defaultdict(list)

    venue_matches = defaultdict(int)
    used = skipped = 0

    for filename in files:
        try:
            with open(os.path.join(folder, filename), "r", encoding="utf-8") as handle:
                match = json.load(handle)
        except Exception:
            skipped += 1
            continue

        curve = _first_innings_curve(match)
        if not curve:
            skipped += 1
            continue

        venue = (match.get("info", {}) or {}).get("venue") or "unknown"
        venue_matches[venue] += 1
        used += 1

        for over, score in curve.items():
            by_venue[venue][over].append(score)
            globally[over].append(score)

    print(f"[par] used {used} first innings, skipped {skipped} (short, D/L, or unreadable)")
    return by_venue, globally, venue_matches


def _average_curve(over_map) -> dict:
    """Mean score at the end of each over, as {over: runs}."""
    return {
        str(over): round(sum(scores) / len(scores), 1)
        for over, scores in sorted(over_map.items())
        if scores
    }


def main():
    if len(sys.argv) < 2:
        print('Usage: python build_venue_par.py "<path to psl_json folder>"')
        sys.exit(1)

    folder = sys.argv[1]
    by_venue, globally, venue_matches = collect(folder)

    if not globally:
        print("[par] nothing usable found, check the folder path")
        sys.exit(1)

    global_curve = _average_curve(globally)

    venues = {}
    for venue, over_map in by_venue.items():
        count = venue_matches[venue]
        if count < MIN_MATCHES_PER_VENUE:
            continue
        venues[venue] = {
            "matches": count,
            "par_by_over": _average_curve(over_map),
        }

    payload = {
        "expected_overs": EXPECTED_OVERS,
        "min_matches_per_venue": MIN_MATCHES_PER_VENUE,
        "scope": "men's T20 internationals, complete non-DLS matches",
        "source": "Cricsheet men's T20I JSON archive",
        "global": {
            "matches": len(globally.get(1, [])),
            "par_by_over": global_curve,
        },
        "venues": venues,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)

    print(f"[par] wrote {OUTPUT_PATH}")
    print(f"[par] {len(venues)} venues have their own curve, rest fall back to global")

    print("\nGlobal par curve:")
    for over in (5, 10, 15, 20):
        value = global_curve.get(str(over))
        if value is not None:
            print(f"  after {over:>2} overs: {value}")

    print("\nVenues with their own curve:")
    for venue, data in sorted(venues.items(), key=lambda kv: -kv[1]["matches"]):
        par20 = data["par_by_over"].get("20", "n/a")
        par10 = data["par_by_over"].get("10", "n/a")
        print(f"  {data['matches']:>3} matches  par@10 {par10:>6}  par@20 {par20:>6}  {venue}")

    dubai = [v for v in venues if "Dubai" in v]
    print("\nCheck for the match currently loaded (Dubai):")
    for venue in dubai:
        print(f"  {venue}: {venues[venue]['par_by_over'].get('10')} after 10 overs")
    if not dubai:
        print("  no Dubai venue met the minimum, it will use the global curve")


if __name__ == "__main__":
    main()
