import main
from unittest.mock import patch


def test_first_over_uses_unused_playing_xi_bowlers():
    details = {
        "status": "ok",
        "lineups": [
            {
                "name": "Bowling Team",
                "short_name": "BT",
                "playing_xi": [
                    {
                        "player_id": 1,
                        "name": "Opening Bowler",
                        "role": "Bowler",
                    },
                    {
                        "player_id": 2,
                        "name": "Second Bowler",
                        "role": "Bowler",
                    },
                    {
                        "player_id": 3,
                        "name": "All Rounder",
                        "role": "Bowling Allrounder",
                    },
                    {
                        "player_id": 4,
                        "name": "Pure Batter",
                        "role": "Batter",
                    },
                ],
            },
        ],
    }
    data = {
        "bowling_team": "BT",
        "total_overs": 20,
        "bowling_card": [
            {
                "bowler": "Opening Bowler",
                "bowler_id": 1,
                "balls": 6,
                "overs": "1",
                "runs": 7,
                "wickets": 0,
                "economy": 7.0,
                "overs_left": 3,
                "quota_used": False,
            },
        ],
    }

    with patch.object(main, "get_match_details", return_value=details):
        card = main._complete_bowling_card(123, data)
    profiles = {
        name: {
            "balls": 900,
            "matches": 50,
            "wickets": 60,
            "economy": 7.5,
            "strike_rate": 18.0,
        }
        for name in ("Opening Bowler", "Second Bowler", "All Rounder")
    }
    analytics = main._live_analytics(
        card,
        profiles,
        current_bowler="Opening Bowler",
        pressure=50,
        momentum_score=0,
        match_finished=False,
    )

    assert analytics["available_bowlers"] == ["Second Bowler", "All Rounder"]
    assert analytics["recommendation"] in {"Second Bowler", "All Rounder"}
    assert analytics["form_scores"].get("Second Bowler") is None
    assert analytics["planning_scores"]["Second Bowler"] > 0


def test_current_batters_receive_career_head_to_head_stats():
    data = {
        "current_bowler": "Test Bowler",
        "current_batsmen": [
            {"name": "First Batter"},
            {"name": "Second Batter"},
        ],
        "bowling_card": [
            {"bowler": "Test Bowler"},
            {"bowler": "Other Bowler"},
        ],
    }

    def matchup(batter, bowler):
        return {
            "batter": batter,
            "bowler": bowler,
            "runs": 24,
            "balls": 20,
            "dismissals": 1,
            "dots": 8,
            "fours": 2,
            "sixes": 1,
            "strike_rate": 120.0,
            "average": 24.0,
            "source": "Cricmetric All T20",
        }

    with patch.object(main, "get_t20_player_matchup", side_effect=matchup):
        result = main._load_current_career_matchups(data)

    assert set(result) == {"First Batter", "Second Batter"}
    assert set(result["First Batter"]) == {"Test Bowler", "Other Bowler"}
    assert result["First Batter"]["Test Bowler"]["bowler"] == "Test Bowler"
    assert result["First Batter"]["Other Bowler"]["balls"] == 20


def test_missing_confidence_samples_score_forty_and_quota_is_not_weighted():
    card = [
        {
            "bowler": "Bowler A",
            "balls": 0,
            "economy": 0,
            "wickets": 0,
            "overs_left": 4,
            "quota_used": False,
        },
        {
            "bowler": "Bowler B",
            "balls": 0,
            "economy": 0,
            "wickets": 0,
            "overs_left": 1,
            "quota_used": False,
        },
    ]

    analytics = main._live_analytics(
        card,
        player_t20_stats={},
        current_bowler=None,
        pressure=50,
        momentum_score=None,
        match_finished=False,
        matchup_stats={},
        current_batsmen=[{"name": "Test Batter"}],
        career_matchup_stats={},
    )

    assert analytics["matchup_scores"] == {"Bowler A": 40, "Bowler B": 40}
    assert analytics["career_matchup_scores"] == {
        "Bowler A": 40,
        "Bowler B": 40,
    }
    assert analytics["planning_confidence_scores"] == {
        "Bowler A": 40,
        "Bowler B": 40,
    }
