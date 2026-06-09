from ..config import get_settings
from .base import TweetProvider
from .twitterapi_io import TwitterApiIoProvider
from .x_official import XOfficialProvider


def get_provider() -> TweetProvider:
    name = get_settings().twitter_provider.lower()
    if name == "x_official":
        return XOfficialProvider()
    return TwitterApiIoProvider()
