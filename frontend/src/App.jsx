import { useEffect, useMemo, useRef, useState } from "react";

import {
  addCollectionItem,
  deleteCollectionItem,
  getCollection,
  refreshAll,
  refreshCard,
  saveGradedValues,
  searchCards,
} from "./api.js";
import CardZoomModal, { useCardZoom } from "./CardZoomModal.jsx";
import Dashboard from "./Dashboard.jsx";
import { money, cardTitle } from "./format.js";

const GRADES = ["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"];
const SEARCH_PAGE_SIZE = 10;

function chunk(items, size) {
  const chunks = [];

  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size));
  }

  return chunks;
}

function gradeLabel(grade) {
  if (grade === "RAW") {
    return "Raw";
  }

  return grade.replace("_", " ");
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
  const searchInputRef = useRef(null);
  const [searchResults, setSearchResults] = useState([]);
  const [searchPage, setSearchPage] = useState(0);
  const [searching, setSearching] = useState(false);
  const [loadingCollection, setLoadingCollection] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busyCardId, setBusyCardId] = useState(null);
  const [refreshingAll, setRefreshingAll] = useState(false);

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

  useEffect(() => {
    loadCollection();
  }, []);

  const ownedTotal = useMemo(
    () => collection.summary?.known_market_total || 0,
    [collection]
  );

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

      if (result.refresh_error) {
        setMessage(
          `Card added. Raw pricing could not refresh yet: ${result.refresh_error}`
        );
      } else {
        setMessage("Card added and raw market pricing refreshed.");
      }
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

  async function handleRefresh(cardId) {
    setBusyCardId(cardId);
    setError("");
    setMessage("");

    try {
      await refreshCard(cardId);
      await loadCollection();
      setMessage("Raw market values refreshed.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyCardId(null);
    }
  }

  async function handleRefreshAll() {
    setRefreshingAll(true);
    setError("");
    setMessage(
      "Refreshing cards at PokeTrace's free-account rate limit..."
    );

    try {
      const result = await refreshAll();
      await loadCollection();

      const failed = result.results.filter(
        (row) => !row.ok
      ).length;

      setMessage(
        `Refresh finished: ${result.attempted - failed} succeeded, ` +
          `${failed} failed, ` +
          `${result.skipped_due_to_daily_safety_limit} skipped.`
      );
    } catch (err) {
      setError(err.message);
      setMessage("");
    } finally {
      setRefreshingAll(false);
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
          <p className="eyebrow">PERSONAL COLLECTION</p>

          <h1>Pokémon Card Market Tracker</h1>

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

          <button
            type="button"
            className="secondary-button"
            onClick={handleRefreshAll}
            disabled={
              refreshingAll || collection.items.length === 0
            }
          >
            {refreshingAll
              ? "Refreshing…"
              : "Refresh All Raw Prices"}
          </button>
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

        {loadingCollection ? (
          <div className="empty-state">
            Loading collection…
          </div>
        ) : collection.items.length === 0 ? (
          <div className="empty-state">
            Search for a card above and add your first card.
          </div>
        ) : (
          <div className="collection-scroll">
            <table className="collection-table">
              <thead>
                <tr>
                  <th>Card</th>
                  <th>Raw</th>
                  <th>PSA 7</th>
                  <th>PSA 8</th>
                  <th>PSA 9</th>
                  <th>PSA 10</th>
                  <th>You Own</th>
                  <th>Your Value</th>
                  <th>Actions</th>
                </tr>
              </thead>

              <tbody>
                {collection.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="table-card-cell">
                        {item.card.image_url && (
                          <img
                            className="thumb-clickable"
                            src={item.card.image_url}
                            alt={cardTitle(item.card)}
                            onClick={() => openZoom(item.card)}
                          />
                        )}

                        <div>
                          <strong>
                            {cardTitle(item.card)}
                          </strong>

                          <span>
                            {item.card.set_name ||
                              "Unknown set"}
                          </span>
                        </div>
                      </div>
                    </td>

                    {GRADES.map((grade) => (
                      <td key={grade}>
                        <div className="price-cell">
                          <strong>
                            {money(
                              item.market_values?.[grade]
                                ?.estimate
                            )}
                          </strong>

                          <span>
                            {item.market_values?.[grade]
                              ?.source_count || 0}{" "}
                            source
                            {(item.market_values?.[grade]
                              ?.source_count || 0) === 1
                              ? ""
                              : "s"}
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

                        <span>
                          Qty {item.quantity}
                        </span>
                      </div>
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
                          disabled={
                            busyCardId === item.card.id
                          }
                          onClick={() =>
                            handleRefresh(item.card.id)
                          }
                        >
                          {busyCardId === item.card.id
                            ? "Refreshing…"
                            : "Refresh raw"}
                        </button>

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
      </section>

      <section className="method-panel">
        <p className="eyebrow">
          HOW VALUES WORK
        </p>

        <h2>Free-data valuation method</h2>

        <div className="method-grid">
          <article>
            <strong>Raw</strong>

            <p>
              Automatically refreshed from eBay raw sold
              averages and TCGPlayer raw market data
              returned by PokeTrace.
            </p>
          </article>

          <article>
            <strong>PSA 7–10</strong>

            <p>
              Stored as independent source snapshots. For
              now, enter legitimate public comps manually
              without scraping restricted sites.
            </p>
          </article>

          <article>
            <strong>General value</strong>

            <p>
              The app uses the median of each source&apos;s
              newest value so one source cannot overpower
              the estimate by being refreshed more often.
            </p>
          </article>
        </div>
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