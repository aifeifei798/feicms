"""HLS playlist cleaner for the site player.

Resource-site streams frequently splice ad clips into their m3u8 playlists:
a block of segments from some *other* directory (or host) appears between two
`#EXT-X-DISCONTINUITY` markers - an ad at the head, mid-roll ads, and so on.
The film's own segments virtually always live in the directory the playlist
itself was served from.

`clean_playlist` therefore splits a media playlist into discontinuity blocks,
and keeps only the blocks whose segments share the playlist's own host+path,
falling back to a majority vote across blocks when they don't match (hosts
that serve everything from one directory but pull ads from another CDN).
Everything else - the ad blocks - is dropped, and `#EXT-X-TARGETDURATION` is
recomputed for what survives. Surviving segment (and KEY/MAP) URLs are
rewritten to the backend segment proxy: the upstream CDNs hotlink-guard,
redirect across hosts and skip CORS headers, so the browser never talks to
them directly - only to our origin.

Master playlists are passed through with variant URIs rewritten to point back
at the cleaning proxy, so sub-playlists get cleaned too.

Limitations: an ad cut in *without* a discontinuity marker, or served from the
same directory as the film, is indistinguishable from the film and stays.
"""

from __future__ import annotations

import math
import re
import time
from urllib.parse import quote, urljoin, urlsplit

# ---------------------------------------------------------------- playlist proxying


def proxied_url(upstream: str) -> str:
    """Address of the cleaning proxy for an upstream playlist."""
    return "/api/site/hls?u=" + quote(upstream, safe="")


def proxied_seg_url(upstream: str) -> str:
    """Address of the segment proxy for an upstream media segment.

    Segments ride through the backend as well (not just the playlist): the
    upstream CDNs hotlink-guard, region-block and redirect across hosts, so a
    browser fetching them directly is unreliable at best and CORS-blocked at
    worst. The backend fetches with the same impersonating client the collector
    uses and streams the bytes back.
    """
    return "/api/site/hls/seg?u=" + quote(upstream, safe="")


def clean_playlist(base_url: str, text: str) -> str:
    if "#EXT-X-STREAM-INF" in text:
        return _clean_master(base_url, text)
    return _clean_media(base_url, text)


# ---------------------------------------------------------------- master playlists


def _clean_master(base_url: str, text: str) -> str:
    out: list[str] = []
    pending_variant = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            pending_variant = True
            out.append(line)
            continue
        if not line.startswith("#") and pending_variant:
            # The URI line that follows a STREAM-INF tag: fetch it back through
            # the proxy so the variant playlist is cleaned as well.
            out.append(proxied_url(urljoin(base_url, line)))
            pending_variant = False
            continue
        if line.startswith("#EXT-X-I-FRAME-STREAM-INF"):
            out.append(_rewrite_attr_uri(base_url, line))
            continue
        out.append(line)
    return "\n".join(out) + "\n"


_URI_ATTR = re.compile(r'URI="([^"]+)"')


def _rewrite_attr_uri(base_url: str, line: str) -> str:
    return _URI_ATTR.sub(lambda m: f'URI="{proxied_url(urljoin(base_url, m.group(1)))}"', line)


def _absolutise_tag(base_url: str, tag: str) -> str:
    """KEY/MAP tags keep effect across segments; their URI must be reachable
    from the browser, so it goes through the segment proxy."""
    match = _KEY_MAP.match(tag)
    if not match:
        return tag
    body = _URI_ATTR.sub(lambda m: f'URI="{proxied_seg_url(urljoin(base_url, m.group(1)))}"', match.group(2))
    return f"#EXT-X-{match.group(1)}:{body}"


# ---------------------------------------------------------------- media playlists

# Tags that describe exactly the segment that follows them.
_SEGMENT_TAGS = ("#EXTINF", "#EXT-X-BYTERANGE", "#EXT-X-PROGRAM-DATE-TIME")
# Tags carrying a URI= attribute that must be absolutised (and stay in effect
# for later segments until re-declared).
_KEY_MAP = re.compile(r"^#EXT-X-(KEY|MAP):(.*)")


