from tools.analytics.line_length_model import recommend_line_length


def test_trained_model_returns_ranked_death_over_plan():
    result = recommend_line_length(
        "left_arm_pace",
        "18.2",
        ["Colin Munro", "Nicholas Pooran"],
    )

    assert result is not None
    assert result["phase"] == "death overs"
    assert result["training_rows"] >= 300_000
    assert result["best"]["line"]
    assert result["best"]["length"]
    assert 0 <= result["best"]["boundary_probability"] <= 100
    assert 0 <= result["best"]["wicket_probability"] <= 100


def test_trained_model_supports_leg_spin_and_phase_routing():
    result = recommend_line_length(
        "right_arm_leg_spin",
        "4.0",
        ["Babar Azam"],
    )

    assert result is not None
    assert result["phase"] == "powerplay"


def test_unknown_bowling_style_uses_rule_fallback_path():
    assert recommend_line_length(None, "10", ["Test Batter"]) is None
