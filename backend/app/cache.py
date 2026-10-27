"""Built responses (the collection and the dashboard), reused until
something changes.

Building them reads every card's price history, which takes seconds on
Render's free instance, so they're built once and served from memory.
Anything that changes the data clears them: main.py after every write
request, price_sync after a sync, when it also rebuilds them so the next
page load is instant. They also expire after a few minutes, as a safety net
for edits made straight in Supabase and so time-based figures (1D changes,
the chart's latest point) keep moving.
"""

import threading
import time
from typing import Any, Callable


MAX_AGE_SECONDS = 600

_lock = threading.RLock()
_builder: Callable[[], Any] | None = None
_value: Any = None
_built_at = 0.0
_built_generation = -1
# Bumped by every change, so a build that started before the change is
# never treated as current.
_generation = 0


def register(builder: Callable[[], Any]) -> None:
    global _builder
    _builder = builder


def _fresh() -> bool:
    return (
        _built_generation == _generation
        and time.monotonic() - _built_at < MAX_AGE_SECONDS
    )


def get() -> Any:
    """The current build, building it first if needed. Concurrent callers
    share one build."""
    global _value, _built_at, _built_generation

    if _fresh():
        return _value

    with _lock:
        if _fresh():
            return _value

        generation = _generation
        value = _builder()
        _value, _built_at, _built_generation = value, time.monotonic(), generation
        return value


def invalidate() -> None:
    global _generation, _value
    _generation += 1
    _value = None


def rebuild() -> None:
    invalidate()
    if _builder is not None:
        get()
