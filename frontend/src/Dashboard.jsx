import { useEffect, useRef, useState } from "react";

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

// Size of an element in CSS pixels, kept current as it resizes, so the
// chart draws at its real size instead of stretching a fixed drawing.
function useElementSize(ref) {
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const element = ref.current;
    if (!element) {
      return undefined;
    }

    function update() {
      const rect = element.getBoundingClientRect();
      setSize((current) =>
        current.width === Math.round(rect.width) &&
        current.height === Math.round(rect.height)
          ? current
          : { width: Math.round(rect.width), height: Math.round(rect.height) }
      );
    }

    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);

  return size;
}

// Round tick spacing (1, 2, 2.5, 5 x 10^n) for about `count` gridlines.
function valueTicks(min, max, count) {
  if (max - min < 1e-9) {
    const pad = Math.max(Math.abs(max) * 0.02, 1);
    min -= pad;
    max += pad;
  }

  const rough = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const step =
    (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10) *
    power;

  const low = Math.floor(min / step) * step;
  const high = Math.ceil(max / step) * step;
  const ticks = [];
  for (let value = low; value <= high + step / 2; value += step) {
    ticks.push(Number(value.toFixed(6)));
  }

  return { low, high, step, ticks };
}

// Smooth line through the points that never overshoots them (monotone
// cubic), so a flat stretch stays flat and peaks aren't exaggerated.
function smoothPath(coords) {
  const count = coords.length;
  if (count === 0) {
    return "";
  }
  if (count < 3) {
    return coords
      .map((c, index) => `${index ? "L" : "M"}${c.x.toFixed(2)},${c.y.toFixed(2)}`)
      .join("");
  }

  const slopes = [];
  for (let i = 0; i < count - 1; i += 1) {
    const dx = coords[i + 1].x - coords[i].x;
    slopes.push(dx ? (coords[i + 1].y - coords[i].y) / dx : 0);
  }

  const tangents = [slopes[0]];
  for (let i = 1; i < count - 1; i += 1) {
    tangents.push(slopes[i - 1] * slopes[i] <= 0 ? 0 : (slopes[i - 1] + slopes[i]) / 2);
  }
  tangents.push(slopes[count - 2]);

  for (let i = 0; i < count - 1; i += 1) {
    if (!slopes[i]) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
      continue;
    }
    const a = tangents[i] / slopes[i];
    const b = tangents[i + 1] / slopes[i];
    const length = a * a + b * b;
    if (length > 9) {
      const scale = 3 / Math.sqrt(length);
      tangents[i] = scale * a * slopes[i];
      tangents[i + 1] = scale * b * slopes[i];
    }
  }

  let path = `M${coords[0].x.toFixed(2)},${coords[0].y.toFixed(2)}`;
  for (let i = 0; i < count - 1; i += 1) {
    const p0 = coords[i];
    const p1 = coords[i + 1];
    const third = (p1.x - p0.x) / 3;
    path +=
      `C${(p0.x + third).toFixed(2)},${(p0.y + tangents[i] * third).toFixed(2)} ` +
      `${(p1.x - third).toFixed(2)},${(p1.y - tangents[i + 1] * third).toFixed(2)} ` +
      `${p1.x.toFixed(2)},${p1.y.toFixed(2)}`;
  }
  return path;
}

function nearestIndex(times, target) {
  let low = 0;
  let high = times.length - 1;
  while (high - low > 1) {
    const mid = (low + high) >> 1;
    if (times[mid] <= target) {
      low = mid;
    } else {
      high = mid;
    }
  }
  return Math.abs(times[high] - target) < Math.abs(times[low] - target) ? high : low;
}

