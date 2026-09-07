// Use the Vite proxy locally so auth cookies stay on the frontend origin.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: "include",
  });

  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    window.dispatchEvent(new Event("student-auth-expired"));
  }

  return response;
}

async function errorMessage(response, fallback) {
  try {
    const data = await response.json();
    return data.detail || data.message || fallback;
  } catch {
    return fallback;
  }
}

export async function loginStudent(studentId, password) {
  const res = await apiFetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ student_id: studentId, password }),
  });

  if (!res.ok) {
    throw new Error(await errorMessage(res, "Login failed"));
  }

  return res.json();
}

export async function registerStudent(fullName, studentId, password) {
  const res = await apiFetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      full_name: fullName,
      student_id: studentId,
      password,
    }),
  });

  if (!res.ok) {
    throw new Error(await errorMessage(res, "Account could not be created"));
  }

  return res.json();
}

export async function getCurrentStudent() {
  const res = await apiFetch("/api/auth/me");
  if (!res.ok) return null;
  const data = await res.json();
  return data.student;
}

export async function logoutStudent() {
  const res = await apiFetch("/api/auth/logout", { method: "POST" });
  if (!res.ok) {
    throw new Error(await errorMessage(res, "Could not sign out"));
  }
}

export async function sendMessage(question, sessionId, matchId = null) {
  const res = await apiFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      session_id: sessionId,
      match_id: matchId,
    }),
  });

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }

  return res.json();
}

export async function getMatch() {
  const res = await apiFetch("/api/match");

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }

  const data = await res.json();

  if (data.status === "error") {
    throw new Error(data.message);
  }

  return data;
}

export async function getLiveMatches() {
  const res = await apiFetch("/api/live-matches");

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }

  const data = await res.json();

  if (data.status === "error") {
    throw new Error(data.message);
  }

  return data;
}

export async function getLiveMatchDetails(matchId) {
  const res = await apiFetch(`/api/live-matches/${matchId}`);

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }

  const data = await res.json();

  if (data.status !== "ok") {
    throw new Error(data.message || "Could not load match details");
  }

  return data;
}

export async function getLiveSituation(
  matchId,
  over = null,
  phase = "chase",
  includeProfiles = false,
  includeTypeMatchups = false
) {
  const params = new URLSearchParams({ phase });
  if (over) params.set("over", over);
  if (includeProfiles) params.set("include_profiles", "true");
  if (includeTypeMatchups) params.set("include_type_matchups", "true");

  const res = await apiFetch(
    `/api/live-matches/${matchId}/situation?${params}`
  );

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }

  const data = await res.json();

  if (data.status !== "ok") {
    throw new Error(data.message || "Could not load the live score");
  }

  return data;
}

/**
 * The scoreboard at one ball: score, both batsmen, who is on strike,
 * what the chase needs, and every bowler's figures so far.
 *
 * This is a plain read, not a question for the agent, so it does not go
 * through /api/chat. No LLM call, no session state to keep in step.
 */
export async function getSituation(
  over,
  phase = "chase",
  includeTypeMatchups = false
) {
  const params = new URLSearchParams({ phase });
  if (over) params.set("over", over);
  if (includeTypeMatchups) params.set("include_type_matchups", "true");

  const res = await apiFetch(`/api/situation?${params}`);

  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }

  const data = await res.json();

  if (data.status === "error") {
    throw new Error(data.message);
  }

  return data;
}

/**
 * Cricsheet appends a disambiguation number to players who share a name,
 * e.g. "Mohammad Nawaz (3)". Useful in the data, noise on screen.
 */
export function displayName(name) {
  return String(name || "").replace(/\s*\(\d+\)\s*$/, "");
}
