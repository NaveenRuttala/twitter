"""
The poller is the heart of the tool.

For every enabled account, on each cycle we:
  1. fetch the latest tweets from the provider
  2. on the FIRST ever poll (no last_tweet_id) -> set a baseline and order
     NOTHING. This stops the tool from placing orders for every historical
     tweet the moment you add an account.
  3. for each tweet newer than last_tweet_id, attempt to claim it via an
     atomic unique insert into processed_tweets. The winner of that insert
     (and only the winner) places the SMM order. A duplicate-key error means
     someone/another cycle already handled it -> skip. This makes
     double-ordering impossible even on restart or overlapping cycles.
  4. advance last_tweet_id to the newest id we saw.

We also reconcile "pending" tweets (claimed but order never confirmed, e.g.
the process crashed mid-order) by retrying them.
"""
import asyncio
import logging
import random
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from .config import get_settings
from .db import get_db
from .models import Tweet
from .notifier import notify
from .smm_client import SmmClient, SmmError
from .twitter import get_provider

log = logging.getLogger("poller")


def _now():
    return datetime.now(timezone.utc)


def _qty_for(account: dict) -> int:
    s = get_settings()
    lo = account.get("min_qty") or s.order_min_qty
    hi = account.get("max_qty") or s.order_max_qty
    if hi < lo:
        lo, hi = hi, lo
    return random.randint(lo, hi)


def _service_for(account: dict) -> int:
    return account.get("service_id") or get_settings().views_service_id


async def _place_order_for_tweet(account: dict, tweet: Tweet, doc_id) -> None:
    """Claimed tweet -> place the SMM order and record the outcome."""
    db = get_db()
    s = get_settings()
    qty = _qty_for(account)
    service_id = _service_for(account)

    if s.dry_run:
        log.info("[DRY_RUN] would order %s views for %s", qty, tweet.url)
        await db.processed_tweets.update_one(
            {"_id": doc_id},
            {"$set": {"status": "dry_run", "quantity": qty, "updated_at": _now()}},
        )
        return

    smm = SmmClient()
    try:
        res = await smm.add_order(service_id=service_id, link=tweet.url, quantity=qty)
        order_id = res["order"]
    except SmmError as e:
        log.error("order failed for %s: %s", tweet.url, e)
        await db.processed_tweets.update_one(
            {"_id": doc_id},
            {"$set": {"status": "failed", "error": str(e), "updated_at": _now()}},
        )
        await notify(f"⚠️ Order FAILED for @{account['username']}\n{tweet.url}\n{e}")
        return

    await db.processed_tweets.update_one(
        {"_id": doc_id},
        {"$set": {"status": "ordered", "order_id": order_id, "quantity": qty, "updated_at": _now()}},
    )
    await db.orders.insert_one(
        {
            "order_id": order_id,
            "username": account["username"],
            "tweet_id": tweet.id,
            "tweet_url": tweet.url,
            "service_id": service_id,
            "quantity": qty,
            "created_at": _now(),
        }
    )
    log.info("ordered %s views (order %s) for %s", qty, order_id, tweet.url)
    await notify(
        f"✅ <b>{qty} views</b> ordered for @{account['username']}\n"
        f"{tweet.url}\norder #{order_id}"
    )


async def _claim_and_order(account: dict, tweet: Tweet) -> None:
    """Atomically claim a tweet; only the insert winner orders."""
    db = get_db()
    try:
        result = await db.processed_tweets.insert_one(
            {
                "tweet_id": tweet.id,
                "username": account["username"],
                "tweet_url": tweet.url,
                "status": "pending",
                "created_at": _now(),
            }
        )
    except DuplicateKeyError:
        return  # already handled by someone else
    await _place_order_for_tweet(account, tweet, result.inserted_id)


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

    # First ever poll for this account -> just set the baseline, order nothing.
    if last_id is None:
        await db.accounts.update_one(
            {"_id": account["_id"]},
            {"$set": {"last_tweet_id": newest_id, "baselined_at": _now()}},
        )
        log.info("baselined @%s at tweet %s (no orders placed)", username, newest_id)
        return

    # Process only tweets newer than the baseline, oldest-first for ordering.
    new_tweets = sorted((t for t in tweets if int(t.id) > last_id), key=lambda x: int(x.id))
    for tweet in new_tweets:
        if s.only_original_tweets and (tweet.is_retweet or tweet.is_reply):
            log.info("skip RT/reply %s", tweet.url)
            continue
        await _claim_and_order(account, tweet)

    if newest_id > last_id:
        await db.accounts.update_one(
            {"_id": account["_id"]}, {"$set": {"last_tweet_id": newest_id}}
        )


async def _reconcile_pending() -> None:
    """Retry tweets that were claimed but whose order never completed."""
    db = get_db()
    s = get_settings()
    cutoff = _now().timestamp() - s.pending_retry_seconds
    cursor = db.processed_tweets.find({"status": "pending"})
    async for doc in cursor:
        created = doc.get("created_at")
        if created and created.timestamp() > cutoff:
            continue  # too recent, give the original attempt time
        account = await db.accounts.find_one({"username": doc["username"]})
        if not account:
            continue
        tweet = Tweet(id=doc["tweet_id"], text="", url=doc["tweet_url"])
        log.info("reconciling pending tweet %s", doc["tweet_id"])
        await _place_order_for_tweet(account, tweet, doc["_id"])


async def poll_once() -> None:
    db = get_db()
    await _reconcile_pending()
    cursor = db.accounts.find({"enabled": True})
    async for account in cursor:
        await _poll_account(account)


async def run_poller(stop_event: asyncio.Event) -> None:
    s = get_settings()
    log.info("poller started (interval=%ss, provider=%s)", s.poll_interval_seconds, s.twitter_provider)
    while not stop_event.is_set():
        try:
            await poll_once()
        except Exception as e:
            log.exception("poll cycle error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=s.poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
