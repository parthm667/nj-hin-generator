"""PostgreSQL-shared limits keyed by a secret HMAC of the ASGI peer address."""

import hashlib
import hmac
import math

from sqlalchemy import text


class RequestLimitExceeded(Exception):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after))
        super().__init__("Too many requests. Please try again later.")


def client_key_for_request(request, secret):
    """Ignore forwarding headers; hosting must preserve a trustworthy peer.

    Behind an unconfigured proxy clients intentionally share the proxy's quota.
    Configure the ASGI server's trusted proxies explicitly, never trust arbitrary
    X-Forwarded-For supplied by the requester.
    """
    if not isinstance(secret, str) or not secret:
        raise ValueError("An anonymous rate-limit secret is required")
    peer = request.client.host if request.client else "unknown"
    return hmac.new(secret.encode(), peer.encode(), hashlib.sha256).hexdigest()


def consume_request_limit(db, client_key, scope, limit, window_seconds):
    """Consume one permit in caller transaction; commit even for later 4xx.

    Fixed windows start with the first accepted request, not a process clock.
    ON CONFLICT locks the shared row, so replica-local counters cannot bypass it.
    """
    if not client_key or len(client_key) > 64 or not scope or len(scope) > 40:
        raise ValueError("Invalid request limit key")
    if limit < 1 or not 1 <= window_seconds <= 86400:
        raise ValueError("Request limits must be positive and windows at most one day")
    parameters = {"key": client_key, "scope": scope, "limit": limit, "seconds": window_seconds}
    accepted = db.execute(text("""
        INSERT INTO request_limits (scope, client_key, window_start, count)
        VALUES (:scope, :key, clock_timestamp(), 1)
        ON CONFLICT (scope, client_key) DO UPDATE SET
            count = CASE WHEN request_limits.window_start <= clock_timestamp()
                         - make_interval(secs => :seconds) THEN 1
                         ELSE request_limits.count + 1 END,
            window_start = CASE WHEN request_limits.window_start <= clock_timestamp()
                         - make_interval(secs => :seconds) THEN clock_timestamp()
                         ELSE request_limits.window_start END
        WHERE request_limits.count < :limit
           OR request_limits.window_start <= clock_timestamp() - make_interval(secs => :seconds)
        RETURNING count
    """), parameters).scalar_one_or_none()
    if accepted is None:
        remaining = db.execute(text("""
            SELECT EXTRACT(EPOCH FROM window_start + make_interval(secs => :seconds)
                           - clock_timestamp())
            FROM request_limits WHERE scope=:scope AND client_key=:key
        """), parameters).scalar_one()
        raise RequestLimitExceeded(math.ceil(float(remaining)))

