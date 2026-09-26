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

  return payload;
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

export function addCollectionItem(payload) {
  return request("/api/collection", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Removes `quantity` copies (all of them if omitted).
export function deleteCollectionItem(itemId, quantity) {
  const params = quantity ? `?quantity=${quantity}` : "";

  return request(`/api/collection/${itemId}${params}`, {
    method: "DELETE",
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

export function saveGradedValues(cardId, payload) {
  return request(`/api/cards/${cardId}/graded-values`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
