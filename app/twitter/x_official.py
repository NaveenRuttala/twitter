"""
Official X (Twitter) API v2 provider.

Requires a Bearer token from a project on at least the **Basic** tier
(~$200/mo). The Free tier does NOT allow reading user timelines, so this
provider will not work on Free — use the twitterapi.io provider instead.

Flow:
  1. resolve username -> user id   GET /2/users/by/username/:name
  2. fetch timeline                GET /2/users/:id/tweets

We cache username->id in memory to avoid burning the (tight) rate limits.
"""
import httpx

from ..config import get_settings
from ..models import Tweet
from .base import TweetProvider

BASE = "https://api.twitter.com/2"


class XOfficialProvider(TweetProvider):
    def __init__(self):
        self.token = get_settings().x_bearer_token
        self._id_cache: dict[str, str] = {}

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def _resolve_id(self, client: httpx.AsyncClient, username: str) -> str:
        if username in self._id_cache:
            return self._id_cache[username]
        r = await client.get(f"{BASE}/users/by/username/{username}", headers=self._headers())
        r.raise_for_status()
        uid = r.json()["data"]["id"]
        self._id_cache[username] = uid
        return uid

    async def get_latest_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        if not self.token:
            raise RuntimeError("X_BEARER_TOKEN is not set")

        params = {
            "max_results": max(5, min(limit, 100)),
            "tweet.fields": "created_at,referenced_tweets",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            uid = await self._resolve_id(client, username)
            r = await client.get(f"{BASE}/users/{uid}/tweets", headers=self._headers(), params=params)
            r.raise_for_status()
            data = r.json().get("data", []) or []

        tweets: list[Tweet] = []
        for t in data:
            refs = t.get("referenced_tweets", []) or []
            kinds = {ref.get("type") for ref in refs}
            tweets.append(
                Tweet(
                    id=str(t["id"]),
                    text=t.get("text", ""),
                    url=f"https://x.com/{username}/status/{t['id']}",
                    created_at=t.get("created_at"),
                    is_retweet="retweeted" in kinds,
                    is_reply="replied_to" in kinds,
                )
            )
        tweets.sort(key=lambda x: int(x.id), reverse=True)
        return tweets
