from __future__ import annotations

from feicms.hls import clean_playlist, proxied_seg_url, proxied_url
from feicms.api_routes import _private_host

PLAYLIST_URL = "https://cdn.example.com/20261030/cWJ62vPX/1500kb/hls/index.m3u8"
FILM_DIR = "https://cdn.example.com/20261030/cWJ62vPX/1500kb/hls/"
AD_DIR = "https://cdn.example.com/20261009/RCP0FQBQ/9622kb/hls/"


def _segment(url: str, seconds: float = 3.0) -> str:
    return f"#EXTINF:{seconds},\n{url}"


def test_head_ad_block_from_another_directory_is_dropped():
    """The lajiao-style sample: an ad clip (other directory) before the film."""
    playlist = "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-TARGETDURATION:6",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-KEY:METHOD=NONE",
            _segment(f"{AD_DIR}lNyGCaVC.ts"),
            _segment(f"{AD_DIR}ESYCf5Kt.ts"),
            "#EXT-X-DISCONTINUITY",
            "#EXT-X-KEY:METHOD=NONE",
            _segment(f"{FILM_DIR}PaNchIgM.ts", 4.76),
            _segment(f"{FILM_DIR}DnLtZsEN.ts"),
            "#EXT-X-ENDLIST",
        ]
    )

    cleaned = clean_playlist(PLAYLIST_URL, playlist)

    assert "lNyGCaVC.ts" not in cleaned  # ad segments gone
    assert "ESYCf5Kt.ts" not in cleaned
    assert proxied_seg_url(f"{FILM_DIR}PaNchIgM.ts") in cleaned  # film kept, proxied
    assert proxied_seg_url(f"{FILM_DIR}DnLtZsEN.ts") in cleaned
    assert "#EXT-X-ENDLIST" in cleaned
    # TARGETDURATION recomputed for what survived (max EXTINF = 4.76 -> 5).
    assert "#EXT-X-TARGETDURATION:5" in cleaned
    # The ad block's re-declared key is gone with it.
    assert cleaned.count("#EXT-X-KEY") == 1


def test_mid_roll_ad_between_film_blocks_is_dropped():
    film_a = [_segment(f"{FILM_DIR}a{i}.ts") for i in range(20)]
    ad = ["#EXT-X-DISCONTINUITY"] + [_segment(f"{AD_DIR}ad{i}.ts") for i in range(1, 4)]
    film_b = ["#EXT-X-DISCONTINUITY"] + [_segment(f"{FILM_DIR}b{i}.ts") for i in range(20)]
    playlist = "\n".join(
        ["#EXTM3U", "#EXT-X-TARGETDURATION:6", *film_a, *ad, *film_b, "#EXT-X-ENDLIST"]
    )

    cleaned = clean_playlist(PLAYLIST_URL, playlist)

    assert "ad1.ts" not in cleaned
    assert proxied_seg_url(f"{FILM_DIR}a1.ts") in cleaned and proxied_seg_url(f"{FILM_DIR}b1.ts") in cleaned
    # The film blocks stay separated by their discontinuity.
    assert cleaned.count("#EXT-X-DISCONTINUITY") == 1


def test_playlist_without_ads_is_only_absolutised():
    playlist = "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-TARGETDURATION:3",
            _segment("aaa.ts"),
            _segment("bbb.ts"),
            "#EXT-X-ENDLIST",
        ]
    )

    cleaned = clean_playlist(PLAYLIST_URL, playlist)

    assert proxied_seg_url(f"{FILM_DIR}aaa.ts") in cleaned
    assert proxied_seg_url(f"{FILM_DIR}bbb.ts") in cleaned
    assert "#EXT-X-ENDLIST" in cleaned


def test_majority_vote_keeps_film_when_no_segment_matches_the_playlist_url():
    """A redirect moved the playlist off its own directory: vote by volume."""
    playlist_url = "https://other.example.com/play/e31jNQMb.m3u8"
    film_dir = "https://film.example.com/vod/2026/mvi/"
    ad_dir = "https://adcdn.example.com/pool/"
    playlist = "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-TARGETDURATION:6",
            _segment(f"{ad_dir}ad0.ts"),
            "#EXT-X-DISCONTINUITY",
            *[_segment(f"{film_dir}f{i}.ts") for i in range(30)],
            "#EXT-X-ENDLIST",
        ]
    )

    cleaned = clean_playlist(playlist_url, playlist)

    assert "ad0.ts" not in cleaned
    assert proxied_seg_url(f"{film_dir}f0.ts") in cleaned


def test_encrypted_key_uri_is_absolutised():
    playlist = "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-TARGETDURATION:6",
            "#EXT-X-KEY:METHOD=AES-128,URI=\"key.key\",IV=0x00000000000000000000000000000001",
            _segment(f"{FILM_DIR}a.ts"),
            "#EXT-X-DISCONTINUITY",
            "#EXT-X-KEY:METHOD=NONE",
            _segment(f"{AD_DIR}ad.ts"),
            "#EXT-X-DISCONTINUITY",
            _segment(f"{FILM_DIR}b.ts"),
            "#EXT-X-ENDLIST",
        ]
    )

    cleaned = clean_playlist(PLAYLIST_URL, playlist)

    assert proxied_seg_url('https://cdn.example.com/20261030/cWJ62vPX/1500kb/hls/key.key') in cleaned
    assert "ad.ts" not in cleaned
    assert proxied_seg_url(f"{FILM_DIR}b.ts") in cleaned


def test_master_playlist_variants_are_routed_through_the_proxy():
    master = "\n".join(
        [
            "#EXTM3U",
            "#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1500000,RESOLUTION=1280x720",
            "/20261030/cWJ62vPX/1500kb/hls/index.m3u8",
            "#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=90000,URI=\"/iframe.m3u8\"",
        ]
    )

    cleaned = clean_playlist(PLAYLIST_URL, master)

    variant = f"{PLAYLIST_URL.rsplit('/', 1)[0]}/index.m3u8"
    assert proxied_url(variant) in cleaned
    assert proxied_url("https://cdn.example.com/iframe.m3u8") in cleaned


def test_private_hosts_are_rejected():
    assert _private_host("127.0.0.1:8000")
    assert _private_host("10.0.0.5")
    assert _private_host("192.168.1.10:9000")
    assert _private_host("localhost")
    assert not _private_host("cdn.example.com")
    assert not _private_host("2609.lajiao2026.com:443")
