"""
Pressure Index Tool — pure math, no external calls.

A simple deterministic formula combining:
 - required run rate vs current run rate (how far behind the chase is)
 - wickets lost (fewer wickets in hand = more pressure)
 - overs remaining (less time left = more pressure)

This is intentionally simple for now. You can tune the weights once you
have real match data to validate against.
"""


def calculate_pressure(
    current_score: int,
    wickets_lost: int,
    overs_bowled: float,
    target: int,
    total_overs: float = 50.0,
) -> dict:
    overs_remaining = max(total_overs - overs_bowled, 0.1)  # avoid div-by-zero
    runs_needed = max(target - current_score, 0)

    current_run_rate = current_score / max(overs_bowled, 0.1)
    required_run_rate = runs_needed / overs_remaining

    # --- weighted components (each roughly 0-100 scale before combining) ---
    rate_pressure = min((required_run_rate - current_run_rate) * 10, 100)
    rate_pressure = max(rate_pressure, 0)

    wicket_pressure = (wickets_lost / 10) * 100

    overs_pressure = (1 - (overs_remaining / total_overs)) * 100

    pressure = (
        0.5 * rate_pressure
        + 0.3 * wicket_pressure
        + 0.2 * overs_pressure
    )
    pressure = round(min(max(pressure, 0), 100))

    if pressure >= 75:
        label = "High"
    elif pressure >= 40:
        label = "Medium"
    else:
        label = "Low"

    return {
        "pressure": pressure,
        "pressure_label": label,
        "required_run_rate": round(required_run_rate, 2),
        "current_run_rate": round(current_run_rate, 2),
    }


if __name__ == "__main__":
    # quick manual sanity checks
    print(calculate_pressure(current_score=165, wickets_lost=4, overs_bowled=32, target=280))
    print(calculate_pressure(current_score=250, wickets_lost=1, overs_bowled=45, target=260))
