import { useEffect, useState } from "react";
import ChatWindow from "./components/ChatWindow";
import MatchBoard from "./components/MatchBoard";
import CoachEntry from "./components/CoachEntry";
import TeamSheet from "./components/TeamSheet";
import MatchHeader from "./components/MatchHeader";
import ScoreStrip from "./components/ScoreStrip";
import BowlingCard from "./components/BowlingCard";
import BattingCard from "./components/BattingCard";
import MatchupsCard from "./components/MatchupsCard";
import BowlerProfiles from "./components/BowlerProfiles";
import LiveMatches from "./components/LiveMatches";
import LiveMatchDetails from "./components/LiveMatchDetails";
import {
  getCurrentStudent,
  getLiveMatchDetails,
  getLiveMatches,
  getMatch,
  logoutStudent,
} from "./api/client";
import "./App.css";

const NAVIGATION_KEY = "tactical-coach-navigation";
const MATCH_KEY = "tactical-coach-selected-match";
const BOARD_KEY = "tactical-coach-board-data";
const DETAILS_KEY = "tactical-coach-match-details";
const MATCH_STAGES = new Set([
  "details",
  "console",
  "batting",
  "bowling",
  "matchups",
  "analytics",
  "profiles",
]);

function readStoredJson(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key)) || null;
  } catch {
    return null;
  }
}

function readStoredStage() {
  const savedStage = sessionStorage.getItem(NAVIGATION_KEY);
  return savedStage || "entry";
}

function resumableStage() {
  const savedStage = readStoredStage();
  const savedMatch = readStoredJson(MATCH_KEY);
  if (MATCH_STAGES.has(savedStage) && !savedMatch) return "matches";
  return savedStage === "entry" ? "matches" : savedStage;
}

