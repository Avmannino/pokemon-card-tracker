import { useEffect, useState } from "react";

import { getPortfolioDashboard } from "./api.js";
import CardZoomModal, { useCardZoom } from "./CardZoomModal.jsx";
import { money, percent, cardTitle } from "./format.js";

const CHART_RANGES = [
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
  { key: "1M", label: "1M" },
  { key: "3M", label: "3M" },
  { key: "1Y", label: "1Y" },
  { key: "ALL", label: "All" },
];

// Short ranges show times on hover; longer ones just the date.
const TIMED_RANGES = new Set(["1D", "1W"]);

// Portfolio-level value change.
const PERFORMANCE_WINDOWS = [
  { key: "1D", label: "Past Day" },
  { key: "1W", label: "Past Week" },
  { key: "1M", label: "Past Month" },
  { key: "3M", label: "Past 3 Months" },
];

// Reported on each biggest-mover entry.
const MOVER_CARD_WINDOWS = [
  { key: "1W", label: "Week" },
  { key: "1M", label: "Month" },
];

function signedMoney(value) {
  if (value === null || value === undefined) {
    return "—";
  }

  return `${value >= 0 ? "+" : "−"}${money(Math.abs(value))}`;
}

function formatTimestamp(value, withTime) {
  return new Date(value).toLocaleString(
    "en-US",
    withTime
      ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
      : { month: "short", day: "numeric", year: "numeric" }
  );
}

function ChangeBadge({ change, changePct }) {
  if (change === null || change === undefined) {
    return null;
  }

  const positive = change >= 0;

  return (
    <span className={`change-badge ${positive ? "positive" : "negative"}`}>
      {positive ? "▲" : "▼"} {money(Math.abs(change))}
      {changePct !== null && changePct !== undefined && ` (${percent(changePct)})`}
    </span>
  );
}

function LineChart({ points, hoveredIndex, onHover }) {
  const width = 640;
  const height = 200;
  const padX = 6;
  const padY = 14;

  // The performance line: raw value minus cards added/removed in the
  // range, so only market movement moves it.
  const values = points.map((point) => point.adjusted_value);
  const times = points.map((point) => new Date(point.timestamp).getTime());
  const firstTime = times[0];
  const timeSpan = times[times.length - 1] - firstTime || 1;
  const plotWidth = width - padX * 2;
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1) * 0.02 || 1;

  const coords = points.map((point, index) => ({
    x: padX + ((times[index] - firstTime) / timeSpan) * plotWidth,
    y:
      height -
      padY -
      ((point.adjusted_value - minValue) / span) * (height - padY * 2),
  }));

  const isPositive =
    points[points.length - 1].adjusted_value >= points[0].adjusted_value;
  const stroke = isPositive ? "#5fe3a4" : "#f18a8a";
  const fillId = isPositive ? "chart-fill-up" : "chart-fill-down";

  const linePath = coords
    .map((c, index) => `${index === 0 ? "M" : "L"} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`)
    .join(" ");

  const areaPath =
    coords.length > 1
      ? `${linePath} L ${coords[coords.length - 1].x.toFixed(2)} ${(height - padY).toFixed(2)} ` +
        `L ${coords[0].x.toFixed(2)} ${(height - padY).toFixed(2)} Z`
      : "";

  function handleMove(event) {
    if (coords.length < 2) {
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();
    const relativeX = ((event.clientX - rect.left) / rect.width) * width;
    const clamped = Math.max(padX, Math.min(width - padX, relativeX));
    const target = firstTime + ((clamped - padX) / plotWidth) * timeSpan;

    let nearest = 0;
    times.forEach((time, index) => {
      if (Math.abs(time - target) < Math.abs(times[nearest] - target)) {
        nearest = index;
      }
    });

    onHover(nearest);
  }

  const hovered =
    hoveredIndex !== null && coords[hoveredIndex] ? coords[hoveredIndex] : null;

  return (
    <div className="chart-svg-wrap">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        onMouseMove={handleMove}
        onMouseLeave={() => onHover(null)}
      >
        <defs>
          <linearGradient id="chart-fill-up" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(95,227,164,0.32)" />
            <stop offset="100%" stopColor="rgba(95,227,164,0)" />
          </linearGradient>

          <linearGradient id="chart-fill-down" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(241,138,138,0.30)" />
            <stop offset="100%" stopColor="rgba(241,138,138,0)" />
          </linearGradient>
        </defs>

        {areaPath && <path d={areaPath} fill={`url(#${fillId})`} stroke="none" />}

        {/* The chart stretches to fill its card, so strokes are non-scaling
            and dots are zero-length round-capped lines (a <circle> would
            squash into an ellipse). */}
        {coords.length > 1 ? (
          <path
            d={linePath}
            fill="none"
            stroke={stroke}
            strokeWidth="2.5"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        ) : (
          <line
            x1={coords[0].x}
            x2={coords[0].x}
            y1={coords[0].y}
            y2={coords[0].y}
            stroke={stroke}
            strokeWidth="8"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        )}

        {hovered && (
          <>
            <line
              x1={hovered.x}
              x2={hovered.x}
              y1={padY}
              y2={height - padY}
              stroke="rgba(255,255,255,0.18)"
              vectorEffect="non-scaling-stroke"
            />

            <line
              x1={hovered.x}
              x2={hovered.x}
              y1={hovered.y}
              y2={hovered.y}
              stroke={stroke}
              strokeWidth="11"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />

            <line
              x1={hovered.x}
              x2={hovered.x}
              y1={hovered.y}
              y2={hovered.y}
              stroke="#0b0f17"
              strokeWidth="6"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          </>
        )}
      </svg>
    </div>
  );
}

