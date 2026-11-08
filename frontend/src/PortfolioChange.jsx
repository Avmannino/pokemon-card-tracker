import { cardTitle, gradeLabel, money, percent } from "./format.js";

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

// Start → market → added → removed → now, each bar spanning the running
// total before and after its step.
function Waterfall({ attribution }) {
  const steps = [];
  let running = attribution.starting_value;

  steps.push({ label: "Start", from: 0, to: running, kind: "total" });
  for (const [label, delta] of [
    ["Market", attribution.market_change],
    ["Added", attribution.acquisitions],
    ["Removed", attribution.removals],
  ]) {
    steps.push({ label, from: running, to: running + delta, kind: tone(delta), delta });
    running += delta;
  }
  steps.push({ label: "Now", from: 0, to: attribution.ending_value, kind: "total" });

  const values = steps.flatMap((step) => [step.from, step.to]);
  const low = Math.min(0, ...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const position = (value) => ((value - low) / span) * 100;

  return (
    <div className="waterfall" role="img" aria-label="How the collection's value changed">
      {steps.map((step) => {
        const left = position(Math.min(step.from, step.to));
        const width = Math.max(position(Math.max(step.from, step.to)) - left, 0.6);

        return (
          <div className="waterfall-row" key={step.label}>
            <span className="waterfall-label">{step.label}</span>

            <div className="waterfall-track">
              <span
                className={`waterfall-bar ${step.kind}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </div>

            <span className={`waterfall-value ${step.kind === "total" ? "" : step.kind}`}>
              {step.kind === "total" ? money(step.to) : signedMoney(step.delta)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// Why the collection's value changed over the chart's selected range:
// market movement (price changes of cards while you held them) vs value
// added and removed by adding and removing cards.
export function PortfolioChange({ attribution, range }) {
  if (!attribution) {
    return null;
  }

  const stats = [
    {
      name: "Total change",
      value: attribution.total_change,
      note: `${money(attribution.starting_value)} → ${money(attribution.ending_value)}`,
    },
    {
      name: "Market movement",
      value: attribution.market_change,
      note:
        attribution.market_change_pct === null || attribution.market_change_pct === undefined
          ? "Price changes of cards you held"
          : `${percent(attribution.market_change_pct)} market performance`,
    },
    {
      name: "Cards added",
      value: attribution.acquisitions,
      note: "At their value when added",
    },
    {
      name: "Cards removed",
      value: attribution.removals,
      note: "At their value when removed",
    },
  ];

  return (
    <section className="analytics-panel attribution-panel">
      <p className="eyebrow">PORTFOLIO CHANGE</p>
      <h3>{RANGE_TITLES[range] || range}</h3>

      <div className="attribution-stats">
        {stats.map((stat) => (
          <div className="attribution-stat" key={stat.name}>
            <span>{stat.name}</span>
            <strong className={tone(stat.value)}>{signedMoney(stat.value)}</strong>
            <small>{stat.note}</small>
          </div>
        ))}
      </div>

      <Waterfall attribution={attribution} />

      <p className="analytics-note">
        Total change = market movement + cards added + cards removed. Buying a
        card adds its value but never counts as a gain.
      </p>
    </section>
  );
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
                  {gradeLabel(entry.grade)}
                  {entry.quantity > 1 ? ` · ×${entry.quantity}` : ""}
                  {entry.quantity === 0 ? " · no longer held" : ""}
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
