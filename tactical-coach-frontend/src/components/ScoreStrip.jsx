import { useEffect, useState } from "react";
import { getLiveSituation, getSituation, displayName } from "../api/client";
import "./ScoreStrip.css";

/**
 * The scoreboard above the console, at whatever over the clock is on.
 *
 * Built as three labelled blocks rather than one row of equal-weight
 * text. Everything used to render at the same size and colour, so the
 * eye landed on the score and then had nowhere to go.
 */
function displayOvers(value) {
  const [completed, balls = "0"] = String(value ?? "0").split(".");
  return Number(balls) === 6 ? String(Number(completed) + 1) : String(value ?? "0");
}

function finalInnings(team) {
  const innings = team?.innings?.at(-1);
  if (!innings) return null;
  return {
    name: team.name,
    score: `${innings.runs}/${innings.wickets}`,
    overs: displayOvers(innings.overs),
  };
}

export default function ScoreStrip({ over, phase = "chase", matchId = null, match = null }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const loadScore = () => {
      const request = matchId
        ? getLiveSituation(matchId, over, phase)
        : getSituation(over, phase);

      request.then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    };

    loadScore();
    const timer = matchId && !over ? window.setInterval(loadScore, 30000) : null;

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [over, phase, matchId]);

  if (error) {
    return <div className="strip strip--error">Scoreboard unavailable: {error}</div>;
  }

  if (!data) {
    return <div className="strip strip--loading">Loading scoreboard…</div>;
  }

  const chase = data.chase;
  const par = data.par;
  const { striker, non_striker, on_strike_next_ball } = data.batsmen || {};
  const battingCardFigures = new Map(
    (data.batting_card || []).map((batter) => [batter.batter, batter])
  );
  const currentBatterFigures = new Map(
    (data.current_batsmen || []).map((batter) => [batter.name, batter])
  );
  const matchComplete = ["complete", "completed", "result", "abandon", "abandoned"]
    .includes(String(match?.state || data.state || "").toLowerCase());
  const finalScores = [finalInnings(match?.team1), finalInnings(match?.team2)]
    .filter(Boolean);
  const ballsInCurrentOver = Number(String(data.over || "").split(".")[1] || 0);
  const overInProgress = ballsInCurrentOver > 0;
  const currentBowlerFigures = (data.bowling_card || []).find(
    (bowler) => bowler.bowler === data.current_bowler
  );

  if (matchComplete && !over && finalScores.length) {
    return (
      <div className="strip strip--final">
        {finalScores.map((innings) => (
          <div key={innings.name} className="strip__block strip__block--final-score">
            <span className="strip__label">{innings.name}</span>
            <div className="strip__value">
              <span className="strip__final-score">{innings.score}</span>
              <span className="strip__sub">{innings.overs} ov</span>
            </div>
          </div>
        ))}
        <div className="strip__block strip__block--result">
          <span className="strip__label">Final result</span>
          <span className="strip__done">{match?.status || data.match_status}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="strip">
        <div className="strip__block">
          <span className="strip__label">{data.batting_team}</span>
          <div className="strip__value">
            <span className="strip__runs">{data.runs}</span>
            <span className="strip__sep">/</span>
            <span className="strip__wkts">{data.wickets}</span>
            <span className="strip__sub">
              {data.over} ov · RR {data.run_rate.toFixed(2)}
            </span>
          </div>
        </div>

        {chase && !chase.achieved && (
          <div className="strip__block strip__block--key">
            <span className="strip__label">To win</span>
            <div className="strip__value">
              <span className="strip__runs strip__runs--key">{chase.runs_needed}</span>
              <span className="strip__sub">
                off {chase.balls_remaining} balls · req {chase.required_run_rate.toFixed(2)}
              </span>
            </div>
          </div>
        )}

        {chase && chase.achieved && (
          <div className="strip__block">
            <span className="strip__label">Result</span>
            <div className="strip__value">
              <span className="strip__done">Target of {chase.target} reached</span>
            </div>
          </div>
        )}

        {par && (
          <div className="strip__block strip__block--key">
            <span className="strip__label">
              Against par
              {par.venue_specific && (
                <span className="strip__data-badge">Venue data</span>
              )}
            </span>
            <div className="strip__value">
              <span
                className={
                  "strip__par-status" +
                  (par.difference >= 0 ? " is-ahead" : " is-behind")
                }
              >
                {Math.abs(par.difference)} runs {par.difference >= 0 ? "above" : "behind"}
              </span>
              <span className="strip__sub">
                benchmark: {par.runs} at this over · {par.source}
              </span>
              {par.method && (
                <span className="strip__method">{par.method}</span>
              )}
            </div>
          </div>
        )}

        {!par && matchId && data.format === "T20" && (
          <div className="strip__block strip__block--unavailable">
            <span className="strip__label">Against par</span>
            <div className="strip__value">
              <span className="strip__par-unavailable">
                Venue benchmark unavailable
              </span>
              <span className="strip__sub">
                No venue-specific T20I sample · no estimate shown
              </span>
            </div>
          </div>
        )}

        <div className="strip__block strip__block--crease">
          <span className="strip__label">At the crease</span>
          <div className="strip__batsmen">
            {[striker, non_striker].filter(Boolean).map((name) => {
              const onStrike = name === on_strike_next_ball;
              const currentFigures = currentBatterFigures.get(name);
              const cardFigures = battingCardFigures.get(name);
              const runs = currentFigures?.runs ?? cardFigures?.runs;
              const balls = currentFigures?.balls ?? cardFigures?.balls;
              const hasFigures = runs !== undefined && runs !== null
                && balls !== undefined && balls !== null;
              return (
                <span
                  key={name}
                  className={"strip__batsman" + (onStrike ? " is-strike" : "")}
                >
                  <span className="strip__batsman-name">
                    {displayName(name)}
                    {onStrike && <span className="strip__star">*</span>}
                  </span>
                  {hasFigures && (
                    <strong className="strip__batsman-figures">
                      {runs}
                      <span> ({balls})</span>
                    </strong>
                  )}
                </span>
              );
            })}
          </div>
          {data.current_bowler && (
            <span className="strip__sub">
              {overInProgress ? "Current over" : "Last over"}: {displayName(data.current_bowler)}
              {currentBowlerFigures && (
                <span className="strip__bowler-figures">
                  {` · ${currentBowlerFigures.overs} ov · ${currentBowlerFigures.runs} runs · ${currentBowlerFigures.wickets} wkts`}
                </span>
              )}
            </span>
          )}
        </div>
      </div>
  );
}