function Dashboard({ refreshKey }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [range, setRange] = useState("1M");
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const { zoomedCard, openZoom, closeZoom } = useCardZoom();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError("");

      try {
        const result = await getPortfolioDashboard();
        if (!cancelled) {
          setData(result);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();

    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  useEffect(() => {
    setHoveredIndex(null);
  }, [range]);

  const rangeData = data?.ranges?.[range];
  const points = rangeData?.points || [];

  if (loading) {
    return (
      <section className="dashboard-panel">
        <div className="empty-state">Loading dashboard…</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="dashboard-panel">
        <div className="status error">{error}</div>
      </section>
    );
  }

  if (!data?.ranges?.ALL?.points?.length) {
    return (
      <section className="dashboard-panel">
        <div className="dashboard-heading">
          <p className="eyebrow">DASHBOARD</p>
          <h2>Portfolio performance</h2>
        </div>

        <div className="empty-state">
          Add cards and refresh prices to start tracking value over time.
        </div>
      </section>
    );
  }

  const activePoint =
    hoveredIndex !== null && points[hoveredIndex] ? points[hoveredIndex] : null;

  return (
    <section className="dashboard-panel">
      <div className="dashboard-heading">
        <p className="eyebrow">DASHBOARD</p>
        <h2>Portfolio performance</h2>
      </div>

      <div className="performance-row">
        {PERFORMANCE_WINDOWS.map(({ key, label }) => {
          const window = data.ranges?.[key] || {};
          const missing =
            window.change === null || window.change === undefined;
          const tone = window.change >= 0 ? "positive" : "negative";

          return (
            <article className="performance-card" key={key}>
              <span className="performance-window">{label}</span>

              {missing ? (
                <strong className="performance-change muted">—</strong>
              ) : (
                <>
                  <strong className={`performance-change ${tone}`}>
                    {signedMoney(window.change)}
                  </strong>

                  <span className={`performance-pct ${tone}`}>
                    {percent(window.change_pct)}
                  </span>
                </>
              )}
            </article>
          );
        })}
      </div>

      <div className="dashboard-main-row">
        <div className="chart-card">
          <div className="chart-card-heading">
            <div>
              <p className="eyebrow">PORTFOLIO VALUE</p>

              <div className="chart-value-row">
                <strong>
                  {money(
                    activePoint
                      ? activePoint.adjusted_value
                      : data.current_total_value
                  )}
                </strong>

                <ChangeBadge
                  change={activePoint ? activePoint.change : rangeData?.change}
                  changePct={
                    activePoint ? activePoint.change_pct : rangeData?.change_pct
                  }
                />
              </div>

              <span className="chart-date-label">
                {activePoint
                  ? formatTimestamp(activePoint.timestamp, TIMED_RANGES.has(range))
                  : rangeData?.end && `As of ${formatTimestamp(rangeData.end, true)}`}
              </span>
            </div>

            <div className="range-tabs">
              {CHART_RANGES.map(({ key, label }) => (
                <button
                  type="button"
                  key={key}
                  className={range === key ? "active" : ""}
                  onClick={() => setRange(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {points.length > 0 && (
            <LineChart
              points={points}
              hoveredIndex={hoveredIndex}
              onHover={setHoveredIndex}
            />
          )}

          <p className="chart-note">
            Performance only: adding or removing cards doesn&apos;t move this
            line, market price changes do.
          </p>
        </div>

        <aside className="top-cards-panel">
          <p className="eyebrow">TOP CARDS</p>
          <h3>By value</h3>

          {data.top_cards.length === 0 ? (
            <div className="empty-state small">No priced cards yet.</div>
          ) : (
            <div className="top-cards-list">
              {data.top_cards.map((row, index) => (
                <div className="top-card-row" key={row.card.id}>
                  <span className="top-card-rank">{index + 1}</span>

                  {row.card.image_url && (
                    <img
                      className="thumb-clickable"
                      src={row.card.image_url}
                      alt={cardTitle(row.card)}
                      onClick={() =>
                        openZoom(row.card, {
                          grade: row.grade,
                          valueEach: row.value_each,
                        })
                      }
                    />
                  )}

                  <div className="top-card-copy">
                    <strong>{cardTitle(row.card)}</strong>
                    <span>{row.card.set_name || "Unknown set"}</span>
                  </div>

                  <strong className="top-card-value">
                    {money(row.value)}
                  </strong>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>

      <section className="movers-panel">
        <p className="eyebrow">BIGGEST MOVERS</p>
        <h3>Largest price changes</h3>

        {(data.top_movers || []).length === 0 ? (
          <div className="empty-state small">
            Not enough price history yet.
          </div>
        ) : (
          <div className="movers-grid">
            {data.top_movers.map((mover, index) => (
              <article
                className="mover-entry"
                key={`${mover.card.id}-${index}`}
              >
                <div className="mover-identity">
                  {mover.card.image_url && (
                    <img
                      className="thumb-clickable"
                      src={mover.card.image_url}
                      alt={cardTitle(mover.card)}
                      onClick={() =>
                        openZoom(mover.card, {
                          grade: mover.grade,
                          valueEach: mover.value_each,
                        })
                      }
                    />
                  )}

                  <div>
                    <strong>{cardTitle(mover.card)}</strong>
                    <span>{mover.card.set_name || "Unknown set"}</span>
                    <span className="mover-value">
                      {money(mover.current_value)}
                    </span>
                  </div>
                </div>

                <div className="mover-changes">
                  {MOVER_CARD_WINDOWS.map(({ key, label }) => {
                    const change = mover.changes?.[key];
                    const tone =
                      change && change.change >= 0 ? "positive" : "negative";

                    return (
                      <div className="mover-change-row" key={key}>
                        <span className="mover-change-label">{label}</span>

                        {change ? (
                          <span className={tone}>
                            <strong>{signedMoney(change.change)}</strong>{" "}
                            {percent(change.change_pct)}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <CardZoomModal card={zoomedCard} onClose={closeZoom} />
    </section>
  );
}

export default Dashboard;
