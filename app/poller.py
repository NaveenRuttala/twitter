"""
Tweet detection loop.

Responsibility: find new tweets and SCHEDULE their order plan. It no longer
places orders itself — it expands each detected tweet into scheduled_orders
rows (see plan.py), and the scheduler (scheduler.py) fires them when due.

Exactly-once detection is still enforced by the unique index on
processed_tweets.tweet_id: only the insert winner expands the plan. Baseline-
on-first-poll still prevents back-ordering historical tweets.
"""
import asyncio
import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from .config import get_settings
from .db import get_db
from .plan import expand_tweet_into_jobs, resolve_plan
from .twitter import get_provider

log = logging.getLogger("poller")


def _now():
    return datetime.now(timezone.utc)


async def _claim_and_schedule(account: dict, tweet) -> None:
    """Atomically claim a tweet; only the winner expands its plan into jobs.
    We store the tweet text/time too, for the Tweets data view."""
    db = get_db()
    try:
        await db.processed_tweets.insert_one(
            {
                "tweet_id": tweet.id,
                "username": account["username"],
                "tweet_url": tweet.url,
                "tweet_text": tweet.text,
                "tweet_created_at": tweet.created_at,
                "status": "pending",
                "created_at": _now(),
            }
        )
    except DuplicateKeyError:
        return  # already handled

    n = await expand_tweet_into_jobs(db, account, tweet)
    await db.processed_tweets.update_one(
        {"tweet_id": tweet.id},
        {"$set": {"status": "scheduled", "step_count": n, "updated_at": _now()}},
    )
    log.info("scheduled %d step(s) for tweet %s (@%s)", n, tweet.id, account["username"])


async def _poll_account(account: dict) -> None:
    db = get_db()
    s = get_settings()
    username = account["username"]
    provider = get_provider()

    try:
        tweets = await provider.get_latest_tweets(username, limit=20)
    except Exception as e:
        log.warning("fetch failed for @%s: %s", username, e)
        return

    if not tweets:
        return

    newest_id = max(int(t.id) for t in tweets)
    last_id = account.get("last_tweet_id")

    # First ever poll -> set baseline, schedule nothing.
    if last_id is None:
        await db.accounts.update_one(
            {"_id": account["_id"]},
            {"$set": {"last_tweet_id": newest_id, "baselined_at": _now()}},
        )
        log.info("baselined @%s at tweet %s (no orders placed)", username, newest_id)
        return

    new_tweets = sorted((t for t in tweets if int(t.id) > last_id), key=lambda x: int(x.id))
    for tweet in new_tweets:
        if s.only_original_tweets and (tweet.is_retweet or tweet.is_reply):
            log.info("skip RT/reply %s", tweet.url)
            continue
        await _claim_and_schedule(account, tweet)

    if newest_id > last_id:
        await db.accounts.update_one(
            {"_id": account["_id"]}, {"$set": {"last_tweet_id": newest_id}}
        )


async def poll_once() -> None:
    """One detection sweep across all enabled accounts."""
    db = get_db()
    cursor = db.accounts.find({"enabled": True})
    async for account in cursor:
        await _poll_account(account)


async def run_poller(stop_event: asyncio.Event) -> None:
    s = get_settings()
    log.info("detector started (interval=%ss, provider=%s)", s.poll_interval_seconds, s.twitter_provider)
    while not stop_event.is_set():
        try:
            await poll_once()
        except Exception as e:
            log.exception("detection cycle error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=s.poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
