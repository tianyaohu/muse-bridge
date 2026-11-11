"""Optional HTTP fan-out after a record is stored.

The webhook URL is operator configuration, not record content. Delivery runs
on a daemon thread and never fails the tool call. Record text is untrusted
and is not written to the error log.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Any

from muse_bridge.clock import utcnow_iso
from muse_bridge.config import Settings
from muse_bridge.storage.locking import exclusive_lock

logger = logging.getLogger("muse_bridge.webhook")


def schedule_webhook(settings: Settings, record: dict[str, Any]) -> None:
    url = settings.webhook_url
    if not url:
        return
    if not url.startswith(("https://", "http://")):
        logger.error("webhook URL is not http(s); skipping delivery")
        return
    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Bridge-User": str(record.get("user_id", "")),
    }
    if settings.webhook_secret:
        headers["X-Bridge-Secret"] = settings.webhook_secret

    def go() -> None:
        try:
            request = urllib.request.Request(url, data=body, method="POST", headers=headers)
            urllib.request.urlopen(request, timeout=10).read()
        except Exception as exc:
            logger.warning("webhook delivery failed for record %s", record.get("id"))
            _log_error(settings.data_dir, record, exc)

    threading.Thread(target=go, name="muse-bridge-webhook", daemon=True).start()


def _log_error(data_dir: Path, record: dict[str, Any], exc: Exception) -> None:
    path = Path(data_dir) / "webhook_errors.jsonl"
    line = json.dumps(
        {
            "at": utcnow_iso(),
            "id": record.get("id"),
            "user_id": record.get("user_id"),
            "error": f"{type(exc).__name__}: {exc}"[:300],
        },
        ensure_ascii=False,
    )
    try:
        with exclusive_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
    except OSError:
        logger.exception("could not record webhook error")
