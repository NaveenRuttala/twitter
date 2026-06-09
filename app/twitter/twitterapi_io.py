"""
twitterapi.io provider — the cheap, no-X-approval option.

Endpoint:  GET https://api.twitterapi.io/twitter/user/last_tweets
Auth:      header  X-API-Key: <key>
Param:     userName=<handle>
Cost:      ~$0.15 / 1000 tweets returned ($1 free credit on signup)

Response shape (relevant bits):
{
  "tweets": [
    {
      "id": "1790...",
      "text": "...",
      "url": "https://x.com/.../status/1790...",
      "createdAt": "...",
      "isReply": false,
      "retweeted_tweet": {...} | null,   # present when it's a retweet
      ...
    }
  ],
  "has_next_page": false
}

Field names on third-party APIs drift over time, so we read defensively.
"""
import httpx

from ..config import get_settings
from ..models import Tweet
from .base import TweetProvider

API_URL = "https://api.twitterapi.io/twitter/user/last_tweets"


class TwitterApiIoProvider(TweetProvider):
    def __init__(self):
        self.key = get_settings().twitterapi_io_key

    async def get_latest_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        if not self.key:
            raise RuntimeError("TWITTERAPI_IO_KEY is not set")

        headers = {"X-API-Key": self.key}
        params = {"userName": username}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(API_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

        # The API has used both top-level "tweets" and a nested "data.tweets"
        # across versions; handle both.
        raw = data.get("tweets")
        if raw is None and isinstance(data.get("data"), dict):
            raw = data["data"].get("tweets")
        raw = raw or []

        tweets: list[Tweet] = []
        for t in raw[:limit]:
            tid = str(t.get("id") or t.get("tweet_id") or "")
            if not tid:
                continue
            is_rt = bool(t.get("retweeted_tweet") or t.get("isRetweet") or t.get("is_retweet"))
            is_reply = bool(
                t.get("isReply")
                or t.get("is_reply")
                or t.get("inReplyToId")
                or t.get("in_reply_to_status_id")
            )
            url = t.get("url") or t.get("twitterUrl") or f"https://x.com/{username}/status/{tid}"
            tweets.append(
                Tweet(
                    id=tid,
                    text=t.get("text", "") or "",
                    url=url,
                    created_at=t.get("createdAt") or t.get("created_at"),
                    is_retweet=is_rt,
                    is_reply=is_reply,
                )
            )

        # ensure newest-first by numeric id (tweet ids are monotonic)
        tweets.sort(key=lambda x: int(x.id), reverse=True)
        return tweets
