# Tactical Coach — AI Cricket Decision Support System

An AI-powered tactical assistant for cricket coaches, built as a LangGraph agent with deterministic analytics, a decision-simulation layer, and an LLM used strictly for *explaining* — not deciding.

Ask it "Who should bowl next?" mid-match and it walks through pressure, momentum, bowler form, and matchup data, ranks the actual candidate strategies, and only then asks Gemini to explain the reasoning in plain language. Ask a quick factual question like "Current score?" and it skips all of that — no wasted computation, no unnecessary LLM calls.

---

## Architecture

![Tactical Coach Architecture](./docs/architecture.svg)

The system has five layers:

| Layer | Tech | Responsibility |
|---|---|---|
| **Frontend** | React (Vite) | Chat interface + live Match Board dashboard |
| **API** | FastAPI | Session handling, request/response, audit logging |
| **Agent** | LangGraph | Intent routing — decides *which* tools actually need to run |
| **Analytics** | Pure Python | Pressure Index, Momentum, Bowler Effectiveness, Recent Form, Decision Simulation |
| **Reasoning** | Gemini Flash | Explains the top-ranked strategy in natural language |

Postgres logs every question and recommendation permanently (the audit trail). Redis holds short-term session memory, so a follow-up like "Why not Naseem?" is answered using the previous turn's context — no tools re-run.

## Design decision: deterministic analytics, LLM only for explanation

The single most important architectural choice in this project: **Gemini never invents the numbers.**

Pressure, Momentum, Bowler Effectiveness, and Recent Form are all computed with plain, testable Python functions — no LLM involved. Decision Simulation ranks strategies from those numbers, still with zero LLM calls. Gemini only receives the finished analysis and is asked to explain *why* the top strategy is best, referencing the real numbers it was given.

This matters for three reasons:

- **Reproducibility** — the same match state always produces the same ranking. A coach can trust the numbers didn't shift because of LLM randomness.
- **Auditability** — every recommendation logged to Postgres can be traced back to the exact formula that produced it.
- **Reduced hallucination risk** — Gemini is explaining a decision that's already made, not generating facts from scratch.

This is what separates the system from "a chatbot connected to an LLM" — it's a decision-support pipeline where the LLM's only job is the last mile: communication.

## Intent routing — why LangGraph, not a fixed script

A naive implementation calls every tool for every question. This system routes dynamically:

- `"Current score?"` → **Live Data Tools only** → done
- `"Who should bowl next?"` → Live Data → Analytics → Decision Simulation → Gemini
- `"Why not Naseem?"` (as a follow-up) → **Session memory only**, no tools re-run at all

Each path only computes what the question actually needs — cheaper, faster, and it keeps the reasoning traceable to a specific route through the graph.

## Analytics implemented

| Metric | What it measures |
|---|---|
| **Pressure Index** | Required run rate vs. current rate, wickets lost, overs remaining |
| **Momentum** | Recent scoring rate vs. overall rate — which side currently has the edge |
| **Bowler Effectiveness** | Base skill adjusted for today's pitch and weather conditions |
| **Recent Form** | Last-5-match wickets and economy, independent of today's conditions |
| **Decision Simulation** | Blends Effectiveness (70%) + Form (30%) into ranked strategies with confidence scores |

## Tech stack

- **Frontend:** React, Vite
- **Backend:** FastAPI, Python
- **Agent orchestration:** LangGraph
- **LLM:** Gemini Flash (via `google-generativeai`)
- **Database:** PostgreSQL (audit log)
- **Cache / session memory:** Redis
- **Infra:** Docker Compose (Postgres + Redis)

## Project structure

