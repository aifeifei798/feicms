from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ.setdefault("FEICMS_DB", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/storage.db")
os.environ.setdefault("FEICMS_AUTO_COLLECT", "false")
os.environ.setdefault("FEICMS_IMAGE_AUTO", "false")

from sqlalchemy import select, text  # noqa: E402

from feicms.collector import optimize_database, pack_plays, repair_plays  # noqa: E402
from feicms.db import init_db, session  # noqa: E402
from feicms.models import Source, SourceItem, Video  # noqa: E402
from feicms.storage import (  # noqa: E402
    MIN_COMPRESS_BYTES,
    compress_text,
    decompress_text,
    packing_report,
)


def _long_plays() -> str:
    episodes = [
        {"name": f"第{i:02d}集", "url": f"https://cdn.example.com/v/{i}/index.m3u8"}
        for i in range(1, 60)
    ]
    return json.dumps([{"from": "wjm3u8", "episodes": episodes}], ensure_ascii=False)


async def _make_source(name: str) -> tuple[int, int]:
    """Return (source_id, video_id) for a fresh test source."""
    async with session() as s:
        source = Source(name=name, url=f"https://{name}.example.com/api")
        s.add(source)
        await s.flush()
        video = Video(name=f"影片 {name}", signature=f"{name}|影片")
        s.add(video)
        await s.flush()
        return source.id, video.id


def test_round_trip_is_transparent() -> None:
    async def _run() -> None:
        await init_db()
        source_id, video_id = await _make_source("round-trip")
        payload = _long_plays()
        async with session() as s:
            s.add(
                SourceItem(
                    source_id=source_id,
                    video_id=video_id,
                    source_vod_id=1,
                    name="影片",
                    plays=payload,
                )
            )
            await s.commit()

        async with session() as s:
            # The bytes on disk are compressed, the Python value is plain JSON.
            raw = (
                await s.execute(
                    text("SELECT typeof(plays), length(plays) FROM source_items WHERE source_id = :s"),
                    {"s": source_id},
                )
            ).fetchone()
            assert raw[0] == "blob"
            assert raw[1] < len(payload)

            item = await s.scalar(select(SourceItem).where(SourceItem.source_id == source_id))
            assert json.loads(item.plays)[0]["episodes"][0]["url"].endswith("index.m3u8")

    asyncio.run(_run())


def test_short_values_stay_plain_so_sql_predicates_work() -> None:
    async def _run() -> None:
        await init_db()
        source_id, video_id = await _make_source("empty-plays")
        async with session() as s:
            s.add(
                SourceItem(
                    source_id=source_id,
                    video_id=video_id,
                    source_vod_id=1,
                    name="影片",
                    plays="[]",
                )
            )
            await s.commit()

        async with session() as s:
            stored = (
                await s.execute(
                    text(
                        "SELECT typeof(plays), length(plays) FROM source_items WHERE source_id = :s"
                    ),
                    {"s": source_id},
                )
            ).fetchone()
            assert stored == ("text", 2)

            empty = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == source_id, SourceItem.plays == "[]")
            )
            assert empty is not None

    asyncio.run(_run())


def test_pack_plays_shrinks_legacy_rows_and_is_idempotent() -> None:
    async def _run() -> None:
        await init_db()
        source_id, video_id = await _make_source("pack-legacy")
        payload = _long_plays()

        # Rows written before packing existed are plain text on disk.
        async with session() as s:
            for vod_id in range(1, 6):
                s.add(
                    SourceItem(
                        source_id=source_id,
                        video_id=video_id,
                        source_vod_id=vod_id,
                        name="影片",
                        plays=payload,
                    )
                )
            await s.commit()
            await s.execute(
                text("UPDATE source_items SET plays = :p WHERE source_id = :s"),
                {"p": payload, "s": source_id},
            )
            await s.commit()

        first = await pack_plays(batch=10)
        assert first["packed"] >= 5  # other tests' rows may also need packing
        second = await pack_plays(batch=10)
        assert second["packed"] == 0  # already packed, nothing to do

        async with session() as s:
            stored = (
                await s.execute(
                    text(
                        "SELECT typeof(plays), length(plays) FROM source_items WHERE source_id = :s "
                        "ORDER BY source_vod_id LIMIT 1"
                    ),
                    {"s": source_id},
                )
            ).fetchone()
            assert stored[0] == "blob"
            assert stored[1] < len(payload)

            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == source_id, SourceItem.source_vod_id == 1)
            )
            assert len(json.loads(item.plays)[0]["episodes"]) == 59

    asyncio.run(_run())


