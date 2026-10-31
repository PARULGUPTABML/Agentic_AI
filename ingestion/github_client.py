"""Minimal GitHub REST v3 client (stdlib only).

Features
--------
* Token auth via ``GITHUB_TOKEN`` (or explicit ``token=``).
* Automatic pagination by following the ``Link: rel="next"`` header.
* Primary + secondary rate-limit handling (403/429 with ``Retry-After`` or
  ``X-RateLimit-Reset``) with bounded exponential back-off.
* Optional on-disk JSON cache so corpus extraction is reproducible and can be
  re-run offline (``offline=True`` serves only cached responses).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

API_ROOT = "https://api.github.com"
_LINK_NEXT = 'rel="next"'


class GitHubError(RuntimeError):
    """Raised for non-retryable GitHub API failures."""


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        cache_dir: str | os.PathLike | None = None,
        offline: bool = False,
        max_retries: int = 5,
        per_page: int = 100,
        user_agent: str = "agentic-ai-issue-pr-extractor",
        sleep_fn=time.sleep,
    ):
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.max_retries = max_retries
        self.per_page = per_page
        self.user_agent = user_agent
        self._sleep = sleep_fn
        self.requests_made = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ #
    #  Cache                                                              #
    # ------------------------------------------------------------------ #
    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.json"

    def _cache_get(self, url: str) -> tuple[Any, str | None] | None:
        p = self._cache_path(url)
        if p and p.exists():
            blob = json.loads(p.read_text(encoding="utf-8"))
            self.cache_hits += 1
            return blob["body"], blob.get("next")
        return None

    def _cache_put(self, url: str, body: Any, next_url: str | None) -> None:
        p = self._cache_path(url)
        if p:
            p.write_text(json.dumps({"url": url, "next": next_url, "body": body}), encoding="utf-8")

    # ------------------------------------------------------------------ #
    #  HTTP                                                               #
    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.user_agent,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    @staticmethod
    def _next_link(link_header: str | None) -> str | None:
        if not link_header:
            return None
        for part in link_header.split(","):
            if _LINK_NEXT in part:
                return part[part.find("<") + 1: part.find(">")]
        return None

    def _fetch(self, url: str) -> tuple[Any, str | None]:
        cached = self._cache_get(url)
        if cached is not None:
            return cached
        if self.offline:
            raise GitHubError(f"offline mode and no cached response for {url}")

        delay = 2.0
        for attempt in range(1, self.max_retries + 1):
            req = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.requests_made += 1
                    body = json.loads(resp.read().decode("utf-8") or "null")
                    nxt = self._next_link(resp.headers.get("Link"))
                    self._cache_put(url, body, nxt)
                    return body, nxt
            except urllib.error.HTTPError as e:
                self.requests_made += 1
                if e.code in (403, 429) and attempt < self.max_retries:
                    wait = self._rate_limit_wait(e.headers, delay)
                    self._sleep(wait)
                    delay *= 2
                    continue
                if e.code >= 500 and attempt < self.max_retries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if e.code == 404:
                    return None, None
                raise GitHubError(f"GitHub API {e.code} for {url}: {e.reason}") from e
            except urllib.error.URLError as e:
                if attempt < self.max_retries:
                    self._sleep(delay)
                    delay *= 2
                    continue
                raise GitHubError(f"network error for {url}: {e.reason}") from e
        raise GitHubError(f"exhausted retries for {url}")

    @staticmethod
    def _rate_limit_wait(headers, default: float) -> float:
        retry_after = headers.get("Retry-After") if headers else None
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        reset = headers.get("X-RateLimit-Reset") if headers else None
        remaining = headers.get("X-RateLimit-Remaining") if headers else None
        if reset and remaining == "0":
            return max(1.0, min(900.0, float(reset) - time.time() + 1))
        return default

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #
    def url(self, path: str, **params: Any) -> str:
        params = {k: v for k, v in params.items() if v is not None}
        params.setdefault("per_page", self.per_page)
        return f"{API_ROOT}{path}?{urllib.parse.urlencode(params)}"

    def get(self, path: str, **params: Any) -> Any:
        body, _ = self._fetch(self.url(path, **params))
        return body

    def paginate(self, path: str, max_items: int | None = None, **params: Any) -> Iterator[dict]:
        url: str | None = self.url(path, **params)
        yielded = 0
        while url:
            body, url = self._fetch(url)
            if not body:
                return
            items = body if isinstance(body, list) else body.get("items", [])
            for item in items:
                yield item
                yielded += 1
                if max_items and yielded >= max_items:
                    return
