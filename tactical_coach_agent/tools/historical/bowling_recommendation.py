def recommend_bowler(analytics):
    effectiveness = analytics["bowler_effectiveness"]

    bowler_scores = effectiveness["bowler_scores"]

    if not bowler_scores:
        return {
            "recommended_bowler": None,
            "reason": "No available bowlers found.",
        }

    ranked = sorted(
        bowler_scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    recommended_bowler = ranked[0][0]
    score = ranked[0][1]

    return {
        "recommended_bowler": recommended_bowler,
        "effectiveness_score": score,
        "reason": f"{recommended_bowler} has the highest effectiveness score.",
        "ranking": ranked,
    }