from tools.analytics.pressure import calculate_pressure
from tools.analytics.momentum import calculate_momentum
from tools.analytics.bowler_effectiveness import calculate_bowler_effectiveness


def analyze_snapshot(
    snapshot,
    recent_runs,
    recent_wickets,
    recent_overs_span,
    available_bowlers,
    pitch="neutral",
    weather_condition="clear",
    humidity=50,
):
    overs_bowled = float(snapshot["overs"].split(".")[0])

    if snapshot["overs"].endswith(".6"):
        overs_bowled += 1

    pressure = calculate_pressure(
        current_score=snapshot["runs"],
        wickets_lost=snapshot["wickets"],
        overs_bowled=overs_bowled,
        target=snapshot["target"],
        total_overs=20,
    )

    current_run_rate = snapshot["runs"] / max(overs_bowled, 0.1)

    momentum = calculate_momentum(
        recent_runs=recent_runs,
        recent_wickets=recent_wickets,
        recent_overs_span=recent_overs_span,
        current_run_rate=current_run_rate,
    )

    effectiveness = calculate_bowler_effectiveness(
        available_bowlers=available_bowlers,
        pitch=pitch,
        weather_condition=weather_condition,
        humidity=humidity,
    )

    return {
        "match_snapshot": snapshot,
        "pressure": pressure,
        "momentum": momentum,
        "bowler_effectiveness": effectiveness,
    }