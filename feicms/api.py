from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api_routes import provide_router, router
from .config import BASE_DIR, settings
from .db import dispose, init_db, session
from .models import Image
from .scheduler import auto_collect_loop, auto_health_loop, auto_image_loop, auto_subscribe_loop
from .subscribe import ensure_default_subscriptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("feicms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_default_subscriptions()
    tasks = []
    if settings.auto_collect:
        tasks.append(asyncio.create_task(auto_collect_loop()))
        tasks.append(asyncio.create_task(auto_health_loop()))
        tasks.append(asyncio.create_task(auto_subscribe_loop()))
    if settings.image_auto:
        tasks.append(asyncio.create_task(auto_image_loop()))
    log.info(
        "feicms API ready (db=%s, auto_collect=%s, images=%s)",
        settings.database_url,
        settings.auto_collect,
        settings.image_auto,
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await dispose()


app = FastAPI(
    title="feicms 资源站采集与维护 API",
    description="Aggregate resource sites into one database, expose a searchable API, and keep sources healthy.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(provide_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


MEDIA_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}


@app.get("/images/{image_id}", response_model=None)
async def serve_image(image_id: int) -> FileResponse | RedirectResponse:
    """Serve a locally stored poster. Falls back to the original remote URL when
    the file has not been downloaded (or was removed from disk)."""
    async with session() as s:
        image = await s.get(Image, image_id)
    if image is None:
        raise HTTPException(404, "image not found")

    path = Path(settings.image_dir) / image.path if image.path else None
    if path and path.exists():
        return FileResponse(
            str(path),
            media_type=MEDIA_TYPES.get(image.ext, "application/octet-stream"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return RedirectResponse(image.url)


_static_candidates = [
    BASE_DIR / "web" / "dist",  # built DaisyUI admin UI (pnpm build)
    Path(__file__).resolve().parent / "static",  # fallback single-page dashboard
]
for _static in _static_candidates:
    if _static.exists():
        app.mount("/", StaticFiles(directory=str(_static), html=True), name="static")
        break


def run() -> None:
    import uvicorn

    uvicorn.run("feicms.api:app", host=settings.host, port=settings.port, reload=False)
