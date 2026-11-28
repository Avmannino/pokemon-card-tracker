import cardImages from "./cardImages.json";

// Your own card images by card id, replacing the catalog's image_url.
function applyCardImages(value) {
  if (Array.isArray(value)) {
    value.forEach(applyCardImages);
  } else if (value && typeof value === "object") {
    if ("image_url" in value && cardImages[value.id]) {
      value.image_url = `${import.meta.env.BASE_URL}${cardImages[value.id]}`;
    }

    Object.values(value).forEach(applyCardImages);
  }

  return value;
}

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;

  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail =
      payload?.detail ||
      `Request failed with HTTP ${response.status}`;
    throw new Error(detail);
  }

  return applyCardImages(payload);
}

export function searchCards(query) {
  const params = new URLSearchParams({ q: query });
  return request(`/api/search?${params.toString()}`);
}

export function getCollection() {
  return request("/api/collection");
}

export function getPortfolioDashboard() {
  return request("/api/portfolio/dashboard");
}

// Raw cards ranked by grading upside, from stored prices only. `gradingCost`
// null = the backend's default.
export function getGradingOpportunities(gradingCost = null) {
  const params = gradingCost === null ? "" : `?grading_cost=${encodeURIComponent(gradingCost)}`;
  return request(`/api/grading-opportunities${params}`);
}

// Grading checker: cards on PriceCharting matching `query`.
export function searchGradingChecker(query) {
  return request(`/api/grading-checker/search?q=${encodeURIComponent(query)}`);
}

// Grading checker: any card's grading outlook from its PriceCharting page,
// without adding it.
export function checkGradingCard(url, gradingCost = null) {
  const params = new URLSearchParams({ url });
  if (gradingCost !== null && gradingCost !== undefined) {
    params.set("grading_cost", String(gradingCost));
  }
  return request(`/api/grading-checker/card?${params.toString()}`);
}

// Links a raw card to its PriceCharting page for Grading Opportunities
// (empty removes it); saving reads that page once.
export function setPriceChartingLink(cardId, url) {
  return request(`/api/cards/${cardId}/pricecharting-link`, {
    method: "PUT",
    body: JSON.stringify({ url: url || null }),
  });
}

export function addCollectionItem(payload) {
  return request("/api/collection", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Sets how many copies of a card you hold (1 or more); more copies count as
// cards added, fewer as cards removed.
export function setCollectionQuantity(itemId, quantity) {
  return request(`/api/collection/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify({ quantity }),
  });
}

// Removes `quantity` copies (all of them if omitted).
export function deleteCollectionItem(itemId, quantity) {
  const params = quantity ? `?quantity=${quantity}` : "";

  return request(`/api/collection/${itemId}${params}`, {
    method: "DELETE",
  });
}

// Confirms (same id) or corrects (another variant of the printing) which
// variant a collection card is.
export function setCollectionVariant(itemId, poketraceId) {
  return request(`/api/collection/${itemId}/variant`, {
    method: "POST",
    body: JSON.stringify({ poketrace_id: poketraceId }),
  });
}

export function getRefreshStatus() {
  return request("/api/refresh-status");
}

// Starts a real price sync in the background; poll getRefreshStatus() for
// progress.
export function startSync() {
  return request("/api/refresh-now", {
    method: "POST",
  });
}

// Re-reads your PSA prices from their PriceCharting pages in the background;
// only ever started by the Refresh Graded Cards button.
export function startGradedRefresh() {
  return request("/api/refresh-graded", {
    method: "POST",
  });
}

// Prices the cards in the Small Hits drawer in the background; only ever
// started by its SYNC SMALL HITS button.
export function startSmallHitsSync() {
  return request("/api/refresh-small-hits", {
    method: "POST",
  });
}

// --- Sports section (CardSight AI) ---

// Your sports cards from saved data; never calls CardSight.
export function getSportsCollection() {
  return request("/api/sports/collection");
}

// One CardSight call (repeats within the hour are free).
export function searchSportsCards({ name, year, number, set }) {
  const params = new URLSearchParams({ name: name.trim() });
  if (year?.trim()) params.set("year", year.trim());
  if (number?.trim()) params.set("number", number.trim());
  if (set?.trim()) params.set("set", set.trim());
  return request(`/api/sports/search?${params.toString()}`);
}

// Values and grading outlook for up to 100 cards: at most one CardSight
// call, none for cards checked recently.
export function getSportsPrices(cardsightIds) {
  return request("/api/sports/prices", {
    method: "POST",
    body: JSON.stringify({ cardsight_ids: cardsightIds }),
  });
}

export function addSportsCard(payload) {
  return request("/api/sports/items", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Only the fields given change; manual_value null clears your own value.
export function updateSportsCard(itemId, fields) {
  return request(`/api/sports/items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export function deleteSportsCard(itemId) {
  return request(`/api/sports/items/${itemId}`, { method: "DELETE" });
}

// Reprices every sports card (one CardSight call per 100 cards).
export function refreshSportsPrices() {
  return request("/api/sports/refresh", { method: "POST" });
}

// CardSight's catalog image, for a card with no sale photo.
export function sportsImageUrl(cardsightId) {
  return `${API_BASE_URL}/api/sports/image/${cardsightId}`;
}

export function saveGradedValues(cardId, payload) {
  return request(`/api/cards/${cardId}/graded-values`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
