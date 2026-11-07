import { useEffect, useMemo, useState } from "react";

import { getGradingOpportunities } from "./api.js";
import { cardTitle, money, variantLabel } from "./format.js";

// Your grading cost per card, remembered on this device. Unset = the
// backend's default (GRADING_COST, $30).
const COST_KEY = "gradingCostPerCard";

function savedCost() {
  try {
    const value = window.localStorage.getItem(COST_KEY);
    return value === null || value === "" ? null : Number(value);
  } catch {
    return null;
  }
}

function saveCost(value) {
  try {
    if (value === null) {
      window.localStorage.removeItem(COST_KEY);
    } else {
      window.localStorage.setItem(COST_KEY, String(value));
    }
  } catch {
    // Not saved; the cost still applies until the page reloads.
  }
}

function signedMoney(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value < 0 ? "−" : "+"}${money(Math.abs(value))}`;
}

function tone(value) {
  return value > 0 ? "positive" : value < 0 ? "negative" : "flat";
}

function multiple(value) {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}x`;
}

const LABEL_TONES = {
  "Excellent Opportunity": "excellent",
  "Strong Opportunity": "strong",
  "Moderate Opportunity": "moderate",
  "High Risk": "risky",
  "Not Worth Grading": "skip",
};

const SORTS = {
  score: { label: "Opportunity score", value: (row) => row.score },
  psa10_value: { label: "PSA 10 value", value: (row) => row.psa10_value },
  raw_value: { label: "Raw value", value: (row) => row.raw_value },
  psa10_multiple: { label: "PSA 10 multiple", value: (row) => row.psa10_multiple },
  psa10_net: { label: "PSA 10 net upside", value: (row) => row.psa10_net },
  psa9_net: { label: "PSA 9 outcome", value: (row) => row.psa9_net },
  name: { label: "Card name", value: (row) => cardTitle(row.card).toLowerCase() },
};

function sortRows(rows, key) {
  const value = SORTS[key].value;

  return [...rows].sort((a, b) => {
    // Cards without graded prices always go last.
    if (a.available !== b.available) {
      return a.available ? -1 : 1;
    }

    const left = value(a);
    const right = value(b);
    if (left === null || left === undefined) return 1;
    if (right === null || right === undefined) return -1;
    if (typeof left === "string") return left.localeCompare(right);
    return right - left;
  });
}

function zoomDetails(row) {
  return {
    grade: "RAW",
    valueEach: row.raw_value,
    variant: row.card.variant,
    grading: row,
  };
}

function LabelPill({ row }) {
  if (!row.available) {
    return <span className="grading-label unavailable">Graded pricing unavailable</span>;
  }

  return (
    <span className={`grading-label ${LABEL_TONES[row.label] || ""}`} title={row.risk_note}>
      {row.label}
    </span>
  );
}

