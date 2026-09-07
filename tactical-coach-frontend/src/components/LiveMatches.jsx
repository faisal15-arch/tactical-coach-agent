import "./LiveMatches.css";

function latestScore(team) {
  const innings = team?.innings || [];
  const score = innings[innings.length - 1];

  if (!score || score.runs == null) return "Yet to bat";

  const wickets = score.wickets == null ? "" : `/${score.wickets}`;
  const overs = score.overs == null ? "" : ` (${score.overs} ov)`;
  return `${score.runs}${wickets}${overs}`;
}

export default function LiveMatches({
  coachName,
  matches,
  status,
  error,
  onRetry,
  onViewDetails,
}) {
  return (
    <main className="live-matches">
      <header className="live-matches__header">
        <p className="live-matches__eyebrow">Welcome, {coachName}</p>
        <h2>Live T20 matches</h2>
        <p>Current T20 and T20-league matches from Cricbuzz.</p>
      </header>

      {status === "loading" && (
        <p className="live-matches__message">Loading live matches…</p>
      )}

      {status === "error" && (
        <div className="live-matches__message live-matches__message--error">
          <p>{error}</p>
          <button type="button" onClick={onRetry}>Try again</button>
        </div>
      )}

      {status === "loaded" && matches.length === 0 && (
        <p className="live-matches__message">There are no live T20 matches right now.</p>
      )}

      {status === "loaded" && matches.length > 0 && (
        <div className="live-matches__grid">
          {matches.map((match) => (
            <article className="live-match" key={match.match_id}>
              <div className="live-match__topline">
                <span>{match.series_name || "Cricket match"}</span>
                <span className="live-match__state">{match.state || "Live"}</span>
              </div>

              <h3>{match.description || match.format || "Live match"}</h3>

              <div className="live-match__team">
                <strong>{match.team1?.name || "Team 1"}</strong>
                <span>{latestScore(match.team1)}</span>
              </div>
              <div className="live-match__team">
                <strong>{match.team2?.name || "Team 2"}</strong>
                <span>{latestScore(match.team2)}</span>
              </div>

              {match.status && <p className="live-match__status">{match.status}</p>}

              <footer className="live-match__footer">
                <span>
                  {[match.venue?.ground, match.venue?.city]
                    .filter(Boolean)
                    .join(", ")}
                </span>
                <button
                  type="button"
                  className="live-match__details"
                  onClick={() => onViewDetails(match)}
                >
                  Match details
                </button>
              </footer>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
