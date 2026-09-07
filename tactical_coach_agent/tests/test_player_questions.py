from main import (
    _answer_player_t20_question,
    _asks_for_explanation,
    _is_previous_over_question,
    _normalize_chat_question,
    _referenced_batter_name,
    _requested_match_team,
    _split_compound_questions,
    _team_score_at_context,
)
from tools.live_data.player_stats import _clean_player_query, _resolve_player


def test_clean_player_query_keeps_name_and_removes_t20_phrase():
    assert (
        _clean_player_query("What is Colin Munro average in T20?")
        == "colin munro"
    )


def test_clean_player_query_removes_compact_strike_rate():
    assert (
        _clean_player_query("What is Colin Muno strikerate in T20?")
        == "colin muno"
    )


def test_clean_player_query_removes_possessive_and_stat_words():
    assert (
        _clean_player_query("What are Colin Munro's best bowling figures?")
        == "colin munro"
    )


def test_clean_player_query_removes_compound_stat_connector():
    assert (
        _clean_player_query("What is economy and wickets of Mitchell Santner?")
        == "mitchell santner"
    )


def test_player_resolution_accepts_small_typo_with_strong_name_match():
    class Response:
        def __init__(self, players):
            self.players = players

        def raise_for_status(self):
            return None

        def json(self):
            return {"player": self.players}

    class Client:
        def get(self, url, **_kwargs):
            if url.endswith("colin%20muno"):
                return Response([])
            return Response([
                {"id": "654", "name": "Colin Smith"},
                {"id": "8085", "name": "Colin Munro"},
            ])

    assert _resolve_player("Colin Muno", Client(), 1) == (
        "8085",
        "colin-munro",
    )


def test_unspecified_average_defaults_to_batting_average():
    profile = {
        "name": "Colin Munro",
        "batting": {"average": "31.25"},
        "bowling": {"avg": "45.00"},
    }

    answer = _answer_player_t20_question(
        "What is Colin Munro average in T20?",
        profile,
    )

    assert answer == "Colin Munro's T20 batting average is 31.25."


def test_that_batsman_resolves_to_last_batter_in_session():
    assert _referenced_batter_name(
        "What is the average of that batsman?",
        None,
        {"current_batter": "Current Striker"},
        {"last_batter": "Colin Munro"},
    ) == "Colin Munro"


def test_that_batsman_falls_back_to_current_striker():
    assert _referenced_batter_name(
        "What is his average?",
        None,
        {"current_batter": "Babar Azam"},
        {},
    ) == "Babar Azam"


def test_explicit_bowling_average_is_not_treated_as_batting_average():
    profile = {
        "name": "Colin Munro",
        "batting": {"average": "31.25"},
        "bowling": {"avg": "45.00"},
    }

    answer = _answer_player_t20_question(
        "What is Colin Munro bowling average in T20?",
        profile,
    )

    assert answer == "Colin Munro's T20 bowling average is 45.00."


def test_compact_strike_rate_question_returns_batting_strike_rate():
    profile = {
        "name": "Colin Munro",
        "batting": {"sr": "156.45"},
        "bowling": {"sr": "42.00"},
    }

    answer = _answer_player_t20_question(
        "What is Colin Muno strikerate in T20?",
        profile,
    )

    assert answer == "Colin Munro's T20 batting strike rate is 156.45."


def test_number_of_matches_question_works_for_any_profile():
    profile = {
        "name": "Example Player",
        "batting": {"matches": "87"},
        "bowling": {},
    }

    answer = _answer_player_t20_question(
        "What is the number of matches of Example Player?",
        profile,
    )

    assert answer == "Example Player has played 87 T20 matches."


def test_multiple_bowling_fields_return_only_requested_values():
    profile = {
        "name": "Mitchell Santner",
        "batting": {"matches": "233", "average": "16.40"},
        "bowling": {
            "wickets": "245",
            "avg": "22.10",
            "eco": "7.05",
            "sr": "18.80",
        },
    }

    answer = _answer_player_t20_question(
        "What is economy and wickets of Mitchell Santner?",
        profile,
    )

    assert answer == (
        "Mitchell Santner's T20 bowling stats: wickets 245 and economy 7.05."
    )
    assert "matches" not in answer
    assert "average" not in answer


