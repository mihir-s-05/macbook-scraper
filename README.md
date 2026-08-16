# macbook-scraper

A small always-on deal monitor for high-memory MacBooks. It checks:

- Apple Certified Refurbished
- B&H Photo
- Best Buy
- Amazon

The default target is deliberately strict: **M4/M5 MacBook Air or Pro, at least 24 GB unified memory, at least 1 TB storage, and $1,200 or less**. Non-Apple sources exclude listings whose titles say renewed/refurbished/open-box/used; Apple Certified Refurbished is allowed.

## How it works

Every five minutes (configurable), the service makes a small set of retailer searches, parses MacBook configuration/price, applies the hard filter, and posts a Discord alert only when:

1. a matching SKU appears for the first time;
2. an already-matching SKU gets even cheaper; or
3. a SKU disappears for long enough and later comes back (six hours by default).

State is stored in `/data/state.json`, so mount `/data` as persistent storage when deploying.

Retailer HTML changes occasionally. Each source is isolated so one parser being blocked or breaking does not stop the other three; failures are written to logs and the state file.

## Quick start with Docker

```bash
cp .env.example .env
# Put your Discord webhook URL in .env
docker compose up -d --build

docker compose logs -f
```

To run one scan locally instead of the continuous loop:

```bash
RUN_ONCE=true STATE_PATH=./data/state.json python macbook_scraper.py
```

## Configuration

| Variable | Default | Meaning |
| --- | ---: | --- |
| `MAX_PRICE` | `1200` | Maximum alert price in USD |
| `MIN_MEMORY_GB` | `24` | Minimum unified memory |
| `MIN_STORAGE_GB` | `1024` | Minimum SSD capacity in GB |
| `ALLOWED_CHIPS` | `M4,M5` | Allowed chip generations; `M4 Pro` still counts as M4 |
| `POLL_INTERVAL_SECONDS` | `300` | Seconds between scans (minimum 60) |
| `STATE_PATH` | `/data/state.json` | Persistent dedupe/reappearance state |
| `REALERT_AFTER_HOURS` | `6` | Re-alert if matching inventory was absent this long |
| `DISCORD_WEBHOOK_URL` | empty | Discord webhook destination |
| `RUN_ONCE` | `false` | Exit after one cycle for cron-style deployment |
| `PORT` | `0` | Optional health endpoint port |

## Deploy

### Railway (simple option)

1. Create a Railway project from this GitHub repository.
2. Railway will build the included `Dockerfile`.
3. Add `DISCORD_WEBHOOK_URL` as a secret/environment variable.
4. Add a persistent volume mounted at `/data`.
5. Keep `RUN_ONCE=false` and `POLL_INTERVAL_SECONDS=300`.
6. Deploy. The process stays alive and polls continuously.

If Railway injects a `PORT`, the script exposes a tiny JSON health endpoint there. It does not need the HTTP server for scraping.

### Any Docker host / VPS

Clone the repository, copy `.env.example` to `.env`, configure the webhook, and run `docker compose up -d --build`. The included compose file persists state under `./data`.

## Notifications

A Discord message contains the exact price, title, source, chip, memory, storage, condition, and a direct product link. Always confirm price and stock on the retailer page before checkout.

## Source behavior

- **Apple:** reads Apple's `REFURB_GRID_BOOTSTRAP` product data, with an HTML fallback.
- **B&H:** searches 24 GB and 32 GB / 1 TB MacBooks and parses product cards.
- **Best Buy:** searches 24 GB and 32 GB / 1 TB MacBooks and parses SKU cards.
- **Amazon:** searches the same configurations and parses normal search-result cards. Renewed listings are filtered out.

Requests use `curl_cffi` with a current Chrome TLS/browser fingerprint rather than a plain `requests` user agent. This improves reliability, but retailers can still change markup or add anti-bot challenges. The monitor logs source-specific failures instead of silently treating them as "no deals."

## A note on polling

Five-minute polling is intentionally conservative: seven lightweight page requests per cycle is fast enough for limited inventory without hammering the stores. Avoid reducing the interval below a minute.
