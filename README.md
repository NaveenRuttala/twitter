# Tweet Views Automation

Monitors a set of Twitter/X accounts and, whenever one of them posts a new tweet,
automatically places a "views" order (3000–4000 by default) on your SMM panel
(dnoxsmm / any Perfect-Panel clone).

- **Stack:** FastAPI + MongoDB (Atlas) + a background poller, deploys on Railway
- **Twitter monitoring:** pluggable provider — `twitterapi.io` (cheap, no X approval)
  or the official X API v2 (needs Basic tier, ~$200/mo)
- **Safety:** baseline-on-add (no backfilling old tweets) + exactly-once ordering
  (unique index, insert-before-order) so a restart or overlapping poll can never
  double-order the same tweet
- Optional Telegram notifications, plus a small password-protected dashboard

## How it decides to order

1. When you add `@account`, the latest tweet id is recorded as a **baseline** —
   nothing already posted gets ordered.
2. Every `POLL_INTERVAL_SECONDS` the poller fetches latest tweets per account.
3. For each tweet newer than the baseline it atomically **claims** the tweet
   (unique insert). Only the claim-winner places the order. Duplicate → skip.
4. By default retweets and replies are skipped (`ONLY_ORIGINAL_TWEETS=true`).

## Setup

1. **MongoDB Atlas** — create a free cluster, copy the connection string.
2. **twitterapi.io** — sign up, grab the API key ($1 free credit; ~$0.15/1k tweets).
   *(Or use official X API v2: set `TWITTER_PROVIDER=x_official` and `X_BEARER_TOKEN`.)*
3. **SMM panel** — get your API key from the dnoxsmm account page.
4. Copy `.env.example` → `.env` and fill it in.

### Find your Views service id

The panel has hundreds of services. Start the app, then:

```
curl -H "X-Admin-Token: <ADMIN_TOKEN>" "https://<your-app>/api/services?q=views"
```

Pick the right "Views" service and put its `service` number in `VIEWS_SERVICE_ID`.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit
uvicorn app.main:app --reload
```

Open http://localhost:8000 , enter your admin token, add an account.

**Tip:** set `DRY_RUN=true` first — it logs what it *would* order without spending money.

## Deploy on Railway

1. Push this folder to a GitHub repo.
2. Railway → New Project → Deploy from repo. Nixpacks auto-detects Python.
3. Add all the variables from `.env.example` under **Variables**.
4. Railway sets `$PORT` automatically; the start command is in `railway.json`.

The poller runs inside the web process, so a single service is enough. If you
later track many accounts, split the poller into its own Railway worker service.

## API (all require `X-Admin-Token` header)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/accounts` | list tracked accounts |
| POST | `/api/accounts` | add `{username, min_qty?, max_qty?, service_id?}` |
| PATCH | `/api/accounts/{username}` | enable/disable or change qty/service |
| DELETE | `/api/accounts/{username}` | stop tracking |
| GET | `/api/orders?limit=50` | recent orders |
| GET | `/api/balance` | panel balance |
| GET | `/api/services?q=views` | search panel services |
| POST | `/api/poll-now` | force a poll cycle |

## Notes & cost control

- twitterapi.io recommends `advanced_search` (or their stream) over `last_tweets`
  for very frequent single-account polling. At 60s intervals for a handful of
  accounts the cost is tiny; if you scale up, swap the provider implementation.
- Polling every 60s means up to ~60s delay between tweet and order — fine for views.
- Heads-up: buying engagement violates X's terms of service. That's a business
  decision on your end; this tool just wires the two APIs together.
