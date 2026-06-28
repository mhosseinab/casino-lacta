"""Structured, secret-safe operational logging.

One concern: emit JSON log records carrying contextual correlation IDs (``trace_id``,
``bet_id``, ``round_id``, …) as structured fields, never free text — so an ops pipeline
can index by bet/trace. Distinct from the durable ``audit_events`` DB trail (S35's
``RiskLog``): that is a persisted audit record; this is stdout operational logging. No
prior logging facility existed in ``app/`` (greenfield), so this is new, not a fork.

★ NO SECRETS IN LOGS (CLAUDE.md privacy/security). :func:`redact_secrets` DROPS any field
whose name is a known secret — the unrevealed raw ``server_seed``/``seed_encrypted``, any
``*_token``/``*_password``/``*_secret``/``*_api_key`` — BEFORE the record is built. The
PUBLIC provable-fairness fields (``server_seed_hash`` the commitment, ``client_seed``,
``nonce``) are intentionally KEPT: they are safe to surface and redacting them would lose
the audit trail. Redaction is key-based (the standard); callers must not stuff a raw seed
into a non-secret field.
"""

from __future__ import annotations

import json
import logging
from typing import Any

LOGGER_NAME = "app.observability"

# Normalized (lowercased, separators stripped) names that are ALWAYS secret.
_SECRET_KEYS = frozenset(
    {
        "serverseed",  # the unrevealed raw seed — NOT serverseedhash (public commitment)
        "seedencrypted",  # the stored raw seed material
        "password",
        "secret",
        "token",
        "authorization",
        "cookie",
        "apikey",
        "sessiontoken",
    }
)
# Suffixes that mark a secret regardless of prefix (access_token, refresh_token,
# db_password, signing_secret, stripe_api_key, …). None of the public fairness
# fields end with these.
_SECRET_SUFFIXES = ("token", "password", "secret", "apikey", "privatekey")


def _is_secret(key: str) -> bool:
    norm = key.lower().replace("_", "").replace("-", "")
    return norm in _SECRET_KEYS or norm.endswith(_SECRET_SUFFIXES)


def redact_secrets(fields: dict[str, Any]) -> dict[str, Any]:
    """Return ``fields`` with every secret-named key DROPPED (not masked) — so the record
    carries no secret field at all."""
    return {key: value for key, value in fields.items() if not _is_secret(key)}


def structured_event(event: str, **fields: Any) -> dict[str, Any]:
    """Build the structured record: the ``event`` name plus redacted contextual fields."""
    record = {"event": event}
    record.update(redact_secrets(fields))
    return record


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> dict[str, Any]:
    """Emit one structured JSON record (secrets dropped) and return the record built."""
    record = structured_event(event, **fields)
    get_logger().log(level, json.dumps(record, default=str, sort_keys=True))
    return record
