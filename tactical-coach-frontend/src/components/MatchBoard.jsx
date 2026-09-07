import StrategyCard from "./StrategyCard";
import "./MatchBoard.css";

// Both meters read from the bowling coach's chair. High pressure on the
// batting side is good news for him; low pressure means they are
// comfortable.
function pressureColor(pressure) {
  if (pressure === null || pressure === undefined) return "var(--text-muted)";
  if (pressure >= 70) return "var(--accent-pitch)";
  if (pressure >= 40) return "var(--accent-flood)";
  return "var(--accent-alert)";
}

function momentumColor(momentumLevel) {
  if (!momentumLevel) return "var(--text-muted)";
  if (momentumLevel === "Very high" || momentumLevel === "High") {
    return "var(--accent-alert)";
  }
  if (momentumLevel === "Very low" || momentumLevel === "Low") {
    return "var(--accent-pitch)";
  }
  return "var(--accent-flood)";
}

function getMomentumLevel(data) {
  if (data?.momentum_level) return data.momentum_level;

  const score = Number(data?.momentum_score || 0);

  if (score >= 60) return "Very high";
  if (score > 20) return "High";
  if (score <= -60) return "Very low";
  if (score < -20) return "Low";
  return "Neutral";
}

export default function MatchBoard({ data }) {
  const hasData = data && data.pressure !== undefined && data.pressure !== null;

  const excluded = data?.excluded_bowlers || null;
  const hasExcluded = excluded && Object.keys(excluded).length > 0;

  const unrated = data?.unrated_bowlers || [];
  const hasUnrated = unrated.length > 0;

  const provisional = data?.provisional_bowlers || {};

  const isStale =
    data?.ranking_phase &&
    data?.current_phase &&
    data.ranking_phase !== data.current_phase;

  const phaseLabel =
    data?.current_phase === "first"
      ? "1st innings"
      : data?.current_phase === "chase"
      ? "Chase"
      : null;

  // Order by the same thing the decision used. Sorting by raw score put
  // Umar Gul (71) above Anwar Ali (67) while the chat recommended Anwar
  // Ali, so the panel and the answer contradicted each other on screen.
  const scores = data?.combined_scores || {};
  const confidenceScores = data?.confidence_scores || {};
  const strategies = data?.strategies || [];
  const bowlingPlan = data?.bowling_plan || [];

  const ranked = Object.keys(confidenceScores).length
    ? Object.entries(confidenceScores).sort((a, b) => b[1] - a[1])
    : Object.entries(scores).sort((a, b) => b[1] - a[1]);

  const orderedByConfidence = Object.keys(confidenceScores).length > 0;
  const momentumLevel = getMomentumLevel(data);
  const momentumScore = data?.momentum_score || 0;
  const projections = data?.score_projections;
  const projectionScenarios = projections?.scenarios || [];

  return (
    <aside className="match-board">
      <div className="match-board__header">
        <span className="match-board__eyebrow">MATCH BOARD</span>
        {data?.current_over && (
          <span className="match-board__clock">
            {phaseLabel ? `${phaseLabel} · ` : ""}
            {data.current_over} overs{data.score ? ` · ${data.score}` : ""}
          </span>
        )}
      </div>

      {data?.batting_team && (
        <p className="match-board__teams">
          {data.batting_team} batting, {data.bowling_team} bowling
        </p>
      )}

      {!hasData && (
        <p className="match-board__empty">
          Move the over slider or ask a tactical question to populate the board.
        </p>
      )}

      {hasData && (
        <>
          {data.par_note && (
            <p className="match-board__note match-board__note--par">{data.par_note}</p>
          )}

          {projectionScenarios.length > 0 && (
            <div className="match-board__section match-board__projections">
              <span className="match-board__section-title">PROJECTED SCORE</span>
              <span className="match-board__section-hint">
                From {projections.from_score} at {projections.from_over} overs · remaining balls only
              </span>
              <div className="match-board__projection-grid">
                {projectionScenarios.map((scenario) => (
                  <div
                    className="match-board__projection-card"
                    key={scenario.run_rate}
                  >
                    <span className="match-board__projection-rate">
                      RR {scenario.run_rate}
                    </span>
                    <strong>{scenario.projected_score}</strong>
                    {scenario.target && (
                      <span
                        className={
                          "match-board__projection-target" +
                          (scenario.reaches_target ? " is-reached" : " is-short")
                        }
                      >
                        {scenario.reaches_target
                          ? `${scenario.target_margin} above target`
                          : `${Math.abs(scenario.target_margin)} short`}
                      </span>
                    )}
                  </div>
                ))}
              </div>
              <span className="match-board__section-hint match-board__projection-note">
                Wickets are not modelled
              </span>
            </div>
          )}

          <div className="match-board__pressure">
            <span className="match-board__pressure-label">BATTING SIDE PRESSURE INDEX</span>
            <span
              className="match-board__pressure-value"
              style={{ color: pressureColor(data.pressure) }}
            >
              {data.pressure}
            </span>
          </div>

          {data.pressure_label && (
            <p className="match-board__sublabel">{data.pressure_label}</p>
          )}

          <div className="meter">
            <div
              className="meter__fill"
              style={{
                width: `${Math.min(Math.max(data.pressure, 0), 100)}%`,
                background: pressureColor(data.pressure),
              }}
            />
          </div>

          {data.momentum_score !== undefined && data.momentum_score !== null && (
            <div className="match-board__momentum">
              <span className="match-board__momentum-label">BATTING SIDE MOMENTUM</span>
              <div className="match-board__momentum-row">
                <span
                  className="match-board__momentum-value"
                  style={{ color: momentumColor(momentumLevel) }}
                >
                  {momentumLevel}
                </span>
                <span className="match-board__momentum-text">{data.momentum_label}</span>
              </div>

              <div className="meter">
                <div
                  className="meter__fill"
                  style={{
                    width: `${Math.min(Math.abs(momentumScore), 100)}%`,
                    background: momentumColor(momentumLevel),
                  }}
                />
              </div>
            </div>
          )}

          {ranked.length > 0 && (
            <div className="match-board__section">
              <span className="match-board__section-title">FINAL RANKING</span>
              <span className="match-board__section-hint">
                Eligible next-over bowlers only · {" "}
                {data.requested_over ? `At ${data.requested_over} overs` : "Latest"}
                {data.ranking_team ? ` · ${data.ranking_team} bowling` : ""}
                {orderedByConfidence ? " · ordered by confidence, not raw score" : ""}
              </span>

              {isStale && (
                <p className="match-board__note match-board__note--stale">
                  From the other innings. Ask who should bowl next to refresh.
                </p>
              )}

              {ranked.map(([bowler, score]) => (
                <div key={bowler} className="match-board__bowler-row">
                  <span>
                    {bowler}
                    {bowler in provisional && (
                      <span className="tag tag--provisional">
                        provisional · {provisional[bowler].overs} ov
                      </span>
                    )}
                  </span>
                  <span className="match-board__bowler-score">
                    {score}{orderedByConfidence ? "%" : ""}
                  </span>
                </div>
              ))}

              {data.choice_note && (
                <p className="match-board__note">{data.choice_note}</p>
              )}
            </div>
          )}

          {data.effectiveness_scores && Object.keys(data.effectiveness_scores).length > 0 && (
            <div className="match-board__section">
              <span className="match-board__section-title">BOWLING EFFECTIVENESS</span>
              <span className="match-board__section-hint">
                T20 profile + current spell, before pressure and momentum adjustment
              </span>
              {Object.entries(data.effectiveness_scores)
                .sort((a, b) => b[1] - a[1])
                .map(([bowler, score]) => (
                  <div key={bowler} className="match-board__bowler-row">
                    <span>{bowler}</span>
                    <span className="match-board__bowler-score">{score}</span>
                  </div>
                ))}
            </div>
          )}

          {data.form_scores && Object.keys(data.form_scores).length > 0 && (
            <div className="match-board__section">
              <span className="match-board__section-title">IN-MATCH FORM</span>
              <span className="match-board__section-hint">
                This innings so far, min. 2 overs bowled
              </span>
              {Object.entries(data.form_scores)
                .sort((a, b) => b[1] - a[1])
                .map(([bowler, score]) => (
                  <div key={bowler} className="match-board__bowler-row">
                    <span>{bowler}</span>
                    <span className="match-board__bowler-score match-board__bowler-score--form">
                      {score}
                    </span>
                  </div>
                ))}
            </div>
          )}

          {hasExcluded && (
            <div className="match-board__section">
              <span className="match-board__section-title">UNAVAILABLE</span>
              <span className="match-board__section-hint">Cannot bowl this over</span>
              {Object.entries(excluded).map(([bowler, reason]) => (
                <div key={bowler} className="match-board__excluded-row">
                  <span>{bowler}</span>
                  <span className="match-board__excluded-reason">{reason}</span>
                </div>
              ))}
            </div>
          )}

          {hasUnrated && (
            <div className="match-board__section">
              <span className="match-board__section-title">NO T20 PROFILE SAMPLE</span>
              <span className="match-board__section-hint">
                No usable Cricbuzz T20 bowling sample
              </span>
              {unrated.map((bowler) => (
                <div key={bowler} className="match-board__excluded-row">
                  <span>{bowler}</span>
                  <span className="match-board__excluded-reason">
                    ranked from current spell with lower confidence
                  </span>
                </div>
              ))}
            </div>
          )}

          {(bowlingPlan.length > 0 || strategies.length > 0) && (
            <div className="match-board__section">
              <span className="match-board__section-title">
                {bowlingPlan.length > 0 ? "NEXT BOWLING PLAN" : "STRATEGIES"}
              </span>
              <span className="match-board__section-hint">
                {bowlingPlan.length > 0
                  ? "Legal rotation · reassess after every over"
                  : "Ranked by confidence"}
              </span>
              {data.current_batter_weakness && (
                <p className="match-board__note">
                  {data.current_batter_weakness}
                </p>
              )}
              {(bowlingPlan.length > 0 ? bowlingPlan : strategies).map((s, i) => {
                const strategy = bowlingPlan.length > 0
                  ? `Over ${s.over}: ${s.bowler}`
                  : s.strategy;
                return (
                  <StrategyCard
                    key={strategy}
                    strategy={strategy}
                    confidence={s.confidence}
                    rank={i}
                  />
                );
              })}
            </div>
          )}
        </>
      )}
    </aside>
  );
}
