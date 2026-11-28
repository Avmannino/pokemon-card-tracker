import { useEffect, useMemo, useState } from "react";

import {
  addSportsCard,
  deleteSportsCard,
  getSportsCollection,
  getSportsPrices,
  refreshSportsPrices,
  searchSportsCards,
  sportsImageUrl,
  updateSportsCard,
} from "./api";
import { gradeLabel, money } from "./format";

// The Sports section: your sports cards priced by CardSight AI (eBay
// auction sales from the past year), separate from the Pokemon collection.
// CardSight's free plan is 750 calls a month: a search is one call, and
// checking or refreshing up to 100 cards is one call.

const OWNED_GRADES = ["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"];
const SHOWN_GRADES = ["RAW", "PSA_8", "PSA_9", "PSA_10"];

const LEAGUES = {
  MLB: "Baseball",
  NFL: "Football",
  NBA: "Basketball",
  NHL: "Hockey",
};

const LABEL_TONES = {
  "Excellent Opportunity": "excellent",
  "Strong Opportunity": "strong",
  "Moderate Opportunity": "moderate",
  "High Risk": "risky",
  "Not Worth Grading": "skip",
};

const SORTS = [
  { key: "value", label: "Your value", numeric: true, value: (item) => item.value_total },
  { key: "grading", label: "Grading score", numeric: true, value: (item) => item.grading?.score },
  { key: "psa10", label: "PSA 10", numeric: true, value: (item) => item.prices?.PSA_10?.value },
  { key: "name", label: "Player", value: (item) => item.card.name?.toLowerCase() },
  { key: "year", label: "Year", value: (item) => item.card.year },
];

function cardLine(card) {
  return [card.year, card.release_name, card.set_name && card.set_name !== "Base Set" ? card.set_name : null]
    .filter(Boolean)
    .join(" ");
}

function cardName(card) {
  return card.number ? `${card.name} #${card.number}` : card.name;
}

function leagueName(league) {
  return LEAGUES[league] || league || "Other";
}

function shortDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function refreshedText(iso) {
  if (!iso) return "not refreshed yet";
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// eBay serves bigger versions of a listing photo by size code.
function photo(card, size = 500) {
  return card.photo_url ? card.photo_url.replace(/s-l\d+\./, `s-l${size}.`) : null;
}

function signedMoney(value) {
  if (value === null || value === undefined) return "—";
  return `${value < 0 ? "−" : "+"}${money(Math.abs(value))}`;
}

function GradingPill({ grading }) {
  if (!grading) return null;
  if (!grading.available) {
    return (
      <span className="grading-label unavailable" title={grading.unavailable_reason || ""}>
        {grading.unavailable_reason || "No graded sales"}
      </span>
    );
  }
  return (
    <span className={`grading-label ${LABEL_TONES[grading.label] || ""}`} title={grading.risk_note}>
      {grading.label} · {grading.score}
    </span>
  );
}

function CardThumb({ card, onClick }) {
  const src = photo(card, 225);
  return src ? (
    <img
      src={src}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      className="thumb-clickable"
      onClick={onClick}
    />
  ) : (
    <button type="button" className="sports-thumb-placeholder" onClick={onClick} title="Details">
      {card.year || "—"}
    </button>
  );
}

// Raw / PSA 8 / PSA 9 / PSA 10 values with their sale counts, plus any
// other graders CardSight has sales for.
function GradeTable({ grades, owned }) {
  const others = Object.keys(grades || {}).filter(
    (key) => !SHOWN_GRADES.includes(key) && key !== "PSA_7"
  );

  return (
    <>
      <div className="sports-grade-grid">
        {SHOWN_GRADES.map((key) => {
          const grade = grades?.[key];
          return (
            <div key={key} className={`sports-grade ${owned === key ? "owned" : ""}`}>
              <span>{gradeLabel(key)}</span>
              <strong>{money(grade?.value)}</strong>
              <small>
                {grade ? `${grade.sales} sale${grade.sales === 1 ? "" : "s"}` : "no sales"}
              </small>
            </div>
          );
        })}
      </div>

      {(others.length > 0 || grades?.PSA_7) && (
        <div className="sports-other-grades">
          {["PSA_7", ...others]
            .filter((key) => grades?.[key])
            .map((key) => (
              <span key={key}>
                {gradeLabel(key)} {money(grades[key].value)}
                <small> ({grades[key].sales})</small>
              </span>
            ))}
        </div>
      )}
    </>
  );
}

function emptySearch() {
  return { name: "", year: "", number: "", set: "" };
}

function emptyAdd(card = null) {
  return {
    card,
    ownership_grade: "RAW",
    quantity: 1,
    manual_value: "",
    manual_source_url: "",
    notes: "",
  };
}

export default function SportsCards() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [view, setView] = useState(() =>
    window.matchMedia?.("(max-width: 620px)").matches ? "gallery" : "table"
  );
  const [query, setQuery] = useState("");
  const [league, setLeague] = useState("ALL");
  const [sort, setSort] = useState({ key: "value", direction: "desc" });
  const [savingQty, setSavingQty] = useState({});

  // Add Card window: search, results (optionally priced), then the card.
  const [addOpen, setAddOpen] = useState(false);
  const [search, setSearch] = useState(emptySearch());
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [resultPrices, setResultPrices] = useState({});
  const [pricing, setPricing] = useState(false);
  const [addForm, setAddForm] = useState(emptyAdd());
  const [adding, setAdding] = useState(false);
  const [modalError, setModalError] = useState("");

  const [detailsItem, setDetailsItem] = useState(null);
  const [editForm, setEditForm] = useState(null);
  const [savingEdit, setSavingEdit] = useState(false);

  async function load({ quiet = false } = {}) {
    if (!quiet) setLoading(true);
    try {
      setData(await getSportsCollection());
      setLoadError("");
    } catch (err) {
      setLoadError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const items = data?.items || [];
  const usage = data?.usage;

  const leagues = useMemo(
    () => [...new Set(items.map((item) => item.card.league || "OTHER"))].sort(),
    [items]
  );

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const sortDef = SORTS.find((each) => each.key === sort.key) || SORTS[0];
    const direction = sort.direction === "asc" ? 1 : -1;

    return items
      .filter((item) => league === "ALL" || (item.card.league || "OTHER") === league)
      .filter((item) => {
        if (!words.length) return true;
        const text = [
          item.card.name,
          item.card.number,
          item.card.year,
          item.card.release_name,
          item.card.set_name,
          gradeLabel(item.ownership_grade),
          leagueName(item.card.league),
          item.notes,
        ]
          .join(" ")
          .toLowerCase();
        return words.every((word) => text.includes(word));
      })
      .sort((a, b) => {
        const left = sortDef.value(a);
        const right = sortDef.value(b);
        const leftMissing = left === null || left === undefined;
        const rightMissing = right === null || right === undefined;
        if (leftMissing || rightMissing) {
          return leftMissing && rightMissing ? 0 : leftMissing ? 1 : -1;
        }
        if (typeof left === "string") return left.localeCompare(right) * direction;
        return (left - right) * direction;
      });
  }, [items, query, league, sort]);

  function toggleSort(key) {
    const def = SORTS.find((each) => each.key === key);
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: def.numeric ? "desc" : "asc" }
    );
  }

  function sortHeader(key, label) {
    return (
      <button
        type="button"
        className={sort.key === key ? "sort-button active" : "sort-button"}
        onClick={() => toggleSort(key)}
      >
        {label}
        <span className="sort-caret">
          {sort.key === key ? (sort.direction === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    );
  }

  async function handleRefresh() {
    setRefreshing(true);
    setError("");
    setMessage("");
    try {
      const result = await refreshSportsPrices();
      setMessage(
        `Prices refreshed: ${result.priced} of ${result.cards} cards had sales (${result.calls} CardSight call${result.calls === 1 ? "" : "s"}).`
      );
      await load({ quiet: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setRefreshing(false);
    }
  }

  async function changeQuantity(item, quantity) {
    setSavingQty((current) => ({ ...current, [item.id]: true }));
    setError("");
    try {
      await updateSportsCard(item.id, { quantity });
      await load({ quiet: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingQty((current) => ({ ...current, [item.id]: false }));
    }
  }

  async function handleRemove(item) {
    if (!window.confirm(`Are you sure you want to remove ${cardName(item.card)} from Sports?`)) {
      return;
    }
    setError("");
    try {
      await deleteSportsCard(item.id);
      setMessage(`Removed ${cardName(item.card)}.`);
      await load({ quiet: true });
    } catch (err) {
      setError(err.message);
    }
  }

  function openAdd() {
    setAddOpen(true);
    setModalError("");
    setAddForm(emptyAdd());
  }

  function closeAdd() {
    setAddOpen(false);
    setAddForm(emptyAdd());
    setModalError("");
  }

  async function handleSearch(event) {
    event.preventDefault();
    if (search.name.trim().length < 2) {
      setModalError("Enter at least 2 letters of the player's name.");
      return;
    }
    setSearching(true);
    setModalError("");
    try {
      const response = await searchSportsCards(search);
      setResults(response.results || []);
      setData((current) => (current ? { ...current, usage: response.usage } : current));
    } catch (err) {
      setModalError(err.message);
    } finally {
      setSearching(false);
    }
  }

  async function priceCards(ids) {
    const response = await getSportsPrices(ids);
    setResultPrices((current) => ({ ...current, ...response.prices }));
    setData((current) => (current ? { ...current, usage: response.usage } : current));
    return response.prices;
  }

  async function priceAllResults() {
    const ids = (results || [])
      .map((card) => card.cardsight_id)
      .filter((id) => !resultPrices[id])
      .slice(0, 100);
    if (!ids.length) return;
    setPricing(true);
    setModalError("");
    try {
      await priceCards(ids);
    } catch (err) {
      setModalError(err.message);
    } finally {
      setPricing(false);
    }
  }

  async function selectCard(card) {
    setAddForm(emptyAdd(card));
    setModalError("");
    if (!resultPrices[card.cardsight_id]) {
      setPricing(true);
      try {
        await priceCards([card.cardsight_id]);
      } catch (err) {
        setModalError(err.message);
      } finally {
        setPricing(false);
      }
    }
  }

  async function handleAdd(event) {
    event.preventDefault();
    setAdding(true);
    setModalError("");
    try {
      const { card } = addForm;
      const response = await addSportsCard({
        card: {
          cardsight_id: card.cardsight_id,
          name: card.name,
          number: card.number,
          year: card.year,
          release_name: card.release_name,
          set_name: card.set_name,
          attributes: card.attributes || [],
        },
        ownership_grade: addForm.ownership_grade,
        quantity: Number(addForm.quantity) || 1,
        notes: addForm.notes.trim() || null,
        manual_value: addForm.manual_value === "" ? null : Number(addForm.manual_value),
        manual_source_url: addForm.manual_source_url.trim() || null,
      });
      setMessage(
        response.merged
          ? `Added to the ${cardName(card)} you already had.`
          : `Added ${cardName(card)} to Sports.`
      );
      if (response.price_error) {
        setError(`Added, but it couldn't be priced yet: ${response.price_error}`);
      }
      setAddForm(emptyAdd());
      await load({ quiet: true });
    } catch (err) {
      setModalError(err.message);
    } finally {
      setAdding(false);
    }
  }

  function openEdit(item) {
    setEditForm({
      item,
      ownership_grade: item.ownership_grade,
      manual_value: item.manual_value ?? "",
      manual_source_url: item.manual_source_url || "",
      notes: item.notes || "",
    });
    setModalError("");
  }

  async function handleEdit(event, clearValue = false) {
    event.preventDefault();
    setSavingEdit(true);
    setModalError("");
    try {
      await updateSportsCard(editForm.item.id, {
        ownership_grade: editForm.ownership_grade,
        manual_value:
          clearValue || editForm.manual_value === "" ? null : Number(editForm.manual_value),
        manual_source_url: clearValue ? null : editForm.manual_source_url.trim() || null,
        notes: editForm.notes.trim() || null,
      });
      setEditForm(null);
      setMessage("Saved.");
      await load({ quiet: true });
    } catch (err) {
      setModalError(err.message);
    } finally {
      setSavingEdit(false);
    }
  }

  function valueCell(item) {
    if (item.value_source === "manual") {
      return item.manual_source_url ? (
        <a className="manual-source-link" href={item.manual_source_url} target="_blank" rel="noreferrer">
          Your value
        </a>
      ) : (
        <span className="manual-source-link">Your value</span>
      );
    }
    const grade = item.prices?.[item.ownership_grade];
    if (!grade) {
      return (
        <button type="button" className="manual-source-link source-count-link" onClick={() => openEdit(item)}>
          {item.priced_at ? "No sales · set value" : "Not priced yet"}
        </button>
      );
    }
    return (
      <button
        type="button"
        className="manual-source-link source-count-link"
        onClick={() => setDetailsItem(item)}
        title={`Median of the last ${grade.used} sales; last sold ${shortDate(grade.last_sale)}`}
      >
        {grade.sales} sale{grade.sales === 1 ? "" : "s"}
      </button>
    );
  }

  const selected = addForm.card;
  const selectedPrices = selected ? resultPrices[selected.cardsight_id] : null;
  const lastRefresh = data?.last_refresh?.finished_at;

  return (
    <section className="collection-panel sports-panel" id="sports">
      <div className="panel-heading sports-heading">
        <div>
          <p className="eyebrow">SPORTS CARDS</p>
          <h2>Sports</h2>
          <div className="sports-total">
            <strong>{money(data?.summary?.total_value || 0)}</strong>
            <span>
              {data?.summary?.card_count || 0} cards · prices refresh daily · last{" "}
              {refreshedText(lastRefresh)}
            </span>
          </div>
        </div>

        <div className="sports-actions">
          <div className="refresh-buttons">
            <button type="button" className="sync-button primary" onClick={openAdd} disabled={!!loadError}>
              + Add Card
            </button>
            <button
              type="button"
              className="sync-button"
              onClick={handleRefresh}
              disabled={refreshing || !items.length || !!loadError}
              title="Reprices every sports card: one CardSight call per 100 cards"
            >
              {refreshing ? "Refreshing…" : "Refresh Prices"}
            </button>
          </div>
          {usage && (
            <small className="sports-usage">
              CardSight calls this month: {usage.calls} of {usage.limit}
            </small>
          )}
        </div>
      </div>

      {(message || error) && (
        <div className="status-area" aria-live="polite">
          {message && <div className="status success">{message}</div>}
          {error && <div className="status error">{error}</div>}
        </div>
      )}

      {loadError ? (
        <div className="empty-state">{loadError}</div>
      ) : loading ? (
        <div className="empty-state">Loading sports cards…</div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          {data && !data.configured
            ? "Add CARDSIGHT_API_KEY to the backend environment to start."
            : "Use + Add Card to look up your first sports card and check its grading potential."}
        </div>
      ) : (
        <>
          <div className="collection-search">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search your sports cards by player, year, set, number..."
              aria-label="Search your sports cards"
            />

            {leagues.length > 1 && (
              <div className="range-tabs" role="group" aria-label="League">
                {["ALL", ...leagues].map((key) => (
                  <button
                    type="button"
                    key={key}
                    className={league === key ? "active" : ""}
                    aria-pressed={league === key}
                    onClick={() => setLeague(key)}
                  >
                    {key === "ALL" ? "All" : leagueName(key === "OTHER" ? null : key)}
                  </button>
                ))}
              </div>
            )}

            <div className="range-tabs" role="group" aria-label="Sports view">
              {["table", "gallery"].map((key) => (
                <button
                  type="button"
                  key={key}
                  className={view === key ? "active" : ""}
                  aria-pressed={view === key}
                  onClick={() => setView(key)}
                >
                  {key === "table" ? "Table" : "Gallery"}
                </button>
              ))}
            </div>

            <div className="mobile-sort">
              <select
                value={sort.key}
                onChange={(event) => {
                  const def = SORTS.find((each) => each.key === event.target.value);
                  setSort({ key: def.key, direction: def.numeric ? "desc" : "asc" });
                }}
                aria-label="Sort by"
              >
                {SORTS.map((each) => (
                  <option key={each.key} value={each.key}>
                    Sort: {each.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="secondary-button"
                aria-label={sort.direction === "asc" ? "Ascending" : "Descending"}
                onClick={() =>
                  setSort((current) => ({
                    ...current,
                    direction: current.direction === "asc" ? "desc" : "asc",
                  }))
                }
              >
                {sort.direction === "asc" ? "↑" : "↓"}
              </button>
            </div>
          </div>

          {shown.length === 0 ? (
            <div className="empty-state">No sports cards match "{query.trim()}".</div>
          ) : view === "gallery" ? (
            <div className="collection-gallery">
              {shown.map((item) => (
                <article className="gallery-card" key={item.id}>
                  {photo(item.card) ? (
                    <img
                      src={photo(item.card)}
                      alt={cardName(item.card)}
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      className="thumb-clickable"
                      onClick={() => setDetailsItem(item)}
                    />
                  ) : (
                    <button
                      type="button"
                      className="gallery-placeholder sports-gallery-placeholder"
                      onClick={() => setDetailsItem(item)}
                    >
                      <strong>{item.card.year}</strong>
                      <span>{item.card.release_name}</span>
                    </button>
                  )}
                  <strong className="gallery-title">{cardName(item.card)}</strong>
                  <span className="gallery-value">{money(item.value_total)}</span>
                  <span className="gallery-grade">
                    {cardLine(item.card)} · {gradeLabel(item.ownership_grade)}
                    {item.quantity > 1 ? ` · Qty ${item.quantity}` : ""}
                  </span>
                  {item.grading?.available && <GradingPill grading={item.grading} />}
                </article>
              ))}
            </div>
          ) : (
            <div className="collection-scroll">
              <table className="collection-table sports-table">
                <thead>
                  <tr>
                    <th>{sortHeader("name", "Card")}</th>
                    <th>Qty</th>
                    <th>Market value</th>
                    <th>You own</th>
                    <th>{sortHeader("value", "Your value")}</th>
                    <th>{sortHeader("psa10", "PSA 9 / PSA 10")}</th>
                    <th>{sortHeader("grading", "Grading")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((item) => (
                    <tr key={item.id}>
                      <td className="cell-card">
                        <div className="table-card-cell">
                          <CardThumb card={item.card} onClick={() => setDetailsItem(item)} />
                          <div>
                            <strong>{cardName(item.card)}</strong>
                            <span>{cardLine(item.card)}</span>
                            {item.card.league && (
                              <span className="sports-league">{leagueName(item.card.league)}</span>
                            )}
                          </div>
                        </div>
                      </td>

                      <td data-label="Qty">
                        <div className="qty-stepper">
                          <button
                            type="button"
                            aria-label={`One fewer ${cardName(item.card)}`}
                            title={item.quantity <= 1 ? "Use Remove to take the last copy out" : "One fewer"}
                            onClick={() => changeQuantity(item, item.quantity - 1)}
                            disabled={item.quantity <= 1 || savingQty[item.id]}
                          >
                            −
                          </button>
                          <strong className="qty-cell">{item.quantity}</strong>
                          <button
                            type="button"
                            aria-label={`One more ${cardName(item.card)}`}
                            title="One more"
                            onClick={() => changeQuantity(item, item.quantity + 1)}
                            disabled={savingQty[item.id]}
                          >
                            +
                          </button>
                        </div>
                      </td>

                      <td data-label="Market value">
                        <div className="price-cell">
                          <strong>{money(item.value_each)}</strong>
                          {valueCell(item)}
                        </div>
                      </td>

                      <td data-label="You own">
                        <strong>{gradeLabel(item.ownership_grade)}</strong>
                      </td>

                      <td data-label="Your value">
                        <strong>{money(item.value_total)}</strong>
                      </td>

                      <td data-label="PSA 9 / PSA 10">
                        <div className="price-cell">
                          <strong>
                            {money(item.prices?.PSA_9?.value)} / {money(item.prices?.PSA_10?.value)}
                          </strong>
                          {item.prices?.PSA_10 && (
                            <span>
                              {item.prices.PSA_10.sales} PSA 10 sale{item.prices.PSA_10.sales === 1 ? "" : "s"}
                            </span>
                          )}
                        </div>
                      </td>

                      <td data-label="Grading" className="cell-wide">
                        {item.grading ? (
                          <div className="price-cell">
                            <GradingPill grading={item.grading} />
                            {item.grading.available && (
                              <span>PSA 10 nets {signedMoney(item.grading.psa10_net)}</span>
                            )}
                          </div>
                        ) : (
                          <span className="grading-unavailable">Already graded</span>
                        )}
                      </td>

                      <td className="cell-actions">
                        <div className="action-stack">
                          <button type="button" className="text-button" onClick={() => setDetailsItem(item)}>
                            Details
                          </button>
                          <button type="button" className="text-button" onClick={() => openEdit(item)}>
                            Edit
                          </button>
                          <button type="button" className="text-button danger" onClick={() => handleRemove(item)}>
                            Remove
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {addOpen && (
        <div
          className="modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeAdd();
          }}
        >
          <div className="modal wide-modal sports-add-modal" role="dialog" aria-modal="true" aria-label="Add a sports card">
            <div className="modal-heading">
              <div>
                <p className="eyebrow">ADD TO SPORTS</p>
                <h2>{selected ? cardName(selected) : "Find a sports card"}</h2>
                <span>
                  {selected
                    ? `${cardLine(selected)}${selected.league ? ` · ${leagueName(selected.league)}` : ""}`
                    : "Search by player, then narrow by year, card number or set. Each search uses 1 CardSight call."}
                </span>
              </div>
              <button type="button" className="close-button" aria-label="Close" onClick={closeAdd}>
                ×
              </button>
            </div>

            {modalError && <div className="status error">{modalError}</div>}

            {!selected ? (
              <>
                <form className="sports-search-form" onSubmit={handleSearch}>
                  <label className="sports-search-name">
                    Player
                    <input
                      value={search.name}
                      onChange={(event) => setSearch((current) => ({ ...current, name: event.target.value }))}
                      placeholder="Ken Griffey Jr."
                      autoFocus
                    />
                  </label>
                  <label>
                    Year
                    <input
                      inputMode="numeric"
                      maxLength={4}
                      value={search.year}
                      onChange={(event) => setSearch((current) => ({ ...current, year: event.target.value }))}
                      placeholder="1991"
                    />
                  </label>
                  <label>
                    Card #
                    <input
                      value={search.number}
                      onChange={(event) => setSearch((current) => ({ ...current, number: event.target.value }))}
                      placeholder="270"
                    />
                  </label>
                  <label>
                    Set (optional)
                    <input
                      value={search.set}
                      onChange={(event) => setSearch((current) => ({ ...current, set: event.target.value }))}
                      placeholder="Stadium Club"
                    />
                  </label>
                  <button type="submit" disabled={searching}>
                    {searching ? "Searching…" : "Search"}
                  </button>
                </form>

                {results && results.length === 0 && (
                  <div className="empty-state">
                    No cards found. Try just the last name, or leave out the card number.
                  </div>
                )}

                {results && results.length > 0 && (
                  <>
                    <div className="sports-results-bar">
                      <span>
                        {results.length} card{results.length === 1 ? "" : "s"} · base cards only
                      </span>
                      {results.some((card) => !resultPrices[card.cardsight_id]) && (
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={priceAllResults}
                          disabled={pricing}
                          title="Prices up to 100 of these cards in one CardSight call"
                        >
                          {pricing ? "Checking…" : `Show values for ${Math.min(results.length, 100)} (1 call)`}
                        </button>
                      )}
                    </div>

                    <div className="sports-results">
                      {results.map((card) => {
                        const prices = resultPrices[card.cardsight_id];
                        return (
                          <button
                            type="button"
                            key={card.cardsight_id}
                            className="sports-result"
                            onClick={() => selectCard(card)}
                          >
                            <span className="sports-result-copy">
                              <strong>{cardName(card)}</strong>
                              <span>
                                {cardLine(card)}
                                {card.league ? ` · ${leagueName(card.league)}` : ""}
                              </span>
                            </span>
                            {prices && (
                              <span className="sports-result-prices">
                                <span>Raw {money(prices.grades?.RAW?.value)}</span>
                                <span>PSA 10 {money(prices.grades?.PSA_10?.value)}</span>
                                {prices.grading?.available && <GradingPill grading={prices.grading} />}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}

                {usage && (
                  <p className="modal-tip">
                    CardSight calls this month: {usage.calls} of {usage.limit}. Checking a card you
                    looked at in the last few hours is free.
                  </p>
                )}
              </>
            ) : (
              <form onSubmit={handleAdd}>
                {!selectedPrices ? (
                  <div className="empty-state">{pricing ? "Checking recent sales…" : "No price data."}</div>
                ) : (
                  <div className="sports-check">
                    {photo(selectedPrices) && (
                      <img
                        src={photo(selectedPrices)}
                        alt=""
                        referrerPolicy="no-referrer"
                        className="sports-check-photo"
                      />
                    )}
                    <div className="sports-check-body">
                      <GradeTable grades={selectedPrices.grades} owned={addForm.ownership_grade} />
                      <div className="sports-check-grading">
                        <GradingPill grading={selectedPrices.grading} />
                        {selectedPrices.grading?.available && (
                          <span>
                            PSA 10 nets {signedMoney(selectedPrices.grading.psa10_net)} · PSA 9 nets{" "}
                            {signedMoney(selectedPrices.grading.psa9_net)} after{" "}
                            {money(selectedPrices.grading.grading_cost)} grading
                          </span>
                        )}
                      </div>
                      <small className="sports-check-note">
                        Median of the most recent eBay auction sales (up to 10) in the past year
                        {selectedPrices.last_sale_date
                          ? ` · last sale ${shortDate(selectedPrices.last_sale_date)}`
                          : ""}
                        .
                      </small>
                    </div>
                  </div>
                )}

                <div className="grade-input-grid">
                  <label>
                    What do you own?
                    <select
                      value={addForm.ownership_grade}
                      onChange={(event) =>
                        setAddForm((current) => ({ ...current, ownership_grade: event.target.value }))
                      }
                    >
                      {OWNED_GRADES.map((grade) => (
                        <option key={grade} value={grade}>
                          {gradeLabel(grade)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Quantity
                    <input
                      type="number"
                      min="1"
                      max="999"
                      value={addForm.quantity}
                      onChange={(event) => setAddForm((current) => ({ ...current, quantity: event.target.value }))}
                    />
                  </label>
                  <label>
                    Your value (optional)
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={addForm.manual_value}
                      onChange={(event) =>
                        setAddForm((current) => ({ ...current, manual_value: event.target.value }))
                      }
                      placeholder="Use if no sales"
                    />
                  </label>
                </div>

                {addForm.manual_value !== "" && (
                  <label>
                    Where that value is from (optional)
                    <input
                      type="url"
                      value={addForm.manual_source_url}
                      onChange={(event) =>
                        setAddForm((current) => ({ ...current, manual_source_url: event.target.value }))
                      }
                      placeholder="https://..."
                    />
                  </label>
                )}

                <label>
                  Notes (optional)
                  <textarea
                    value={addForm.notes}
                    onChange={(event) => setAddForm((current) => ({ ...current, notes: event.target.value }))}
                    placeholder="Centering looks good, corners sharp, etc."
                  />
                </label>

                <div className="modal-actions">
                  <button type="button" className="secondary-button" onClick={() => setAddForm(emptyAdd())}>
                    ← Back to results
                  </button>
                  <button type="submit" disabled={adding}>
                    {adding ? "Adding…" : "Add to Sports"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {editForm && (
        <div className="modal-backdrop">
          <form className="modal" onSubmit={handleEdit}>
            <div className="modal-heading">
              <div>
                <p className="eyebrow">EDIT SPORTS CARD</p>
                <h2>{cardName(editForm.item.card)}</h2>
                <span>{cardLine(editForm.item.card)}</span>
              </div>
              <button type="button" className="close-button" aria-label="Close" onClick={() => setEditForm(null)}>
                ×
              </button>
            </div>

            {modalError && <div className="status error">{modalError}</div>}

            <label>
              What do you own?
              <select
                value={editForm.ownership_grade}
                onChange={(event) => setEditForm((current) => ({ ...current, ownership_grade: event.target.value }))}
              >
                {OWNED_GRADES.map((grade) => (
                  <option key={grade} value={grade}>
                    {gradeLabel(grade)}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Your value per card (leave empty to use recent sales)
              <input
                type="number"
                min="0"
                step="0.01"
                value={editForm.manual_value}
                onChange={(event) => setEditForm((current) => ({ ...current, manual_value: event.target.value }))}
                placeholder={
                  editForm.item.prices?.[editForm.ownership_grade]
                    ? `Recent sales: ${money(editForm.item.prices[editForm.ownership_grade].value)}`
                    : "No recent sales"
                }
              />
            </label>

            <label>
              Where that value is from (optional)
              <input
                type="url"
                value={editForm.manual_source_url}
                onChange={(event) => setEditForm((current) => ({ ...current, manual_source_url: event.target.value }))}
                placeholder="https://..."
              />
            </label>

            <label>
              Notes
              <textarea
                value={editForm.notes}
                onChange={(event) => setEditForm((current) => ({ ...current, notes: event.target.value }))}
              />
            </label>

            <div className="modal-actions">
              {editForm.item.manual_value !== null && editForm.item.manual_value !== undefined && (
                <button
                  type="button"
                  className="secondary-button"
                  onClick={(event) => handleEdit(event, true)}
                  disabled={savingEdit}
                >
                  Use recent sales
                </button>
              )}
              <button type="submit" disabled={savingEdit}>
                {savingEdit ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}

      {detailsItem && (
        <div
          className="modal-backdrop"
          onClick={(event) => {
            if (event.target === event.currentTarget) setDetailsItem(null);
          }}
        >
          <div className="modal wide-modal" role="dialog" aria-modal="true" aria-label="Card details">
            <div className="modal-heading">
              <div>
                <p className="eyebrow">{leagueName(detailsItem.card.league).toUpperCase()}</p>
                <h2>{cardName(detailsItem.card)}</h2>
                <span>
                  {cardLine(detailsItem.card)} · You own {gradeLabel(detailsItem.ownership_grade)}
                  {detailsItem.quantity > 1 ? ` × ${detailsItem.quantity}` : ""}
                </span>
              </div>
              <button type="button" className="close-button" aria-label="Close" onClick={() => setDetailsItem(null)}>
                ×
              </button>
            </div>

            <div className="sports-check">
              <img
                src={photo(detailsItem.card, 1600) || sportsImageUrl(detailsItem.card.cardsight_id)}
                alt={cardName(detailsItem.card)}
                referrerPolicy="no-referrer"
                className="sports-check-photo"
                onError={(event) => {
                  event.currentTarget.style.display = "none";
                }}
              />
              <div className="sports-check-body">
                <GradeTable grades={detailsItem.prices} owned={detailsItem.ownership_grade} />

                {detailsItem.grading && (
                  <div className="sports-check-grading">
                    <GradingPill grading={detailsItem.grading} />
                    {detailsItem.grading.available && <span>{detailsItem.grading.risk_note}</span>}
                    {detailsItem.grading.score_parts?.map((part) => (
                      <small key={part.name}>
                        {part.name}: {part.points}/{part.max} · {part.detail}
                      </small>
                    ))}
                  </div>
                )}

                {detailsItem.prices?.[detailsItem.ownership_grade]?.recent?.length > 0 && (
                  <div className="sports-recent">
                    <strong>Recent {gradeLabel(detailsItem.ownership_grade)} sales</strong>
                    {detailsItem.prices[detailsItem.ownership_grade].recent.map((sale, index) => (
                      <a key={index} href={sale.url || undefined} target="_blank" rel="noreferrer">
                        {money(sale.price)} · {shortDate(sale.date)}
                      </a>
                    ))}
                  </div>
                )}

                {detailsItem.notes && <p className="modal-tip">{detailsItem.notes}</p>}

                <small className="sports-check-note">
                  Prices from CardSight AI, refreshed {refreshedText(detailsItem.priced_at)}.
                </small>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
