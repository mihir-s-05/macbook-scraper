# macbook-scraper

A small deal monitor for high-memory MacBooks. It checks:

- Apple Certified Refurbished
- B&H Photo
- Best Buy
- Amazon

The default target is deliberately strict: **M4/M5 MacBook Air or Pro, at least 24 GB unified memory, at least 1 TB storage, and $1,200 or less**. Non-Apple sources exclude listings whose titles say renewed/refurbished/open-box/used; Apple Certified Refurbished is allowed.

## Recommended deployment: AWS Lambda

For this workload, Lambda is preferable to keeping a VM alive. EventBridge Scheduler wakes the scraper every five minutes, Lambda checks the stores and exits, DynamoDB keeps dedupe state, and ntfy pushes a match to your phone.

```text
EventBridge Scheduler (5 min)
          |
          v
      AWS Lambda
          |
          +---- Apple Refurb
          +---- B&H
          +---- Best Buy
          +---- Amazon
          |
          v
      DynamoDB state
          |
       match?
          |
          v
       ntfy.sh ---> phone
```

`template.yaml` is an AWS SAM template that creates the Lambda function, EventBridge Scheduler schedule, DynamoDB state table, IAM permissions, and a CloudWatch log group with seven-day retention.

The DynamoDB table deliberately uses **provisioned capacity at 1 RCU / 1 WCU**, rather than on-demand capacity, so it fits comfortably inside DynamoDB's ongoing provisioned-capacity free tier. Lambda runs with 512 MB RAM and a 180-second timeout.

### AWS prerequisites

Install and configure:

1. AWS CLI
2. AWS SAM CLI
3. Docker (recommended because `curl-cffi` contains native code and `sam build --use-container` builds it for the Lambda environment)

Then verify your AWS credentials:

```bash
aws sts get-caller-identity
```

### Deploy to AWS

Clone the repository and run:

```bash
git clone https://github.com/mihir-s-05/macbook-scraper.git
cd macbook-scraper
sam build --use-container
sam deploy --guided
```

Suggested guided-deploy answers:

```text
Stack Name: macbook-scraper
AWS Region: us-west-2 (or your preferred region)
Parameter NtfyTopic: <your long secret ntfy topic>
Parameter NtfyToken: <blank unless you protected the topic>
Parameter NtfyServer: https://ntfy.sh
Parameter MaxPrice: 1200
Parameter MinMemoryGB: 24
Parameter MinStorageGB: 1024
Parameter AllowedChips: M4,M5
Confirm changes before deploy: Y
Allow SAM CLI IAM role creation: Y
Disable rollback: N
Save arguments to configuration file: Y
```

Do not commit the ntfy topic/token. SAM passes them to CloudFormation as `NoEcho` parameters.

After deployment, find the generated function name:

```bash
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name macbook-scraper \
  --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue" \
  --output text)

echo "$FUNCTION_NAME"
```

Run one manual scan immediately:

```bash
aws lambda invoke \
  --function-name "$FUNCTION_NAME" \
  --payload '{}' \
  response.json

cat response.json
```

A successful response looks roughly like:

```json
{
  "ok": true,
  "listings": 30,
  "matches": 0,
  "notifications_sent": 0,
  "error_sources": []
}
```

No notification is expected unless a qualifying deal exists.

Watch logs while testing:

```bash
aws logs tail "/aws/lambda/$FUNCTION_NAME" --since 10m --follow
```

The EventBridge Scheduler will then invoke it automatically every five minutes.

### Updating the AWS deployment later

After pulling code changes:

```bash
git pull
sam build --use-container
sam deploy
```

Because guided deploy saves the stack parameters locally, later deployments do not need all of the answers again.

### Remove all AWS resources

If you ever stop using it:

```bash
sam delete --stack-name macbook-scraper
```

That removes the Lambda function, schedule, state table, and associated stack resources.

## How alerts work

Each scan parses MacBook configuration/price, applies the hard filter, and sends an **urgent ntfy push notification** only when:

1. a matching SKU appears for the first time;
2. an already-matching SKU gets even cheaper; or
3. a SKU disappears for long enough and later comes back (six hours by default).

Tapping an alert opens the exact retailer listing.

In Lambda, dedupe state is stored in DynamoDB. In Docker/VPS mode, state is stored in `/data/state.json`.

Retailer HTML changes occasionally. Each source is isolated so one parser being blocked or breaking does not stop the other three; failures are written to logs/state rather than being silently treated as no inventory.

## Set up ntfy on your phone

You do **not** need a Discord app, bot, or webhook.

### 1. Install ntfy

Install the official **ntfy** app from the iOS App Store or Google Play/F-Droid and allow notifications.

### 2. Pick a secret topic

With the public `ntfy.sh` service, a topic name is effectively the secret used to reach your notifications. Use a long random value that nobody could guess. Generate one with:

```bash
python -c "import secrets; print('macbook-deals-' + secrets.token_hex(16))"
```

Do not commit the generated topic to this public repository.

### 3. Subscribe on the phone

Open ntfy, add/subscribe to your topic on the default `ntfy.sh` server, and enter the exact topic string you generated.

The scraper publishes deal alerts with `Priority: urgent`. You can further customize ntfy notification sounds/settings in your phone's notification settings.

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

The scraper supports authenticated ntfy topics. If you reserve/protect a topic and create an access token, set `NTFY_TOKEN`; the scraper publishes with `Authorization: Bearer <token>`.

## Docker / VPS mode

The original always-running mode remains supported:

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
| `POLL_INTERVAL_SECONDS` | `300` | Docker-mode seconds between scans (minimum 60) |
| `STATE_PATH` | `/data/state.json` | Docker-mode persistent state |
| `REALERT_AFTER_HOURS` | `6` | Re-alert if matching inventory was absent this long |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server base URL |
| `NTFY_TOPIC` | empty | Secret topic subscribed to on your phone |
| `NTFY_TOKEN` | empty | Optional Bearer token for a protected topic |
| `DYNAMODB_TABLE` | empty | Lambda-only state table; SAM sets this automatically |
| `RUN_ONCE` | `false` | Exit after one Docker-mode cycle |
| `PORT` | `0` | Optional Docker-mode health endpoint port |

## Notifications

An ntfy push contains the exact price, title, source, chip, memory, storage, condition, and a direct click-through to the product page. Alerts use ntfy's `urgent` priority. Always confirm price and stock on the retailer page before checkout.

## Source behavior

- **Apple:** reads Apple's `REFURB_GRID_BOOTSTRAP` product data, with an HTML fallback.
- **B&H:** searches 24 GB and 32 GB / 1 TB MacBooks and parses product cards.
- **Best Buy:** searches 24 GB and 32 GB / 1 TB MacBooks and parses SKU cards.
- **Amazon:** searches the same configurations and parses normal search-result cards. Renewed listings are filtered out.

Requests use `curl_cffi` with a current Chrome TLS/browser fingerprint rather than a plain `requests` user agent. This improves reliability, but retailers can still change markup or add anti-bot challenges. The monitor logs source-specific failures instead of silently treating them as "no deals."

## Polling

Five-minute polling is intentionally conservative: seven lightweight page requests per cycle is fast enough for limited inventory without hammering the stores. Avoid reducing the interval below a minute.
