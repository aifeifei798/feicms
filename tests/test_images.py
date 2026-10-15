from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select  # noqa: E402

from feicms.config import settings  # noqa: E402
from feicms.db import init_db, session  # noqa: E402
from feicms.fetcher import FetchResult  # noqa: E402
from feicms.images import download_images, image_stats, normalize_image_url, repair_images, scan_images  # noqa: E402
from feicms.models import Image, Source, SourceItem, Video  # noqa: E402
from feicms.normalizer import build_signature  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes-0001"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"vp10data"
# Two different URLs serving byte-identical content.
SAME_A = "https://a.example.com/poster-a.jpg"
SAME_B = "https://b.example.com/poster-b.jpg"
SAME_BYTES = b"\x89PNG\r\n\x1a\n" + b"identical-bytes-served-by-two-hosts"


class StubClient:
    def __init__(self, payload: dict[str, bytes]) -> None:
        self.payload = payload
        self.calls = 0

    async def get_bytes(self, url: str, referer: str | None = None, max_size: int | None = None) -> FetchResult:
        self.calls += 1
        body = self.payload.get(url)
        if body is None:
            raise RuntimeError(f"404 {url}")
        return FetchResult("", 200, 1.0, "stub", body)

    async def aclose(self) -> None:
        pass


async def _image_by_url(url: str) -> Image | None:
    async with session() as s:
        return await s.scalar(select(Image).where(Image.url == url))


def test_normalize_image_url():
    assert normalize_image_url("//cdn.example.com/a.jpg") == "https://cdn.example.com/a.jpg"
    assert normalize_image_url("http://CDN.example.com/a.jpg/") == "https://cdn.example.com/a.jpg"
    assert normalize_image_url("https://cdn.example.com/a.jpg#crop") == "https://cdn.example.com/a.jpg"
    assert normalize_image_url("data:image/png;base64,AAA") is None
    assert normalize_image_url("/relative/a.jpg") is None
    # query strings are kept: ?w=300 and ?w=600 are different images
    assert normalize_image_url("https://cdn.example.com/a.jpg?w=300") == "https://cdn.example.com/a.jpg?w=300"


def test_scan_and_download_shards_files_by_sha():
    async def _run() -> None:
        await init_db()
        async with session() as s:
            src = Source(name="海报测试源", url="https://cdn.example.com/api.php/provide/vod/")
            s.add(src)
            await s.flush()
            video = Video(name="海报测试片", signature=build_signature("海报测试片"), pic="https://cdn.example.com/a.jpg")
            s.add(video)
            await s.flush()
            s.add(
                SourceItem(
                    source_id=src.id,
                    source_vod_id=1,
                    video_id=video.id,
                    pic="https://cdn.example.com/b.webp",
                    plays="[]",
                )
            )

        queued = await scan_images()
        assert queued["queued"] >= 2

        client = StubClient({"https://cdn.example.com/a.jpg": PNG, "https://cdn.example.com/b.webp": WEBP})
        result = await download_images(limit=50, concurrency=2, client=client)
        assert result.downloaded >= 2

        stats = await image_stats()
        assert stats["by_status"]["downloaded"] >= 2
        assert stats["bytes"] > 0
        assert stats["image_dir"] == settings.image_dir

        root = Path(settings.image_dir)
        for url in ("https://cdn.example.com/a.jpg", "https://cdn.example.com/b.webp"):
            image = await _image_by_url(url)
            assert image is not None and image.status == "downloaded"
            path = root / image.path
            assert path.exists()
            # <sha[0:2]>/<sha[2:4]>/<sha>.<ext>
            parts = image.path.split("/")
            assert len(parts) == 3
            assert parts[0] == image.sha256[:2]
            assert parts[1] == image.sha256[2:4]
            assert parts[2] == f"{image.sha256}.{image.ext}"

    asyncio.run(_run())


def test_identical_bytes_from_different_urls_are_deduped():
    async def _run() -> None:
        await init_db()
        async with session() as s:
            s.add(Video(name="重复海报", signature=build_signature("重复海报"), pic=SAME_A))
            s.add(Video(name="重复海报2", signature=build_signature("重复海报2"), pic=SAME_B))

        await scan_images()
        client = StubClient({SAME_A: SAME_BYTES, SAME_B: SAME_BYTES})
        result = await download_images(limit=50, concurrency=2, client=client)
        assert result.duplicates >= 1

        first = await _image_by_url(SAME_A)
        second = await _image_by_url(SAME_B)
        assert first is not None and second is not None
        assert first.sha256 == second.sha256
        assert first.path == second.path  # the duplicate row points at the same file
        assert (Path(settings.image_dir) / first.path).exists()

    asyncio.run(_run())


def test_failed_download_is_recorded_and_retried_later():
    async def _run() -> None:
        await init_db()
        async with session() as s:
            s.add(Video(name="坏海报", signature=build_signature("坏海报"), pic="https://broken.example.com/x.jpg"))
        await scan_images()

        result = await download_images(limit=50, concurrency=1, client=StubClient({}))
        assert result.failed >= 1

        image = await _image_by_url("https://broken.example.com/x.jpg")
        assert image is not None
        assert image.attempts >= 1
        assert image.status in ("pending", "failed")  # retried until max attempts
        assert "404" in (image.last_error or "")

    asyncio.run(_run())


def test_repair_reconciles_index_with_disk():
    async def _run() -> None:
        await init_db()
        async with session() as s:
            image = await s.scalar(select(Image).where(Image.status == "downloaded"))
            assert image is not None
            image_id = image.id
            # simulate what a crash or a racing second batch leaves behind
            image.status = "pending"
            image.last_error = "connection reset"

        result = await repair_images()
        assert result["restored"] >= 1

        async with session() as s:
            fixed = await s.get(Image, image_id)
        assert fixed is not None and fixed.status == "downloaded"
        assert fixed.last_error is None
        assert (Path(settings.image_dir) / fixed.path).exists()

    asyncio.run(_run())


def test_image_api_endpoints():
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from feicms.api import app

    test_client = TestClient(app)

    stats = test_client.get("/api/images/stats").json()
    assert stats["total"] >= 1 and stats["image_dir"]

    downloaded = test_client.get("/api/images", params={"status": "downloaded", "limit": 5}).json()
    assert downloaded, "expected at least one downloaded image"

    response = test_client.get(f"/images/{downloaded[0]['id']}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert len(response.content) > 0

    video = test_client.get("/api/videos", params={"q": "海报测试片"}).json()
    assert video["items"][0]["local_pic"], "poster should resolve to a local image"
