from abc import ABC, abstractmethod

from ..models import Tweet


class TweetProvider(ABC):
    """
    A provider knows how to fetch the most recent tweets for a username.

    Implementations must return tweets sorted NEWEST FIRST. The poller handles
    dedup/baseline, so a provider just needs to return the latest N tweets.
    """

    @abstractmethod
    async def get_latest_tweets(self, username: str, limit: int = 20) -> list[Tweet]:
        ...
