"""
Decision Simulation Tool — pure math, no LLM.

Takes bowler effectiveness scores + pressure context and turns them into
2-3 ranked tactical strategies, each with a confidence score. This is the
tool your doc calls the most unique part of the project — it doesn't
write English, it only compares tactical options.

Gemini (next task) will explain WHY the top strategy is best. This tool
just decides WHAT the options are and HOW confident we are in each.
"""


def simulate_strategies(bowler_scores: dict, pressure: int) -> dict:
    if not bowler_scores:
        return {"strategies": [], "top_strategy": None}

    max_score = max(bowler_scores.values())

    strategies = []
    for bowler, score in bowler_scores.items():
        # Confidence = how close this bowler's score is to the best option,
        # nudged down slightly under high pressure (favors proven/safe picks).
        relative_strength = score / max_score  # 1.0 for the best bowler
        pressure_penalty = (pressure / 100) * 5 if score != max_score else 0

        confidence = round(relative_strength * 100 - pressure_penalty)
        confidence = min(max(confidence, 0), 100)

        strategies.append({
            "strategy": f"Continue with {bowler}" if score == max_score else f"Bring on {bowler}",
            "bowler": bowler,
            "confidence": confidence,
        })

    strategies.sort(key=lambda s: s["confidence"], reverse=True)

    return {
        "strategies": strategies,
        "top_strategy": strategies[0]["strategy"],
    }


if __name__ == "__main__":
    # matches the worked example from the original doc
    result = simulate_strategies(
        bowler_scores={"Shaheen": 91, "Naseem": 84, "Abrar": 71},
        pressure=81,
    )
    for s in result["strategies"]:
        print(f"{s['strategy']} — confidence {s['confidence']}%")
    print("Top strategy:", result["top_strategy"])