```
tactical_coach_agent/
├── main.py                  # FastAPI app — /api/chat endpoint, session handling
├── graph.py                  # LangGraph agent — intent routing, node wiring
├── state.py                   # Shared state schema
├── docker-compose.yml          # Postgres + Redis for local dev
│
├── tools/
│   ├── live_data/               # Score, pitch, weather (mock, real-API-ready)
│   ├── analytics/                # Pressure, Momentum, Bowler Effectiveness, Form
│   └── decision_simulation.py     # Strategy ranking with confidence scores
│
├── llm/
│   └── gemini_reasoner.py         # The only file that calls an LLM
│
├── core/
│   ├── db.py                       # Postgres connection (SQLAlchemy)
│   ├── models.py                    # QueryLog table — the audit trail
│   └── redis_client.py               # Session memory + caching
│
└── docs/
    └── architecture.svg              # This diagram
```

## Running it locally

**1. Start Postgres + Redis:**
```bash
docker compose up -d
```

**2. Backend:**
```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Create a `.env` file:
```
GEMINI_API_KEY=your_key_here
DATABASE_URL=postgresql://coach:coach_dev_password@localhost:5432/tactical_coach
REDIS_URL=redis://localhost:6379/0
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
AUTH_SESSION_HOURS=12
AUTH_COOKIE_SECURE=false
```

Accounts can be created from the entry page. An administrator can also create
one from the terminal:

```bash
python create_student.py --student-id STU001 --name "Student Name"
```

```bash
uvicorn main:app --reload --port 8001
```

**3. Frontend** (separate terminal):
```bash
cd tactical-coach-frontend
npm install
npm run dev
```

Visit the printed `localhost` URL, and try:
- `"Current score?"` — a quick factual answer, no analytics run
- `"Who should bowl next?"` — full pipeline: analytics, decision simulation, Gemini explanation
- `"Why not Naseem?"` (right after) — answered from session memory, nothing recomputed

## API

All `/api/*` routes except `/api/auth/*` require the HttpOnly student session
cookie returned by `POST /api/auth/login`. Set `AUTH_COOKIE_SECURE=true` when
the app is served over HTTPS in production.

**`POST /api/auth/login`**
```json
{ "student_id": "STU001", "password": "student-password" }
```

**`POST /api/auth/register`**
```json
{
  "full_name": "Student Name",
  "student_id": "STU001",
  "password": "minimum-10-characters"
}
```

`GET /api/auth/me` restores an existing session and `POST /api/auth/logout`
revokes it.

**`POST /api/chat`**
```json
{ "question": "Who should bowl next?", "session_id": null }
```
Returns the answer plus the full analytics breakdown (pressure, momentum, bowler scores, form scores, ranked strategies, confidence) and a `session_id` to carry into follow-up questions.

## Trained line-and-length model

The live console answers questions such as `What line and length should he
bowl?` with a compact statistical model trained from annotated historical T20
deliveries. The model ranks line-length options using smoothed expected runs,
boundary rate, and bowler-attributed wicket rate for the match phase, bowling
style, and current batters when their sample is available. If the model or a
verified bowling style is unavailable, the API returns the deterministic T20
rule-based fallback.

The generated runtime artifact is:

```text
data/line_length/line_length_model.json.gz
```

Retrain it without extracting the multi-gigabyte CSV:

```bash
python train_line_length_model.py "C:\path\to\archive.zip"
```

The supplied training run scanned 4,431,818 deliveries and retained 339,486
usable T20 deliveries. Its 2024+ temporal holdout contains 54,932 deliveries.
Because line and length are commentary annotations rather than tracking
coordinates and are missing non-randomly, runtime rates are explicitly labelled
as annotated-sample estimates rather than calibrated Hawk-Eye probabilities.

## Current limitations / next steps

- Live match data (score, pitch, weather) is currently mocked — `tools/live_data/live_score.py` is built to swap in a real cricket API (CricketData.org) with graceful fallback to mock data
- Bowler profiles are hardcoded for three named bowlers — real deployment would map a real squad to live stats
- Student self-registration is available; institutional student-ID verification is not yet included
- Not yet deployed — currently runs locally only

## Credits

Built as an exploration of AI-assisted decision support systems — using LangGraph for orchestration, deterministic analytics for trustworthy numbers, and an LLM strictly for the final communication step.
