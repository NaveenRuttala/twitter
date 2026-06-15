"""
Order-plan logic shared by the detector (poller) and the executor (scheduler).

An account has an `order_plan`: a list of steps, each with its own service_id,
quantity range, and delay_minutes. When a tweet is detected we EXPAND the plan
into one `scheduled_orders` row per step, each stamped with run_at = now + delay.
The scheduler later fires each row when it comes due.

Design notes:
- Expansion is idempotent (unique index on tweet_id+step_index), so it can be
  safely retried after a crash without creating duplicate orders.
- A step value of 0 for service_id/min_qty/max_qty means "fall back to the
  account's own setting, then to the global default".
- Quantity is rolled at placement time (not schedule time) so each order gets a
  fresh random value within its range.
"""
import logging
import random
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from .config import get_settings
from .notifier import notify
from .smm_client import SmmClient, SmmError

log = logging.getLogger("plan")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_plan(account: dict) -> list[dict]:
    """Return the account's order plan. If none is set, synthesize a single
    immediate step from the account/global defaults (backward compatible)."""
    plan = account.get("order_plan") or []
    if not plan:
        return [
            {
                "service_id": account.get("service_id") or 0,
                "min_qty": account.get("min_qty") or 0,
                "max_qty": account.get("max_qty") or 0,
                "delay_minutes": 0,
            }
        ]
    return plan


def _resolve_qty(step: dict) -> int:
    s = get_settings()
    lo = step.get("min_qty") or s.order_min_qty
    hi = step.get("max_qty") or s.order_max_qty
    if hi < lo:
        lo, hi = hi, lo
    return random.randint(lo, hi)


def _resolve_service(step: dict, account: dict) -> int:
    return step.get("service_id") or account.get("service_id") or get_settings().views_service_id


async def expand_tweet_into_jobs(db, account: dict, tweet) -> int:
    """Create one scheduled_orders row per plan step for this tweet.
    Idempotent: duplicate (tweet_id, step_index) inserts are ignored."""
    plan = resolve_plan(account)
    now = _now()
    inserted = 0
    for i, step in enumerate(plan):
        run_at = now + timedelta(minutes=int(step.get("delay_minutes") or 0))
        doc = {
            "tweet_id": tweet.id,
            "username": account["username"],
            "tweet_url": tweet.url,
            "step_index": i,
            "service_id": int(step.get("service_id") or 0),
            "min_qty": int(step.get("min_qty") or 0),
            "max_qty": int(step.get("max_qty") or 0),
            "delay_minutes": int(step.get("delay_minutes") or 0),
            "run_at": run_at,
            "status": "scheduled",
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db.scheduled_orders.insert_one(doc)
            inserted += 1
        except DuplicateKeyError:
            pass  # already scheduled (idempotent re-run)
    return inserted


async def place_job(db, job: dict) -> None:
    """Place a single scheduled order. The job must already be claimed
    (status == 'processing'). Records the outcome on the job row and, on
    success, appends to the `orders` log."""
    s = get_settings()
    account = await db.accounts.find_one({"username": job["username"]})
    if not account:
        await db.scheduled_orders.update_one(
            {"_id": job["_id"]},
            {"$set": {"status": "failed", "error": "account removed", "updated_at": _now()}},
        )
        return

    qty = _resolve_qty(job)
    service_id = _resolve_service(job, account)

    if s.dry_run:
        await db.scheduled_orders.update_one(
            {"_id": job["_id"]},
            {"$set": {"status": "dry_run", "quantity": qty,
                      "service_id_used": service_id, "updated_at": _now()}},
        )
        log.info("[DRY_RUN] step %s would order %s views (svc %s) for %s",
                 job["step_index"] + 1, qty, service_id, job["tweet_url"])
        return

    smm = SmmClient()
    try:
        res = await smm.add_order(service_id=service_id, link=job["tweet_url"], quantity=qty)
        order_id = res["order"]
    except SmmError as e:
        await db.scheduled_orders.update_one(
            {"_id": job["_id"]},
            {"$set": {"status": "failed", "error": str(e),
                      "service_id_used": service_id, "updated_at": _now()}},
        )
        log.error("order failed (step %s) for %s: %s", job["step_index"] + 1, job["tweet_url"], e)
        await notify(
            f"⚠️ Order FAILED @{job['username']} step {job['step_index']+1}\n{job['tweet_url']}\n{e}"
        )
        return

    await db.scheduled_orders.update_one(
        {"_id": job["_id"]},
        {"$set": {"status": "ordered", "order_id": order_id, "quantity": qty,
                  "service_id_used": service_id, "updated_at": _now()}},
    )
    await db.orders.insert_one(
        {
            "order_id": order_id,
            "username": job["username"],
            "tweet_id": job["tweet_id"],
            "tweet_url": job["tweet_url"],
            "service_id": service_id,
            "quantity": qty,
            "step_index": job["step_index"],
            "created_at": _now(),
        }
    )
    log.info("ordered %s views (order %s, svc %s, step %s) for %s",
             qty, order_id, service_id, job["step_index"] + 1, job["tweet_url"])
    await notify(
        f"✅ {qty} views ordered @{job['username']} "
        f"(svc {service_id}, step {job['step_index']+1})\n{job['tweet_url']}\norder #{order_id}"
    )