function App() {
  const [boardData, setBoardData] = useState(() => readStoredJson(BOARD_KEY));

  // Keep this small screen flow local; a router is unnecessary here.
  const [stage, setStage] = useState(readStoredStage);
  const [coachName, setCoachName] = useState("");
  const [student, setStudent] = useState(null);
  const [authStatus, setAuthStatus] = useState("checking");

  const [liveMatches, setLiveMatches] = useState([]);
  const [liveMatchesStatus, setLiveMatchesStatus] = useState("idle");
  const [liveMatchesError, setLiveMatchesError] = useState(null);
  const [selectedLiveMatch, setSelectedLiveMatch] = useState(
    () => readStoredJson(MATCH_KEY),
  );
  const [liveMatchDetails, setLiveMatchDetails] = useState(
    () => readStoredJson(DETAILS_KEY),
  );
  const [detailsStatus, setDetailsStatus] = useState("idle");
  const [detailsError, setDetailsError] = useState(null);

  const [match, setMatch] = useState(null);
  const [matchError, setMatchError] = useState(null);

  const loadLiveMatches = () => {
    setLiveMatchesStatus("loading");
    setLiveMatchesError(null);

    getLiveMatches()
      .then((data) => {
        setLiveMatches(data.matches || []);
        setLiveMatchesStatus("loaded");
      })
      .catch((err) => {
        setLiveMatchesError(err.message);
        setLiveMatchesStatus("error");
      });
  };

  const loadMatchDetails = (selectedMatch = selectedLiveMatch) => {
    if (!selectedMatch) return;

    setSelectedLiveMatch(selectedMatch);
    setStage("details");
    setDetailsStatus("loading");
    setDetailsError(null);

    getLiveMatchDetails(selectedMatch.match_id)
      .then((data) => {
        setLiveMatchDetails(data);
        setDetailsStatus("loaded");
      })
      .catch((err) => {
        setDetailsError(err.message);
        setDetailsStatus("error");
      });
  };

  useEffect(() => {
    let active = true;

    getCurrentStudent().then((savedStudent) => {
      if (!active) return;
      if (savedStudent) {
        setStudent(savedStudent);
        setCoachName(savedStudent.full_name);
        const restoredStage = resumableStage();
        setStage(restoredStage);
        setAuthStatus("authenticated");
        if (restoredStage === "matches") loadLiveMatches();
      } else {
        setStage("entry");
        setAuthStatus("anonymous");
      }
    });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const handleExpiredSession = () => {
      setStudent(null);
      setCoachName("");
      setStage("entry");
      setAuthStatus("anonymous");
    };

    window.addEventListener("student-auth-expired", handleExpiredSession);
    return () => {
      window.removeEventListener("student-auth-expired", handleExpiredSession);
    };
  }, []);

  const handleAuthenticated = (authenticatedStudent) => {
    setStudent(authenticatedStudent);
    setCoachName(authenticatedStudent.full_name);
    setAuthStatus("authenticated");
    const restoredStage = resumableStage();
    setStage(restoredStage);
    if (restoredStage === "matches") loadLiveMatches();
  };

  const handleHome = () => {
    setStage("matches");
    loadLiveMatches();
  };

  const handleLogout = async () => {
    try {
      await logoutStudent();
    } finally {
      setStudent(null);
      setCoachName("");
      setSelectedLiveMatch(null);
      setLiveMatchDetails(null);
      setBoardData(null);
      setStage("entry");
      setAuthStatus("anonymous");
      sessionStorage.removeItem(NAVIGATION_KEY);
      sessionStorage.removeItem(MATCH_KEY);
      sessionStorage.removeItem(BOARD_KEY);
      sessionStorage.removeItem(DETAILS_KEY);
    }
  };

  useEffect(() => {
    if (authStatus !== "authenticated") return;

    sessionStorage.setItem(NAVIGATION_KEY, stage);
    if (selectedLiveMatch) {
      sessionStorage.setItem(MATCH_KEY, JSON.stringify(selectedLiveMatch));
    } else {
      sessionStorage.removeItem(MATCH_KEY);
    }
    if (boardData) {
      sessionStorage.setItem(BOARD_KEY, JSON.stringify(boardData));
    } else {
      sessionStorage.removeItem(BOARD_KEY);
    }
    if (liveMatchDetails) {
      sessionStorage.setItem(DETAILS_KEY, JSON.stringify(liveMatchDetails));
    } else {
      sessionStorage.removeItem(DETAILS_KEY);
    }
  }, [authStatus, boardData, liveMatchDetails, selectedLiveMatch, stage]);

  useEffect(() => {
    if (stage !== "sheet" || match) return;

    getMatch()
      .then(setMatch)
      .catch((err) => setMatchError(err.message));
  }, [stage, match]);

  const totalOvers = match?.total_overs || 20;
  const liveMatchId = selectedLiveMatch?.match_id || null;
  const liveConsoleMatch = selectedLiveMatch
    ? {
        batting_first: selectedLiveMatch.team1?.name,
        chasing: selectedLiveMatch.team2?.name,
        venue: selectedLiveMatch.venue?.ground,
        city: selectedLiveMatch.venue?.city,
        dates: selectedLiveMatch.start_time_ms
          ? [new Date(selectedLiveMatch.start_time_ms).toLocaleDateString()]
          : [],
        match_number: selectedLiveMatch.description,
        event_name: selectedLiveMatch.series_name,
        total_overs: 20,
        state: selectedLiveMatch.state,
        status: selectedLiveMatch.status,
        team1: selectedLiveMatch.team1,
        team2: selectedLiveMatch.team2,
      }
    : null;
  const activeMatch = liveConsoleMatch || match;

  return (
    <div className="app">
      <header className="app__header">
        <span className="app__dot" />
        <h1>Tactical Coach</h1>
        <span className="app__subtitle">
          {stage === "console" || stage === "batting" || stage === "bowling" || stage === "matchups" || stage === "analytics" || stage === "profiles"
            ? `Match Intelligence${coachName ? ` · ${coachName}` : ""}`
            : "Match Intelligence"}
        </span>

        {(stage === "console" || stage === "batting" || stage === "bowling" || stage === "matchups" || stage === "analytics" || stage === "profiles") && (
          <nav className="app__nav">
            <button
              className="app__navbtn"
              type="button"
              onClick={handleHome}
              title="Back to live matches"
            >
              Home
            </button>
            <button
              className={"app__navbtn" + (stage === "console" ? " is-active" : "")}
              type="button"
              onClick={() => setStage("console")}
            >
              Console
            </button>
            <button
              className={"app__navbtn" + (stage === "batting" ? " is-active" : "")}
              onClick={() => setStage("batting")}
            >
              Batting scorecard
            </button>
            <button
              className={"app__navbtn" + (stage === "bowling" ? " is-active" : "")}
              onClick={() => setStage("bowling")}
            >
              Bowling figures
            </button>
            <button
              className={"app__navbtn" + (stage === "matchups" ? " is-active" : "")}
              onClick={() => setStage("matchups")}
            >
              Batter vs bowler
            </button>
            <button
              className={"app__navbtn" + (stage === "analytics" ? " is-active" : "")}
              onClick={() => setStage("analytics")}
            >
              Analytics
            </button>
            <button
              className={"app__navbtn" + (stage === "profiles" ? " is-active" : "")}
              onClick={() => setStage("profiles")}
            >
              Bowler profiles
            </button>
          </nav>
        )}

        {student && (
          <button className="app__logout" type="button" onClick={handleLogout}>
            Sign out
          </button>
        )}
      </header>

      {authStatus === "checking" && (
        <p className="app__loading">Checking student access…</p>
      )}

      {authStatus === "anonymous" && stage === "entry" && (
        <CoachEntry
          onAuthenticated={handleAuthenticated}
        />
      )}

      {authStatus === "authenticated" && stage === "matches" && (
        <LiveMatches
          coachName={coachName}
          matches={liveMatches}
          status={liveMatchesStatus}
          error={liveMatchesError}
          onRetry={loadLiveMatches}
          onViewDetails={loadMatchDetails}
        />
      )}

      {authStatus === "authenticated" && stage === "details" && (
        <LiveMatchDetails
          details={liveMatchDetails}
          status={detailsStatus}
          error={detailsError}
          onBack={() => setStage("matches")}
          onContinue={() => setStage("console")}
          onRetry={() => loadMatchDetails()}
        />
      )}

      {authStatus === "authenticated" && stage === "sheet" && (
        <>
          {matchError && (
            <p className="app__error">
              Could not load the match: {matchError}. Check that the backend is
              running on port 8001.
            </p>
          )}
          {!match && !matchError && (
            <p className="app__loading">Loading team sheets…</p>
          )}
          {match && (
            <TeamSheet
              match={match}
              coachName={coachName}
              onContinue={() => setStage("console")}
            />
          )}
        </>
      )}

      {authStatus === "authenticated" &&
        stage !== "entry" &&
        stage !== "matches" &&
        stage !== "details" &&
        stage !== "sheet" && (
        <section className={stage === "console" ? "app__console-page" : "app__screen--hidden"}>
          <MatchHeader match={activeMatch} />

          {/* The strip follows the clock the chat reports back, so it
              stays in step with the slider without a second source of
              truth for where the coach is standing. */}
          <ScoreStrip
            over={boardData?.current_over}
            phase={boardData?.current_phase || "chase"}
            matchId={liveMatchId}
            match={activeMatch}
          />

          <main className="app__layout app__layout--console">
            <ChatWindow
              onResult={setBoardData}
              onReset={() => setBoardData(null)}
              totalOvers={activeMatch?.total_overs || totalOvers}
              match={activeMatch}
              matchId={liveMatchId}
            />
          </main>
        </section>
      )}

      {stage === "bowling" && (
        <>
          <MatchHeader match={activeMatch} />
          <BowlingCard
            selectedOver={boardData?.current_over}
            selectedPhase={boardData?.current_phase}
            onBack={() => setStage("console")}
            matchId={liveMatchId}
          />
        </>
      )}

      {stage === "batting" && (
        <>
          <MatchHeader match={activeMatch} />
          <BattingCard
            selectedOver={boardData?.current_over}
            selectedPhase={boardData?.current_phase}
            onBack={() => setStage("console")}
            matchId={liveMatchId}
          />
        </>
      )}

      {stage === "matchups" && (
        <>
          <MatchHeader match={activeMatch} />
          <MatchupsCard
            selectedOver={boardData?.current_over}
            selectedPhase={boardData?.current_phase}
            onBack={() => setStage("console")}
            matchId={liveMatchId}
          />
        </>
      )}

      {stage === "analytics" && (
        <>
          <MatchHeader match={activeMatch} />
          <main className="app__analytics-page">
            <MatchBoard data={boardData} />
          </main>
        </>
      )}

      {stage === "profiles" && (
        <>
          <MatchHeader match={activeMatch} />
          <BowlerProfiles
            selectedOver={boardData?.current_over}
            selectedPhase={boardData?.current_phase}
            onBack={() => setStage("console")}
            matchId={liveMatchId}
            fallbackData={boardData}
          />
        </>
      )}
    </div>
  );
}

export default App;
