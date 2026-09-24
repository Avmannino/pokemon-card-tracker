import { useEffect, useMemo, useRef, useState } from "react";

import {
  addCollectionItem,
  deleteCollectionItem,
  getCollection,
  getRefreshStatus,
  saveGradedValues,
  searchCards,
  startSync,
} from "./api.js";
import CardZoomModal, { useCardZoom } from "./CardZoomModal.jsx";
import Dashboard from "./Dashboard.jsx";
import { money, cardTitle, gradeLabel, percent } from "./format.js";

const GRADES = ["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"];

// Grades that get their own column in the collection table. PSA 7 is still
// ownable and still enterable via the PSA values modal, it just isn't worth
// a column.
const TABLE_GRADES = GRADES.filter((grade) => grade !== "PSA_7");
const SEARCH_PAGE_SIZE = 10;

function pullTime(iso) {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function refreshStatusText(status) {
  if (!status) {
    return "";
  }

  if (status.syncing && status.progress) {
    const { phase, done, total } = status.progress;

    return `Syncing ${phase === "graded" ? "graded" : "raw"} prices… ${done}/${total}`;
  }

  const last = status.last_pull_at
    ? `Last synced ${pullTime(status.last_pull_at)}`
    : "Not synced yet";

  return `${last} · Next auto sync ${pullTime(status.next_pull_at)}`;
}

function syncSummary(status) {
  const manual = status?.manual;

  if (!manual) {
    return "Sync finished.";
  }

  if (manual.error) {
    return `Sync stopped early: ${manual.error}`;
  }

  const { raw, graded } = manual;
  const parts = [`raw prices updated for ${raw.succeeded} of ${raw.attempted} cards`];

  if (graded.attempted > 0) {
    parts.push(`graded prices found for ${graded.with_data} of ${graded.attempted}`);
  }

  const problems = [raw, graded]
    .map((part) => part.stopped_early)
    .filter(Boolean);

  const failed = raw.failed + graded.failed;

  return (
    `Synced: ${parts.join("; ")}.` +
    (failed ? ` ${failed} failed.` : "") +
    (problems.length ? ` ${problems.join(" ")}` : "")
  );
}

function TrendBadge({ week }) {
  if (!week) {
    return null;
  }

  if (!week.change) {
    return (
      <span
        className="trend-arrow flat"
        title={
          week.has_history
            ? "Raw value unchanged over the past week"
            : "Not enough price history yet"
        }
      >
        {week.has_history ? "▬ $0.00 (0.00%)" : "– New"}
      </span>
    );
  }

  const up = week.change > 0;

  return (
    <span
      className={`trend-arrow ${up ? "up" : "down"}`}
      title="Raw value over the past week"
    >
      {up ? "▲ +" : "▼ −"}
      {money(Math.abs(week.change))}
      {week.change_pct !== null && ` (${percent(week.change_pct)})`}
    </span>
  );
}

function chunk(items, size) {
  const chunks = [];

  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size));
  }

  return chunks;
}

// key === null means the column can't be sorted.
const COLLECTION_COLUMNS = [
  { key: "card", label: "Card", numeric: false },
  ...TABLE_GRADES.map((grade) => ({
    key: grade,
    label: gradeLabel(grade),
    numeric: true,
  })),
  { key: "owned", label: "You Own", numeric: true },
  { key: "qty", label: "Qty", numeric: true },
  { key: "value", label: "Your Value", numeric: true },
  { key: null, label: "Actions", numeric: false },
];

function sortValue(item, key) {
  switch (key) {
    case "card":
      return cardTitle(item.card).toLowerCase();
    case "owned":
      return GRADES.indexOf(item.ownership_grade);
    case "qty":
      return item.quantity;
    case "value":
      return item.owned_market_value_total;
    default:
      return item.market_values?.[key]?.estimate;
  }
}

function emptyAddForm(card = null) {
  return {
    card,
    quantity: 1,
    ownership_grade: "RAW",
    purchase_price: "",
    notes: "",
  };
}

