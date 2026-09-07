import { useEffect, useState } from "react";
import { displayName, getLiveSituation, getSituation } from "../api/client";
import "./BowlerProfiles.css";

const METRICS = [
  ["matches", "Matches"],
  ["wickets", "Wickets"],
  ["average", "Average"],
  ["economy", "Economy"],
  ["strike_rate", "Strike rate"],
];

function metricValue(profile, key) {
  const value = profile?.[key];
  return value === null || value === undefined ? "-" : value;
}

export default function BowlerProfiles({
  selectedOver,
  selectedPhase,
  onBack,
  matchId = null,
  fallbackData = null,
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const load = () => {
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

  const situation = data || fallbackData;
  const profiles = situation?.player_t20_stats || fallbackData?.player_t20_stats || {};
  const bowlers = situation?.bowling_card?.map((row) => row.bowler)
    || Object.keys(profiles);

  return (
    <main className="profiles-page">
      <header className="profiles-page__head">
        <div>
          <p className="profiles-page__eyebrow">T20 career data</p>
          <h2>Bowler profiles</h2>
          {situation && (
            <p className="profiles-page__meta">
              {situation.bowling_team || "Bowling side"}
              {situation.over ? ` · selected at ${situation.over} overs` : ""}
            </p>
          )}
        </div>
        <button type="button" onClick={onBack}>Back to console</button>
      </header>

      {error && !situation && (
        <p className="profiles-page__empty">Could not load profiles: {error}</p>
      )}
      {!error && !situation && (
        <p className="profiles-page__empty">Loading bowler profiles…</p>
      )}

      {situation && (
        <>
          <p className="profiles-page__note">
            Cricbuzz career T20 bowling stats—not this match's spell figures.
          </p>
          <section className="profiles-page__grid">
            {bowlers.map((bowler) => {
              const profile = profiles[bowler];
              return (
                <article key={bowler} className="profiles-page__card">
                  <h3>{displayName(bowler)}</h3>
                  {profile ? (
                    <div className="profiles-page__metrics">
                      {METRICS.map(([key, label]) => (
                        <div key={key}>
                          <span>{label}</span>
                          <strong>{metricValue(profile, key)}</strong>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="profiles-page__unavailable">
                      No usable Cricbuzz T20 profile sample.
                    </p>
                  )}
                </article>
              );
            })}
          </section>
          {bowlers.length === 0 && (
            <p className="profiles-page__empty">No bowlers are available at this point.</p>
          )}
        </>
      )}
    </main>
  );
}
