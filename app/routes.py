from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from .config import get_settings
from .db import get_db
from .models import AddAccountRequest, PlanUpdateRequest, UpdateAccountRequest
from .poller import poll_once
from .scheduler import scheduler_once
from .smm_client import SmmClient
from .twitter import get_provider

router = APIRouter(prefix="/api", tags=["admin"])


def require_admin(x_admin_token: str = Header(default="")):
    if x_admin_token != get_settings().admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")


def _clean(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


@router.get("/accounts", dependencies=[Depends(require_admin)])
async def list_accounts():
    db = get_db()
    return [_clean(d) async for d in db.accounts.find().sort("username", 1)]


@router.post("/accounts", dependencies=[Depends(require_admin)])
async def add_account(req: AddAccountRequest):
    db = get_db()
    existing = await db.accounts.find_one({"username": req.username})
    if existing:
        raise HTTPException(status_code=409, detail="already tracked")

    # Baseline immediately so we never backfill historical tweets.
    last_id = None
    try:
        tweets = await get_provider().get_latest_tweets(req.username, limit=5)
        if tweets:
            last_id = max(int(t.id) for t in tweets)
    except Exception:
        last_id = None  # poller will baseline on first successful fetch

    doc = {
        "username": req.username,
        "enabled": req.enabled,
        "min_qty": req.min_qty,
        "max_qty": req.max_qty,
        "service_id": req.service_id,
        "last_tweet_id": last_id,
        "created_at": datetime.now(timezone.utc),
    }
    if last_id is not None:
        doc["baselined_at"] = datetime.now(timezone.utc)
    await db.accounts.insert_one(doc)
    return {"ok": True, "username": req.username, "baseline_tweet_id": last_id}


@router.patch("/accounts/{username}", dependencies=[Depends(require_admin)])
async def update_account(username: str, req: UpdateAccountRequest):
    db = get_db()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True, "updated": 0}
    res = await db.accounts.update_one({"username": username.lower()}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "updated": res.modified_count}


@router.delete("/accounts/{username}", dependencies=[Depends(require_admin)])
async def remove_account(username: str):
    db = get_db()
    res = await db.accounts.delete_one({"username": username.lower()})
    return {"ok": True, "deleted": res.deleted_count}


@router.get("/accounts/{username}/plan", dependencies=[Depends(require_admin)])
async def get_plan(username: str):
    """Return the account's order plan (the per-tweet order sequence)."""
    db = get_db()
    acc = await db.accounts.find_one({"username": username.lower()})
    if not acc:
        raise HTTPException(status_code=404, detail="not found")
    plan = acc.get("order_plan") or []
    return {"username": username.lower(), "steps": plan}


@router.put("/accounts/{username}/plan", dependencies=[Depends(require_admin)])
async def set_plan(username: str, req: PlanUpdateRequest):
    """Replace the account's order plan. Each step has its own service_id,
    quantity range, and delay_minutes. An empty list reverts to the single
    default order."""
    db = get_db()
    steps = [s.model_dump() for s in req.steps]
    res = await db.accounts.update_one(
        {"username": username.lower()}, {"$set": {"order_plan": steps}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "username": username.lower(), "steps": steps}


@router.get("/orders", dependencies=[Depends(require_admin)])
async def list_orders(limit: int = 50):
    db = get_db()
    cur = db.orders.find().sort("created_at", -1).limit(min(limit, 200))
    return [_clean(d) async for d in cur]


@router.get("/tweets", dependencies=[Depends(require_admin)])
async def list_tweets(limit: int = 50):
    """Complete data: each detected tweet plus all of its scheduled/placed
    orders (one row per plan step), newest tweet first."""
    db = get_db()
    tweets = [_clean(d) async for d in
              db.processed_tweets.find().sort("created_at", -1).limit(min(limit, 200))]
    for t in tweets:
        jobs = [_clean(j) async for j in
                db.scheduled_orders.find({"tweet_id": t["tweet_id"]}).sort("step_index", 1)]
        t["orders"] = jobs
        t["ordered_count"] = sum(1 for j in jobs if j.get("status") == "ordered")
        t["pending_count"] = sum(1 for j in jobs if j.get("status") in ("scheduled", "processing"))
    return tweets


@router.get("/stats", dependencies=[Depends(require_admin)])
async def stats():
    """Counts for the dashboard header."""
    db = get_db()
    return {
        "tweets_detected": await db.processed_tweets.count_documents({}),
        "orders_placed": await db.orders.count_documents({}),
        "orders_scheduled": await db.scheduled_orders.count_documents(
            {"status": {"$in": ["scheduled", "processing"]}}),
        "orders_failed": await db.scheduled_orders.count_documents({"status": "failed"}),
    }


@router.post("/poll-now", dependencies=[Depends(require_admin)])
async def poll_now():
    """Run one detection sweep, then immediately run the scheduler so any
    zero-delay steps fire right away (useful for testing)."""
    await poll_once()
    await scheduler_once()
    return {"ok": True}


@router.get("/balance", dependencies=[Depends(require_admin)])
async def balance():
    return await SmmClient().balance()


@router.get("/services", dependencies=[Depends(require_admin)])
async def services(q: str = ""):
    """List panel services. Pass ?q=views to find the Views service id."""
    data = await SmmClient().services()
    if q:
        ql = q.lower()
        data = [s for s in data if ql in str(s.get("name", "")).lower()
                or ql in str(s.get("category", "")).lower()]
    return data
