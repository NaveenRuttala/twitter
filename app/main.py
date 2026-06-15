import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import init_indexes
from .poller import run_poller
from .scheduler import run_scheduler
from .routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_indexes()
    stop_event = asyncio.Event()
    detector = asyncio.create_task(run_poller(stop_event))
    scheduler = asyncio.create_task(run_scheduler(stop_event))
    app.state.stop_event = stop_event
    app.state.tasks = [detector, scheduler]
    try:
        yield
    finally:
        stop_event.set()
        for t in (detector, scheduler):
            try:
                await asyncio.wait_for(t, timeout=10)
            except asyncio.TimeoutError:
                t.cancel()


app = FastAPI(title="Tweet Views Automation", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def dashboard():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"status": "ok", "note": "dashboard not found"}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
