import { useEffect, useRef, useState } from "react";

// How far (after resistance) a pull has to go before letting go refreshes.
const THRESHOLD = 70;
const MAX_PULL = 110;
// Finger movement ignored before deciding whether this is a pull at all.
const SLOP = 8;

// Phones and tablets only; desktop never gets it.
function isTouchDevice() {
  return window.matchMedia?.("(pointer: coarse)").matches;
}

// A fresh copy of the site: drops anything the page cached itself, then
// reloads from a new URL so the browser can't reuse its cached copy.
async function hardRefresh() {
  try {
    if ("caches" in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    }

    if (navigator.serviceWorker) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((registration) => registration.unregister()));
    }
  } catch {
    // Best effort; the reload below still fetches a fresh page.
  }

  const url = new URL(window.location.href);
  url.searchParams.set("refresh", Date.now().toString());
  window.location.replace(url.toString());
}

// A pull that starts in an open modal, or in something scrolled down on its
// own, belongs to that element.
function startsInScrolledElement(target) {
  for (let element = target; element && element !== document.body; element = element.parentElement) {
    if (element.scrollTop > 0 || element.classList?.contains("modal-backdrop")) {
      return true;
    }
  }

  return false;
}

// Mobile pull-to-refresh: at the top of the page, pull down and let go to
// clear the cache and reload, like an app.
function PullToRefresh() {
  const [pull, setPull] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const start = useRef(null);
  const pullRef = useRef(0);

  useEffect(() => {
    // Tidy away the ?refresh= a hard refresh added.
    const url = new URL(window.location.href);
    if (url.searchParams.has("refresh")) {
      url.searchParams.delete("refresh");
      window.history.replaceState(null, "", url.toString());
    }

    if (!isTouchDevice()) {
      return undefined;
    }

    function update(value) {
      pullRef.current = value;
      setPull(value);
    }

    function onStart(event) {
      start.current =
        event.touches.length === 1 &&
        window.scrollY <= 0 &&
        !startsInScrolledElement(event.target)
          ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
          : null;
    }

    function onMove(event) {
      if (!start.current) {
        return;
      }

      const dx = event.touches[0].clientX - start.current.x;
      const dy = event.touches[0].clientY - start.current.y;

      if (pullRef.current === 0) {
        // Scrolling up or swiping sideways (the chart, the table) isn't a pull.
        if (dy < SLOP) {
          if (dy < -SLOP || Math.abs(dx) > SLOP) {
            start.current = null;
          }
          return;
        }

        if (Math.abs(dx) > dy) {
          start.current = null;
          return;
        }
      }

      if (window.scrollY > 0) {
        start.current = null;
        update(0);
        return;
      }

      update(Math.min(MAX_PULL, Math.max(0, dy - SLOP) * 0.5));
    }

    function onEnd() {
      start.current = null;

      if (pullRef.current >= THRESHOLD) {
        setRefreshing(true);
        update(THRESHOLD);
        hardRefresh();
      } else if (pullRef.current > 0) {
        update(0);
      }
    }

    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("touchmove", onMove, { passive: true });
    window.addEventListener("touchend", onEnd);
    window.addEventListener("touchcancel", onEnd);

    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
      window.removeEventListener("touchcancel", onEnd);
    };
  }, []);

  if (!pull && !refreshing) {
    return null;
  }

  const progress = Math.min(pull / THRESHOLD, 1);

  return (
    <div
      className="pull-refresh"
      style={{ transform: `translate(-50%, ${pull - 44}px)`, opacity: progress }}
      role="status"
    >
      <span
        className={`pull-refresh-icon ${refreshing ? "spinning" : ""}`}
        style={refreshing ? undefined : { transform: `rotate(${progress * 270}deg)` }}
      >
        ↻
      </span>

      <span>
        {refreshing
          ? "Refreshing…"
          : progress >= 1
            ? "Release to refresh"
            : "Pull to refresh"}
      </span>
    </div>
  );
}

export default PullToRefresh;
