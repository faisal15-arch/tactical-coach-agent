# Tactical Coach

AI-powered cricket **bowling recommendation app** for live and completed matches. Tactical Coach recommends the best eligible bowler for the next over using current bowling form, batter matchups, career T20 profiles, pressure, momentum, and bowling-plan analysis.

The system is designed around the central coaching question:

> **Who should bowl next?**

It also supports related coaching questions such as:

- What is the score at a specific over?
- Which bowlers are eligible?
- What is the current pressure or momentum?
- Why was this bowler recommended?
- What line and length should the bowler use?

## Key Design Principle

The system separates **decision-making** from **language generation**:

- Python analytics calculate scores, eligibility, pressure, momentum, matchups, and recommendations.
- LangGraph classifies live questions and orchestrates the decision workflow.
- Gemini explains an already-calculated recommendation; it does not invent the underlying numbers.

This makes recommendations more reproducible, explainable, and easier to audit.

## Architecture

```text
React + Vite frontend
          |
          v
FastAPI main.py
          |
          +--> Authentication and session middleware
          +--> Live/completed match workflow
          |        |
          |        +--> LangGraph live intent classifier
          |        +--> Cricbuzz data collection
          |        +--> Matchup and player statistics
          |        +--> Deterministic live analytics
          |
          +--> PostgreSQL + Redis persistence
          |
          +--> Gemini explanation layer
```

## Current Chat Workflow

Chat requires a selected live or completed match and its `match_id`.

```text
POST /api/chat with match_id
          |
          v
main.py: chat()
          |
          v
_live_chat_response()
          |
          v
graph.py: classify_live_question()
          |
          v
get_live_situation()
          |
          v
pressure, momentum, matchups, profiles
          |
          v
_live_analytics()
          |
          v
ChatResponse
```

Historical chat without `match_id` is disabled. Legacy historical endpoints are retired; the active console is based on selected live or completed matches.

## Live Intent Classification

A lightweight LangGraph in `graph.py` classifies questions before the live workflow selects the appropriate response logic.

Supported intent categories include:

- `live_score`
- `go_live`
- `score_info`
- `bowling_recommendation`
- `bowling_plan`
- `bowling_ranking`
- `line_length`
- `pressure`
- `momentum`
- `matchup`
- `explanation`
- `general`

For example:

```text
"First innings go to 8 overs"
  -> phase = first, requested_over = 8

"Who should bowl next?"
  -> live_intent = bowling_recommendation
```

## Recommendation Analytics

The live recommendation score combines:

| Factor | Weight |
|---|---:|
| In-match bowling form | 30% |
| Current-innings batter matchup | 25% |
| Career T20 profile | 20% |
| Career batter-bowler matchup | 15% |
| Momentum fit | 6% |
| Pressure fit | 4% |

Eligibility rules are hard filters. A bowler who just bowled the previous over or has used the bowling quota is excluded before ranking.

## Data Collection

The project does not use an official paid Cricbuzz developer API.

It uses request-based collection from public web pages and website data endpoints:

- Cricbuzz public pages: live match list, scorecards, squads, and embedded Next.js JSON.
- Cricbuzz internal JSON endpoints used by the website: over history and over detail data.
- Cricmetric pages and JSON data endpoints: historical batter-bowler and bowling-type matchups.
- OpenWeatherMap API: optional weather and humidity data.

The data collectors normalize external responses into the project’s own structures before analytics use them. In-memory caches reduce repeated external requests.

## Repository Structure

```text
.
├── tactical_coach_agent/
│   ├── main.py                    # FastAPI endpoints and live workflow
│   ├── graph.py                   # LangGraph workflows and analytics routing
│   ├── state.py                   # Shared MatchState schema
│   ├── core/
│   │   ├── auth.py                # Password hashing and auth sessions
│   │   ├── conversation_memory.py # Durable chat context and messages
│   │   ├── db.py                  # SQLAlchemy/PostgreSQL setup
│   │   ├── models.py              # Database tables
│   │   └── player_stats_cache.py  # Cached player statistics
│   ├── llm/
│   │   └── gemini_reasoner.py     # Explanation and follow-up language layer
│   ├── tools/
│   │   ├── analytics/             # Pressure, momentum, form, par and profiles
│   │   ├── historical/             # Replay and historical utilities
│   │   ├── live_data/               # Cricbuzz, Cricmetric and weather collectors
│   │   └── decision_simulation.py   # Strategy ranking
│   ├── data/                       # Match and trained-model data
│   ├── tests/                      # Backend tests
│   ├── docker-compose.yml          # PostgreSQL and Redis
│   └── requirements.txt
└── tactical-coach-frontend/
    ├── src/
    │   ├── api/client.js           # Backend API client
    │   ├── components/             # Dashboard and chat components
    │   ├── App.jsx
    │   └── App.css
    └── package.json
```

## Database

PostgreSQL stores durable application data:

