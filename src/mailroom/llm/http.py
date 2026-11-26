"""HTTP transport shared by the adapters: one retry policy for every provider."""

from __future__ import annotations

import random
import time
from typing import Any, Callable

import httpx

from .types import LLMError

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def post_json_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """POST JSON; retry rate limits, overloads and network errors with exponential backoff.

    Backoff is `base * 2^attempt` with full jitter, capped at `max_delay`. A `Retry-After` header
    (seconds) wins when present. Client errors other than the retryable set fail immediately: a 400
    will not fix itself.
    """
    last_error: LLMError | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.post(url, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = LLMError(f"network error calling {url}: {exc!r}")
            retry_after = None
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise LLMError("provider returned non-JSON body", response.status_code, response.text[:500]) from exc
            last_error = LLMError(
                f"HTTP {response.status_code} from {url}", response.status_code, response.text[:2000]
            )
            if response.status_code not in RETRYABLE_STATUS:
                raise last_error
            retry_after = _parse_retry_after(response.headers.get("retry-after"))

        if attempt == max_retries:
            break
        delay = retry_after if retry_after is not None else random.uniform(0, min(max_delay, base_delay * 2**attempt))
        sleep(min(delay, max_delay))

    assert last_error is not None
    raise last_error


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form; fall back to computed backoff
