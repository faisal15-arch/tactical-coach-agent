import { useEffect, useState } from "react";
import { getLiveSituation, getSituation, displayName } from "../api/client";
import "./BowlingCard.css";

/**
 * Bowling figures at any point in either innings, on its own page.
 *
 * It carries its own innings toggle and over slider rather than reading
 * the console's clock: this page is away from the console, so it has to
 * be able to stand somewhere on its own.
 */
export default function BowlingCard({
  selectedOver,
  selectedPhase,
  onBack,
  matchId = null,
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const request = matchId
      ? getLiveSituation(matchId, selectedOver, selectedPhase || "chase", true)
      : getSituation(selectedOver, selectedPhase || "chase");

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

    return () => {
      cancelled = true;
    };
  }, [selectedOver, selectedPhase, matchId]);

  const card = data?.bowling_card || [];
  const profiles = data?.player_t20_stats || {};

  const totals = card.reduce(
    (acc, b) => ({
      balls: acc.balls + b.balls,
      runs: acc.runs + b.runs,
      wickets: acc.wickets + b.wickets,
    }),
    { balls: 0, runs: 0, wickets: 0 }
  );

  // Cricket writes economy to two places. Left as a raw number, 5.0
  // renders as "5" and 11.0 as "11", which reads like a count rather
  // than a rate and makes the column hard to scan.
  const econ = (value) =>
    value === null || value === undefined ? "-" : value.toFixed(2);

  const totalEconomy = totals.balls ? totals.runs / (totals.balls / 6) : null;

  return (
    <div className="bcard">
      <div className="bcard__head">
        <div>
          <p className="bcard__eyebrow">Bowling figures</p>
          <h2 className="bcard__title">{data ? data.bowling_team : "Loading…"}</h2>
          {data && (
            <p className="bcard__meta">
              to {data.over} overs · {data.batting_team} {data.score}
            </p>
          )}
        </div>
        <button className="bcard__back" onClick={onBack}>
          Back to console
        </button>
      </div>

      {error && <p className="bcard__error">Could not load figures: {error}</p>}

      {!error && !data && (
        <p className="bcard__empty">Loading bowling figures…</p>
      )}

      {!error && data && (
        <table className="bcard__table">
          <thead>
            <tr>
              <th>Bowler</th>
              <th>O</th>
              <th>R</th>
              <th>W</th>
              <th>Econ</th>
              <th>Left</th>
            </tr>
          </thead>
          <tbody>
            {card.map((b) => (
              <tr key={b.bowler} className={b.is_bowling ? "is-bowling" : undefined}>
                <td>
                  <span className="bcard__bowler-name">
                    {displayName(b.bowler)}
                    {b.is_bowling && <span className="tag tag--live">bowling</span>}
                    {!b.rated && <span className="tag tag--unrated">not rated</span>}
                    {b.provisional && (
                      <span className="tag tag--provisional">provisional</span>
                    )}
                  </span>
                  {profiles[b.bowler] ? (
                    <span className="bcard__profile">
                      T20: {profiles[b.bowler].matches} matches · {profiles[b.bowler].wickets} wickets · avg {profiles[b.bowler].average} · econ {profiles[b.bowler].economy} · SR {profiles[b.bowler].strike_rate}
                    </span>
                  ) : (
                    <span className="bcard__profile">No usable T20 profile sample</span>
                  )}
                </td>
                <td>{b.overs}</td>
                <td>{b.runs}</td>
                <td className={b.wickets > 0 ? "is-wickets" : undefined}>
                  {b.wickets}
                </td>
                <td>{econ(b.economy)}</td>
                {/* Overs left is the four-over quota, the thing that
                    actually constrains the next decision. */}
                <td className={b.quota_used ? "is-spent" : undefined}>
                  {b.overs_left == null ? "—" : b.quota_used ? "spent" : b.overs_left}
                </td>
              </tr>
            ))}

            {card.length > 0 && (
              <tr className="bcard__totals">
                <td>Total</td>
                <td>
                  {Math.floor(totals.balls / 6)}.{totals.balls % 6}
                </td>
                <td>{totals.runs}</td>
                <td>{totals.wickets}</td>
                <td>{econ(totalEconomy)}</td>
                <td />
              </tr>
            )}

            {card.length === 0 && (
              <tr>
                <td colSpan="6" className="bcard__empty">
                  No overs bowled yet at this point.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {data && (
        <p className="bcard__footnote">
          {matchId
            ? "Figures come from the latest live scorecard and refresh when this page is opened."
            : "Figures are cumulative to the selected over and contain nothing from later in the innings."}
        </p>
      )}
    </div>
  );
}