function LineChart({ points, hoveredIndex, onHover, formatLabel, formatAxis }) {
  const wrapRef = useRef(null);
  const { width, height } = useElementSize(wrapRef);

  // A point picked by touch stays put; tapping anywhere off the chart
  // clears it.
  useEffect(() => {
    if (hoveredIndex === null) {
      return undefined;
    }

    function handlePointerDown(event) {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) {
        onHover(null);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [hoveredIndex, onHover]);
  const ready = width > 0 && height > 0 && points.length > 0;

  const values = points.map((point) => point.value);
  const times = points.map((point) => new Date(point.timestamp).getTime());
  const isPositive = values[values.length - 1] >= values[0];
  const color = isPositive ? "#5fe3a4" : "#f18a8a";

  // Layout in real pixels. Fewer gridlines and dates on a narrow screen.
  const compact = width < 480;
  const yTicks = valueTicks(Math.min(...values), Math.max(...values), compact ? 3 : 4);
  const decimals = yTicks.step < 1 ? 2 : 0;
  const tickLabel = (value) =>
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(value);
  const labels = yTicks.ticks.map(tickLabel);

  const pad = {
    top: 12,
    bottom: 24,
    left: 4,
    right: Math.max(...labels.map((label) => label.length)) * 6.6 + 12,
  };
  const plotWidth = Math.max(width - pad.left - pad.right, 1);
  const plotHeight = Math.max(height - pad.top - pad.bottom, 1);
  const firstTime = times[0];
  const timeSpan = times[times.length - 1] - firstTime || 1;

  const x = (time) => pad.left + ((time - firstTime) / timeSpan) * plotWidth;
  const y = (value) =>
    pad.top + (1 - (value - yTicks.low) / (yTicks.high - yTicks.low)) * plotHeight;

  const coords = points.map((point, index) => ({
    x: points.length > 1 ? x(times[index]) : pad.left + plotWidth / 2,
    y: y(point.value),
  }));
  const linePath = smoothPath(coords);
  const bottom = pad.top + plotHeight;
  const areaPath =
    coords.length > 1
      ? `${linePath}L${coords[coords.length - 1].x.toFixed(2)},${bottom}L${coords[0].x.toFixed(2)},${bottom}Z`
      : "";

  const xTickCount = Math.max(2, Math.min(compact ? 3 : 5, Math.floor(plotWidth / 110)));
  const xTicks =
    points.length > 1
      ? Array.from({ length: xTickCount }, (_, i) => firstTime + (timeSpan * i) / (xTickCount - 1))
      : [];

  function pointerToIndex(event) {
    if (points.length < 2) {
      return 0;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const px = Math.min(Math.max(event.clientX - rect.left, pad.left), pad.left + plotWidth);
    return nearestIndex(times, firstTime + ((px - pad.left) / plotWidth) * timeSpan);
  }

  function handleKeyDown(event) {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const current = hoveredIndex ?? points.length - 1;
      const next = current + (event.key === "ArrowRight" ? 1 : -1);
      onHover(Math.max(0, Math.min(points.length - 1, next)));
    } else if (event.key === "Escape") {
      onHover(null);
    }
  }

  const hovered = hoveredIndex !== null && coords[hoveredIndex] ? coords[hoveredIndex] : null;
  const gradientId = isPositive ? "chart-fill-up" : "chart-fill-down";

  return (
    <div className="chart-svg-wrap" ref={wrapRef}>
      {ready && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Portfolio value over the selected range. Use the arrow keys to step through it."
          tabIndex={0}
          onPointerDown={(event) => onHover(pointerToIndex(event))}
          onPointerMove={(event) => {
            // Mouse: follow the cursor. Touch/pen: scrub while pressed.
            if (event.pointerType === "mouse" || event.buttons) {
              onHover(pointerToIndex(event));
            }
          }}
          // Touch: the point stays where you lift your finger, until you tap
          // elsewhere on the chart or anywhere off it.
          onPointerLeave={(event) => event.pointerType === "mouse" && onHover(null)}
          // A cancelled touch is the page scrolling, not a tap.
          onPointerCancel={() => onHover(null)}
          onKeyDown={handleKeyDown}
          onBlur={() => onHover(null)}
        >
          <defs>
            <linearGradient id="chart-fill-up" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(95,227,164,0.28)" />
              <stop offset="100%" stopColor="rgba(95,227,164,0)" />
            </linearGradient>
            <linearGradient id="chart-fill-down" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(241,138,138,0.26)" />
              <stop offset="100%" stopColor="rgba(241,138,138,0)" />
            </linearGradient>
          </defs>

          {yTicks.ticks.map((tick, index) => (
            <g key={tick}>
              <line
                className="chart-grid"
                x1={pad.left}
                x2={pad.left + plotWidth}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text
                className="chart-axis-label"
                x={width - 4}
                y={y(tick)}
                textAnchor="end"
                dominantBaseline="middle"
              >
                {labels[index]}
              </text>
            </g>
          ))}

          {xTicks.map((time, index) => (
            <text
              key={time}
              className="chart-axis-label"
              x={x(time)}
              y={height - 6}
              textAnchor={index === 0 ? "start" : index === xTicks.length - 1 ? "end" : "middle"}
            >
              {formatAxis(time)}
            </text>
          ))}

          {/* Where the range started, for reference. */}
          {points.length > 1 && (
            <line
              className="chart-baseline"
              x1={pad.left}
              x2={pad.left + plotWidth}
              y1={coords[0].y}
              y2={coords[0].y}
            />
          )}

          {areaPath && <path d={areaPath} fill={`url(#${gradientId})`} />}

          {coords.length > 1 ? (
            <path
              d={linePath}
              fill="none"
              stroke={color}
              strokeWidth="2.25"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ) : (
            <circle cx={coords[0].x} cy={coords[0].y} r="4" fill={color} />
          )}

          {hovered && (
            <g pointerEvents="none">
              <line
                className="chart-crosshair"
                x1={hovered.x}
                x2={hovered.x}
                y1={pad.top}
                y2={bottom}
              />
              <circle cx={hovered.x} cy={hovered.y} r="9" fill={color} opacity="0.18" />
              <circle
                cx={hovered.x}
                cy={hovered.y}
                r="4.5"
                fill="#0b0f17"
                stroke={color}
                strokeWidth="2.5"
              />
            </g>
          )}
        </svg>
      )}

      {ready && hovered && (
        <div
          className="chart-tooltip"
          style={{
            // Centered over the point, kept inside the chart at the edges,
            // and below the point when it's near the top.
            left: `${Math.min(Math.max(hovered.x, 64), width - 64)}px`,
            top: `${hovered.y}px`,
            transform: `translate(-50%, ${hovered.y < 64 ? "14px" : "calc(-100% - 14px)"})`,
          }}
        >
          <strong>{money(points[hoveredIndex].value)}</strong>
          <span>{formatLabel(points[hoveredIndex].timestamp)}</span>
        </div>
      )}
    </div>
  );
}

function DashboardHeading({ actions }) {
  return (
    <div className="dashboard-heading">
      <div>
        <p className="eyebrow">DASHBOARD</p>
        <h2>Portfolio performance</h2>
      </div>

      {actions}
    </div>
  );
}

// `actions` (the sync buttons) sit at the top right of every state of the
// panel, so they're reachable even while it loads or fails.
function Dashboard({ refreshKey, actions }) {
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
        <DashboardHeading actions={actions} />

        <div className="empty-state">Loading dashboard…</div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="dashboard-panel">
        <DashboardHeading actions={actions} />

        <div className="status error">{error}</div>
      </section>
    );
  }

  if (!data?.ranges?.ALL?.points?.length) {
    return (
      <section className="dashboard-panel">
        <DashboardHeading actions={actions} />

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
      <DashboardHeading actions={actions} />

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
                    activePoint ? activePoint.value : data.current_total_value
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
              formatLabel={(timestamp) =>
                formatTimestamp(timestamp, TIMED_RANGES.has(range))
              }
              formatAxis={(time) =>
                new Date(time).toLocaleString(
                  "en-US",
                  range === "1D"
                    ? { hour: "numeric", minute: "2-digit" }
                    : range === "1Y" || range === "ALL"
                      ? { month: "short", day: "numeric", year: "2-digit" }
                      : { month: "short", day: "numeric" }
                )
              }
            />
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

export default Dashboard;
