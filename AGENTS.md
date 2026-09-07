# Repository Guidelines

## Project Structure & Module Organization

This repository contains two applications. `tactical-coach-frontend/` is the React 19/Vite client; application code lives in `src/`, reusable UI is in `src/components/`, API access is in `src/api/client.js`, and static files belong in `public/` or `src/assets/`. `tactical_coach_agent/` is the FastAPI/LangGraph backend. Its entry point is `main.py`, graph orchestration is in `graph.py`, shared state is in `state.py`, persistence adapters are under `core/`, Gemini integration is under `llm/`, and deterministic cricket logic is grouped under `tools/`. Reference match data lives in `data/`; architecture documentation lives in `docs/`.

Do not commit generated or local artifacts such as `dist/`, `node_modules/`, `venv/`, `__pycache__/`, or `.env`.

## Build, Test, and Development Commands

- `cd tactical_coach_agent && docker compose up -d`: start PostgreSQL and Redis.
- `python -m venv venv && venv\Scripts\activate`: create and activate the Windows backend environment.
- `pip install -r requirements.txt`: install backend dependencies.
- `uvicorn main:app --reload`: run the API with reload from `tactical_coach_agent/`.
- `cd tactical-coach-frontend && npm install`: install locked frontend dependencies.
- `npm run dev`: start Vite's development server.
- `npm run lint`: run ESLint on JavaScript and JSX.
- `npm run build` / `npm run preview`: create and locally serve the production bundle.

## Coding Style & Naming Conventions

Use four spaces and PEP 8 conventions for Python: `snake_case` functions/modules and `PascalCase` classes. Keep analytics deterministic and isolate external-service access in `llm/`, `core/`, or `tools/live_data/`. Follow the existing frontend style: two-space indentation, single quotes, extensionless local imports, `PascalCase.jsx` components, and matching `PascalCase.css` files. Run ESLint before submitting frontend changes.

## Testing Guidelines

No project-owned automated test suite or coverage threshold is currently configured. New backend tests should use `pytest`, live under `tactical_coach_agent/tests/`, and be named `test_*.py`. Frontend tests should live beside components as `*.test.jsx` once a runner is added. Until then, lint and build the frontend, exercise `POST /api/chat`, and verify deterministic analytics with representative match states.

## Commit & Pull Request Guidelines

Use short, imperative commits with an optional scope, for example `feat(frontend): add bowling card state` or `fix(agent): preserve follow-up context`. Keep unrelated backend and UI changes separate. Pull requests should explain the user-visible behavior, list verification commands, link relevant issues, note configuration/schema changes, and include screenshots for UI work. Never include API keys; document required variables such as `GEMINI_API_KEY`, `DATABASE_URL`, and `REDIS_URL` using placeholders.
