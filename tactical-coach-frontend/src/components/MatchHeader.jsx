import "./MatchHeader.css";

/**
 * Thin context strip above the console, so the coach always knows which
 * match the answers refer to.
 */
export default function MatchHeader({ match }) {
  if (!match) return null;

  const place = [match.venue, match.city].filter(Boolean).join(", ");

  return (
    <div className="mhead">
      <span className="mhead__teams">
        {match.batting_first} <em>v</em> {match.chasing}
      </span>

      {match.match_number && (
        <span className="mhead__tag">Match {match.match_number}</span>
      )}

      {place && <span className="mhead__meta">{place}</span>}
      {match.dates?.[0] && <span className="mhead__meta">{match.dates[0]}</span>}
    </div>
  );
}
