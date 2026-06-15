"""
Scheduler / executor loop.

Runs on a short fixed tick (independent of the paid tweet polling) and:
  1. recovers any tweets whose plan expansion didn't finish (crash mid-expand)
  2. resets scheduled orders stuck in 'processing' (crash mid-placement)
  3. fires every scheduled order whose run_at is now due

Claiming is atomic: find_one_and_update flips status scheduled->processing for a
single job, so two overlapping ticks can never place the same order twice.
This is what makes the timeframe gaps reliable across restarts.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import get_db
from .models import Tweet
from .plan import expand_tweet_into_jobs, place_job

log = logging.getLogger("scheduler")

MAX_ATTEMPTS = 5


def _now():
    return datetime.now(timezone.utc)


async def _reconcile_pending_expansions(db) -> None:
    """A tweet claimed but never fully expanded (crash) -> re-expand idempotently."""
    s = get_settings()
    cutoff = _now() - timedelta(seconds=s.pending_retry_seconds)
    async for doc in db.processed_tweets.find({"status": "pending"}):
        created = doc.get("created_at")
        if created and created > cutoff:
            continue
        account = await db.accounts.find_one({"username": doc["username"]})
        if not account:
            continue
        tweet = Tweet(id=doc["tweet_id"], text=doc.get("tweet_text", ""), url=doc["tweet_url"])
        n = await expand_tweet_into_jobs(db, account, tweet)
        await db.processed_tweets.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": "scheduled", "step_count": n, "updated_at": _now()}},
        )
        log.info("recovered expansion for tweet %s (%d steps)", doc["tweet_id"], n)


async def _reconcile_stuck_jobs(db) -> None:
    """Jobs claimed (processing) but never finished -> requeue, or fail after MAX_ATTEMPTS."""
    s = get_settings()
    cutoff = _now() - timedelta(seconds=s.processing_retry_seconds)
    await db.scheduled_orders.update_many(
        {"status": "processing", "claimed_at": {"$lt": cutoff}, "attempts": {"$lt": MAX_ATTEMPTS}},
        {"$set": {"status": "scheduled", "updated_at": _now()}},
    )
    await db.scheduled_orders.update_many(
        {"status": "processing", "claimed_at": {"$lt": cutoff}, "attempts": {"$gte": MAX_ATTEMPTS}},
        {"$set": {"status": "failed", "error": "max attempts exceeded", "updated_at": _now()}},
    )


async def _run_due_jobs(db) -> int:
    """Place every order that is due. Returns count placed this tick."""
    now = _now()
    placed = 0
    while True:
        job = await db.scheduled_orders.find_one_and_update(
            {"status": "scheduled", "run_at": {"$lte": now}},
            {"$set": {"status": "processing", "claimed_at": now}, "$inc": {"attempts": 1}},
            sort=[("run_at", 1)],
        )
        if not job:
            break
        await place_job(db, job)
        placed += 1
    return placed


async def scheduler_once() -> None:
    db = get_db()
    await _reconcile_pending_expansions(db)
    await _reconcile_stuck_jobs(db)
    await _run_due_jobs(db)


async def run_scheduler(stop_event: asyncio.Event) -> None:
    s = get_settings()
    log.info("scheduler started (tick=%ss)", s.scheduler_tick_seconds)
    while not stop_event.is_set():
        try:
            await scheduler_once()
        except Exception as e:
            log.exception("scheduler tick error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=s.scheduler_tick_seconds)
        except asyncio.TimeoutError:
            pass
