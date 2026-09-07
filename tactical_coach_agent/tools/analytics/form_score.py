"""
Recent Form Tool — pure math, no external calls.

Scores each bowler based on their recent performance (last few matches):
wickets taken (more = better) weighed against economy rate (lower = better).
This is separate from Bowler Effectiveness (which is about THIS match's
conditions) — Form is about how well they've been performing recently,
regardless of today's pitch/weather.

Mock recent-form data for now, same pattern as bowler_effectiveness.py.
"""

# Mock recent form (last 5 matches) — replace with a real stats lookup later
RECENT_FORM_PROFILES = {
    "Shaheen": {"recent_wickets": 8, "recent_economy": 6.2},
    "Naseem": {"recent_wickets": 5, "recent_economy": 7.1},
    "Abrar": {"recent_wickets": 10, "recent_economy": 5.5},
}


def calculate_form_score(bowlers: list[str]) -> dict:
    form_scores = {}

    for bowler in bowlers:
        profile = RECENT_FORM_PROFILES.get(bowler)
        if not profile:
            continue

        score = (profile["recent_wickets"] * 10) - (profile["recent_economy"] * 5)
        form_scores[bowler] = round(min(max(score, 0), 100))

    return {"form_scores": form_scores}


if __name__ == "__main__":
    print(calculate_form_score(["Shaheen", "Naseem", "Abrar"]))
