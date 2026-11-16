"""SalesFlow API (FastAPI modular monolith, PRD section 9.2)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db as dbm
from .db import get_db

FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "frontend", "dist")


async def _workers(db):
    """Background workers (in-process for this build; PRD section 13 maps them to
    Prefect/Arq workers in production): waitlist expiry, queued message dispatch."""
    from . import campaigns, booking
    while True:
        try:
            await asyncio.sleep(60)
            await booking.expire_offers(db)
            live = await db.campaigns.find({"org_id": config.ORG_ID, "status": "live"})
            for c in live:
                queued = await db.campaign_messages.count_documents(
                    {"campaign_id": c["_id"], "status": "queued"})
                if queued and campaigns.in_quiet_hours():
                    await campaigns.dispatch(db, c["_id"])
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await get_db()
    count = await db.users.count_documents({})
    if count == 0:
        from .seed import seed
        await seed(db)
        print("[salesflow] database seeded with demo data")
    if config.DEMO_REALTIME:
        from .realtime import start_simulator
        await start_simulator(db)
    task = asyncio.create_task(_workers(db))
    yield
    task.cancel()


app = FastAPI(title="SalesFlow API", version="1.0.0",
              description="Lead-to-appointment-to-renewal platform (PRD v1.0)",
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"])

from .routers import analytics, core, growth, people, scheduling, team  # noqa: E402

app.include_router(core.router)
app.include_router(people.router)
app.include_router(scheduling.router)
app.include_router(growth.router)
app.include_router(analytics.router)
app.include_router(team.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "backend": "mongo" if (await get_db()).is_mongo else "in-process"}


@app.exception_handler(Exception)
async def unhandled(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL",
                                                            "message": str(exc)}})


# Static SPA (built frontend) with fallback for client-side routes
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        target = os.path.join(FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(target):
            return FileResponse(target)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))