def test_optimize_reports_a_smaller_file() -> None:
    async def _run() -> None:
        await init_db()
        source_id, video_id = await _make_source("optimize")
        async with session() as s:
            s.add(
                SourceItem(
                    source_id=source_id,
                    video_id=video_id,
                    source_vod_id=1,
                    name="影片",
                    play_url="第01集$https://a.example.com/1.m3u8#第02集$https://a.example.com/2.m3u8",
                    plays=_long_plays(),
                )
            )
            await s.commit()
            await s.execute(
                text("UPDATE source_items SET plays = :p WHERE source_id = :s"),
                {"p": _long_plays(), "s": source_id},
            )
            await s.commit()

        result = await optimize_database()
        assert result["cleared_play_url"] >= 1
        assert result["packed_plays"] >= 1
        assert result["bytes_after"] <= result["bytes_before"]

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == source_id, SourceItem.source_vod_id == 1)
            )
            assert item.play_url == ""
            assert len(json.loads(item.plays)[0]["episodes"]) == 59

    asyncio.run(_run())


def test_compress_text_is_idempotent_for_short_values() -> None:
    assert compress_text("[]") == "[]"
    assert len(compress_text("x" * 500)) < 500
    assert decompress_text(compress_text("hello world")) == "hello world"
    assert MIN_COMPRESS_BYTES == 64


def test_duplicate_indexes_are_dropped_and_singles_are_kept() -> None:
    async def _run() -> None:
        await init_db()
        async with session() as s:
            # A second index over exactly the columns ix_images_status already covers.
            await s.execute(text("CREATE INDEX IF NOT EXISTS ix_image_status ON images (status)"))
            await s.commit()

        dropped = await init_db()
        assert "ix_image_status" in dropped

        async with session() as s:
            names = {
                row[0]
                for row in await s.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                )
            }
            assert "ix_image_status" not in names
            assert "ix_images_status" in names

            # A name in the redundant list is only dropped when its twin is present:
            # remove the twin and the "duplicate" is the only index on that column.
            await s.execute(text("DROP INDEX ix_images_status"))
            await s.execute(text("CREATE INDEX ix_image_status ON images (status)"))
            await s.commit()

        assert "ix_image_status" not in await init_db()

        async with session() as s:
            names = {
                row[0]
                for row in await s.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                )
            }
            assert "ix_image_status" in names

    asyncio.run(_run())


def test_prefix_indexes_are_dropped_in_favour_of_the_composite() -> None:
    async def _run() -> None:
        await init_db()
        async with session() as s:
            # ix_videos_type_name is a shorter copy of ix_videos_type_updated.
            await s.execute(text("CREATE INDEX IF NOT EXISTS ix_videos_type_name ON videos (type_name)"))
            await s.execute(text("CREATE INDEX IF NOT EXISTS ix_videos_year ON videos (year)"))
            await s.commit()

        dropped = await init_db()
        assert "ix_videos_type_name" in dropped
        assert "ix_videos_year" in dropped

        async with session() as s:
            names = {
                row[0]
                for row in await s.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                )
            }
            assert "ix_videos_type_updated" in names
            assert "ix_videos_year_updated" in names
            assert "ix_videos_type_name" not in names

            # The composite still answers a filter on the single column.
            row = (
                await s.execute(
                    text("SELECT count(*) FROM videos WHERE type_name = :t"),
                    {"t": "国产剧"},
                )
            ).fetchone()
            assert row[0] >= 0

    asyncio.run(_run())


def test_packing_report_counts_packed_rows() -> None:
    async def _run() -> None:
        await init_db()
        source_id, video_id = await _make_source("report")
        async with session() as s:
            s.add(
                SourceItem(
                    source_id=source_id,
                    video_id=video_id,
                    source_vod_id=1,
                    name="影片",
                    plays="[]",
                )
            )
            s.add(
                SourceItem(
                    source_id=source_id,
                    video_id=video_id,
                    source_vod_id=2,
                    name="影片",
                    plays=_long_plays(),
                )
            )
            await s.commit()

        report = await packing_report()
        assert report["rows"] >= 2
        assert report["packed"] >= 1
        assert report["plain"] >= 1
        assert report["stored_bytes"] > 0

    asyncio.run(_run())


def test_repair_plays_rebuilds_from_raw_and_batches() -> None:
    async def _run() -> None:
        await init_db()
        source_id, video_id = await _make_source("repair")
        async with session() as s:
            for vod_id in range(1, 4):
                s.add(
                    SourceItem(
                        source_id=source_id,
                        video_id=video_id,
                        source_vod_id=vod_id,
                        name="影片",
                        play_from="wjm3u8",
                        play_url="第01集$https://a.example.com/1.m3u8#第02集$https://a.example.com/2.m3u8",
                        plays="[]",
                    )
                )
            await s.commit()

        result = await repair_plays(batch=2)
        assert result["total"] >= 3
        assert result["changed"] >= 3

        async with session() as s:
            item = await s.scalar(
                select(SourceItem).where(SourceItem.source_id == source_id, SourceItem.source_vod_id == 1)
            )
            episodes = json.loads(item.plays)[0]["episodes"]
            assert len(episodes) == 2

    asyncio.run(_run())
