import { useEffect, useMemo, useRef, useState } from "react";

import {
  addCollectionItem,
  deleteCollectionItem,
  getCollection,
  getRefreshStatus,
  saveGradedValues,
  searchCards,
  setCollectionVariant,
  startGradedRefresh,
  startSmallHitsSync,
  startSync,
} from "./api.js";
import CardZoomModal, { useCardZoom } from "./CardZoomModal.jsx";
import ItemImage, { slabPhoto } from "./ItemImage.jsx";
import Dashboard from "./Dashboard.jsx";
import {
  money,
  cardTitle,
  gradeLabel,
  percent,
  printingKey,
  variantLabel,
} from "./format.js";

// Grades you can own and track. PSA 7 isn't tracked.
const GRADES = ["RAW", "PSA_8", "PSA_9", "PSA_10"];
const SEARCH_PAGE_SIZE = 10;

// Search as you type: each new search is a PokeTrace request (250 a day),
// so it waits for a pause in typing and a few letters.
const SUGGEST_MIN_CHARS = 3;
const SUGGEST_DELAY_MS = 500;
const SUGGEST_LIMIT = 8;

// How long the Small Hits drawer takes to slide up or down.
const DRAWER_MS = 320;

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
    const { kind, phase, done, total } = status.progress;

    if (phase === "finishing") {
      return "Updating your collection…";
    }

    if (kind === "graded_refresh") {
      return `Refreshing graded cards… ${done}/${total}`;
    }

    if (kind === "small_hits") {
      return `Syncing small hits… ${done}/${total}`;
    }

    return `Syncing ${phase === "graded" ? "graded" : "raw"} prices… ${done}/${total}`;
  }

  const last = status.last_pull_at
    ? `Last synced ${pullTime(status.last_pull_at)}`
    : "Not synced yet";

  return `${last} · Next auto sync ${pullTime(status.next_pull_at)}`;
}

function gradedRefreshText(status) {
  const finished = status?.pricecharting?.finished_at;

  return finished
    ? `Graded cards last refreshed ${pullTime(finished)}`
    : "Graded cards only refresh when you press the button";
}

function smallHitsSyncSummary(status) {
  const run = status?.small_hits;

  if (!run) {
    return "Small hits sync finished.";
  }

  if (run.error) {
    return `Small hits sync stopped early: ${run.error}`;
  }

  return (
    `Small hits: prices updated for ${run.succeeded} of ${run.attempted} cards.` +
    (run.failed ? ` ${run.failed} failed.` : "") +
    (run.stopped_early ? ` ${run.stopped_early}` : "")
  );
}