def test_generic_bowling_fields_are_answered_from_requested_profile():
    profile = {
        "name": "Example Bowler",
        "batting": {},
        "bowling": {"bbi": "5/19", "5w": "2", "maidens": "8"},
    }

    assert _answer_player_t20_question(
        "What are Example Bowler's best bowling figures?",
        profile,
    ) == "Example Bowler's best T20 bowling in an innings is 5/19."
    assert _answer_player_t20_question(
        "How many five wicket hauls has Example Bowler?",
        profile,
    ) == "Example Bowler has 2 T20 five-wicket hauls."
    assert _answer_player_t20_question(
        "How many maidens has Example Bowler bowled?",
        profile,
    ) == "Example Bowler has bowled 8 T20 maidens."


def test_equivalent_crease_questions_share_one_canonical_intent():
    questions = (
        "Who is at crease?",
        "Who is at the crease?",
        "Who is batting right now?",
        "Which batsmen are on the crease?",
        "Current batters?",
    )

    assert {
        _normalize_chat_question(question) for question in questions
    } == {"who is batting"}


def test_batting_order_question_is_not_changed_to_crease_question():
    assert _normalize_chat_question("Who is batting 1st?") == "who is batting 1st?"
    assert _normalize_chat_question("Who is batting second?") == "who is batting second?"


def test_team_score_uses_selected_point_for_current_batting_team():
    data = {
        "batting_team": "TKR",
        "bowling_team": "JKM",
        "score": "111/1",
        "over": "11",
        "run_rate": 10.09,
        "match_team_info": [
            {"battingTeamShortName": "TKR", "bowlingTeamShortName": "JKM"},
            {"battingTeamShortName": "JKM", "bowlingTeamShortName": "TKR"},
        ],
        "innings_scores": [
            {"innings_id": 1, "batting_team": "Trinbago Knight Riders", "score": "180/4", "over": "20", "run_rate": 9.0},
            {"innings_id": 2, "batting_team": "Jamaica Kingsmen", "score": "150/7", "over": "20", "run_rate": 7.5},
        ],
    }

    assert _requested_match_team("What is TKR's score?", data, {}) == "TKR"
    assert _requested_match_team(
        "What is its run rate?", data, {"last_team": "JKM"}
    ) == "JKM"
    assert _team_score_at_context(data, "TKR")["score"] == "111/1"
    assert _team_score_at_context(data, "JKM")["score"] == "150/7"


def test_zero_dismissal_phrases_are_normalized_to_ducks():
    questions = (
        "How many duck Babar Azam have in T20s?",
        "How many times was Babar Azam out on 0.s?",
        "How often has Babar Azam been dismissed for zero?",
        "How many times was Babar Azam dismissed without scoring?",
    )

    for question in questions:
        normalized = _normalize_chat_question(question)
        assert "ducks" in normalized
        assert _clean_player_query(normalized) == "babar azam"


def test_fast_question_and_explanation_intents_are_distinct():
    assert not _asks_for_explanation("What is momentum right now?")
    assert not _asks_for_explanation("What is the pressure index right now?")
    assert not _asks_for_explanation("Who should bowl next?")
    assert _asks_for_explanation("What is meant by pressure index?")
    assert _asks_for_explanation("How is momentum calculated?")
    assert _asks_for_explanation("Why should he bowl next?")
    assert _asks_for_explanation("What is meant by confidence?")


def test_player_query_ignores_ui_appended_over_context():
    assert _clean_player_query(
        "What is Colin Munro average in T20? at over 14.4"
    ) == "colin munro"


def test_previous_over_wording_variants_share_an_intent():
    assert _is_previous_over_question("Who bowled the previous over?")
    assert _is_previous_over_question("Who bowls the last over?")
    assert _is_previous_over_question("Previous over bowler?")


def test_compound_questions_are_split_without_losing_either_intent():
    assert _split_compound_questions(
        "What is Colin Munro average in T20?who bowled the previous over?"
    ) == [
        "What is Colin Munro average in T20",
        "who bowled the previous over",
    ]


def test_ui_appended_over_suffix_stays_with_the_original_question():
    assert _split_compound_questions(
        "What is Colin Munro average in T20? at over 14.4"
    ) == ["What is Colin Munro average in T20 at over 14.4"]
