import { useEffect, useState } from "react";
import { displayName, getLiveSituation, getSituation } from "../api/client";
import "./MatchupsCard.css";

function strikeRate(stats) {
  return stats?.balls ? ((stats.runs / stats.balls) * 100).toFixed(1) : "-";
}

function batterNames(data) {
  const names = (data?.current_batsmen || [])
    .map((batter) => typeof batter === "string" ? batter : batter?.name)
    .filter(Boolean);

  if (names.length) return names;
  return [data?.batsmen?.striker, data?.batsmen?.non_striker].filter(Boolean);
}

export default function MatchupsCard({
  selectedOver,
  selectedPhase,
  onBack,
  matchId = null,
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const load = () => {
      const request = matchId
        ? getLiveSituation(
            matchId,
            selectedOver,
            selectedPhase || "chase",
            false,
            true
          )
        : getSituation(selectedOver, selectedPhase || "chase", true);

      request
        .then((result) => {
          if (!cancelled) {
            setData(result);
            setError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setError(err.message);
        });
    };

    load();
    const timer = matchId && !selectedOver
      ? window.setInterval(load, 30000)
      : null;

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [selectedOver, selectedPhase, matchId]);

  if (!data && !error) {
    return (
      <main className="matchups-card">
        <p className="matchups-card__empty">Loading batter vs bowler data…</p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="matchups-card">
        <p className="matchups-card__empty">Could not load matchups: {error}</p>
      </main>
    );
  }

  const batters = batterNames(data);
  const currentBowler = data?.current_bowler || data?.bowling_card?.find(
    (bowler) => bowler.is_bowling
  )?.bowler;
  const bowlerFigures = data?.bowling_card?.find(
    (bowler) => bowler.bowler === currentBowler
  );
  const allMatchups = data?.matchup_stats || {};
  const typeMatchups = data?.bowling_type_matchups || {};
  const careerMatchups = data?.career_matchup_stats || {};

  const currentRows = batters
    .map((batter) => ({
      batter,
      bowler: currentBowler,
      ...(allMatchups[batter]?.[currentBowler] || {
        balls: 0,
        runs: 0,
        dismissals: 0,
      }),
    }))
    .filter((row) => Number(row.balls) > 0);

  const otherRows = batters.flatMap((batter) =>
    Object.entries(allMatchups[batter] || {})
      .filter(([bowler, stats]) => (
        bowler !== currentBowler && Number(stats.balls) > 0
      ))
      .map(([bowler, stats]) => ({ batter, bowler, ...stats }))
  ).sort((a, b) => b.balls - a.balls);

  const careerRows = batters.flatMap((batter) =>
    Object.entries(careerMatchups[batter] || {})
      .filter(([, stats]) => !stats.error && Number(stats.balls) > 0)
      .map(([bowler, stats]) => ({ batter, bowler, ...stats }))
      .sort((a, b) => a.bowler.localeCompare(b.bowler))
  );

  const typeProfiles = batters.map((batter) => {
    const profile = typeMatchups[batter];
    const categories = Object.values(profile?.categories || {}).filter(
      (row) => Number(row.balls) > 0
        && String(row.assessment || "").toLowerCase() !== "no sample"
    );
    return { batter, profile, categories };
  }).filter((item) => item.categories.length > 0);

  return (
    <main className="matchups-card">
      <header className="matchups-card__head">
        <div>
          <p className="matchups-card__eyebrow">Batter vs bowler</p>
          <h2>{currentBowler ? displayName(currentBowler) : "Current bowler unavailable"}</h2>
          {data && (
            <p className="matchups-card__meta">
              {data.batting_team} {data.score} · {data.over} overs
            </p>
          )}
        </div>
        <button type="button" onClick={onBack}>Back to console</button>
      </header>

      {error && <p className="matchups-card__empty">Could not load matchups: {error}</p>}

      {!error && !data && <p className="matchups-card__empty">Loading matchups…</p>}

      {!error && data && (
        <>
          <div className="matchups-card__sections">
          {typeProfiles.length > 0 && (
          <section className="matchups-card__types">
            <div className="matchups-card__section-head">
              <div>
                <h3>Current batters vs bowling types</h3>
                <p>Career All-T20 record · strength combines scoring rate and wicket resistance</p>
              </div>
              <span className="matchups-card__source">Cricmetric All T20</span>
            </div>

            <div className="matchups-card__type-batters">
              {typeProfiles.map(({ batter, profile, categories }) => {
                return (
                  <article key={batter} className="matchups-card__type-batter">
                    <h4>{displayName(profile?.player || batter)}</h4>
                    {categories.length ? (
                      <div className="matchups-card__table-wrap">
                        <table className="matchups-card__type-table">
                          <thead>
                            <tr>
                              <th>Bowling type</th>
                              <th>R</th>
                              <th>B</th>
                              <th>Out</th>
                              <th>SR</th>
                              <th>Avg</th>
                              <th>Assessment</th>
                            </tr>
                          </thead>
                          <tbody>
                            {categories.map((row) => (
                              <tr key={row.label}>
                                <td>{row.label}</td>
                                <td>{row.runs}</td>
                                <td>{row.balls}</td>
                                <td>{row.dismissals}</td>
                                <td>{row.strike_rate ?? "-"}</td>
                                <td>{row.average ?? "-"}</td>
                                <td>
                                  <span className={
                                    "matchups-card__assessment is-" +
                                    row.assessment.toLowerCase().replace(" ", "-")
                                  }>
                                    {row.assessment}
                                    {row.strength_score !== null && ` ${row.strength_score}`}
                                  </span>
                                  <small>{row.sample_reliability} reliability</small>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="matchups-card__empty">
                        {profile?.error || "Bowling-type T20 data is unavailable."}
                      </p>
                    )}
                  </article>
                );
              })}
            </div>
            <p className="matchups-card__footnote">
              Out means batter dismissals. Under 18 balls is shown as a limited sample.
            </p>
          </section>
          )}

          {currentBowler && currentRows.length > 0 && (
          <section className="matchups-card__current">
            <div className="matchups-card__section-head">
              <div>
                <h3>Current bowler vs current batters</h3>
                <p>This innings only · up to the selected live ball</p>
              </div>
              {bowlerFigures && (
                <span className="matchups-card__spell">
                  Spell {bowlerFigures.overs}-{bowlerFigures.runs}-{bowlerFigures.wickets}
                  {` · econ ${Number(bowlerFigures.economy).toFixed(2)}`}
                </span>
              )}
            </div>

            <div className="matchups-card__duels">
                {currentRows.map((row) => (
                  <article key={row.batter} className="matchups-card__duel">
                    <span className="matchups-card__batter">
                      {displayName(row.batter)}
                      {row.batter === data.batsmen?.on_strike_next_ball && " *"}
                    </span>
                    <span className="matchups-card__versus">
                      vs {displayName(row.bowler)}
                    </span>
                    <strong>{row.runs} runs</strong>
                    <div className="matchups-card__numbers">
                      <span>{row.balls} balls</span>
                      <span>SR {strikeRate(row)}</span>
                      <span>{row.dismissals} dismissals</span>
                    </div>
                  </article>
                ))}
            </div>
          </section>
          )}

          {careerRows.length > 0 && (
          <section className="matchups-card__career">
            <div className="matchups-card__section-head">
              <div>
                <h3>Current batters vs all available bowlers — career</h3>
                <p>Recorded head-to-head figures across all T20 matches</p>
              </div>
              <span className="matchups-card__source">Cricmetric All T20</span>
            </div>

            <div className="matchups-card__duels">
                {careerRows.map((row) => (
                  <article
                    key={`${row.batter}-${row.bowler}`}
                    className="matchups-card__duel"
                  >
                    <span className="matchups-card__batter">
                      {displayName(row.batter)}
                    </span>
                    <span className="matchups-card__versus">
                      vs {displayName(row.bowler)}
                    </span>
                    <strong>{row.runs} runs</strong>
                    <div className="matchups-card__numbers">
                      <span>{row.balls} balls</span>
                      <span>SR {Number(row.strike_rate).toFixed(1)}</span>
                      <span>{row.dismissals} dismissals</span>
                      <span>Avg {row.average ?? "-"}</span>
                      <span>{row.dots} dots</span>
                      <span>{row.fours} fours</span>
                      <span>{row.sixes} sixes</span>
                    </div>
                  </article>
                ))}
            </div>
          </section>
          )}

          {otherRows.length > 0 && (
          <section className="matchups-card__history">
            <h3>Other matchups at this point</h3>
            <p>Current batters against bowlers already used in this innings</p>
            <div className="matchups-card__table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Batter</th>
                    <th>Bowler</th>
                    <th>B</th>
                    <th>R</th>
                    <th>SR</th>
                    <th>Out</th>
                  </tr>
                </thead>
                <tbody>
                  {otherRows.map((row) => (
                    <tr key={`${row.batter}-${row.bowler}`}>
                      <td>{displayName(row.batter)}</td>
                      <td>{displayName(row.bowler)}</td>
                      <td>{row.balls}</td>
                      <td>{row.runs}</td>
                      <td>{strikeRate(row)}</td>
                      <td>{row.dismissals}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          )}
          </div>
        </>
      )}
    </main>
  );
}