function OpportunityTable({ rows, onZoom }) {
  return (
    <div className="collection-scroll">
      <table className="collection-table grading-table">
        <thead>
          <tr>
            <th>Card</th>
            <th>Raw</th>
            <th>PSA 9</th>
            <th>PSA 10</th>
            <th>PSA 10 premium</th>
            <th>PSA 10 net</th>
            <th>PSA 9 net</th>
            <th>Score</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((row) => (
            <tr
              key={row.card.id}
              className="grading-row"
              onClick={() => onZoom(row.card, zoomDetails(row))}
            >
              <td>
                <div className="table-card-cell">
                  {row.card.image_url && <img src={row.card.image_url} alt="" />}

                  <div>
                    <strong>{cardTitle(row.card)}</strong>
                    <span>
                      {row.card.set_name || "Unknown set"}
                      {variantLabel(row.card.variant) ? ` · ${variantLabel(row.card.variant)}` : ""}
                      {row.quantity > 1 ? ` · ×${row.quantity}` : ""}
                    </span>
                    <LabelPill row={row} />
                  </div>
                </div>
              </td>

              <td>{money(row.raw_value)}</td>

              {row.available ? (
                <>
                  <td>
                    {money(row.psa9_value)}
                    {row.psa9_multiple !== null && (
                      <span className="grading-sub">{multiple(row.psa9_multiple)}</span>
                    )}
                  </td>
                  <td>
                    {money(row.psa10_value)}
                    <span className="grading-sub">{multiple(row.psa10_multiple)}</span>
                  </td>
                  <td>
                    {signedMoney(row.psa10_premium)}
                    <span className="grading-sub">
                      {row.psa10_premium_pct !== null ? `${row.psa10_premium_pct.toFixed(0)}%` : ""}
                    </span>
                  </td>
                  <td className={tone(row.psa10_net)}>{signedMoney(row.psa10_net)}</td>
                  <td className={tone(row.psa9_net)}>{signedMoney(row.psa9_net)}</td>
                  <td>
                    <strong>{row.score}</strong>
                  </td>
                </>
              ) : (
                <td colSpan={6} className="grading-unavailable">
                  {row.unavailable_reason}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Raw cards you own, ranked by how much a PSA 10 (and a PSA 9) would be
// worth over the raw card plus grading. Built from stored prices only.
function GradingOpportunities({ refreshKey, onZoom }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [cost, setCost] = useState(savedCost);
  const [costDraft, setCostDraft] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [sortKey, setSortKey] = useState("score");
  const [filters, setFilters] = useState({
    minPsa10: "",
    minMultiple: "",
    minNet: "",
    set: "",
    language: "",
    variant: "",
  });

  useEffect(() => {
    let cancelled = false;

    getGradingOpportunities(cost)
      .then((result) => {
        if (!cancelled) {
          setReport(result);
          setCostDraft(String(result.grading_cost));
          setError("");
        }
      })
      .catch((err) => !cancelled && setError(err.message));

    return () => {
      cancelled = true;
    };
  }, [refreshKey, cost]);

  useEffect(() => {
    if (!showAll) {
      return undefined;
    }
    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setShowAll(false);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [showAll]);

  const rows = report?.opportunities || [];

  const options = useMemo(() => {
    const unique = (pick) =>
      [...new Set(rows.map(pick).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    return {
      sets: unique((row) => row.card.set_name),
      languages: unique((row) => row.language),
      variants: unique((row) => row.card.variant),
    };
  }, [rows]);

  const visible = useMemo(() => {
    const atLeast = (value, min) =>
      min === "" || (value !== null && value !== undefined && value >= Number(min));

    return sortRows(
      rows.filter(
        (row) =>
          atLeast(row.psa10_value, filters.minPsa10) &&
          atLeast(row.psa10_multiple, filters.minMultiple) &&
          atLeast(row.psa10_net, filters.minNet) &&
          (!filters.set || row.card.set_name === filters.set) &&
          (!filters.language || row.language === filters.language) &&
          (!filters.variant || row.card.variant === filters.variant)
      ),
      sortKey
    );
  }, [rows, filters, sortKey]);

  function applyCost(event) {
    event.preventDefault();
    const value = costDraft.trim() === "" ? null : Number(costDraft);
    if (value !== null && (Number.isNaN(value) || value < 0)) {
      return;
    }
    saveCost(value);
    setCost(value);
  }

  const top = rows.filter((row) => row.available).slice(0, 5);
  const setFilter = (key) => (event) =>
    setFilters((current) => ({ ...current, [key]: event.target.value }));

  return (
    <section className="analytics-panel grading-panel">
      <div className="grading-heading">
        <div>
          <p className="eyebrow">GRADING OPPORTUNITIES</p>
          <h3>Raw cards with grading upside</h3>
          {report && (
            <small>
              {report.available} of {report.raw_cards} raw cards have graded prices · grading
              cost {money(report.grading_cost)} per card
            </small>
          )}
        </div>

        {rows.length > 0 && (
          <button type="button" className="secondary-button grading-view-all" onClick={() => setShowAll(true)}>
            View all
          </button>
        )}
      </div>

      {error && <div className="status error">{error}</div>}

      {report && top.length === 0 && (
        <div className="empty-state small">
          None of your raw cards have stored PSA 9 / PSA 10 prices yet.
        </div>
      )}

      {top.length > 0 && <OpportunityTable rows={top} onZoom={onZoom} />}

      <p className="analytics-note">
        Market opportunity only. Actual grade depends on card condition.
      </p>

      {showAll && report && (
        <div
          className="modal-backdrop"
          onClick={(event) => event.target === event.currentTarget && setShowAll(false)}
        >
          <div className="modal grading-modal" role="dialog" aria-modal="true" aria-label="Grading opportunities">
            <div className="modal-heading">
              <div>
                <p className="eyebrow">GRADING OPPORTUNITIES</p>
                <h2>All raw cards</h2>
                <span>
                  {report.available} of {report.raw_cards} have graded prices
                </span>
              </div>

              <button type="button" className="close-button" aria-label="Close" onClick={() => setShowAll(false)}>
                ×
              </button>
            </div>

            <div className="grading-controls">
              <form className="grading-cost" onSubmit={applyCost}>
                <label>
                  Grading cost per card
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={costDraft}
                    onChange={(event) => setCostDraft(event.target.value)}
                    onBlur={applyCost}
                  />
                </label>
              </form>

              <label>
                Sort by
                <select value={sortKey} onChange={(event) => setSortKey(event.target.value)}>
                  {Object.entries(SORTS).map(([key, sort]) => (
                    <option key={key} value={key}>
                      {sort.label}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Min PSA 10 value
                <input type="number" min="0" value={filters.minPsa10} onChange={setFilter("minPsa10")} />
              </label>

              <label>
                Min PSA 10 multiple
                <input type="number" min="0" step="0.5" value={filters.minMultiple} onChange={setFilter("minMultiple")} />
              </label>

              <label>
                Min net upside
                <input type="number" value={filters.minNet} onChange={setFilter("minNet")} />
              </label>

              {[
                ["set", "Set", options.sets, (value) => value],
                ["language", "Language", options.languages, (value) => value],
                ["variant", "Variant", options.variants, (value) => variantLabel(value) || value],
              ].map(([key, label, choices, show]) => (
                <label key={key}>
                  {label}
                  <select value={filters[key]} onChange={setFilter(key)}>
                    <option value="">All</option>
                    {choices.map((choice) => (
                      <option key={choice} value={choice}>
                        {show(choice)}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>

            {visible.length === 0 ? (
              <div className="empty-state small">No cards match these filters.</div>
            ) : (
              <OpportunityTable rows={visible} onZoom={onZoom} />
            )}

            <p className="analytics-note">{report.score_explanation}</p>
          </div>
        </div>
      )}
    </section>
  );
}

export default GradingOpportunities;
