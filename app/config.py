from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Mongo ---
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "tweet_views"

    # --- SMM panel (dnoxsmm) ---
    smm_api_url: str = "https://dnoxsmm.com/api/v2"
    smm_api_key: str = ""
    views_service_id: int = 0           # the service id for "Views" on the panel
    order_min_qty: int = 3000
    order_max_qty: int = 4000

    # --- Twitter provider ---
    # "twitterapi_io"  -> cheap third-party polling (recommended)
    # "x_official"     -> official X API v2 (needs Basic tier+, ~$200/mo)
    twitter_provider: str = "twitterapi_io"
    twitterapi_io_key: str = ""
    x_bearer_token: str = ""

    # --- Behaviour ---
    poll_interval_seconds: int = 60
    scheduler_tick_seconds: int = 20    # how often to check for due scheduled orders
    only_original_tweets: bool = True   # skip retweets and replies
    dry_run: bool = False               # if True, log orders but don't actually place them
    pending_retry_seconds: int = 120    # retry orders stuck in "pending" older than this
    processing_retry_seconds: int = 300 # reset scheduled orders stuck "processing" (crash recovery)

    # --- Admin auth (protects the management API) ---
    admin_token: str = "change-me"

    # --- Telegram notifications (optional) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
