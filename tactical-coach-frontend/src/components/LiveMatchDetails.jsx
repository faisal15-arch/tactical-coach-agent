import "./LiveMatchDetails.css";

function matchDate(timestamp) {
  if (!timestamp) return null;

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(timestamp));
}

export default function LiveMatchDetails({
  details,
  status,
  error,
  onBack,
  onContinue,
  onRetry,
}) {
  if (status === "loading") {
    return <p className="app__loading">Loading match details…</p>;
  }

  if (status === "error") {
    return (
      <div className="live-details__error">
        <p>{error}</p>
        <button type="button" onClick={onRetry}>Try again</button>
        <button type="button" onClick={onBack}>Back to matches</button>
      </div>
    );
  }

  if (!details) return null;

  const match = details.match;
  const venue = [match.venue?.ground, match.venue?.city]
    .filter(Boolean)
    .join(", ");

  return (
    <main className="live-details">
      <button type="button" className="live-details__back" onClick={onBack}>
        ← Back to live matches
      </button>

      <header className="live-details__header">
        <p>{match.series_name}</p>
        <h2>{match.team1?.name} <span>v</span> {match.team2?.name}</h2>
        <div className="live-details__facts">
          <span>{match.description || match.format}</span>
          {venue && <span>{venue}</span>}
          {match.start_time_ms && <span>{matchDate(match.start_time_ms)}</span>}
        </div>
        {match.status && <strong>{match.status}</strong>}
      </header>

      <div className="live-details__lineups">
        {details.lineups.map((lineup) => (
          <section className="live-details__team" key={lineup.team_id}>
            <h3>{lineup.name} <span>Playing XI</span></h3>
            <ol>
              {lineup.playing_xi.map((player) => (
                <li key={player.player_id}>
                  <span>{player.name}</span>
                  <small>
                    {player.role}
                    {player.captain ? " · Captain" : ""}
                    {player.keeper ? " · WK" : ""}
                  </small>
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>

      <button type="button" className="live-details__continue" onClick={onContinue}>
        Open coaching console
      </button>
    </main>
  );
}