def _host_dir(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    path = parts.path.rsplit("/", 1)[0]
    return (parts.netloc, path)


class _Segment:
    __slots__ = ("tags", "uri", "disc")

    def __init__(self, tags: list[str], uri: str, disc: bool) -> None:
        self.tags = tags  # already absolutised where needed
        self.uri = uri
        self.disc = disc  # a #EXT-X-DISCONTINUITY preceded this segment


def _parse_media(text: str) -> tuple[list[str], list[_Segment], bool]:
    """Split into header tag lines, ordered segments, and the ENDLIST flag.

    A `#EXT-X-DISCONTINUITY` sets the `disc` flag on the segment that follows
    it; blocks are rebuilt from that flag later.
    """
    header: list[str] = []
    segments: list[_Segment] = []
    endlist = False
    seen_segment_tag = False
    pending: list[str] = []
    disc = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-ENDLIST"):
            endlist = True
            continue
        if not line.startswith("#"):
            segments.append(_Segment(pending, line, disc))
            pending = []
            disc = False
            seen_segment_tag = True
            continue
        if line.startswith(_SEGMENT_TAGS):
            pending.append(line)
            seen_segment_tag = True
            continue
        if line == "#EXT-X-DISCONTINUITY":
            disc = True
            seen_segment_tag = True
            continue
        if _KEY_MAP.match(line):
            # A key/map may sit in the header (applies to the film from the
            # start) or between blocks; either way it travels with the next
            # segment so it survives block dropping at the same position.
            # The URI= attribute is absolutised later, against the final URL.
            pending.append(line)
            seen_segment_tag = True
            continue
        if not seen_segment_tag:
            header.append(line)
        # Unknown tag after the first segment (e.g. DISCONTINUITY-SEQUENCE):
        # drop it - it describes the removed ad blocks anyway.
    return header, segments, endlist


def _blocks(segments: list[_Segment], base_url: str) -> list[list[_Segment]]:
    blocks: list[list[_Segment]] = []
    current: list[_Segment] = []
    for seg in segments:
        if seg.disc and current:
            blocks.append(current)
            current = []
        current.append(seg)
    if current:
        blocks.append(current)
    # Resolve every segment URI (and any KEY/MAP URI= attribute) against the
    # (possibly redirected) base URL.
    for block in blocks:
        for seg in block:
            seg.uri = urljoin(base_url, seg.uri)
            seg.tags = [_absolutise_tag(base_url, tag) for tag in seg.tags]
    return blocks


def _clean_media(base_url: str, text: str) -> str:
    header, segments, endlist = _parse_media(text)
    blocks = _blocks(segments, base_url)

    playlist_dir = _host_dir(base_url)
    # Primary rule: the film lives in the playlist's own directory.
    matching = [b for b in blocks if any(_host_dir(s.uri) == playlist_dir for s in b)]
    if not matching:
        # Fallback: majority vote by (host, directory) across all segments of
        # each block - covers hosts serving ads from a different CDN while the
        # film directory differs from the playlist's own (e.g. after a redirect).
        votes: dict[tuple[str, str], int] = {}
        for block in blocks:
            for seg in block:
                votes[_host_dir(seg.uri)] = votes.get(_host_dir(seg.uri), 0) + 1
        if votes:
            dominant = max(votes.items(), key=lambda kv: kv[1])[0]
            matching = [b for b in blocks if any(_host_dir(s.uri) == dominant for s in b)]
    if not matching:
        matching = blocks  # nothing identifiable: play it as-is

    # Recompute the header for the surviving segments.
    kept = [seg for block in matching for seg in block]
    max_duration = 0.0
    for seg in kept:
        for tag in seg.tags:
            if tag.startswith("#EXTINF"):
                try:
                    max_duration = max(max_duration, float(tag[len("#EXTINF:") :].split(",")[0]))
                except ValueError:
                    pass
    out: list[str] = []
    for line in header:
        if line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXT-X-TARGETDURATION"):
            out.append(f"#EXT-X-TARGETDURATION:{max(1, math.ceil(max_duration)) if max_duration else 6}")
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE"):
            out.append("#EXT-X-MEDIA-SEQUENCE:0")
            continue
        out.append(line)
    out.insert(0, "#EXTM3U")
    if not any(line.startswith("#EXT-X-TARGETDURATION") for line in out):
        out.insert(1, f"#EXT-X-TARGETDURATION:{max(1, math.ceil(max_duration)) if max_duration else 6}")

    first_emitted = True
    for block in matching:
        # A discontinuity before the very first segment is meaningless noise
        # (it used to separate the dropped ad from the film).
        if block[0].disc and not first_emitted:
            out.append("#EXT-X-DISCONTINUITY")
        for seg in block:
            out.extend(seg.tags)
            out.append(proxied_seg_url(seg.uri))
            first_emitted = False
    if endlist:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- response cache
# The player (and hls.js on every seek/reload) re-fetches the playlist; the
# upstream fetch costs a round trip to a slow CDN, so keep cleaned playlists
# around briefly. VOD playlists of these sites are immutable once published.

CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 300
_cache: dict[str, tuple[float, str]] = {}


def cached_clean(base_url: str, requested_url: str, text: str) -> str:
    now = time.monotonic()
    hit = _cache.get(requested_url)
    if hit and hit[0] > now:
        return hit[1]
    cleaned = clean_playlist(base_url, text)
    if len(_cache) >= CACHE_MAX_ENTRIES:
        expired = [k for k, (deadline, _) in _cache.items() if deadline <= now]
        for key in expired or list(_cache)[: len(_cache) // 2]:
            _cache.pop(key, None)
    _cache[requested_url] = (now + CACHE_TTL_SECONDS, cleaned)
    return cleaned
