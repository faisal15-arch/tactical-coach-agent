"""
Momentum Tool — pure math, no external calls.

Momentum captures which side currently has the upper hand, based on
recent scoring rate compared to the overall run rate, and wickets lost
in that recent span. Positive score = batting side building momentum,
negative = bowling side on top.

Uses mock "recent overs" data for now — same pattern as pressure.py.
Once real ball-by-ball data is wired in, recent_runs/recent_wickets
would come from the live feed instead of being hardcoded.
"""


def calculate_momentum(
    recent_runs: int,
    recent_wickets: int,
    recent_overs_span: float,
    current_run_rate: float,
) -> dict:
    recent_run_rate = recent_runs / max(recent_overs_span, 0.1)

    momentum_score = round(
        (recent_run_rate - current_run_rate) * 15 - recent_wickets * 20
    )
    momentum_score = max(min(momentum_score, 100), -100)

    if momentum_score >= 60:
        level = "Very high"
        label = "Batting side building momentum"
    elif momentum_score > 20:
        level = "High"
        label = "Batting side building momentum"
    elif momentum_score <= -60:
        level = "Very low"
        label = "Bowling side building momentum"
    elif momentum_score < -20:
        level = "Low"
        label = "Bowling side building momentum"
    else:
        level = "Neutral"
        label = "Momentum neutral"

    return {
        "momentum_score": momentum_score,
        "momentum_level": level,
        "momentum_label": label,
        "recent_run_rate": round(recent_run_rate, 2),
    }


if __name__ == "__main__":
    # scoring quickly, few wickets lost -> batting side momentum
    print(calculate_momentum(recent_runs=42, recent_wickets=0, recent_overs_span=5, current_run_rate=5.16))
    # two quick wickets -> bowling side momentum
    print(calculate_momentum(recent_runs=15, recent_wickets=2, recent_overs_span=5, current_run_rate=5.16))
