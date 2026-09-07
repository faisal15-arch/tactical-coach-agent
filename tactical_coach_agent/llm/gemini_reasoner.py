"""
Gemini reasoning layer.

This module:
- explains the recommendation
- answers follow-up questions
- answers open questions

It does NOT run analytics.
It works only with the state/context supplied by graph.py.
"""

import os

from dotenv import load_dotenv


load_dotenv()


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODEL_NAME = os.getenv(
    "LLM_MODEL",
    "models/gemini-3.5-flash-lite",
)


_LEAK_MARKERS = (
    "wait,",
    "fix logic",
    "the prompt says",
    "let's write",
    "let me ",
    "as an ai",
    "i should",
    "hmm",
)


_MAX_EXPLANATION_CHARS = 700
_MAX_RECOMMENDATION_CHARS = 45


GLOSSARY = """
- Pressure Index, 0 to 100: how much pressure the batting side is under.
  High means greater batting pressure.
- Momentum, -100 to +100: recent scoring performance versus the benchmark.
- Par: expected average score at that over on the ground.
- Career rating: 0 to 100 based on historical bowling performance.
- In-match form: form shown in this innings so far.
- Final ranking score: 70% career rating + 30% in-match form.
- Conditions: neutral pitch unless stated otherwise.
- Provisional rating: a rating based on a thin career sample. It is less
  reliable and is pulled toward the pool average.
"""


CAPABILITIES = """
This system tracks:
- bowler career ratings
- in-match form
- combined ranking score
- pressure
- momentum
- score
- over
- target
- provisional ratings

This system does NOT track:
- individual batsman ratings
- field placements
- exact wicket mechanisms
- detailed ball-by-ball tactical events
- final match outcome unless explicitly supplied
"""


def _clean_explanation(text: str | None) -> str | None:
    if not text:
        return None

    text = text.strip()

    if not text:
        return None

    lowered = text.lower()

    if any(marker in lowered for marker in _LEAK_MARKERS):
        return None

    if len(text) > _MAX_EXPLANATION_CHARS:
        return None

    return text


def _plain_explanation(state: dict) -> str:
    strategies = state.get("strategies") or []

    if not strategies:
        return "No recommendation is available yet."

    top = strategies[0]

    bowler = top.get("bowler", "the top option")
    confidence = top.get("confidence", 0)

    context = state.get("previous_context") or {}

    caveat = (
        context.get("choice_note")
        or state.get("choice_note")
    )

    if caveat:
        return (
            f"{bowler} is the current pick at "
            f"{confidence}% confidence, with a decision caveat."
        )

    return (
        f"{bowler} is the strongest current option at "
        f"{confidence}% confidence."
    )


def _fallback_explanation(state: dict) -> dict:
    strategies = state.get("strategies") or []

    if not strategies:
        return {
            "recommendation": "No recommendation",
            "explanation": "No rated bowler is available.",
            "confidence": 0,
        }

    top = strategies[0]

    return {
        "recommendation": top.get("strategy"),
        "explanation": _plain_explanation(state),
        "confidence": top.get("confidence", 0),
    }


def _format_matchup_evidence(context: dict) -> str:
    matchups = context.get("matchup_stats") or {}
    batsmen = context.get("current_batsmen") or []
    lines = []

    for batter in batsmen:
        for bowler, figures in (matchups.get(batter) or {}).items():
            balls = figures.get("balls", 0)
            runs = figures.get("runs", 0)
            dismissals = figures.get("dismissals", 0)
            strike_rate = (runs / balls * 100) if balls else 0
            lines.append(
                f"{batter} vs {bowler}: {balls} balls, {runs} runs, "
                f"SR {strike_rate:.1f}, {dismissals} dismissals"
            )

    return "\n".join(lines[:12]) or "No matchup sample available"


def _get_model():
    if not GEMINI_API_KEY:
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)

        return genai.GenerativeModel(MODEL_NAME)

    except Exception as exc:
        print(f"[gemini_reasoner] model init failed: {exc}")
        return None


def generate_recommendation(state: dict) -> dict:
    """
    Generate explanation for the already-computed top strategy.
    """

    model = _get_model()

    if model is None:
        return _fallback_explanation(state)

    context = state.get("previous_context") or {}

    strategies = state.get("strategies") or []

    if not strategies:
        return _fallback_explanation(state)

    top_strategy = (
        context.get("top_strategy")
        or context.get("recommendation")
        or strategies[0].get("strategy")
    )

    active_bowler = context.get(
        "current_bowler",
        "Unknown",
    )

    excluded = list(
        (context.get("excluded_bowlers") or {}).keys()
    )

    choice_note = (
        context.get("choice_note")
        or state.get("choice_note")
    )

    provisional = (
        context.get("provisional_bowlers")
        or state.get("provisional_bowlers")
        or {}
    )

    matchup_evidence = _format_matchup_evidence(context)

    prompt = f"""
You are a cricket coach assistant. Give one concise tactical reason.

Match: 
- Score: {state.get("score") or context.get("score")}
- Over: {state.get("overs") or context.get("current_over")}
- Pressure: {state.get("pressure", context.get("pressure"))}
- Pressure label: {state.get("pressure_label", context.get("pressure_label"))}
- Momentum label: {state.get("momentum_label", context.get("momentum_label"))}

Matchup evidence:
{matchup_evidence}

Bowling:
- Current bowler: {active_bowler}
- Excluded: {", ".join(excluded) if excluded else "none"}
- Caveat: {choice_note or "none"}

Required recommendation:
{top_strategy}

Rules:
1. Do not change the supplied bowler.
2. Use matchup evidence first, then pressure or momentum if useful.
3. Output one sentence of at most 12 words; no caveats unless essential.
"""

    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.1,
                "max_output_tokens": 40,
            },
        )

        text = _clean_explanation(
            getattr(response, "text", None)
        )

        if text and len(text) > _MAX_RECOMMENDATION_CHARS:
            text = "Matchup and pressure support this choice."

        if not text:
            return _fallback_explanation(state)

        confidence = strategies[0].get(
            "confidence",
            0,
        )

        return {
            "recommendation": top_strategy,
            "explanation": text,
            "confidence": confidence,
        }

    except Exception as exc:
        print(f"[gemini_reasoner] recommendation failed: {exc}")
        return _fallback_explanation(state)


