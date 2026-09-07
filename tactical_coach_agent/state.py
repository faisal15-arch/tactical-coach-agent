from typing import TypedDict


class MatchState(TypedDict, total=False):
    # User input / routing
    question: str
    intent: str
    live_intent: str

    # Persistent context between turns
    previous_context: dict

    # Match situation
    score: str
    overs: float
    requested_over: str
    current_over: str
    current_phase: str

    # Teams / players
    batting_team: str
    bowling_team: str
    current_bowler: str
    current_batter: str
    current_batsmen: list
    available_bowlers: list
    excluded_bowlers: dict
    unrated_bowlers: list
    provisional_bowlers: dict

    # Analytics
    pressure: int
    pressure_label: str
    required_run_rate: float
    current_run_rate: float

    momentum_score: int
    momentum_level: str
    momentum_label: str

    bowler_scores: dict
    form_scores: dict
    combined_scores: dict
    matchup_stats: dict

    # Decision simulation
    top_bowler: str
    top_strategy: str
    strategies: list
    choice_note: str

    # Gemini reasoning
    recommendation: str
    explanation: str
    confidence: int

    # Final answer
    answer: str