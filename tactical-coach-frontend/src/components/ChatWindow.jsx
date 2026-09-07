import { useState, useRef, useEffect } from "react";
import { sendMessage } from "../api/client";
import "./ChatWindow.css";

const WELCOME_MESSAGE = {
  role: "assistant",
  text:
    "The match is replayed ball by ball. Pick an innings, enter an over number " +
    "to stand at a point in it, then ask who should have bowled next.",
};

const SUGGESTIONS = [
  "Who should bowl next?",
  "Why?",
  "Show ranking",
  "What's the score?",
];

export default function ChatWindow({
  onResult,
  onReset,
  totalOvers = 20,
  match,
  matchId = null,
}) {
  const sessionStorageKey = `tactical-coach-session:${matchId || "historical"}`;
  const initiallyFinished = ["complete", "completed", "result", "abandon", "abandoned"]
    .includes(String(match?.state || "").toLowerCase());
  const initialMessage = matchId && initiallyFinished
    ? {
        role: "assistant",
        text: `This match has been completed. Final result: ${match?.status || "Result unavailable"}. You can review its scorecards and analytics.`,
      }
    : matchId
    ? {
        role: "assistant",
        text: "This console is connected to the selected live match. Ask about scores, batting or bowling figures, player T20 stats, venue par, pressure, momentum, form, matchups, eligibility, rankings, or who should bowl next and why.",
      }
    : WELCOME_MESSAGE;
  const [messages, setMessages] = useState([initialMessage]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() =>
    window.localStorage.getItem(sessionStorageKey)
  );
  const [matchFinished, setMatchFinished] = useState(initiallyFinished);

  const [currentOver, setCurrentOver] = useState(null);
  const [phase, setPhase] = useState("chase");
  const [targetOverInput, setTargetOverInput] = useState("10");

  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  async function ask(question) {
    if (!question || loading) return;

    setMessages((prev) => prev.concat([{ role: "user", text: question }]));
    setInput("");
    setLoading(true);

    const hasOver = /\bover\s+(?:to\s+)?\d+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\s*overs?\b|\b\d+\.\d+\b/i.test(
      question
    );
    const asksForLatestScore = /\b(?:live|latest)\s+(?:match\s+)?score\b|\bscore\s+(?:right\s+)?now\b/i.test(
      question
    );
    const isRecommendationWhy = /^\s*(?:why(?:\s+(?:him|this bowler|that bowler))?|reason|what is the reason)\s*[?!.]*\s*$/i.test(
      question
    );
    const asksToGoLive = /\b(?:go|back|return|take me)\s+(?:to\s+)?live\b|\bgo to current situation\b/i.test(
      question
    );
    const questionAtCurrentOver =
      currentOver &&
      !hasOver &&
      !asksForLatestScore &&
      !isRecommendationWhy &&
      !asksToGoLive
        ? `${question.replace(/[?!.]+\s*$/, "")} at over ${currentOver}`
        : question;

    try {
      const result = await sendMessage(
        questionAtCurrentOver,
        sessionId,
        matchId
      );
      setSessionId(result.session_id);
      window.localStorage.setItem(sessionStorageKey, result.session_id);

      if (result.match_finished) {
        setMatchFinished(true);
        setCurrentOver(null);
        if (onResult) {
          onResult({ ...result, current_over: null });
        }
      } else if (result.live_mode) {
        setMatchFinished(false);
        setCurrentOver(null);
        if (onResult) {
          onResult({ ...result, current_over: null });
        }
      } else if (result.current_over) {
        setMatchFinished(false);
        const requestedOver = String(result.requested_over || "");
        const displayOver = /^\d+$/.test(requestedOver)
          ? requestedOver
          : String(result.current_over);

        setCurrentOver(displayOver);
        setTargetOverInput(displayOver);

        if (onResult) {
          onResult({ ...result, current_over: displayOver });
        }
      } else if (onResult) {
        onResult(result);
      }
      if (result.current_phase) setPhase(result.current_phase);

      setMessages((prev) => prev.concat([{ role: "assistant", text: result.answer }]));
    } catch (err) {
      const errorText =
        err instanceof TypeError
          ? "Can't reach the backend. Check that uvicorn is running on port 8001."
          : "Something went wrong on the server side. Check the backend terminal for details.";
      setMessages((prev) => prev.concat([{ role: "assistant", text: errorText }]));
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  // Helper to validate valid cricket over format (e.g., 6, 6.3 - max 5 balls after point)
  function formatAndValidateOver(val) {
    if (!val) return null;
    const parts = val.toString().split(".");
    const overNum = parseInt(parts[0], 10);
    
    if (isNaN(overNum) || overNum < 1 || overNum > totalOvers) return null;

    if (parts.length > 1) {
      let balls = parseInt(parts[1], 10);
      if (isNaN(balls) || balls < 0) balls = 0;
      if (balls > 6) balls = 6;
      return `${overNum}.${balls}`;
    }

    return `${overNum}`;
  }

  function goToOver(overVal, forPhase = phase) {
    const validOver = formatAndValidateOver(overVal);
    if (!validOver) return;

    const prefix = forPhase === "first" ? "First innings, " : "Second innings, ";
    ask(`${prefix}go to over ${validOver}`);
  }

  function goLive() {
    if (!matchId || loading) return;
    ask("Go to current situation");
  }

  function handleInputChange(e) {
    const val = e.target.value;
    setTargetOverInput(val);

    // Wait for the complete value so typing "3.4" cannot submit "3" first.
  }

  function handleOverKeyDown(e) {
    if (e.key !== "Enter") return;

    e.preventDefault();
    goToOver(targetOverInput);
  }

  function switchPhase(next) {
    if (next === phase || loading) return;
    setPhase(next);
    goToOver(targetOverInput, next);
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask(input.trim());
    }
  }

  function handleReset() {
    setMessages([initialMessage]);
    setInput("");
    setSessionId(null);
    window.localStorage.removeItem(sessionStorageKey);
    setMatchFinished(initiallyFinished);
    setCurrentOver(null);
    setPhase("chase");
    setTargetOverInput("10");
    if (onReset) onReset();
    inputRef.current?.focus();
  }

  const battingFirst = match?.batting_first;
  const chasing = match?.chasing;

  return (
    <div className="chat-window">
      <div className="chat-window__header">
        <div>
          <span className="chat-window__eyebrow">TACTICAL COACH</span>
          <span className="chat-window__title">Match Console</span>
        </div>
        <button
          className="chat-window__reset"
          onClick={handleReset}
          disabled={loading}
          title="Start a new chat"
        >
          New Chat
        </button>
      </div>

      <div className="over-clock">
        <div className="phase-toggle">
          <button
            className={"phase-toggle__btn" + (phase === "first" ? " is-active" : "")}
            onClick={() => switchPhase("first")}
            disabled={loading}
          >
            1st innings{!matchId && battingFirst ? ` · ${battingFirst}` : ""}
          </button>
          <button
            className={"phase-toggle__btn" + (phase === "chase" ? " is-active" : "")}
            onClick={() => switchPhase("chase")}
            disabled={loading}
          >
            {matchId ? "2nd innings" : "Chase"}
            {!matchId && chasing ? ` · ${chasing}` : ""}
          </button>
        </div>

        <div className="over-clock__input-group">
          <label htmlFor="over-number-input" className="over-clock__label">
            GO TO OVER:
          </label>
          <input
            id="over-number-input"
            className="over-clock__input"
            type="text"
            value={targetOverInput}
            disabled={loading}
            onChange={handleInputChange}
            onKeyDown={handleOverKeyDown}
            placeholder="e.g. 6.3"
          />
          {matchId && (
            <button
              className={
                "over-clock__live-btn" + (!currentOver && !matchFinished ? " is-active" : "")
              }
              type="button"
              onClick={goLive}
              disabled={loading || matchFinished}
              title={matchFinished
                ? "This match has been completed"
                : "Return the console to the latest live ball"}
            >
              <span className="over-clock__live-dot" aria-hidden="true" />
              {matchFinished ? "Match complete" : "Go live"}
            </button>
          )}
        </div>

        <div className="over-clock__standing">
          <span className="over-clock__label">Standing at: </span>
          <strong className="over-clock__value">
            {matchFinished
              ? "Final result"
              : currentOver
              ? `${currentOver} ov`
              : matchId
              ? "Live now"
              : `over ${targetOverInput}`}
          </strong>
        </div>
      </div>

      <div className="chat-window__messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={"chat-bubble chat-bubble--" + m.role}>
            {m.text}
          </div>
        ))}
        {loading && (
          <div className="chat-bubble chat-bubble--assistant chat-bubble--loading">
            Thinking<span className="chat-window__dots"></span>
          </div>
        )}
      </div>

      <div className="chip-row">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chip" onClick={() => ask(s)} disabled={loading}>
            {s}
          </button>
        ))}
      </div>

      <div className="chat-window__input-row">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Who should bowl next?"
          rows={1}
          disabled={loading}
          autoFocus
        />
        <button onClick={() => ask(input.trim())} disabled={loading || !input.trim()}>
          Ask
        </button>
      </div>
    </div>
  );
}
