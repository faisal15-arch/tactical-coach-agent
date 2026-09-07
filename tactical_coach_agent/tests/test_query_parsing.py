from graph import parse_over
import main
from main import (
    _asks_for_bowling_ranking,
    _asks_for_latest_live_score,
    _asks_what_venue_par_means,
    _asks_to_go_live,
    _asks_why_not_bowler,
    _is_bare_recommendation_why,
    _is_line_length_question,
    _describe_line_length,
    _normalize_chat_question,
    _pressure_index,
    parse_query_metadata,
)
from tools.live_data.cricbuzz import _partial_over_batter_names


def test_line_length_intent_accepts_follow_ups_names_and_common_typo():
    assert _is_line_length_question("What line and length should he bowl?")
    assert _is_line_length_question("hat line should Saim Ayub bowl?")
    assert _is_line_length_question("Which length should Shaheen use?")
    assert _is_line_length_question("Where should this bowler bowl?")


def test_line_length_categories_become_plain_coaching_language():
    assert _describe_line_length("off stump", "good length") == (
        "a good length on off stump"
    )
    assert _describe_line_length("wide outside off", "yorker") == (
        "a wide yorker outside off stump"
    )


def test_bare_why_uses_saved_line_length_evidence_without_refetch(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("A line-length follow-up must not refetch the scorecard")

    monkeypatch.setattr(main, "get_live_situation", fail_if_called)
    response = main._live_chat_response(
        "Why?",
        "line-length-why",
        123,
        {
            "last_answer_type": "line_length_advice",
            "line_length_plan": {
                "source": "model",
                "expected_runs": 0.8,
                "boundaries_per_hundred": 5,
                "wickets_per_hundred": 4,
                "reliability": "medium",
                "group_balls": 1525,
                "batter_balls": 0,
                "batter_specific": False,
            },
        },
    )

    assert response.answer.startswith("Why: this option")
    assert "1525 comparable T20 balls" in response.answer
    assert "no reliable current-batter-specific sample" in response.answer


def test_live_parser_understands_ordinal_over_after_singular_inning():
    phase, over = parse_query_metadata(
        "Go to 1st inning, 10th over",
        {},
    )

    assert phase == "first"
    assert over == "10"


def test_illegible_bowler_typo_normalizes_to_ineligible_bowler():
    assert _normalize_chat_question(
        "Who is illegible bowler and why?"
    ) == "who is ineligible bowler and why?"


def test_live_parser_understands_standard_over_after_plural_innings():
    phase, over = parse_query_metadata(
        "First innings go to over 10",
        {},
    )

    assert phase == "first"
    assert over == "10"


def test_live_parser_understands_second_innings_ordinal_over():
    phase, over = parse_query_metadata(
        "Take me to the 3rd over of the 2nd inning",
        {},
    )

    assert phase == "chase"
    assert over == "3"


def test_live_parser_preserves_partial_over_navigation():
    phase, over = parse_query_metadata(
        "Second innings, go to 1.3 overs",
        {},
    )

    assert phase == "chase"
    assert over == "1.3"


def test_live_score_and_bare_why_ignore_frontend_over_suffix():
    assert _asks_for_latest_live_score("live score at over 3")
    assert _is_bare_recommendation_why("Why? at over 3.3")


def test_against_par_definition_is_not_a_player_matchup():
    normalized = _normalize_chat_question(
        "hat is against Par venue Score?"
    )

    assert normalized.startswith("what is against par")
    assert _asks_what_venue_par_means(normalized)
    assert not _asks_what_venue_par_means("How far above par are we right now?")


def test_go_live_clears_historical_over_for_follow_up_questions():
    assert _asks_to_go_live("Go to current situation")
    assert _asks_to_go_live("Take me live")

    phase, over = parse_query_metadata(
        "Who should bowl next?",
        {
            "current_phase": "first",
            "current_over": "3.3",
            "requested_over": None,
            "live_mode": True,
        },
    )

    assert phase == "first"
    assert over is None


def test_show_ranking_is_a_bowling_ranking_request_not_a_player_query():
    assert _asks_for_bowling_ranking("Show ranking")
    assert _asks_for_bowling_ranking("Show ranking at over 11")
    assert _asks_for_bowling_ranking("Show ranking at over 11.3")
    assert _asks_for_bowling_ranking("Display the final ranking")
    assert not _asks_for_bowling_ranking("What is Babar Azam's T20 ranking?")


def test_why_not_bowler_phrasings_use_comparison_intent():
    assert _asks_why_not_bowler("Why not Hunain Shah?")
    assert _asks_why_not_bowler("Why didn't you pick Saim Ayub?")
    assert _asks_why_not_bowler("Why would you not recommend Keemo Paul?")
    assert _asks_why_not_bowler("Why can't I bring JJ Smit?")
    assert not _asks_why_not_bowler("Why Jediah Blades?")


def test_plan_explanation_is_a_saved_recommendation_follow_up():
    assert _is_bare_recommendation_why("Why this plan at over 13?")


def test_partial_over_keeps_non_striker_and_rotates_strike():
    names = _partial_over_batter_names(
        ["Nicholas Pooran", "Alex Hales"],
        [{
            "batsmanStriker": {"batName": "Nicholas Pooran"},
            "event": "NONE",
            "totalRuns": 1,
        }],
    )

    assert names == ["Alex Hales", "Nicholas Pooran"]


def test_completed_match_go_live_returns_final_result(monkeypatch):
    monkeypatch.setattr(main, "get_live_situation", lambda *args, **kwargs: {
        "status": "ok",
        "state": "Complete",
        "match_status": "Test XI won by 7 runs",
        "historical_point": False,
        "format": "T20",
        "runs": 150,
        "wickets": 8,
        "score": "150/8",
        "over": "20",
        "current_over": "20",
        "current_phase": "chase",
        "run_rate": 7.5,
        "batting_team": "Test XI",
        "bowling_team": "Other XI",
        "bowling_card": [],
        "current_batsmen": [],
        "recent_over_runs": [],
        "matchup_stats": {},
        "total_overs": 20,
    })
    monkeypatch.setattr(main, "_add_live_par", lambda data: data)
    monkeypatch.setattr(main, "_live_analytics", lambda *args, **kwargs: {
        "recommendation": None,
        "bowler_scores": {},
        "player_t20_stats": {},
        "effectiveness_scores": {},
        "form_scores": {},
        "matchup_scores": {},
        "combined_scores": {},
        "confidence_scores": {},
        "strategies": [],
        "available_bowlers": [],
        "excluded_bowlers": {},
        "unrated_bowlers": [],
        "provisional_bowlers": {},
        "top_strategy": None,
        "choice_note": "No next-over choice after the final result.",
    })

    response = main._live_chat_response(
        "Go to current situation",
        "complete-test",
        123,
        {"requested_over": "10", "current_phase": "chase"},
    )

    assert response.answer == (
        "This match has been completed. Final result: Test XI won by 7 runs."
    )
    assert response.match_finished is True
    assert response.match_status == "Test XI won by 7 runs"
    assert response.live_mode is False


def test_bare_why_uses_previous_recommendation_without_scorecard_lookup():
    original = main.get_live_situation

    def fail_if_called(*args, **kwargs):
        raise AssertionError("A recommendation follow-up must not refetch a ball")

    main.get_live_situation = fail_if_called
    try:
        response = main._live_chat_response(
            "Why? at over 3.3",
            "why-test",
            123,
            {
                "last_answer_type": "bowling_recommendation",
                "recommendation": "Test Bowler",
                "confidence": 82,
                "effectiveness_scores": {"Test Bowler": 71},
                "form_scores": {"Test Bowler": 64},
                "bowling_card": [{
                    "bowler": "Test Bowler",
                    "economy": 6.5,
                    "overs_left": 2,
                }],
                "current_over": "3.3",
                "requested_over": "3.3",
                "current_phase": "first",
            },
        )
    finally:
        main.get_live_situation = original

    assert response.current_over == "3.3"
    assert "Pick Test Bowler (82% confidence)" in response.answer
    assert "career T20 profile" in response.answer
    assert "legally eligible" not in response.answer


def test_historical_parser_understands_ordinal_over():
    assert parse_over("Go to the 10th over") == "10"
    assert parse_over("What happened in over 3rd?") == "3"


def test_live_par_uses_real_venue_t20i_curve_without_global_scaling():
    original = main.par_at_over

    def fake_par_at_over(venue, over):
        if venue == "Queen's Park Oval":
            return 58.3, "Queen's Park Oval men's T20I history (6 matches)"
        return 70.4 if over == 10 else 156.6, "all venues"

    main.par_at_over = fake_par_at_over
    try:
        result = main._add_live_par({
            "format": "T20",
            "venue": "Queen's Park Oval",
            "over": "10",
            "runs": 75,
            "venue_t20_average": 115,
        })
    finally:
        main.par_at_over = original

    assert result["par"] == {
        "runs": 58.3,
        "difference": 16.7,
        "source": "Queen's Park Oval men's T20I history (6 matches)",
        "venue_specific": True,
        "method": "Historical men's T20I mean at this over",
    }


def test_live_par_does_not_invent_current_over_from_full_innings_average():
    original = main.par_at_over
    main.par_at_over = lambda venue, over: (70.4, "all venues")
    try:
        result = main._add_live_par({
            "format": "T20",
            "venue": "Unknown Ground",
            "over": "10",
            "runs": 75,
            "venue_t20_average": 155,
        })
    finally:
        main.par_at_over = original

    assert result["par"] is None


def test_score_projections_apply_rates_to_remaining_first_innings_balls():
    result = main._score_projections({
        "score": "105/1",
        "runs": 105,
        "over": "10",
        "total_overs": 20,
        "current_phase": "first",
    })

    assert result["balls_remaining"] == 60
    assert [
        scenario["projected_score"] for scenario in result["scenarios"]
    ] == [185, 205, 225]


def test_score_projections_show_second_innings_target_outcome():
    result = main._score_projections({
        "score": "80/2",
        "runs": 80,
        "over": "7.3",
        "total_overs": 20,
        "current_phase": "chase",
        "target": 181,
    })

    assert result["balls_remaining"] == 75
    assert [
        (scenario["projected_score"], scenario["target_margin"])
        for scenario in result["scenarios"]
    ] == [(180, -1), (205, 24), (230, 49)]


def test_six_wickets_at_eleven_overs_is_high_pressure():
    pressure, label = _pressure_index({
        "runs": 67,
        "wickets": 6,
        "over": "11",
        "total_overs": 20,
        "run_rate": 6.09,
        "chase": None,
    })

    assert pressure >= 65
    assert label == "Batting side under pressure"


def test_current_over_bowler_is_excluded_from_next_over_recommendation():
    bowling_card = [{
        "bowler": "Current Bowler",
        "balls": 20,
        "runs": 25,
        "wickets": 1,
        "economy": 7.5,
        "overs_left": 0.7,
        "quota_used": False,
    }, {
        "bowler": "Alternative Bowler",
        "balls": 12,
        "runs": 14,
        "wickets": 1,
        "economy": 7.0,
        "overs_left": 2,
        "quota_used": False,
    }]

    analytics = main._live_analytics(
        bowling_card,
        {},
        "Current Bowler",
        50,
        0,
        False,
        current_over_active=True,
    )

    assert "Current Bowler" not in analytics["available_bowlers"]
    assert analytics["excluded_bowlers"]["Current Bowler"] == (
        "currently bowling; cannot also bowl the next over"
    )
    assert analytics["recommendation"] == "Alternative Bowler"


def test_current_batter_matchup_changes_recommendation_and_confidence():
    bowling_card = [{
        "bowler": name,
        "balls": 18,
        "runs": 21,
        "wickets": 1,
        "economy": 7.0,
        "overs_left": 1,
        "quota_used": False,
    } for name in ("Good Matchup", "Bad Matchup")]
    profiles = {
        name: {
            "balls": 600,
            "matches": 50,
            "wickets": 50,
            "economy": 7.5,
            "strike_rate": 20,
        }
        for name in ("Good Matchup", "Bad Matchup")
    }
    matchup_stats = {
        "Current Batter": {
            "Good Matchup": {"balls": 18, "runs": 10, "dismissals": 0},
            "Bad Matchup": {"balls": 18, "runs": 36, "dismissals": 0},
        },
    }

    analytics = main._live_analytics(
        bowling_card,
        profiles,
        None,
        50,
        0,
        False,
        matchup_stats=matchup_stats,
        current_batsmen=[{"name": "Current Batter"}],
    )

    assert analytics["matchup_scores"]["Good Matchup"] > (
        analytics["matchup_scores"]["Bad Matchup"]
    )
    assert analytics["combined_scores"]["Good Matchup"] > (
        analytics["combined_scores"]["Bad Matchup"]
    )
    assert analytics["recommendation"] == "Good Matchup"
    assert len(analytics["confidence_scores"]) == 2