- `student_accounts`: student identity and password hashes.
- `student_auth_sessions`: hashed authentication tokens and expiry times.
- `conversation_sessions`: latest match/chat context.
- `conversation_messages`: user and assistant messages.
- `player_type_stats_cache`: cached external player-type statistics.
- `query_logs`: recommendation audit records defined by the schema.

Redis stores short-lived session context for fast follow-up requests. The application can fall back to in-memory session storage when Redis is unavailable.

## Authentication

The application uses an opaque server-side session token, not JWT:

1. Login creates a cryptographically random token.
2. The raw token is stored in an HttpOnly browser cookie.
3. Only the SHA-256 token hash is stored in PostgreSQL.
4. Protected requests hash the cookie token again and check the database session.
5. Expiry and logout revoke the session server-side.

## Local Setup

### Prerequisites

- Python 3.11+ recommended
- Node.js and npm
- Docker Desktop for PostgreSQL and Redis
- Optional: Gemini API key for generated explanations
- Optional: weather API key

### Start infrastructure

```powershell
cd tactical_coach_agent
docker compose up -d
```

### Install and run the backend

```powershell
cd tactical_coach_agent
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `tactical_coach_agent/.env`:

```env
DATABASE_URL=postgresql://coach:coach_dev_password@localhost:5432/tactical_coach
REDIS_URL=redis://localhost:6379/0
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
AUTH_SESSION_HOURS=12
AUTH_COOKIE_SECURE=false
GEMINI_API_KEY=your_gemini_key_here
LLM_MODEL=models/gemini-3.5-flash-lite
WEATHER_API_KEY=your_weather_key_here
```

Run the API:

```powershell
uvicorn main:app --reload --port 8001
```

Backend URLs:

- API: `http://127.0.0.1:8001`
- Health: `http://127.0.0.1:8001/health`

### Install and run the frontend

```powershell
cd tactical-coach-frontend
npm install
npm run dev
```

Frontend URL:

```text
http://localhost:5173
```

## Main API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Check backend and graph/Redis status |
| `POST` | `/api/auth/register` | Create a student account |
| `POST` | `/api/auth/login` | Authenticate and create a session cookie |
| `GET` | `/api/auth/me` | Return the current student |
| `POST` | `/api/auth/logout` | Revoke the current session |
| `GET` | `/api/live-matches` | List available live and completed matches |
| `GET` | `/api/live-matches/{match_id}` | Load selected match details and playing XIs |
| `GET` | `/api/live-matches/{match_id}/situation` | Load live or requested-over situation data |
| `POST` | `/api/chat` | Ask a question about the selected match |

`/api/chat` requires `match_id`; historical no-match-id chat is intentionally disabled.

## Testing and Checks

Backend tests:

```powershell
cd tactical_coach_agent
pytest
```

Frontend checks:

```powershell
cd tactical-coach-frontend
npm run lint
npm run build
```

## Deploying to Railway

Deploy this repository as two Railway services from the same GitHub repository:

1. Create a Railway PostgreSQL service and a Redis service.
2. Create a backend service with root directory `tactical_coach_agent`.
  Railway will use `tactical_coach_agent/Dockerfile`.
3. Add these backend variables:

  ```text
  DATABASE_URL=${{Postgres.DATABASE_URL}}
  REDIS_URL=${{Redis.REDIS_URL}}
  FRONTEND_ORIGINS=https://<frontend-domain>
  AUTH_COOKIE_SECURE=true
  AUTH_COOKIE_SAMESITE=lax
  GEMINI_API_KEY=<optional>
  WEATHER_API_KEY=<optional>
  ```

4. Generate a public domain for the backend and verify `/health`.
5. Create a frontend service from the same repository with root directory
  `tactical-coach-frontend`. Railway will use `tactical-coach-frontend/Dockerfile`.
6. Set the frontend service variable `BACKEND_URL` to the backend's public URL,
  including the scheme, for example `https://backend-production.up.railway.app`.
7. Generate a public domain for the frontend and replace `<frontend-domain>` in
  the backend's `FRONTEND_ORIGINS` variable with that exact origin.

The frontend proxy keeps API requests and authentication cookies on the frontend
origin. Redeploy the backend after setting the final frontend domain.

## Limitations

- Cricbuzz and Cricmetric website structures can change because some collected data comes from undocumented website payloads or internal endpoints.
- External collection depends on network availability, response formats, rate limits, and the providers’ terms of use.
- Gemini is optional; deterministic fallback explanations are used when no Gemini key is configured.
- Weather collection is available separately and should be explicitly connected to analytics if weather-aware recommendations are required.
- The line-and-length model uses historical annotated deliveries and should not be interpreted as calibrated tracking-system probabilities.
- Production deployments should use HTTPS, secure cookies, database migrations, rate limiting, and provider-compliant data access.

## License

No license has been specified yet. Add a license before publishing the repository for reuse.
