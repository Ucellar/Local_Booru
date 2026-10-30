"""Process-local media download policy and durable blocked intents.

The session switch is intentionally process-local: it is chosen at startup and
cannot be silently changed by reconnecting the browser extension.  Every shared
media downloader must call :func:`require_allowed` before network I/O.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any


_LOCK = threading.RLock()
_SESSION_ENABLED = True
_SESSION_REASON = "startup_default"
_SESSION_CONFIGURED = False


@dataclass(frozen=True)
class DownloadDecision:
    allowed: bool
    operation: str
    source_site: str
    media_url: str
    reason: str
    intent_id: int = 0


class DownloadBlockedByPolicy(RuntimeError):
    def __init__(self, decision: DownloadDecision):
        self.decision = decision
        super().__init__(
            f"media download blocked by session policy: operation={decision.operation} "
            f"source={decision.source_site or '-'} reason={decision.reason}"
        )


def configure_session(enabled: bool, *, reason: str = "startup_dialog") -> None:
    global _SESSION_ENABLED, _SESSION_REASON, _SESSION_CONFIGURED
    with _LOCK:
        _SESSION_ENABLED = bool(enabled)
        _SESSION_REASON = str(reason or "session_policy")
        _SESSION_CONFIGURED = True


def configure_from_settings(settings: dict | None) -> bool:
    """Configure once from defaults when startup did not show the dialog."""
    global _SESSION_CONFIGURED
    with _LOCK:
        if _SESSION_CONFIGURED:
            return _SESSION_ENABLED
    default = bool((settings or {}).get("session_media_downloads_default", True))
    configure_session(default, reason="settings_default")
    return default


def session_status(settings: dict | None = None) -> dict[str, Any]:
    if not _SESSION_CONFIGURED:
        configure_from_settings(settings)
    with _LOCK:
        return {
            "enabled": bool(_SESSION_ENABLED),
            "configured": bool(_SESSION_CONFIGURED),
            "reason": str(_SESSION_REASON),
        }


def downloads_enabled(settings: dict | None = None) -> bool:
    return bool(session_status(settings).get("enabled"))


def _record_intent(
    settings: dict | None,
    *,
    operation: str,
    source_site: str,
    media_url: str,
    post_url: str,
    payload: dict[str, Any] | None,
    reason: str,
) -> int:
    if not settings:
        return 0
    try:
        from core.database.connection import db
        now = int(time.time())
        with db(settings, write=True) as con:
            con.execute(
                """INSERT INTO download_intents(
                       operation,source_site,media_url,post_url,payload_json,state,reason,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'blocked_session_policy',?,?,?)
                   ON CONFLICT(operation,source_site,media_url) DO UPDATE SET
                       post_url=excluded.post_url,
                       payload_json=excluded.payload_json,
                       state='blocked_session_policy',
                       reason=excluded.reason,
                       updated_at=excluded.updated_at""",
                (
                    str(operation or "media_download"),
                    str(source_site or ""),
                    str(media_url or ""),
                    str(post_url or ""),
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    str(reason or "session_download_disabled"),
                    now,
                    now,
                ),
            )
            row = con.execute(
                "SELECT intent_id FROM download_intents WHERE operation=? AND source_site=? AND media_url=?",
                (str(operation or "media_download"), str(source_site or ""), str(media_url or "")),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def decide(
    settings: dict | None,
    *,
    operation: str,
    source_site: str = "",
    media_url: str = "",
    post_url: str = "",
    payload: dict[str, Any] | None = None,
    create_intent: bool = True,
) -> DownloadDecision:
    if downloads_enabled(settings):
        return DownloadDecision(True, str(operation), str(source_site), str(media_url), "allowed", 0)
    reason = "session_download_disabled"
    intent_id = 0
    if create_intent:
        intent_id = _record_intent(
            settings,
            operation=operation,
            source_site=source_site,
            media_url=media_url,
            post_url=post_url,
            payload=payload,
            reason=reason,
        )
    return DownloadDecision(False, str(operation), str(source_site), str(media_url), reason, intent_id)


def require_allowed(settings: dict | None, **kwargs: Any) -> DownloadDecision:
    decision = decide(settings, **kwargs)
    if not decision.allowed:
        raise DownloadBlockedByPolicy(decision)
    return decision
