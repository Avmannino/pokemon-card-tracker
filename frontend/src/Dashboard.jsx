import { useEffect, useMemo, useState } from "react";

import { getPortfolioDashboard } from "./api.js";
import CardZoomModal, { useCardZoom } from "./CardZoomModal.jsx";
import { money, percent, cardTitle } from "./format.js";

const CHART_RANGES = [
  { key: "1D", label: "1D", days: 1 },
  { key: "1W", label: "1W", days: 7 },
  { key: "1M", label: "1M", days: 30 },
  { key: "ALL", label: "All", days: null },
];

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

function formatDate(value) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
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

  const values = points.map((point) => point.total_value);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1) * 0.02 || 1;

  const stepX = points.length > 1 ? (width - padX * 2) / (points.length - 1) : 0;

  const coords = points.map((point, index) => ({
    x: padX + stepX * index,
    y:
      height -
      padY -
      ((point.total_value - minValue) / span) * (height - padY * 2),
  }));

  const isPositive =
    points[points.length - 1].total_value >= points[0].total_value;
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
    const index = Math.round((clamped - padX) / stepX);

    onHover(Math.max(0, Math.min(points.length - 1, index)));
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

        {coords.length > 1 ? (
          <path
            d={linePath}
            fill="none"
            stroke={stroke}
            strokeWidth="2.5"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ) : (
          <circle cx={coords[0].x} cy={coords[0].y} r="4" fill={stroke} />
        )}

        {hovered && (
          <>
            <line
              x1={hovered.x}
              x2={hovered.x}
              y1={padY}
              y2={height - padY}
              stroke="rgba(255,255,255,0.18)"
            />

            <circle
              cx={hovered.x}
              cy={hovered.y}
              r="4.5"
              fill="#0b0f17"
              stroke={stroke}
              strokeWidth="2"
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

  const filteredHistory = useMemo(() => {
    if (!data) {
      return [];
    }

    const known = data.history.filter((point) => point.total_value !== null);
    const activeRange = CHART_RANGES.find((entry) => entry.key === range);

    if (!activeRange || activeRange.days === null) {
      return known;
    }

    const cutoff = new Date();
    cutoff.setUTCDate(cutoff.getUTCDate() - activeRange.days);
    const cutoffKey = cutoff.toISOString().slice(0, 10);

    const sliced = known.filter((point) => point.date >= cutoffKey);

    // Keep at least the latest point so a sparse range (refreshes less
    // often than the range itself) still shows a value instead of going
    // blank.
    return sliced.length > 0 ? sliced : known.slice(-1);
  }, [data, range]);

  const rangeChange = useMemo(() => {
    if (filteredHistory.length === 0) {
      return null;
    }

    const first = filteredHistory[0].total_value;
    const last = filteredHistory[filteredHistory.length - 1].total_value;
    const change = round2(last - first);
    const changePct = first ? round2((change / first) * 100) : null;

    return { change, changePct };
  }, [filteredHistory]);

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

  if (!data || filteredHistory.length === 0) {
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

  const latest = filteredHistory[filteredHistory.length - 1];
  // The chart values the cards you hold now at each day's prices, so a card
  // added recently is flat-lined before its first known price.
  const partialBefore =
    data.full_history_since &&
    filteredHistory[0].date < data.full_history_since
      ? data.full_history_since
      : null;
  const activePoint =
    hoveredIndex !== null && filteredHistory[hoveredIndex]
      ? filteredHistory[hoveredIndex]
      : latest;

  return (
    <section className="dashboard-panel">
      <div className="dashboard-heading">
        <p className="eyebrow">DASHBOARD</p>
        <h2>Portfolio performance</h2>
      </div>

      <div className="performance-row">
        {PERFORMANCE_WINDOWS.map(({ key, label }) => {
          const window = data.performance?.[key] || {};
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
                <strong>{money(activePoint.total_value)}</strong>

                {hoveredIndex === null && (
                  <ChangeBadge
                    change={rangeChange?.change}
                    changePct={rangeChange?.changePct}
                  />
                )}
              </div>

              <span className="chart-date-label">
                {hoveredIndex !== null
                  ? formatDate(activePoint.date)
                  : `As of ${formatDate(latest.date)}`}
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

          <LineChart
            points={filteredHistory}
            hoveredIndex={hoveredIndex}
            onHover={setHoveredIndex}
          />

          {partialBefore && (
            <p className="chart-note">
              Values your current cards at each day&apos;s market prices.
              Before {formatDate(partialBefore)}, cards added later are held
              flat at their first known price.
            </p>
          )}
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

function round2(value) {
  return Math.round(value * 100) / 100;
}

export default Dashboard;