def generate_followup_answer(
    question: str,
    previous_context: dict,
) -> str:
    """
    Answer a follow-up using ONLY previous_context.
    """

    previous_context = previous_context or {}

    top_strategy = (
        previous_context.get("top_strategy")
        or previous_context.get("recommendation")
        or "No recommendation"
    )

    active_bowler = previous_context.get(
        "current_bowler"
    ) or "Unknown"

    choice_note = previous_context.get(
        "choice_note"
    )

    provisional = (
        previous_context.get("provisional_bowlers")
        or {}
    )

    plain = (
        f"Top recommendation: {top_strategy}. "
        f"Score: {previous_context.get('score', 'unknown')}, "
        f"Pressure: {previous_context.get('pressure', 'N/A')}."
    )

    model = _get_model()

    if model is None:
        return plain

    excluded = list(
        (previous_context.get("excluded_bowlers") or {}).keys()
    )

    prompt = f"""
You are a cricket tactical analyst answering a coach.

{GLOSSARY}

Match context:
- Over: {previous_context.get("requested_over")}
- Score: {previous_context.get("score")}
- Pressure: {previous_context.get("pressure")}
- Pressure label: {previous_context.get("pressure_label")}
- Momentum: {previous_context.get("momentum_score")}
- Momentum label: {previous_context.get("momentum_label")}
- Current bowler: {active_bowler}
- Excluded bowlers: {", ".join(excluded) if excluded else "none"}
- Provisional bowlers: {provisional or "none"}
- Recommendation: {top_strategy}
- Decision caveat: {choice_note or "none"}

Coach question:
"{question}"

Rules:
1. Answer the exact question.
2. If asked who should bowl next, use the supplied recommendation.
3. Never say the current bowler should bowl next.
4. If the question is about a provisional bowler, explain that the rating
   is based on a thin sample.
5. Do not invent statistics.
6. Keep it to 2-3 short sentences.
7. Output only the answer.
"""

    try:
        response = model.generate_content(prompt)

        text = _clean_explanation(
            getattr(response, "text", None)
        )

        return text or plain

    except Exception as exc:
        print(f"[gemini_reasoner] follow-up failed: {exc}")
        return plain


def answer_open_question(
    question: str,
    previous_context: dict,
) -> str:
    """
    Answer an unmatched/open question.
    """

    previous_context = previous_context or {}

    model = _get_model()

    if model is None:
        if previous_context:
            return (
                f"Score: {previous_context.get('score', 'N/A')}, "
                f"Pressure: {previous_context.get('pressure', 'N/A')}."
            )

        return (
            "No match-specific context has been computed yet."
        )

    has_context = bool(previous_context)

    top_strategy = (
        previous_context.get("top_strategy")
        or previous_context.get("recommendation")
        or "None"
    )

    active_bowler = (
        previous_context.get("current_bowler")
        or "None"
    )

    if has_context:
        context_text = f"""
- Over: {previous_context.get("requested_over")}
- Score: {previous_context.get("score")}
- Pressure: {previous_context.get("pressure")}
- Pressure label: {previous_context.get("pressure_label")}
- Momentum: {previous_context.get("momentum_score")}
- Momentum label: {previous_context.get("momentum_label")}
- Current bowler: {active_bowler}
- Recommendation: {top_strategy}
- Provisional bowlers:
  {previous_context.get("provisional_bowlers") or "none"}
- Decision caveat:
  {previous_context.get("choice_note") or "none"}
"""
    else:
        context_text = "No active match context."

    prompt = f"""
Answer this coach question concisely:

"{question}"

{GLOSSARY}

{CAPABILITIES}

Match context:
{context_text}

Rules:
1. If relevant match context exists, use the real supplied numbers.
2. Never invent match-specific numbers.
3. If asked who should bowl next, use:
   {top_strategy}
4. Never recommend {active_bowler} as the next bowler.
5. If asked about pressure, momentum, or score, give the supplied value.
6. If the system does not track something, say that plainly.
7. If there is no match context, answer general cricket knowledge and say
   that match-specific figures are not available yet.
8. Keep the answer to 2-4 short sentences.
9. Output only the answer.
"""

    try:
        response = model.generate_content(prompt)

        text = _clean_explanation(
            getattr(response, "text", None)
        )

        return text or "No useful answer is available."

    except Exception as exc:
        print(f"[gemini_reasoner] open question failed: {exc}")
        return "I could not generate the answer right now."


def list_available_models():
    """
    Optional diagnostic helper.
    """

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set.")
        return

    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)

    for model in genai.list_models():
        if "generateContent" in model.supported_generation_methods:
            print(model.name)


if __name__ == "__main__":
    print(
        f"[gemini_reasoner] model={MODEL_NAME} "
        f"key_set={bool(GEMINI_API_KEY)}"
    )