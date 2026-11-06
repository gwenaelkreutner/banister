"""Minimal HTTP client (stdlib only) for Banister's agent API, and the plugin's settings."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path


@lru_cache
def settings() -> dict:
    path = Path(__file__).parent / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


def owner_chat_id() -> str:
    return str(settings().get("chat_id", "")).strip()


class BanisterError(RuntimeError):
    def __init__(self, status: int, detail):
        super().__init__(f"banister {status}: {detail}")
        self.status, self.detail = status, detail


def call(method: str, path: str, body=None, *, params: dict | None = None, timeout: float = 30):
    url = settings().get("api", "http://127.0.0.1:8000").rstrip("/") + "/agent/v1" + path
    if params:
        query = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {settings().get('token', '')}"}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = exc.reason
        raise BanisterError(exc.code, detail) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BanisterError(0, f"Banister is unreachable ({exc})") from None
