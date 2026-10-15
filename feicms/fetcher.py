"""HTTP layer for 资源站 collection.

Why curl_cffi instead of plain httpx:
most of these采集站 sit behind Cloudflare / nginx fingerprint checks and reject
"python-httpx/1.x" TLS handshakes. curl_cffi impersonates a real browser's TLS
+ HTTP2 fingerprint, which in practice turns ~30 of the 73 sources from dead to
reachable. httpx is kept as a last-resort fallback.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.requests.impersonate import BrowserType

from .config import settings

# Browser fingerprints worth rotating through when one gets blocked. Validated
# against the fingerprints this curl_cffi build actually supports.
_AVAILABLE_PROFILES = {b.value for b in BrowserType}
IMPERSONATE_PROFILES = tuple(
    p for p in ("chrome131", "chrome124", "chrome110", "firefox133", "safari170") if p in _AVAILABLE_PROFILES
) or ("chrome124",)

DEFAULT_HEADERS = {
    "Accept": "application/json, text/xml, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class FetchError(Exception):
    """Raised when every engine/profile fails for a URL."""


def build_query(url: str, ac: str, page: int, limit: int) -> str:
    """Append/replace query params while keeping whatever path the source uses."""
    parts = urlsplit(url)
    query = dict(pair.split("=", 1) for pair in parts.query.split("&") if "=" in pair)
    query["ac"] = ac
    query["pg"] = str(page)
    query["page"] = str(page)
    query["limit"] = str(limit)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(f"{k}={v}" for k, v in query.items()), parts.fragment)
    )


def origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


@dataclass
class FetchResult:
    text: str
    status_code: int
    latency_ms: float
    engine: str
    content: bytes | None = None


class Fetcher:
    """Browser-impersonating HTTP client with retries and an httpx fallback."""

    def __init__(self, impersonate: str | None = None, timeout: float | None = None):
        self.timeout = timeout or settings.request_timeout
        requested = impersonate or settings.impersonate
        self.impersonate = requested if requested in _AVAILABLE_PROFILES else IMPERSONATE_PROFILES[0]
        self._curl: dict[str, curl_requests.AsyncSession] = {}
        self._httpx = httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": settings.user_agent, **DEFAULT_HEADERS},
            follow_redirects=True,
            verify=False,
        )

    def _session(self, profile: str) -> curl_requests.AsyncSession:
        session = self._curl.get(profile)
        if session is None:
            session = curl_requests.AsyncSession(
                impersonate=profile,
                timeout=self.timeout,
                allow_redirects=True,
                verify=False,
            )
            self._curl[profile] = session
        return session

    async def get(self, url: str, referer: str | None = None) -> FetchResult:
        headers = dict(DEFAULT_HEADERS)
        if referer:
            headers["Referer"] = referer

        profiles = [self.impersonate] + [p for p in IMPERSONATE_PROFILES if p != self.impersonate]
        last_error: Exception | None = None

        for attempt in range(settings.max_retries):
            profile = profiles[attempt % len(profiles)]
            started = time.perf_counter()
            try:
                resp = await self._session(profile).get(url, headers=headers)
                text = resp.text
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}: {text[:160]}")
                if not text:
                    raise FetchError(f"empty response body from {url}")
                return FetchResult(text, resp.status_code, (time.perf_counter() - started) * 1000, profile)
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                if attempt < settings.max_retries - 1:
                    await asyncio.sleep(1.2 * (attempt + 1))

        # Last resort: plain httpx (some hosts reject browser fingerprints too).
        try:
            started = time.perf_counter()
            resp = await self._httpx.get(url, headers=headers)
            resp.raise_for_status()
            if not resp.text:
                raise FetchError(f"empty response body from {url}")
            return FetchResult(resp.text, resp.status_code, (time.perf_counter() - started) * 1000, "httpx")
        except Exception as exc:  # noqa: BLE001
            message = str(last_error) or str(exc)
            raise FetchError(f"{url} -> {message}") from exc

    async def get_bytes(self, url: str, referer: str | None = None, max_size: int | None = None) -> FetchResult:
        """Same as get(), but returns raw bytes (used for poster images)."""
        headers = dict(DEFAULT_HEADERS)
        if referer:
            headers["Referer"] = referer

        profiles = [self.impersonate] + [p for p in IMPERSONATE_PROFILES if p != self.impersonate]
        last_error: Exception | None = None

        for attempt in range(settings.max_retries):
            profile = profiles[attempt % len(profiles)]
            started = time.perf_counter()
            try:
                resp = await self._session(profile).get(url, headers=headers)
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}: {resp.text[:160]}")
                body = resp.content
                if not body:
                    raise FetchError(f"empty response body from {url}")
                if max_size and len(body) > max_size:
                    raise FetchError(f"response too large ({len(body)} bytes > {max_size})")
                return FetchResult("", resp.status_code, (time.perf_counter() - started) * 1000, profile, body)
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                if attempt < settings.max_retries - 1:
                    await asyncio.sleep(1.2 * (attempt + 1))

        try:
            started = time.perf_counter()
            resp = await self._httpx.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.content
            if not body:
                raise FetchError(f"empty response body from {url}")
            if max_size and len(body) > max_size:
                raise FetchError(f"response too large ({len(body)} bytes > {max_size})")
            return FetchResult("", resp.status_code, (time.perf_counter() - started) * 1000, "httpx", body)
        except Exception as exc:  # noqa: BLE001
            raise FetchError(f"{url} -> {str(last_error) or str(exc)}") from exc

    async def aclose(self) -> None:
        for session in self._curl.values():
            try:
                await session.close()
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass
        await self._httpx.aclose()


def make_client() -> Fetcher:
    """Backwards-compatible alias used by collector/health."""
    return Fetcher()


async def fetch(client: Fetcher, url: str) -> FetchResult:
    return await client.get(url)
