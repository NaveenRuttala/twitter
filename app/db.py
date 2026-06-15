from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

from .config import get_settings

_client: AsyncIOMotorClient | None = None


def get_db():
    global _client
    settings = get_settings()
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client[settings.mongodb_db]


async def init_indexes():
    db = get_db()

    # one document per tracked account
    await db.accounts.create_index([("username", ASCENDING)], unique=True)

    # exactly-once guarantee: a tweet can only ever be inserted once.
    # The unique index is what makes double-ordering impossible even across
    # restarts or two overlapping poll cycles.
    await db.processed_tweets.create_index([("tweet_id", ASCENDING)], unique=True)
    await db.processed_tweets.create_index([("status", ASCENDING), ("created_at", ASCENDING)])

    await db.orders.create_index([("tweet_id", ASCENDING)])
    await db.orders.create_index([("created_at", DESCENDING)])

    # Persistent scheduled-order queue. One row per plan-step per tweet.
    # Unique (tweet_id, step_index) makes expansion idempotent: re-running it
    # after a crash can never create duplicate steps.
    await db.scheduled_orders.create_index(
        [("tweet_id", ASCENDING), ("step_index", ASCENDING)], unique=True
    )
    # The scheduler queries this every tick to find due work.
    await db.scheduled_orders.create_index([("status", ASCENDING), ("run_at", ASCENDING)])
