from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable


class WebhookSignatureError(ValueError):
    """Raised when a signed webhook request cannot be authenticated."""


class WebhookReplayError(ValueError):
    """Raised when a signed webhook was already accepted."""


class InMemoryWebhookReplayStore:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def claim(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def verify_webhook_signature(
    *,
    secret: str | None,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    clock: Callable[[], float] | None = None,
    tolerance_seconds: int | None = None,
    replay_store: InMemoryWebhookReplayStore | None = None,
) -> None:
    """Verify an Auto-Crypto HMAC signature.

    Signature format: sha256=<hex digest>
    Signed payload: <timestamp>.<raw request body>
    """

    if not secret:
        return
    if not timestamp or not signature:
        raise WebhookSignatureError("missing webhook signature headers")
    if not signature.startswith("sha256="):
        raise WebhookSignatureError("invalid webhook signature format")
    if tolerance_seconds is not None:
        try:
            timestamp_value = float(timestamp)
        except ValueError as exc:
            raise WebhookSignatureError("invalid webhook timestamp") from exc
        now = (clock or time.time)()
        if abs(now - timestamp_value) > tolerance_seconds:
            raise WebhookSignatureError("stale webhook timestamp")

    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    supplied = signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected, supplied):
        raise WebhookSignatureError("invalid webhook signature")
    if replay_store is not None:
        replay_key = hashlib.sha256(timestamp.encode("utf-8") + b"." + body).hexdigest()
        if not replay_store.claim(replay_key):
            raise WebhookReplayError("webhook replay detected")
