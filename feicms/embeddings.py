"""Semantic search helpers built on top of the local embeddings service.

The service is OpenAI-compatible (POST /v1/embeddings). We store one vector per
video in the ``video_embeddings`` table and answer a query by embedding it and
ranking the stored vectors by cosine similarity. Everything degrades gracefully:
if the service is unreachable or no vectors are stored we fall back to the normal
title search.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from typing import Any

import httpx
from sqlalchemy import select

from .config import settings
from .db import session
from .models import Video, VideoEmbedding

log = logging.getLogger("feicms.embeddings")


def embeddings_url() -> str:
    return (getattr(settings, "embeddings_url", "") or "").rstrip("/")


def embeddings_model() -> str:
    return getattr(settings, "embeddings_model", "") or "granite"


def embeddings_enabled() -> bool:
    url = embeddings_url()
    return bool(url)


def video_text(video: Any) -> str:
    """Build the text we embed for a video."""
    parts = [
        getattr(video, "name", "") or "",
        getattr(video, "type_name", "") or "",
        getattr(video, "year", "") or "",
        getattr(video, "area", "") or "",
        getattr(video, "lang", "") or "",
        getattr(video, "blurb", "") or "",
        getattr(video, "actor", "") or "",
        getattr(video, "director", "") or "",
        getattr(video, "remarks", "") or "",
    ]
    return " ".join(p.strip() for p in parts if p and p.strip())


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    url = embeddings_url()
    if not url:
        raise RuntimeError("embeddings service not configured")
    model = embeddings_model()
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        resp = await client.post(
            f"{url}/v1/embeddings",
            json={"input": texts, "model": model},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        data.sort(key=lambda d: d.get("index", 0))
        out = []
        for item in data:
            out.append(_normalize([float(x) for x in item["embedding"]]))
        if len(out) != len(texts):
            raise RuntimeError(f"embedding count mismatch: {len(out)} != {len(texts)}")
        return out


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob[: n * 4]))


async def service_available() -> bool:
    url = embeddings_url()
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(f"{url}/health")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def ensure_embeddings(video_ids: list[int]) -> int:
    """Compute + store embeddings for the given videos if missing. Returns count added."""
    if not video_ids or not embeddings_enabled():
        return 0
    # Avoid accidentally embedding a whole library on a single request. Pre-build
    # with `feicms embed` (or POST /api/embeddings/rebuild) for large datasets.
    if len(video_ids) > 2000:
        return 0
    async with session() as s:
        existing = set(
            await s.scalars(
                select(VideoEmbedding.video_id).where(VideoEmbedding.video_id.in_(video_ids))
            )
        )
        missing = [vid for vid in video_ids if vid not in existing]
        if not missing:
            return 0
        videos = list(await s.scalars(select(Video).where(Video.id.in_(missing))))
        if not videos:
            return 0
        texts = [video_text(v) for v in videos]
        try:
            vecs = await embed_texts(texts)
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding failed: %s", exc)
            return 0
        model = embeddings_model()
        for v, vec in zip(videos, vecs):
            row = VideoEmbedding(video_id=v.id, model=model, dim=len(vec), vector=pack_vector(vec))
            s.add(row)
        await s.commit()
        return len(videos)


async def rebuild_embeddings(batch_size: int = 128, force: bool = False, limit: int | None = None) -> dict[str, Any]:
    from .db import init_db

    await init_db()
    if not embeddings_enabled():
        return {"enabled": False, "processed": 0, "added": 0, "error": "embeddings service not configured"}
    if not await service_available():
        return {"enabled": True, "processed": 0, "added": 0, "error": "embeddings service unreachable"}
    model = embeddings_model()
    processed = 0
    added = 0
    async with session() as s:
        stmt = select(Video.id)
        if not force:
            already = set(await s.scalars(select(VideoEmbedding.video_id).where(VideoEmbedding.model == model)))
            # we'll filter in python for simplicity
        all_ids = list(await s.scalars(stmt))
    if not force:
        async with session() as s:
            already = set(await s.scalars(select(VideoEmbedding.video_id).where(VideoEmbedding.model == model)))
        all_ids = [vid for vid in all_ids if vid not in already]
    if limit is not None:
        all_ids = all_ids[:limit]
    # process in batches
    for i in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[i : i + batch_size]
        n = await ensure_embeddings(batch_ids)
        processed += len(batch_ids)
        added += n
        # yield control
        await asyncio.sleep(0)
    # if force, recompute all (ensure skips existing) -> need force path
    if force:
        # re-embed everything
        added = 0
        processed = 0
        async with session() as s:
            all_ids = list(await s.scalars(select(Video.id)))
        if limit is not None:
            all_ids = all_ids[:limit]
        for i in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[i : i + batch_size]
            # delete existing
            async with session() as s:
                for vid in batch_ids:
                    row = await s.get(VideoEmbedding, vid)
                    if row is not None:
                        await s.delete(row)
                await s.commit()
            n = await ensure_embeddings(batch_ids)
            processed += len(batch_ids)
            added += n
            await asyncio.sleep(0)
    # count total
    async with session() as s:
        total = await s.scalar(__import__("sqlalchemy").func.count(VideoEmbedding.id)) if False else None
    from sqlalchemy import func

    async with session() as s:
        total_emb = await s.scalar(select(func.count()).select_from(VideoEmbedding))
    return {"enabled": True, "processed": processed, "added": added, "total_embeddings": total_emb or 0, "model": model}


async def semantic_search(
    q: str,
    type_name: str | None = None,
    year: str | None = None,
    hide_adult: bool | None = None,
) -> list[tuple[int, float]]:
    """Return list of (video_id, score) sorted by score desc (all candidates)."""
    if not q or not embeddings_enabled():
        return []
    if not await service_available():
        return []
    try:
        qv = (await embed_texts([q]))[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("query embedding failed: %s", exc)
        return []
    from .config import settings as _s

    effective_hide = _s.hide_adult if hide_adult is None else hide_adult

    async with session() as s:
        # candidate video ids with filters
        stmt = select(Video.id)
        if type_name:
            if type_name == "未分类":
                stmt = stmt.where(Video.type_name.is_(None) | (Video.type_name == ""))
            else:
                stmt = stmt.where(Video.type_name == type_name)
        if year:
            stmt = stmt.where(Video.year == year)
        if effective_hide:
            stmt = stmt.where(Video.is_adult.is_(False))
        candidate_ids = list(await s.scalars(stmt))
    if not candidate_ids:
        return []
    # ensure embeddings for a bounded set only
    await ensure_embeddings(candidate_ids)
    # score via joined query to avoid a huge IN (...) clause
    scored: list[tuple[float, int]] = []
    async with session() as s:
        stmt = (
            select(VideoEmbedding.video_id, VideoEmbedding.vector)
            .join(Video, Video.id == VideoEmbedding.video_id)
        )
        if type_name:
            if type_name == "未分类":
                stmt = stmt.where(Video.type_name.is_(None) | (Video.type_name == ""))
            else:
                stmt = stmt.where(Video.type_name == type_name)
        if year:
            stmt = stmt.where(Video.year == year)
        if effective_hide:
            stmt = stmt.where(Video.is_adult.is_(False))
        rows = await s.execute(stmt)
        for vid, blob in rows:
            vec = unpack_vector(blob)
            if not vec:
                continue
            score = sum(a * b for a, b in zip(qv, vec))
            scored.append((score, vid))
    scored.sort(reverse=True)
    return [(vid, score) for score, vid in scored]