function emptyPsaForm(item = null) {
  return {
    item,
    source: "PSA CardFacts — Average Price",
    source_url: "",
    psa_7: "",
    psa_8: "",
    psa_9: "",
    psa_10: "",
  };
}

function App() {
  const [collection, setCollection] = useState({
    items: [],
    summary: {
      item_count: 0,
      known_market_total: 0,
      items_missing_owned_value: 0,
    },
  });

  const [query, setQuery] = useState("");
  const [collectionQuery, setCollectionQuery] = useState("");
  const [sort, setSort] = useState({ key: "value", direction: "desc" });
  const searchInputRef = useRef(null);
  const [searchResults, setSearchResults] = useState([]);
  const [searchPage, setSearchPage] = useState(0);
  const [searching, setSearching] = useState(false);
  const [loadingCollection, setLoadingCollection] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState(null);
  const watchingSync = useRef(false);

  const [addForm, setAddForm] = useState(emptyAddForm());
  const [psaForm, setPsaForm] = useState(emptyPsaForm());
  const [dashboardVersion, setDashboardVersion] = useState(0);
  const { zoomedCard, openZoom, closeZoom } = useCardZoom();

  async function loadCollection() {
    setLoadingCollection(true);

    try {
      const data = await getCollection();
      setCollection(data);
      setDashboardVersion((version) => version + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingCollection(false);
    }
  }

  // Polls until the backend finishes the sync that's running (ours, or the
  // scheduled one), then reloads the collection. Returns the final status.
  async function watchSync() {
    if (watchingSync.current) {
      return null;
    }

    watchingSync.current = true;
    setSyncing(true);

    try {
      let status = await getRefreshStatus();
      setRefreshStatus(status);

      while (status.syncing) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        status = await getRefreshStatus();
        setRefreshStatus(status);
      }

      await loadCollection();
      return status;
    } finally {
      watchingSync.current = false;
      setSyncing(false);
    }
  }

  async function loadRefreshStatus() {
    try {
      const status = await getRefreshStatus();
      setRefreshStatus(status);

      // A sync is already running (e.g. the page was reloaded mid-sync, or
      // the scheduled pull just started), so pick up its progress.
      if (status.syncing) {
        watchSync();
      }
    } catch {
      // Status is informational only; the collection view still works.
    }
  }

  useEffect(() => {
    loadCollection();
    loadRefreshStatus();
  }, []);

  const ownedTotal = useMemo(
    () => collection.summary?.known_market_total || 0,
    [collection]
  );

  // Every space-separated term must match somewhere on the item, so
  // "lugia psa 9" narrows rather than widens.
  const filteredItems = useMemo(() => {
    const terms = collectionQuery
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

    if (terms.length === 0) {
      return collection.items;
    }

    return collection.items.filter((item) => {
      const haystack = [
        item.card.name,
        item.card.card_number,
        item.card.set_name,
        item.card.rarity,
        item.card.variant,
        gradeLabel(item.ownership_grade),
        item.notes,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return terms.every((term) => haystack.includes(term));
    });
  }, [collection.items, collectionQuery]);

  // Cards with no price for a column sort to the bottom either way, so
  // flipping the direction never buries the priced ones.
  const sortedItems = useMemo(() => {
    if (!sort.key) {
      return filteredItems;
    }

    const direction = sort.direction === "asc" ? 1 : -1;

    return [...filteredItems].sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);

      const leftMissing = left === null || left === undefined;
      const rightMissing = right === null || right === undefined;

      if (leftMissing || rightMissing) {
        return leftMissing && rightMissing ? 0 : leftMissing ? 1 : -1;
      }

      if (typeof left === "string") {
        return left.localeCompare(right) * direction;
      }

      return (left - right) * direction;
    });
  }, [filteredItems, sort]);

  function toggleSort(column) {
    if (!column.key) {
      return;
    }

    setSort((current) =>
      current.key === column.key
        ? {
            key: column.key,
            direction: current.direction === "asc" ? "desc" : "asc",
          }
        // Money and counts are most useful highest-first; names A-Z.
        : { key: column.key, direction: column.numeric ? "desc" : "asc" }
    );
  }

  const searchPages = useMemo(
    () => chunk(searchResults, SEARCH_PAGE_SIZE),
    [searchResults]
  );
  const totalSearchPages = Math.max(searchPages.length, 1);
  const currentSearchPage = Math.min(searchPage, totalSearchPages - 1);

  async function handleSearch(event) {
    event.preventDefault();

    if (query.trim().length < 2) {
      return;
    }

    setSearching(true);
    setError("");
    setMessage("");

    try {
      const data = await searchCards(query.trim());
      setSearchResults(data.results || []);
      setSearchPage(0);

      if ((data.results || []).length === 0) {
        setMessage("No cards matched that search.");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSearching(false);
    }
  }

  async function handleAdd(event) {
    event.preventDefault();

    if (!addForm.card) {
      return;
    }

    setError("");
    setMessage("");

    try {
      const payload = {
        card: addForm.card,
        quantity: Number(addForm.quantity),
        ownership_grade: addForm.ownership_grade,
        purchase_price:
          addForm.purchase_price === ""
            ? null
            : Number(addForm.purchase_price),
        notes: addForm.notes.trim() || null,
      };

      const result = await addCollectionItem(payload);

      setAddForm(emptyAddForm());
      setQuery("");
      setSearchResults([]);
      searchInputRef.current?.focus();
      await loadCollection();

      setMessage(
        result.price_error
          ? `Card added, but its raw price could not be fetched: ${result.price_error}. ` +
            "It will fill in at the next scheduled pull."
          : "Card added and raw market price fetched."
      );
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDelete(itemId) {
    const confirmed = window.confirm(
      "Remove this copy from your collection?"
    );

    if (!confirmed) {
      return;
    }

    setError("");
    setMessage("");

    try {
      await deleteCollectionItem(itemId);
      await loadCollection();
      setMessage("Card removed from your collection.");
    } catch (err) {
      setError(err.message);
    }
  }

  // Manual sync: pulls fresh prices now, on top of the twice-daily schedule.
  async function handleSyncNow() {
    setError("");
    setMessage("");

    try {
      await startSync();
    } catch (err) {
      // Includes "already running" and the cooldown message from the server.
      setError(err.message);
      loadRefreshStatus();
      return;
    }

    try {
      const status = await watchSync();
      setMessage(syncSummary(status));
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleSavePsa(event) {
    event.preventDefault();

    if (!psaForm.item) {
      return;
    }

    setError("");
    setMessage("");

    const toNumberOrNull = (value) =>
      value === "" ? null : Number(value);

    try {
      await saveGradedValues(psaForm.item.card.id, {
        source: psaForm.source.trim(),
        source_url: psaForm.source_url.trim() || null,
        psa_7: toNumberOrNull(psaForm.psa_7),
        psa_8: toNumberOrNull(psaForm.psa_8),
        psa_9: toNumberOrNull(psaForm.psa_9),
        psa_10: toNumberOrNull(psaForm.psa_10),
      });

      setPsaForm(emptyPsaForm());
      await loadCollection();
      setMessage("PSA market values saved.");
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="app-shell">
      <section className="hero">
        <div>

          <h1>Pokémon Card Market Dashboard</h1>

          <p className="hero-copy">
            Track your collection, compare raw and PSA values,
            and keep market snapshots over time.
          </p>
        </div>

        <div className="portfolio-card">
          <span>Known collection value</span>

          <strong>{money(ownedTotal)}</strong>

          <small>
            {collection.summary?.item_count || 0} collection entries
          </small>
        </div>
      </section>

      <Dashboard refreshKey={dashboardVersion} />

      <section className="search-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ADD A CARD</p>
            <h2>Search Pokémon cards</h2>
          </div>

          <div className="refresh-control">
            <button
              type="button"
              className="secondary-button"
              onClick={handleSyncNow}
              disabled={syncing}
            >
              {syncing ? "Syncing…" : "Refresh Prices"}
            </button>

            <small>{refreshStatusText(refreshStatus)}</small>
          </div>
        </div>

        <form
          className="search-form"
          onSubmit={handleSearch}
        >
          <input
            ref={searchInputRef}
            value={query}
            onChange={(event) =>
              setQuery(event.target.value)
            }
            placeholder="Try: Glaceon GX, Charizard ex, Pikachu..."
          />

          <button
            type="submit"
            disabled={searching}
          >
            {searching ? "Searching…" : "Search"}
          </button>
        </form>

        {searchResults.length > 0 && (
          <>
            <div className="search-sort-note">
              Your top 2 matches for this search are pinned
              first, then the rest are sorted by current raw
              market value, highest to lowest. Printed rarity
              is used as a fallback when pricing is unavailable.
            </div>

            <div className="search-results-viewport">
              <div
                className="search-results-track"
                style={{
                  width: `${totalSearchPages * 100}%`,
                  transform: `translateX(-${
                    (100 / totalSearchPages) * currentSearchPage
                  }%)`,
                }}
              >
                {searchPages.map((pageItems, pageIndex) => (
                  <div
                    className="search-results"
                    style={{ width: `${100 / totalSearchPages}%` }}
                    key={pageIndex}
                  >
                    {pageItems.map((card) => (
                      <article
                        className="search-result"
                        key={card.poketrace_id}
                      >
                        <div className="card-image-wrap">
                          {card.image_url ? (
                            <img
                              className="thumb-clickable"
                              src={card.image_url}
                              alt={cardTitle(card)}
                              onClick={() => openZoom(card)}
                            />
                          ) : (
                            <div className="image-placeholder">
                              No image
                            </div>
                          )}
                        </div>

                        <div className="search-result-copy">
                          <strong className="search-result-title">
                            {cardTitle(card)}
                          </strong>

                          <span>
                            {card.set_name || "Unknown set"}
                          </span>

                          <div className="search-result-meta">
                            {card.rarity && (
                              <span className="rarity-pill">
                                {card.rarity}
                              </span>
                            )}

                            {card.variant && (
                              <span className="variant-label">
                                {card.variant}
                              </span>
                            )}
                          </div>

                          <div className="search-market-value">
                            <span>Raw market estimate</span>

                            <strong>
                              {money(card.raw_market_estimate)}
                            </strong>
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() =>
                            setAddForm(emptyAddForm(card))
                          }
                        >
                          Add
                        </button>
                      </article>
                    ))}
                  </div>
                ))}
              </div>
            </div>

            {totalSearchPages > 1 && (
              <div className="search-pagination">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={currentSearchPage === 0}
                  onClick={() =>
                    setSearchPage((page) => Math.max(0, page - 1))
                  }
                >
                  ‹ Prev
                </button>

                <div className="search-pagination-dots">
                  {searchPages.map((_, pageIndex) => (
                    <button
                      type="button"
                      key={pageIndex}
                      className={
                        pageIndex === currentSearchPage ? "active" : ""
                      }
                      aria-label={`Go to results page ${pageIndex + 1}`}
                      onClick={() => setSearchPage(pageIndex)}
                    />
                  ))}
                </div>

                <button
                  type="button"
                  className="secondary-button"
                  disabled={currentSearchPage === totalSearchPages - 1}
                  onClick={() =>
                    setSearchPage((page) =>
                      Math.min(totalSearchPages - 1, page + 1)
                    )
                  }
                >
                  Next ›
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {(message || error) && (
        <section
          className="status-area"
          aria-live="polite"
        >
          {message && (
            <div className="status success">
              {message}
            </div>
          )}

          {error && (
            <div className="status error">
              {error}
            </div>
          )}
        </section>
      )}

      <section className="collection-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">YOUR CARDS</p>
            <h2>Collection</h2>
          </div>

          <div className="collection-meta">
            <span>
              Missing owned value:{" "}
              <strong>
                {collection.summary
                  ?.items_missing_owned_value || 0}
              </strong>
            </span>
          </div>
        </div>

        {!loadingCollection && collection.items.length > 0 && (
          <div className="collection-search">
            <input
              type="search"
              value={collectionQuery}
              onChange={(event) =>
                setCollectionQuery(event.target.value)
              }
              placeholder="Search your collection by name, set, number, grade..."
              aria-label="Search your collection"
            />

            {collectionQuery.trim() && (
              <span>
                Showing {filteredItems.length} of{" "}
                {collection.items.length}
              </span>
            )}
          </div>
        )}

        {loadingCollection ? (
          <div className="empty-state">
            Loading collection…
          </div>
        ) : collection.items.length === 0 ? (
          <div className="empty-state">
            Search for a card above and add your first card.
          </div>
        ) : (
          <div
            className="collection-scroll"
            hidden={filteredItems.length === 0}
          >
            <table className="collection-table">
              <thead>
                <tr>
                  {COLLECTION_COLUMNS.map((column) => (
                    <th
                      key={column.label}
                      aria-sort={
                        sort.key === column.key
                          ? sort.direction === "asc"
                            ? "ascending"
                            : "descending"
                          : undefined
                      }
                    >
                      {column.key ? (
                        <button
                          type="button"
                          className={
                            sort.key === column.key
                              ? "sort-button active"
                              : "sort-button"
                          }
                          onClick={() => toggleSort(column)}
                        >
                          {column.label}

                          <span className="sort-caret">
                            {sort.key === column.key
                              ? sort.direction === "asc"
                                ? "▲"
                                : "▼"
                              : "↕"}
                          </span>
                        </button>
                      ) : (
                        column.label
                      )}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {sortedItems.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="table-card-cell">
                        {item.card.image_url && (
                          <img
                            className="thumb-clickable"
                            src={item.card.image_url}
                            alt={cardTitle(item.card)}
                            onClick={() =>
                              openZoom(item.card, {
                                grade: item.ownership_grade,
                                valueEach: item.owned_market_value_each,
                              })
                            }
                          />
                        )}

                        <div>
                          <strong>
                            {cardTitle(item.card)}

                            <TrendBadge week={item.week_change} />
                          </strong>

                          <span>
                            {item.card.set_name ||
                              "Unknown set"}
                          </span>
                        </div>
                      </div>
                    </td>

                    {TABLE_GRADES.map((grade) => (
                      <td key={grade}>
                        <div className="price-cell">
                          <strong>
                            {money(
                              item.market_values?.[grade]
                                ?.estimate
                            )}
                          </strong>

                          <span>
                            {item.market_values?.[grade]?.is_manual
                              ? "Manual"
                              : `${
                                  item.market_values?.[grade]
                                    ?.source_count || 0
                                } source${
                                  (item.market_values?.[grade]
                                    ?.source_count || 0) === 1
                                    ? ""
                                    : "s"
                                }`}
                          </span>
                        </div>
                      </td>
                    ))}

                    <td>
                      <div className="owned-cell">
                        <strong>
                          {gradeLabel(
                            item.ownership_grade
                          )}
                        </strong>
                      </div>
                    </td>

                    <td>
                      <strong className="qty-cell">
                        {item.quantity}
                      </strong>
                    </td>

                    <td>
                      <strong>
                        {money(
                          item.owned_market_value_total
                        )}
                      </strong>
                    </td>

                    <td>
                      <div className="action-stack">
                        <button
                          type="button"
                          className="text-button"
                          onClick={() =>
                            setPsaForm(
                              emptyPsaForm(item)
                            )
                          }
                        >
                          PSA values
                        </button>

                        <button
                          type="button"
                          className="text-button danger"
                          onClick={() =>
                            handleDelete(item.id)
                          }
                        >
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

        {!loadingCollection &&
          collection.items.length > 0 &&
          filteredItems.length === 0 && (
            <div className="empty-state">
              No cards in your collection match "
              {collectionQuery.trim()}".
            </div>
          )}
      </section>

      {addForm.card && (
        <div className="modal-backdrop">
          <form
            className="modal"
            onSubmit={handleAdd}
          >
            <div className="modal-heading">
              <div>
                <p className="eyebrow">
                  ADD TO COLLECTION
                </p>

                <h2>
                  {cardTitle(addForm.card)}
                </h2>

                <span>
                  {addForm.card.set_name}
                </span>
              </div>

              <button
                type="button"
                className="close-button"
                onClick={() =>
                  setAddForm(emptyAddForm())
                }
              >
                ×
              </button>
            </div>

            <label>
              What do you own?

              <select
                value={addForm.ownership_grade}
                onChange={(event) =>
                  setAddForm((current) => ({
                    ...current,
                    ownership_grade:
                      event.target.value,
                  }))
                }
              >
                {GRADES.map((grade) => (
                  <option
                    value={grade}
                    key={grade}
                  >
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
                max="100"
                value={addForm.quantity}
                onChange={(event) =>
                  setAddForm((current) => ({
                    ...current,
                    quantity: event.target.value,
                  }))
                }
              />
            </label>

            <label>
              Purchase price per card (optional)

              <input
                type="number"
                min="0"
                step="0.01"
                value={addForm.purchase_price}
                onChange={(event) =>
                  setAddForm((current) => ({
                    ...current,
                    purchase_price:
                      event.target.value,
                  }))
                }
                placeholder="25.00"
              />
            </label>

            <label>
              Notes (optional)

              <textarea
                value={addForm.notes}
                onChange={(event) =>
                  setAddForm((current) => ({
                    ...current,
                    notes: event.target.value,
                  }))
                }
                placeholder="Pulled from a Power Pack, bought at a show, etc."
              />
            </label>

            <div className="modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  setAddForm(emptyAddForm())
                }
              >
                Cancel
              </button>

              <button type="submit">
                Add Card
              </button>
            </div>
          </form>
        </div>
      )}

      {psaForm.item && (
        <div className="modal-backdrop">
          <form
            className="modal wide-modal"
            onSubmit={handleSavePsa}
          >
            <div className="modal-heading">
              <div>
                <p className="eyebrow">
                  PSA MARKET VALUES
                </p>

                <h2>
                  {cardTitle(psaForm.item.card)}
                </h2>

                <span>
                  Enter values from a source you checked
                  manually.
                </span>
              </div>

              <button
                type="button"
                className="close-button"
                onClick={() =>
                  setPsaForm(emptyPsaForm())
                }
              >
                ×
              </button>
            </div>

            <label>
              Source name

              <input
                value={psaForm.source}
                onChange={(event) =>
                  setPsaForm((current) => ({
                    ...current,
                    source: event.target.value,
                  }))
                }
                required
              />
            </label>

            <label>
              Source page URL (recommended)

              <input
                type="url"
                value={psaForm.source_url}
                onChange={(event) =>
                  setPsaForm((current) => ({
                    ...current,
                    source_url:
                      event.target.value,
                  }))
                }
                placeholder="https://..."
              />
            </label>

            <div className="grade-input-grid">
              {[
                "psa_7",
                "psa_8",
                "psa_9",
                "psa_10",
              ].map((field, index) => (
                <label key={field}>
                  PSA {index + 7}

                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={psaForm[field]}
                    onChange={(event) =>
                      setPsaForm((current) => ({
                        ...current,
                        [field]:
                          event.target.value,
                      }))
                    }
                    placeholder="0.00"
                  />
                </label>
              ))}
            </div>

            <div className="modal-tip">
              Recommended starting point: on the exact
              PSA CardFacts page for the card, use the{" "}
              <strong>Average Price</strong> column for
              PSA 7, 8, 9 and 10. Make sure
              edition/variant matches exactly.
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  setPsaForm(emptyPsaForm())
                }
              >
                Cancel
              </button>

              <button type="submit">
                Save PSA Values
              </button>
            </div>
          </form>
        </div>
      )}

      <CardZoomModal card={zoomedCard} onClose={closeZoom} />
    </main>
  );
}

export default App;