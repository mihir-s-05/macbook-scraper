# macbook-scraper

A small always-on deal monitor for high-memory MacBooks. It checks:

- Apple Certified Refurbished
- B&H Photo
- Best Buy
- Amazon

The default target is deliberately strict: **M4/M5 MacBook Air or Pro, at least 24 GB unified memory, at least 1 TB storage, and $1,200 or less**. Non-Apple sources exclude listings whose titles say renewed/refurbished/open-box/used; Apple Certified Refurbished is allowed.

## How it works

Every five minutes (configurable), the service makes a small set of retailer searches, parses MacBook configuration/price, applies the hard filter, and sends an **urgent ntfy push notification** only when:

1. a matching SKU appears for the first time;
2. an already-matching SKU gets even cheaper; or
3. a SKU disappears for long enough and later comes back (six hours by default).

Tapping an alert opens the exact retailer listing.

State is stored in `/data/state.json`, so mount `/data` as persistent storage when deploying.

Retailer HTML changes occasionally. Each source is isolated so one parser being blocked or breaking does not stop the other three; failures are written to logs and the state file.

## Set up ntfy on your phone

You do **not** need a Discord app, bot, or webhook.

### 1. Install ntfy

Install the official **ntfy** app from the iOS App Store or Google Play/F-Droid and allow notifications.

### 2. Pick a secret topic

With the public `ntfy.sh` service, a topic name is effectively the secret used to reach your notifications. Use a long random value that nobody could guess, for example:

```text
macbook-deals-3f9c1e7a0b6d4a82b13e59c07421d8af
```

Do not use that example literally. Generate your own value with something like:

```bash
python -c "import secrets; print('macbook-deals-' + secrets.token_hex(16))"
```

Do not commit the topic to this public repository.

### 3. Subscribe on the phone

Open ntfy, add/subscribe to your topic on the default `ntfy.sh` server, and enter the exact topic string you generated.

The scraper publishes deal alerts to that same topic with `Priority: urgent`. On supported phones, high/urgent ntfy notifications use prominent notification behavior. You can further customize ntfy notification sounds/settings in your phone's notification settings.

### 4. Test the phone before deploying

Replace `<YOUR_TOPIC>` below with your topic and run:

```bash
curl \
  -H "Title: MacBook scraper test" \
  -H "Priority: urgent" \
  -H "Tags: test_tube,computer" \
  -H "Click: https://www.apple.com/shop/refurbished/mac" \
  -d "If you can read this, ntfy is ready. Tap this notification to test the link." \
  https://ntfy.sh/<YOUR_TOPIC>
```

You should receive the notification on the phone within moments. Tapping it should open Apple's refurbished Mac page.

### Optional: protected/reserved topic

The scraper also supports authenticated ntfy topics. If you later reserve/protect a topic and create an ntfy access token, set `NTFY_TOKEN`; the scraper will publish with `Authorization: Bearer <token>`. Leave it blank for the simple random-topic setup above.

## Quick start with Docker

```bash
cp .env.example .env
# Put your secret ntfy topic in .env as NTFY_TOPIC
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
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server base URL |
| `NTFY_TOPIC` | empty | Secret topic subscribed to on your phone |
| `NTFY_TOKEN` | empty | Optional Bearer token for a protected topic |
| `RUN_ONCE` | `false` | Exit after one cycle for cron-style deployment |
| `PORT` | `0` | Optional health endpoint port |

## Deploy

### Railway (simple option)

1. Create a Railway project from this GitHub repository.
2. Railway will build the included `Dockerfile`.
3. Add `NTFY_TOPIC` as a secret/environment variable. Use the exact topic you subscribed to on your phone.
4. Optionally set `NTFY_TOKEN` if your topic requires authentication.
5. Add a persistent volume mounted at `/data`.
6. Keep `RUN_ONCE=false` and `POLL_INTERVAL_SECONDS=300`.
7. Deploy. The process stays alive and polls continuously.

If Railway injects a `PORT`, the script exposes a tiny JSON health endpoint there. It does not need the HTTP server for scraping.

### Any Docker host / VPS

Clone the repository, copy `.env.example` to `.env`, configure `NTFY_TOPIC`, and run `docker compose up -d --build`. The included compose file persists state under `./data`.

## Notifications

An ntfy push contains the exact price, title, source, chip, memory, storage, condition, and a direct click-through to the product page. Alerts use ntfy's `urgent` priority. Always confirm price and stock on the retailer page before checkout.

## Source behavior

- **Apple:** reads Apple's `REFURB_GRID_BOOTSTRAP` product data, with an HTML fallback.
- **B&H:** searches 24 GB and 32 GB / 1 TB MacBooks and parses product cards.
- **Best Buy:** searches 24 GB and 32 GB / 1 TB MacBooks and parses SKU cards.
- **Amazon:** searches the same configurations and parses normal search-result cards. Renewed listings are filtered out.

Requests use `curl_cffi` with a current Chrome TLS/browser fingerprint rather than a plain `requests` user agent. This improves reliability, but retailers can still change markup or add anti-bot challenges. The monitor logs source-specific failures instead of silently treating them as "no deals."

## A note on polling

Five-minute polling is intentionally conservative: seven lightweight page requests per cycle is fast enough for limited inventory without hammering the stores. Avoid reducing the interval below a minute.
