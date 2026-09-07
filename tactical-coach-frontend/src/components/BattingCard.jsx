import { useEffect, useState } from "react";
import { displayName, getLiveSituation, getSituation } from "../api/client";
import "./BattingCard.css";

export default function BattingCard({
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
      ? getLiveSituation(matchId, selectedOver, selectedPhase || "chase")
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

  const card = data?.batting_card || [];
  const strikeRate = (value) =>
    value === null || value === undefined ? "-" : Number(value).toFixed(2);

  return (
    <div className="batcard">
      <div className="batcard__head">
        <div>
          <p className="batcard__eyebrow">Batting scorecard</p>
          <h2 className="batcard__title">{data ? data.batting_team : "Loading…"}</h2>
          {data && (
            <p className="batcard__meta">
              {data.score} after {data.over} overs
            </p>
          )}
        </div>
        <button className="batcard__back" type="button" onClick={onBack}>
          Back to console
        </button>
      </div>

      {error && <p className="batcard__error">Could not load scorecard: {error}</p>}

      {!error && !data && (
        <p className="batcard__empty">Loading batting scorecard…</p>
      )}

      {!error && data && (
        <table className="batcard__table">
          <thead>
            <tr>
              <th>Batter</th>
              <th>R</th>
              <th>B</th>
              <th>4s</th>
              <th>6s</th>
              <th>SR</th>
            </tr>
          </thead>
          <tbody>
            {card.map((batter) => (
              <tr key={batter.batter}>
                <td>
                  <span className="batcard__batter">{displayName(batter.batter)}</span>
                  <span className="batcard__status">{batter.status}</span>
                </td>
                <td className="batcard__runs">{batter.runs}</td>
                <td>{batter.balls}</td>
                <td>{batter.fours}</td>
                <td>{batter.sixes}</td>
                <td>{strikeRate(batter.strike_rate)}</td>
              </tr>
            ))}
            {card.length === 0 && (
              <tr>
                <td colSpan="6" className="batcard__empty">
                  No batting data is available at this point.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
