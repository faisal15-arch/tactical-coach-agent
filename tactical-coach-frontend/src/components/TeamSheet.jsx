import { displayName } from "../api/client";
import "./TeamSheet.css";

/**
 * Both XIs, side by side, before the console opens.
 *
 * The RATED marker is the point of this screen. Only players with a bowling
 * profile can ever be scored or recommended, so anyone without one would
 * otherwise just be missing from the console with no explanation. Marking
 * them here means nobody vanishes silently later.
 *
 * The result is deliberately not shown. The whole exercise is deciding what
 * should have happened, and knowing the winner first spoils that.
 */
export default function TeamSheet({ match, coachName, onContinue }) {
  if (!match) return null;

  const rated = new Set(match.rated_bowlers || []);
  const teams = Object.entries(match.squads || {});

  return (
    <div className="sheet">
      <div className="sheet__head">
        <p className="sheet__eyebrow">
          {match.event_name}
          {match.match_number ? ` · Match ${match.match_number}` : ""}
        </p>
        <h2 className="sheet__title">
          {match.batting_first} <span>v</span> {match.chasing}
        </h2>
        <p className="sheet__meta">
          {[match.venue, match.city, match.dates?.[0]].filter(Boolean).join(" · ")}
        </p>
        {match.toss?.winner && (
          <p className="sheet__toss">
            {match.toss.winner} won the toss and chose to {match.toss.decision}
          </p>
        )}
      </div>

      <div className="sheet__teams">
        {teams.map(([team, players]) => {
          const ratedCount = players.filter((p) => rated.has(p)).length;

          return (
            <section className="sheet__team" key={team}>
              <header className="sheet__teamhead">
                <h3>{team}</h3>
                <span className="sheet__count">
                  {ratedCount} rated {ratedCount === 1 ? "bowler" : "bowlers"}
                </span>
              </header>

              <ul className="sheet__list">
                {players.map((player) => (
                  <li className="sheet__player" key={player}>
                    <span>{displayName(player)}</span>
                    {rated.has(player) && (
                      <span className="sheet__badge">Rated</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      <p className="sheet__legend">
        Rated players have a bowling profile, so the console can score them.
        Everyone else can bat but will never appear in a recommendation.
      </p>

      <button className="sheet__button" onClick={onContinue}>
        Open the console{coachName ? `, ${coachName}` : ""}
      </button>
    </div>
  );
}
