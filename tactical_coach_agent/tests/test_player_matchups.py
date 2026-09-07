from tools.live_data.player_matchups import (
    _aggregate_bowling_type_rows,
    _google_table_row,
    _resolve_matchup_player,
)
from main import (
    _best_ranked_bowler_for_types,
    _build_bowling_plan,
    _bowler_type_key,
    _confidence_x_factor,
    _explain_bowling_plan,
    _factor_evidence_reason,
    _is_bowling_type_question,
    _named_matchup_players,
    _normalize_chat_question,
    _requested_bowling_type_keys,
    _requested_plan_length,
)


def test_named_matchup_parser_removes_question_and_over_context():
    assert _named_matchup_players(
        "What are the stats of Ravichandran Ashwan against Trent Boult at over 11"
    ) == ("ravichandran ashwan", "trent boult")


def test_matchup_player_resolution_accepts_a_small_typo():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"id": "R Ashwin", "text": "Ravichandran Ashwin"},
                    {"id": "R Sasidharan", "text": "Ravichandran Sasidharan"},
                ]
            }

    class Client:
        def get(self, *_args, **_kwargs):
            return Response()

    assert _resolve_matchup_player(
        "ravichandran ashwan",
        Client(),
        1,
    ) == ("R Ashwin", "Ravichandran Ashwin")


def test_google_table_matchup_row_uses_column_ids():
    payload = {
        "data": {
            "cols": [
                {"id": "pkey"},
                {"id": "runs"},
                {"id": "balls"},
                {"id": "outs"},
                {"id": "sr"},
            ],
            "rows": [
                {"c": [
                    {"v": "R Ashwin"},
                    {"v": 6.0},
                    {"v": 8.0},
                    {"v": 2.0},
                    {"v": 75.0},
                ]}
            ],
        }
    }

    assert _google_table_row(payload) == {
        "pkey": "R Ashwin",
        "runs": 6.0,
        "balls": 8.0,
        "outs": 2.0,
        "sr": 75.0,
    }


def test_bowling_types_are_aggregated_into_tactical_groups():
    rows = [
        {
            "key": "Right-arm Offbreak", "I": 4, "R": 60,
            "B": 40, "Outs": 1, "Dots": 25,
        },
        {
            "key": "Left-arm Orthodox", "I": 3, "R": 30,
            "B": 30, "Outs": 2, "Dots": 40,
        },
        {
            "key": "Left-arm Fast", "I": 2, "R": 25,
            "B": 20, "Outs": 1, "Dots": 30,
        },
        {
            "key": "Left-arm Medium", "I": 2, "R": 20,
            "B": 20, "Outs": 0, "Dots": 20,
        },
    ]

    result = _aggregate_bowling_type_rows(rows)

    assert result["right_arm_off_spin"]["runs"] == 60
    assert result["left_arm_finger_spin"]["balls"] == 30
    assert result["left_arm_finger_spin"]["dismissals"] == 2
    assert result["left_arm_pace"]["runs"] == 45
    assert result["left_arm_pace"]["strike_rate"] == 112.5
    assert result["right_arm_leg_spin"]["assessment"] == "No sample"


def test_bowling_type_question_does_not_need_exact_wording():
    assert _is_bowling_type_question("How strong is Pooran against offspinners?")
    assert _requested_bowling_type_keys(
        "Show both current batters against left arm fast bowlers"
    ) == ["left_arm_pace"]
    assert _requested_bowling_type_keys(
        "How strong is Ryan Burl against off spinners?"
    ) == ["right_arm_off_spin", "left_arm_finger_spin"]
    assert _requested_bowling_type_keys(
        "Show his record against right arm leg spin"
    ) == ["right_arm_leg_spin"]
    assert _normalize_chat_question(
        "Current batsmen stats against off spinners"
    ) == "current batsmen stats against off spinners"