function gradedRefreshSummary(status) {
  const run = status?.pricecharting;

  if (!run) {
    return "Graded refresh finished.";
  }

  if (run.error) {
    return `Graded refresh stopped early: ${run.error}`;
  }

  if (run.attempted === 0) {
    return "No graded cards have a PriceCharting link to refresh from.";
  }

  return (
    `Graded cards: ${run.updated} of ${run.attempted} updated from PriceCharting.` +
    (run.no_price ? ` ${run.no_price} had no price there.` : "") +
    (run.failed ? ` ${run.failed} failed.` : "") +
    (run.stopped_early ? ` ${run.stopped_early}` : "")
  );
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

const WINDOW_NAMES = { "1D": "day", "1W": "week", "1M": "month" };

function shortDay(value) {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

// One window's move of the grade you own - a PSA 10 shows PSA 10 movement,
// never the raw card's.
function ChangeCell({ change, window }) {
  if (!change) {
    return <span className="change-cell flat">—</span>;
  }

  const label = change.grade_label || gradeLabel(change.grade || "RAW");

  if (!change.has_history || change.change === null) {
    return (
      <span
        className="change-cell flat"
        title={`Not enough ${label} price history yet`}
      >
        —
      </span>
    );
  }

  const direction =
    change.change > 0 ? "up" : change.change < 0 ? "down" : "flat";
  const title =
    window === "ADDED"
      ? `${label} value per card since you added it on ${shortDay(change.added_at)}`
      : `${label} value over the past ${WINDOW_NAMES[window]}` +
        (change.since ? ` (since ${shortDay(change.since)}, its first price)` : "");

  return (
    <span className={`change-cell ${direction}`} title={title}>
      <strong>
        {change.change_pct !== null
          ? percent(change.change_pct)
          : `${change.change < 0 ? "−" : "+"}${money(Math.abs(change.change))}`}
      </strong>

      <span>
        {change.change < 0 ? "−" : "+"}
        {money(Math.abs(change.change))}
      </span>
    </span>
  );
}

function VariantPill({ variant }) {
  const label = variantLabel(variant);
  if (!label) {
    return null;
  }

  return (
    <span className={`variant-pill variant-${variant.toLowerCase()}`}>
      {label}
    </span>
  );
}

// What the zoom modal needs to explain an owned card's value.
function ownedDetails(item) {
  return {
    grade: item.ownership_grade,
    valueEach: item.owned_market_value_each,
    quantity: item.quantity,
    valuation: item.market_values?.[item.ownership_grade],
    variant: item.card.variant,
    item,
  };
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
  { key: "qty", label: "Qty", numeric: true },
  { key: "each", label: "Market Value", numeric: true },
  { key: "change_1D", label: "1D", numeric: true, window: "1D" },
  { key: "change_1W", label: "1W", numeric: true, window: "1W" },
  { key: "change_1M", label: "1M", numeric: true, window: "1M" },
  { key: "change_ADDED", label: "Since Added", numeric: true, window: "ADDED" },
  { key: "owned", label: "You Own", numeric: true },
  { key: "value", label: "Your Value", numeric: true },
  { key: null, label: "Actions", numeric: false },
];

function sortValue(item, key) {
  if (key.startsWith("change_")) {
    const change = item.owned_grade_changes?.[key.slice(7)];
    return change?.has_history ? change.change_pct ?? change.change : null;
  }

  switch (key) {
    case "card":
      return cardTitle(item.card).toLowerCase();
    case "owned":
      return GRADES.indexOf(item.ownership_grade);
    case "each":
      return item.owned_market_value_each;
    case "qty":
      return item.quantity;
    case "value":
      return item.owned_market_value_total;
    default:
      return item.market_values?.[key]?.estimate;
  }
}

// `smallHit`: opened from the Small Hits drawer, so the card goes there.
function emptyAddForm(card = null, smallHit = false) {
  return {
    card,
    smallHit,
    quantity: 1,
    ownership_grade: "RAW",
    purchase_price: "",
    notes: "",
  };
}

const PSA_GRADES = ["PSA_8", "PSA_9", "PSA_10"];

// Opens the PSA values modal pre-filled from what you last entered for this
// card: the same source and URL (kept until you clear it with the X), and
// your current manual values, so updating is just changing the numbers.
function emptyPsaForm(item = null) {
  const manualEntries = PSA_GRADES.flatMap(
    (grade) => item?.market_values?.[grade]?.sources || []
  ).filter((source) => source.metadata?.entry_method === "manual");

  const latest = manualEntries.reduce(
    (newest, entry) =>
      !newest || (entry.observed_at || "") > (newest.observed_at || "")
        ? entry
        : newest,
    null
  );

  const currentValue = (grade) => {
    const values = item?.market_values?.[grade];
    return values?.is_manual && values.estimate !== null
      ? String(values.estimate)
      : "";
  };

  return {
    item,
    source: latest?.source || "PSA CardFacts — Average Price",
    source_url: latest?.source_url || "",
    psa_8: currentValue("PSA_8"),
    psa_9: currentValue("PSA_9"),
    psa_10: currentValue("PSA_10"),
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
  const [collectionView, setCollectionView] = useState("table");
  const [sort, setSort] = useState({ key: "value", direction: "desc" });
  const searchInputRef = useRef(null);
  const [searchResults, setSearchResults] = useState([]);
  const [searchPage, setSearchPage] = useState(0);
  const [searching, setSearching] = useState(false);
  // Search-as-you-type: null (nothing to show), "loading", "error" or the
  // results for the current text.
  const [suggestions, setSuggestions] = useState(null);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const searchCache = useRef(new Map());
  const latestSuggestQuery = useRef("");
  const [loadingCollection, setLoadingCollection] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [gradedRefreshing, setGradedRefreshing] = useState(false);
  const [refreshStatus, setRefreshStatus] = useState(null);
  const watchingSync = useRef(false);
  // Also true when the page loads while a graded refresh is running.
  const gradedRunning =
    gradedRefreshing ||
    (syncing && refreshStatus?.progress?.kind === "graded_refresh");
  const smallHitsRunning =
    syncing && refreshStatus?.progress?.kind === "small_hits";

  // The Small Hits drawer: mounted while open or closing, shown (slid up)
  // once it's in the page, so it animates both ways.
  const [smallHitsMounted, setSmallHitsMounted] = useState(false);
  const [smallHitsShown, setSmallHitsShown] = useState(false);
  const drawerTimer = useRef(null);
  const backdropPressed = useRef(false);
  // The drawer's Add Card window (card search, then the usual Add form).
  const [smallHitSearchOpen, setSmallHitSearchOpen] = useState(false);
  const searchBackdropPressed = useRef(false);

  const [addForm, setAddForm] = useState(emptyAddForm());
  const [psaForm, setPsaForm] = useState(emptyPsaForm());
  const [variantItem, setVariantItem] = useState(null);
  const [variantChoice, setVariantChoice] = useState("");
  const [savingVariant, setSavingVariant] = useState(false);
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

  // Small hits live in their own drawer; the main table has the rest.
  const regularItems = useMemo(
    () => collection.items.filter((item) => !item.small_hit),
    [collection.items]
  );
  const smallHitItems = useMemo(
    () => collection.items.filter((item) => item.small_hit),
    [collection.items]
  );

  // Every space-separated term must match somewhere on the item, so
  // "lugia psa 9" narrows rather than widens.
  const filteredItems = useMemo(() => {
    const terms = collectionQuery
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

    if (terms.length === 0) {
      return regularItems;
    }

    return regularItems.filter((item) => {
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
  }, [regularItems, collectionQuery]);

  // Cards with no price for a column sort to the bottom either way, so
  // flipping the direction never buries the priced ones.
  function sortRows(rows) {
    if (!sort.key) {
      return rows;
    }

    const direction = sort.direction === "asc" ? 1 : -1;

    return [...rows].sort((a, b) => {
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
  }

  const sortedItems = useMemo(() => sortRows(filteredItems), [filteredItems, sort]);
  const sortedSmallHits = useMemo(() => sortRows(smallHitItems), [smallHitItems, sort]);

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

  // Search results that are variants of the same printing, so each result
  // can show the variants it's easy to confuse it with.
  const searchVariants = useMemo(() => {
    const groups = {};

    for (const card of searchResults) {
      const key = printingKey(card);
      if (key) {
        groups[key] = [...(groups[key] || []), card];
      }
    }

    return groups;
  }, [searchResults]);

  function otherVariants(card) {
    return (searchVariants[printingKey(card)] || []).filter(
      (other) => other.poketrace_id !== card.poketrace_id
    );
  }

  const searchPages = useMemo(
    () => chunk(searchResults, SEARCH_PAGE_SIZE),
    [searchResults]
  );
  const totalSearchPages = Math.max(searchPages.length, 1);
  const currentSearchPage = Math.min(searchPage, totalSearchPages - 1);

  // Each search is a PokeTrace request, so a query is fetched once per page
  // load however often it's typed.
  async function cachedSearch(text) {
    const key = text.toLowerCase();

    if (!searchCache.current.has(key)) {
      const data = await searchCards(text);
      searchCache.current.set(key, data.results || []);
    }

    return searchCache.current.get(key);
  }

  // Search as you type: once typing pauses, the matches drop down under
  // the box.
  useEffect(() => {
    const text = query.trim();
    latestSuggestQuery.current = text;
    setHighlighted(-1);

    if (text.length < SUGGEST_MIN_CHARS) {
      setSuggestions(null);
      return undefined;
    }

    const cached = searchCache.current.get(text.toLowerCase());
    if (cached) {
      setSuggestions(cached);
      return undefined;
    }

    setSuggestions("loading");

    const timer = setTimeout(async () => {
      let results;
      try {
        results = await cachedSearch(text);
      } catch {
        results = "error";
      }

      if (latestSuggestQuery.current === text) {
        setSuggestions(results);
      }
    }, SUGGEST_DELAY_MS);

    return () => clearTimeout(timer);
  }, [query]);

  const suggestionList = Array.isArray(suggestions)
    ? suggestions.slice(0, SUGGEST_LIMIT)
    : [];

  // Opens the Add form for a dropdown match. Its full results stand in as
  // the search, so the form knows which other variants it could be.
  function pickSuggestion(card) {
    setSuggestOpen(false);
    setSearchResults(Array.isArray(suggestions) ? suggestions : [card]);
    setSearchPage(0);
    setAddForm(emptyAddForm(card, smallHitsMounted));
  }

  function handleSearchKeyDown(event) {
    if (event.key === "Escape") {
      if (suggestOpen) {
        event.stopPropagation();
      }
      setSuggestOpen(false);
      return;
    }

    if (!suggestionList.length) {
      return;
    }

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setSuggestOpen(true);
      setHighlighted((current) => {
        const step = event.key === "ArrowDown" ? 1 : -1;
        return (current + step + suggestionList.length) % suggestionList.length;
      });
    } else if (event.key === "Enter" && suggestOpen && highlighted >= 0) {
      event.preventDefault();
      pickSuggestion(suggestionList[highlighted]);
    }
  }

  async function handleSearch(event) {
    event.preventDefault();

    if (query.trim().length < 2) {
      return;
    }

    setSuggestOpen(false);
    setSearching(true);
    setError("");
    setMessage("");

    try {
      const results = await cachedSearch(query.trim());
      setSearchResults(results);
      setSearchPage(0);

      if (results.length === 0) {
        setMessage("No cards matched that search.");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSearching(false);
    }
  }

  function clearCardSearch() {
    setQuery("");
    setSearchResults([]);
    setSearchPage(0);
    setSuggestOpen(false);
  }

  // The drawer and the main page share one card search, so each starts
  // from a clear box.
  function openSmallHits() {
    clearCardSearch();
    // Reopened while still closing: keep it instead of removing it.
    clearTimeout(drawerTimer.current);
    setSmallHitsMounted(true);
    // Shown a frame after mounting, so it slides up instead of appearing.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => setSmallHitsShown(true))
    );
  }

  function openSmallHitSearch() {
    clearCardSearch();
    setSmallHitSearchOpen(true);
    setTimeout(() => searchInputRef.current?.focus(), 0);
  }

  function closeSmallHitSearch() {
    clearCardSearch();
    setSmallHitSearchOpen(false);
  }

  function closeSmallHits() {
    setSmallHitSearchOpen(false);
    clearCardSearch();
    setSmallHitsShown(false);
    clearTimeout(drawerTimer.current);
    drawerTimer.current = setTimeout(() => setSmallHitsMounted(false), DRAWER_MS);
  }

  // While the drawer is open the page behind it doesn't scroll, and Escape
  // closes it (unless a window on top of it is open).
  useEffect(() => {
    if (!smallHitsMounted) {
      return undefined;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function handleKeyDown(event) {
      if (
        event.key !== "Escape" ||
        addForm.card ||
        psaForm.item ||
        variantItem ||
        zoomedCard
      ) {
        return;
      }

      if (smallHitSearchOpen) {
        closeSmallHitSearch();
      } else {
        closeSmallHits();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [smallHitsMounted, smallHitSearchOpen, addForm.card, psaForm.item, variantItem, zoomedCard]);

  // Small hits are only ever priced from the drawer, after you confirm.
  async function handleSmallHitsSync() {
    const cards = new Set(smallHitItems.map((item) => item.card_id)).size;
    const requests = Math.max(1, Math.ceil(cards / 20));

    const confirmed = window.confirm(
      `Are you sure you want to sync your ${cards} small hits?\n\n` +
        `This uses about ${requests} PokeTrace request${requests === 1 ? "" : "s"} ` +
        "of your 250 a day."
    );
    if (!confirmed) {
      return;
    }

    setError("");
    setMessage("");

    try {
      await startSmallHitsSync();
    } catch (err) {
      // Includes "already running" from the server.
      setError(err.message);
      loadRefreshStatus();
      return;
    }

    try {
      const status = await watchSync();
      setMessage(smallHitsSyncSummary(status));
    } catch (err) {
      setError(err.message);
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
        // You picked this variant with it shown above, so it's confirmed.
        variant_confirmed: true,
        variant_siblings: otherVariants(addForm.card).map((other) => ({
          poketrace_id: other.poketrace_id,
          variant: other.variant,
          image_url: other.image_url,
          rarity: other.rarity,
          raw_market_estimate: other.raw_market_estimate,
        })),
        small_hit: addForm.smallHit,
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
          : addForm.smallHit
            ? "Added to Small Hits."
            : "Card added and raw market price fetched."
      );
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDelete(item) {
    let quantity = 1;

    if (item.quantity > 1) {
      const answer = window.prompt(
        `How many copies do you want to remove? You own ${item.quantity}.`,
        "1"
      );

      if (answer === null) {
        return;
      }

      quantity = Number(answer);

      if (
        !Number.isInteger(quantity) ||
        quantity < 1 ||
        quantity > item.quantity
      ) {
        setError(`Enter a whole number from 1 to ${item.quantity}.`);
        return;
      }
    } else if (!window.confirm("Remove this card from your collection?")) {
      return;
    }

    setError("");
    setMessage("");

    try {
      await deleteCollectionItem(item.id, quantity);
      await loadCollection();
      setMessage(
        quantity === 1
          ? "Card removed from your collection."
          : `Removed ${quantity} copies from your collection.`
      );
    } catch (err) {
      setError(err.message);
    }
  }

  // Your PSA prices re-read from their PriceCharting pages. Nothing else
  // ever triggers this.
  async function handleGradedRefresh() {
    setError("");
    setMessage("");
    setGradedRefreshing(true);

    try {
      try {
        await startGradedRefresh();
      } catch (err) {
        // "Already running" from the server.
        setError(err.message);
        loadRefreshStatus();
        return;
      }

      const status = await watchSync();
      setMessage(gradedRefreshSummary(status));
    } catch (err) {
      setError(err.message);
    } finally {
      setGradedRefreshing(false);
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

  useEffect(() => {
    setVariantChoice(variantItem?.card.poketrace_id || "");
  }, [variantItem]);

  async function handleSaveVariant(event) {
    event.preventDefault();

    if (!variantItem || !variantChoice) {
      return;
    }

    const switching = variantChoice !== variantItem.card.poketrace_id;
    setSavingVariant(true);
    setError("");
    setMessage("");

    try {
      await setCollectionVariant(variantItem.id, variantChoice);
      setVariantItem(null);
      await loadCollection();
      setMessage(
        switching
          ? "Variant changed. Its price now comes from the variant you own."
          : "Variant confirmed."
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setSavingVariant(false);
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

  // The card search box, its dropdown and its results. The main page and
  // the Small Hits drawer show it one at a time, sharing its state.
  function renderCardSearch() {
    return (
      <>
        <form
          className="search-form"
          onSubmit={handleSearch}
        >
          <div className="search-input-wrap">
            <input
              ref={searchInputRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setSuggestOpen(true);
              }}
              onFocus={() => setSuggestOpen(true)}
              onBlur={() => setSuggestOpen(false)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Try: Glaceon GX, Charizard ex, Pikachu..."
              role="combobox"
              aria-expanded={suggestOpen && suggestions !== null}
              aria-controls="search-suggestions"
              autoComplete="off"
            />

            {suggestOpen && suggestions !== null && (
              <div
                id="search-suggestions"
                className="search-suggestions"
                role="listbox"
                // Keeps focus in the box, so choosing a match doesn't close
                // the list before the choice registers.
                onMouseDown={(event) => event.preventDefault()}
              >
                {suggestions === "loading" && (
                  <div className="search-suggestion-note">Searching…</div>
                )}

                {suggestions === "error" && (
                  <div className="search-suggestion-note">
                    Search isn't available right now. Press Search to try again.
                  </div>
                )}

                {Array.isArray(suggestions) && suggestions.length === 0 && (
                  <div className="search-suggestion-note">No matching cards.</div>
                )}

                {suggestionList.map((card, index) => (
                  <button
                    type="button"
                    key={card.poketrace_id}
                    role="option"
                    aria-selected={index === highlighted}
                    className={`search-suggestion ${
                      index === highlighted ? "highlighted" : ""
                    }`}
                    onMouseEnter={() => setHighlighted(index)}
                    onClick={() => pickSuggestion(card)}
                  >
                    {card.image_url ? (
                      <img src={card.image_url} alt="" loading="lazy" />
                    ) : (
                      <span className="search-suggestion-thumb" />
                    )}

                    <span className="search-suggestion-copy">
                      <strong>{cardTitle(card)}</strong>
                      <span>
                        {card.set_name || "Unknown set"}
                        {variantLabel(card.variant)
                          ? ` · ${variantLabel(card.variant)}`
                          : ""}
                      </span>
                    </span>

                    <span className="search-suggestion-price">
                      {money(card.raw_market_estimate)}
                    </span>
                  </button>
                ))}

                {suggestionList.length > 0 && (
                  <div className="search-suggestion-note">
                    Press Search for all {suggestions.length} results.
                  </div>
                )}
              </div>
            )}
          </div>

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
                            <VariantPill variant={card.variant} />

                            {card.rarity && (
                              <span className="rarity-pill">
                                {card.rarity}
                              </span>
                            )}
                          </div>

                          {otherVariants(card).length > 0 && (
                            <span className="variant-also">
                              Also comes as{" "}
                              {otherVariants(card)
                                .map((other) => variantLabel(other.variant))
                                .join(", ")}
                            </span>
                          )}

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
                            setAddForm(emptyAddForm(card, smallHitsMounted))
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
      </>
    );
  }

  function renderStatus() {
    if (!message && !error) {
      return null;
    }

    return (
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
      );
  }

  // The collection table, for the main collection or the small hits.
  function renderTable(rows) {
    return (
          <div
            className="collection-scroll"
            hidden={rows.length === 0}
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
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="table-card-cell">
                        <ItemImage
                          item={item}
                          compact
                          onClick={() =>
                            openZoom(item.card, ownedDetails(item))
                          }
                        />

                        <div>
                          <strong>{cardTitle(item.card)}</strong>

                          <span>
                            {item.card.set_name ||
                              "Unknown set"}
                          </span>

                          <span className="table-variant-row">
                            <VariantPill variant={item.card.variant} />

                            {item.variant_info?.needs_confirmation ? (
                              <button
                                type="button"
                                className="variant-confirm-button"
                                onClick={() => setVariantItem(item)}
                              >
                                Confirm variant
                              </button>
                            ) : item.variant_info?.options?.length > 1 ? (
                              <button
                                type="button"
                                className="text-button variant-change-link"
                                onClick={() => setVariantItem(item)}
                              >
                                Change
                              </button>
                            ) : null}
                          </span>
                        </div>
                      </div>
                    </td>

                    <td>
                      <strong className="qty-cell">
                        {item.quantity}
                      </strong>
                    </td>

                    <td>
                      <div className="price-cell">
                        <strong>
                          {money(
                            item.market_values?.[item.ownership_grade]
                              ?.estimate
                          )}
                        </strong>

                        {item.market_values?.[item.ownership_grade]?.is_manual ? (
                          item.market_values[item.ownership_grade].source_url ? (
                            <a
                              className="manual-source-link"
                              href={item.market_values[item.ownership_grade].source_url}
                              target="_blank"
                              rel="noreferrer"
                              title={
                                item.market_values[item.ownership_grade].auto_source
                                  ? "Updated from this page by Refresh Graded Cards"
                                  : "Open the source you entered this price from"
                              }
                            >
                              {item.market_values[item.ownership_grade].auto_source ||
                                "Manual"}
                            </a>
                          ) : (
                            <span
                              className="manual-source-link"
                              title="No source URL saved — add one via PSA values to link this"
                            >
                              Manual
                            </span>
                          )
                        ) : (
                          <button
                            type="button"
                            className="manual-source-link source-count-link"
                            title="See where this price came from"
                            onClick={() =>
                              openZoom(item.card, ownedDetails(item))
                            }
                          >
                            {item.market_values?.[item.ownership_grade]
                              ?.source_count || 0}{" "}
                            source
                            {(item.market_values?.[item.ownership_grade]
                              ?.source_count || 0) === 1
                              ? ""
                              : "s"}
                          </button>
                        )}
                      </div>
                    </td>

                    {COLLECTION_COLUMNS.filter((column) => column.window).map(
                      (column) => (
                        <td key={column.key}>
                          <ChangeCell
                            change={item.owned_grade_changes?.[column.window]}
                            window={column.window}
                          />
                        </td>
                      )
                    )}

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
                            openZoom(item.card, ownedDetails(item))
                          }
                        >
                          Details
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
                            handleDelete(item)
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
    );
  }

  return (
    <main className="app-shell">
      <section className="hero">
        <div className="portfolio-card">
          <div className="portfolio-card-copy">
            <span>Known collection value</span>

            <strong>{money(ownedTotal)}</strong>

            <small>
              {collection.summary?.item_count || 0} collection entries ·
              Small Hits {money(collection.summary?.small_hits_total || 0)}
            </small>
          </div>

          <button
            type="button"
            className="small-hits-button desktop-only"
            onClick={openSmallHits}
          >
            Small Hits
          </button>
        </div>
      </section>

      <Dashboard
        refreshKey={dashboardVersion}
        actions={
          <div className="refresh-control">
            <div className="refresh-buttons">
              <button
                type="button"
                className="sync-button primary"
                onClick={handleSyncNow}
                disabled={syncing}
              >
                {syncing && !gradedRunning && !smallHitsRunning ? "Syncing…" : "Refresh Prices"}
              </button>

              <button
                type="button"
                className="sync-button"
                onClick={handleGradedRefresh}
                disabled={syncing}
              >
                {gradedRunning ? "Refreshing…" : "Refresh Graded Cards"}
              </button>
            </div>

            <small>{refreshStatusText(refreshStatus)}</small>
            <small>{gradedRefreshText(refreshStatus)}</small>
          </div>
        }
      />

      <section className="search-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ADD A CARD</p>
            <h2>Search Pokémon cards</h2>
          </div>

        </div>

        {!smallHitsMounted && renderCardSearch()}
      </section>

      {!smallHitsMounted && renderStatus()}

      <section className="collection-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">YOUR CARDS</p>
            <h2>Collection</h2>
          </div>

          <div className="collection-heading-actions">
            <button
              type="button"
              className="small-hits-button mobile-only"
              onClick={openSmallHits}
            >
              Small Hits
            </button>

            <div className="collection-meta">
              <span>
                Missing owned value:{" "}
                <strong>
                  {collection.summary
                    ?.items_missing_owned_value || 0}
                </strong>
              </span>
            </div>

            <div className="range-tabs" role="group" aria-label="Collection view">
              {[
                { key: "table", label: "Table" },
                { key: "gallery", label: "Gallery" },
              ].map(({ key, label }) => (
                <button
                  type="button"
                  key={key}
                  className={collectionView === key ? "active" : ""}
                  aria-pressed={collectionView === key}
                  onClick={() => setCollectionView(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {!loadingCollection && regularItems.length > 0 && (
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
                {regularItems.length}
              </span>
            )}
          </div>
        )}

        {loadingCollection ? (
          <div className="empty-state">
            Loading collection…
          </div>
        ) : regularItems.length === 0 ? (
          <div className="empty-state">
            Search for a card above and add your first card.
          </div>
        ) : collectionView === "gallery" ? (
          <div
            className="collection-gallery"
            hidden={filteredItems.length === 0}
          >
            {sortedItems.map((item) => (
              <article className="gallery-card" key={item.id}>
                {item.card.image_url || slabPhoto(item) ? (
                  <ItemImage
                    item={item}
                    onClick={() => openZoom(item.card, ownedDetails(item))}
                  />
                ) : (
                  <div className="gallery-placeholder">No image</div>
                )}

                <strong className="gallery-title">
                  {cardTitle(item.card)}
                </strong>

                <span className="gallery-value">
                  {money(item.owned_market_value_total)}
                </span>

                <span className="gallery-grade">
                  {gradeLabel(item.ownership_grade)}
                  {variantLabel(item.card.variant)
                    ? ` · ${variantLabel(item.card.variant)}`
                    : ""}
                  {item.quantity > 1 ? ` · Qty ${item.quantity}` : ""}
                </span>
              </article>
            ))}
          </div>
        ) : (
          renderTable(sortedItems)
        )}

        {!loadingCollection &&
          regularItems.length > 0 &&
          filteredItems.length === 0 && (
            <div className="empty-state">
              No cards in your collection match "
              {collectionQuery.trim()}".
            </div>
          )}
      </section>

      {smallHitsMounted && (
        <div
          className={`modal-backdrop small-hits-backdrop ${smallHitsShown ? "open" : ""}`}
          // A click anywhere off the drawer slides it back down and closes
          // it; one that started inside it (e.g. selecting text) doesn't.
          onMouseDown={(event) => {
            backdropPressed.current = event.target === event.currentTarget;
          }}
          onClick={(event) => {
            if (backdropPressed.current && event.target === event.currentTarget) {
              closeSmallHits();
            }
            backdropPressed.current = false;
          }}
        >
          <section
            className="small-hits-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Small Hits"
          >
            <div className="small-hits-header">
              <div className="small-hits-summary">
                <p className="eyebrow">SMALL HITS</p>
                <h2>{money(collection.summary?.small_hits_total || 0)}</h2>
                <span>
                  {smallHitItems.length} {smallHitItems.length === 1 ? "card" : "cards"} ·
                  {refreshStatus?.small_hits?.finished_at
                    ? ` last synced ${pullTime(refreshStatus.small_hits.finished_at)}`
                    : " not synced here yet"}
                </span>
              </div>

              {/* Kept well away from the close button so closing never
                  starts a sync by accident. */}
              <button
                type="button"
                className="small-hits-sync-button"
                onClick={handleSmallHitsSync}
                disabled={syncing || smallHitItems.length === 0}
              >
                {smallHitsRunning ? "Syncing…" : "SYNC SMALL HITS"}
              </button>

              <button
                type="button"
                className="close-button small-hits-close"
                aria-label="Close Small Hits"
                onClick={closeSmallHits}
              >
                ×
              </button>
            </div>

            <div className="small-hits-body">
              {!smallHitSearchOpen && renderStatus()}

              <div className="small-hits-toolbar">
                <button
                  type="button"
                  className="sync-button primary"
                  onClick={openSmallHitSearch}
                >
                  + Add Card
                </button>
              </div>

              {smallHitItems.length === 0 ? (
                <div className="empty-state">
                  Use Add Card to add your first small hit.
                </div>
              ) : (
                renderTable(sortedSmallHits)
              )}
            </div>
          </section>
        </div>
      )}

      {smallHitsMounted && smallHitSearchOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            searchBackdropPressed.current = event.target === event.currentTarget;
          }}
          onClick={(event) => {
            if (searchBackdropPressed.current && event.target === event.currentTarget) {
              closeSmallHitSearch();
            }
            searchBackdropPressed.current = false;
          }}
        >
          <div
            className="modal small-hit-search-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Add a small hit"
          >
            <div className="modal-heading">
              <div>
                <p className="eyebrow">ADD TO SMALL HITS</p>
                <h2>Search Pokémon cards</h2>
                <span>Cards you add here go to Small Hits.</span>
              </div>

              <button
                type="button"
                className="close-button"
                aria-label="Close"
                onClick={closeSmallHitSearch}
              >
                ×
              </button>
            </div>

            {renderStatus()}

            {renderCardSearch()}
          </div>
        </div>
      )}

      {addForm.card && (
        <div className="modal-backdrop">
          <form
            className="modal"
            onSubmit={handleAdd}
          >
            <div className="modal-heading">
              <div>
                <p className="eyebrow">
                  {addForm.smallHit ? "ADD TO SMALL HITS" : "ADD TO COLLECTION"}
                </p>

                <h2>
                  {cardTitle(addForm.card)}
                </h2>

                <span>
                  {addForm.card.set_name}
                </span>

                <div className="add-variant-row">
                  <span>Variant:</span>
                  <VariantPill variant={addForm.card.variant} />
                </div>

                {otherVariants(addForm.card).length > 0 && (
                  <p className="variant-warning">
                    This card also comes as{" "}
                    {otherVariants(addForm.card)
                      .map((other) => variantLabel(other.variant))
                      .join(", ")}
                    , priced separately. Make sure this is the one you own.
                  </p>
                )}
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
                  manually. With a PriceCharting URL, Refresh
                  Graded Cards updates the grade you own.
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

              <div className="url-input-row">
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

                {psaForm.source_url && (
                  <button
                    type="button"
                    className="url-clear-button"
                    aria-label="Remove source URL"
                    title="Remove source URL"
                    onClick={() =>
                      setPsaForm((current) => ({
                        ...current,
                        source_url: "",
                      }))
                    }
                  >
                    ×
                  </button>
                )}
              </div>
            </label>

            <div className="grade-input-grid">
              {[
                "psa_8",
                "psa_9",
                "psa_10",
              ].map((field, index) => (
                <label key={field}>
                  PSA {index + 8}

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

      {variantItem && (
        <div className="modal-backdrop">
          <form className="modal" onSubmit={handleSaveVariant}>
            <div className="modal-heading">
              <div>
                <p className="eyebrow">WHICH VARIANT DO YOU OWN?</p>
                <h2>{cardTitle(variantItem.card)}</h2>
                <span>{variantItem.card.set_name}</span>
              </div>

              <button
                type="button"
                className="close-button"
                onClick={() => setVariantItem(null)}
              >
                ×
              </button>
            </div>

            <p className="modal-tip">
              Each variant has its own market price. Reverse Holo has the
              shine on the card body outside the artwork; Holo has it on the
              artwork.
            </p>

            <div className="variant-options">
              {(variantItem.variant_info?.options || []).map((option) => (
                <label
                  key={option.poketrace_id}
                  className={`variant-option ${
                    variantChoice === option.poketrace_id ? "selected" : ""
                  }`}
                >
                  <input
                    type="radio"
                    name="variant"
                    value={option.poketrace_id}
                    checked={variantChoice === option.poketrace_id}
                    onChange={() => setVariantChoice(option.poketrace_id)}
                  />

                  {option.image_url && (
                    <img src={option.image_url} alt={option.label || ""} />
                  )}

                  <span className="variant-option-copy">
                    <strong>{option.label || variantLabel(option.variant)}</strong>
                    <span>
                      {option.raw_market_estimate !== null &&
                      option.raw_market_estimate !== undefined
                        ? `Raw ${money(option.raw_market_estimate)}`
                        : "No raw price yet"}
                      {option.poketrace_id === variantItem.card.poketrace_id
                        ? " · saved now"
                        : ""}
                    </span>
                  </span>
                </label>
              ))}
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setVariantItem(null)}
              >
                Cancel
              </button>

              <button type="submit" disabled={savingVariant || !variantChoice}>
                {savingVariant
                  ? "Saving…"
                  : variantChoice === variantItem.card.poketrace_id
                    ? "Confirm variant"
                    : "Switch variant"}
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