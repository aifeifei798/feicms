from __future__ import annotations

from feicms.fetcher import (
    FetchError,
    Fetcher,
    IMPERSONATE_PROFILES,
    build_query,
    fetch,
    origin,
)


def test_build_query_overwrites_page_params():
    url = build_query("https://example.com/api.php/provide/vod/?t=1", "list", 3, 20)
    assert "ac=list" in url
    assert "pg=3" in url
    assert "page=3" in url
    assert "limit=20" in url
    assert "t=1" in url


def test_origin():
    assert origin("https://a.example.com/api.php/provide/vod/") == "https://a.example.com"


def test_profiles_are_valid_for_installed_curl_cffi():
    from curl_cffi.requests.impersonate import BrowserType

    available = {b.value for b in BrowserType}
    assert all(profile in available for profile in IMPERSONATE_PROFILES)


def test_unknown_impersonate_falls_back():
    fetcher = Fetcher(impersonate="not-a-real-browser")
    assert fetcher.impersonate in IMPERSONATE_PROFILES


def test_fetch_raises_fetch_error_on_unreachable_host():
    import asyncio

    fetcher = Fetcher(timeout=5)
    try:
        asyncio.run(fetch(fetcher, "https://definitely-not-a-real-host.invalid/api.php/provide/vod/"))
    except FetchError as exc:
        assert "definitely-not-a-real-host.invalid" in str(exc)
    finally:
        asyncio.run(fetcher.aclose())


def test_unsupported_proxy_env_does_not_break_the_client(monkeypatch):
    """`ALL_PROXY=socks://...` (clash/v2ray default) must not crash construction."""
    import os

    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:10808")
    fetcher = Fetcher()
    assert fetcher.impersonate in IMPERSONATE_PROFILES
    # Only httpx is shielded; libcurl (the primary engine) still sees the proxy.
    assert os.environ["ALL_PROXY"] == "socks://127.0.0.1:10808"