def test_bowler_style_maps_to_arm_specific_type():
    assert _bowler_type_key("Right-arm offbreak") == "right_arm_off_spin"
    assert _bowler_type_key("Left-arm orthodox") == "left_arm_finger_spin"
    assert _bowler_type_key("Right-arm legbreak") == "right_arm_leg_spin"
    assert _bowler_type_key("Left-arm fast-medium") == "left_arm_pace"


def test_type_recommendation_uses_best_eligible_confidence_rank():
    analytics = {
        "confidence_scores": {
            "Off Spinner A": 61,
            "Off Spinner B": 74,
            "Fast Bowler": 90,
        },
    }
    profiles = {
        "Off Spinner A": {"bowling_style": "Right-arm offbreak"},
        "Off Spinner B": {"bowling_style": "Left-arm orthodox"},
        "Fast Bowler": {"bowling_style": "Right-arm fast"},
    }

    assert _best_ranked_bowler_for_types(
        analytics,
        profiles,
        ["right_arm_off_spin", "left_arm_finger_spin"],
    ) == ("Off Spinner B", 74, "Left-arm orthodox")


def test_three_over_plan_respects_rotation_and_quota():
    data = {
        "over": "10",
        "total_overs": 20,
        "current_bowler": "Bowler A",
        "bowling_card": [
            {"bowler": "Bowler A", "overs_left": 2},
            {"bowler": "Bowler B", "overs_left": 2},
            {"bowler": "Bowler C", "overs_left": 1},
        ],
    }
    analytics = {
        "planning_confidence_scores": {
            "Bowler A": 90,
            "Bowler B": 80,
            "Bowler C": 70,
        },
    }

    plan = _build_bowling_plan(data, analytics, 3)

    assert [row["over"] for row in plan] == [11, 12, 13]
    assert [row["bowler"] for row in plan] == [
        "Bowler B", "Bowler A", "Bowler B",
    ]
    assert all(
        plan[index]["bowler"] != plan[index - 1]["bowler"]
        for index in range(1, len(plan))
    )
    assert _requested_plan_length("Give me the next four overs plan") == 4


def test_plan_why_reports_each_bowlers_strongest_factor_only():
    assert _confidence_x_factor({
        "in_match_form": 90,
        "innings_matchup": 70,
        "bowler_profile": 60,
        "career_matchup": 55,
        "momentum_fit": 50,
        "pressure_fit": 50,
    }) == "in-match form 90/100"

    answer = _explain_bowling_plan([
        {
            "over": 11,
            "bowler": "Bowler A",
            "confidence": 72,
            "x_factor": "in-match form 90/100",
        },
        {
            "over": 12,
            "bowler": "Bowler B",
            "confidence": 68,
            "x_factor": "current-innings matchup 82/100",
        },
        {
            "over": 13,
            "bowler": "Bowler A",
            "confidence": 72,
            "x_factor": "in-match form 90/100",
        },
    ])

    assert answer == (
        "Over 11: Bowler A because in-match form is his strongest factor "
        "(90/100). Over 12: Bowler B because current-innings matchup is his "
        "strongest factor (82/100). Over 13: Bowler A because in-match form "
        "is his strongest factor (90/100)."
    )


def test_career_matchup_reason_uses_actual_figures():
    reason = _factor_evidence_reason(
        "Test Bowler",
        "career_matchup",
        {
            "current_batsmen": [
                {"name": "Batter A"},
                {"name": "Batter B"},
            ],
            "career_matchup_stats": {
                "Batter A": {
                    "Test Bowler": {
                        "runs": 18,
                        "balls": 20,
                        "dismissals": 2,
                    },
                },
                "Batter B": {
                    "Test Bowler": {
                        "runs": 12,
                        "balls": 10,
                        "dismissals": 1,
                    },
                },
            },
        },
        {},
    )

    assert reason == (
        "he has conceded 30 runs from 30 balls and dismissed the current "
        "batters 3 times in career T20s"
    )
