import { cardTitle, gradeLabel, money } from "./format.js";

const RANGE_TITLES = {
  "1D": "Today",
  "1W": "Past week",
  "1M": "Past month",
  "3M": "Past 3 months",
  "1Y": "Past year",
  ALL: "All time",
};

function signedMoney(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value < 0 ? "−" : "+"}${money(Math.abs(value))}`;
}

function tone(value) {
  return value > 0 ? "positive" : value < 0 ? "negative" : "flat";
}

// The cards whose price moves added or took away the most dollars over the
// range (price change × copies held), not the biggest percentages.
export function Contributors({ attribution, range, onZoom }) {
  const contributors = [...(attribution?.contributors || [])].sort(
    (a, b) => b.market_change - a.market_change
  );

  return (
    <section className="analytics-panel contributors-panel">
      <p className="eyebrow">BIGGEST PORTFOLIO CONTRIBUTORS</p>
      <h3>{RANGE_TITLES[range] || range}</h3>

      {contributors.length === 0 ? (
        <div className="empty-state small">No price changes in this range.</div>
      ) : (
        <div className="sync-summary-list">
          {contributors.map((entry) => (
            <div className="sync-change-row" key={`${entry.card.id}-${entry.grade}`}>
              {entry.card.image_url && (
                <img
                  className="thumb-clickable"
                  src={entry.card.image_url}
                  alt={cardTitle(entry.card)}
                  onClick={() => onZoom(entry.card)}
                />
              )}

              <div className="sync-change-copy">
                <strong>{cardTitle(entry.card)}</strong>
                <span>
                  {entry.kind === "sports" ? (
                    entry.card.set_name
                  ) : (
                    <>
                      {gradeLabel(entry.grade)}
                      {entry.quantity > 1 ? ` · ×${entry.quantity}` : ""}
                      {entry.quantity === 0 ? " · no longer held" : ""}
                    </>
                  )}
                </span>
              </div>

              <div className={`sync-change-amount ${tone(entry.market_change)}`}>
                <strong>{signedMoney(entry.market_change)}</strong>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
