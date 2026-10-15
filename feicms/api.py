from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from .api_routes import auth_router, keys_router, MEDIA_TYPES, provide_router, router, site_router
from .auth import auth_enabled, require_auth
from .config import BASE_DIR, settings
from .db import dispose, init_db, session
from .mcp import mcp_router
from .models import Image
from .scheduler import auto_collect_loop, auto_health_loop, auto_image_loop, auto_subscribe_loop
from .subscribe import ensure_default_subscriptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("feicms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_default_subscriptions()
    tasks = [
        asyncio.create_task(auto_collect_loop()),
        asyncio.create_task(auto_health_loop()),
        asyncio.create_task(auto_subscribe_loop()),
        asyncio.create_task(auto_image_loop()),
    ]
    log.info(
        "feicms API ready (db=%s, auto_collect=%s, images=%s, auth=%s)",
        settings.database_url,
        settings.auto_collect,
        settings.image_auto,
        auth_enabled(),
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

app.include_router(auth_router)
app.include_router(keys_router)
app.include_router(router)
app.include_router(site_router)
app.include_router(provide_router)
app.include_router(mcp_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/images/{image_id}", response_model=None, dependencies=[Depends(require_auth)])
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
    BASE_DIR / "web" / "dist",  # built DaisyUI admin UI + public front site (pnpm build)
    Path(__file__).resolve().parent / "static",  # fallback single-page dashboard
]
STATIC_ROOT = next((path for path in _static_candidates if path.exists()), None)


if STATIC_ROOT:

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """Serve the built SPA.

        Real files (assets, favicons) are streamed as-is; every other path -
        `/`, `/admin`, `/video/123` - falls back to index.html so deep links work.
        """
        root = STATIC_ROOT.resolve()
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(root)):
                return FileResponse(str(candidate), headers={"Cache-Control": "public, max-age=300"})
            if "." in full_path.rsplit("/", 1)[-1]:
                raise HTTPException(404, "not found")
        index = root / "index.html"
        if not index.exists():
            raise HTTPException(404, "no built frontend - run ./server.sh build")
        return FileResponse(str(index), headers={"Cache-Control": "no-cache"})


def run() -> None:
    import uvicorn

    uvicorn.run("feicms.api:app", host=settings.host, port=settings.port, reload=False)
