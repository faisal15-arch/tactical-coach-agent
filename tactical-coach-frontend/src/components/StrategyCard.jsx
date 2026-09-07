import "./StrategyCard.css";

export default function StrategyCard({ strategy, confidence, rank }) {
  const isTop = rank === 0;

  return (
    <div className={`strategy-card ${isTop ? "strategy-card--top" : ""}`}>
      <div className="strategy-card__rank">{String(rank + 1).padStart(2, "0")}</div>
      <div className="strategy-card__body">
        <div className="strategy-card__name">{strategy}</div>
        <div className="strategy-card__bar-track">
          <div
            className="strategy-card__bar-fill"
            style={{ width: `${confidence}%` }}
          />
        </div>
      </div>
      <div className="strategy-card__confidence">{confidence}</div>
    </div>
  );
}
