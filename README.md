# sentry-on-call

> Your phone rings when prod goes down. Sentry alerts to real phone calls and texts, via [AgentPhone](https://agentphone.ai).

A ~120-line Python service that turns Sentry alerts into actual phone calls and SMS. New `error` or `fatal` in your Sentry project? Your phone rings and an AI voice reads the alert out loud. A `warning` or `info`? You get a text. Multiple recipients are rung in parallel.

## What an alert looks like

**SMS (warning / info):**

```
[WARNING] my-app (production): Slow database query on /api/users
https://sentry.io/issues/4567...
```

**Phone call (error / fatal):**

> "Urgent production alert. An error in my-app: NullPointerException at UserService.java. Tell them to check Sentry for details."

The AI voice waits for you to say "got it" and hangs up.

## Deploy in ~3 minutes

You need:

1. An [AgentPhone](https://agentphone.ai) account (free signup, gives you a real US/Canada phone number)
2. A Sentry project you want alerts from
3. Somewhere to run this service

### Option A: Render (one-click)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/manav2modi/sentry-on-call)

Set the four env vars from the table below when prompted.

### Option B: Docker (anywhere)

```bash
docker build -t sentry-on-call .
docker run -p 8000:8000 \
  -e AGENTPHONE_API_KEY=sk_live_xxx \
  -e AGENTPHONE_AGENT_ID=agt_xxx \
  -e ALERT_PHONE_NUMBERS=+14155551234 \
  -e SENTRY_WEBHOOK_SECRET=xxx \
  sentry-on-call
```

### Option C: Railway / Fly.io / Heroku / anywhere with a Procfile

The repo has a `Procfile`, so any platform that follows the Procfile convention (Railway, Fly, Heroku, Dokku, etc.) will just work. Push the repo, set the env vars, done.

### Option D: Local + ngrok (for testing)

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in real values
export $(grep -v '^#' .env | xargs)
uvicorn main:app --port 8000 &
ngrok http 8000  # gives you a public URL to paste into Sentry
```

## Env vars

| Name | Required | Where to get it |
| --- | --- | --- |
| `AGENTPHONE_API_KEY` | Yes | [agentphone.ai/dashboard](https://agentphone.ai/dashboard) -> Settings -> API Keys |
| `AGENTPHONE_AGENT_ID` | Yes | `curl https://api.agentphone.ai/v1/agents -H "Authorization: Bearer $AGENTPHONE_API_KEY"` and copy the `id` field |
| `ALERT_PHONE_NUMBERS` | Yes | Comma-separated E.164 numbers, e.g. `+14155551234,+14155559999`. Each one gets the SMS / call. |
| `SENTRY_WEBHOOK_SECRET` | Yes | Sentry Internal Integration's Client Secret (see Sentry setup below) |
| `SMS_COOLDOWN_MINUTES` | No | Default `15`. Same issue won't SMS again within this window. |
| `CALL_COOLDOWN_MINUTES` | No | Default `30`. Same issue won't call again within this window. |

## Sentry setup

1. In Sentry: Settings -> Developer Settings -> Custom Integrations -> **Create New Integration** -> **Internal Integration**.
2. Fill in:
   - **Name**: `On-Call Phone` (or anything you like)
   - **Webhook URL**: `https://<your-deploy-url>/sentry`
   - **Permissions**: set `Issue & Event` to `Read`
   - **Webhooks** section: tick `issue`
3. Click **Save Changes**. The page reloads with a **Client Secret** displayed near the top. Copy it into `SENTRY_WEBHOOK_SECRET` in your deploy.

That's it. Every new issue in any project in that org will now page you. No alert rules required: Sentry's Internal Integrations fire on subscribed events automatically.

## Test it

Once `https://<your-deploy-url>/health` returns `{"ok":true,"configured":true}`, send a test event into your Sentry project:

```bash
# Replace DSN with the one from your Sentry project's Client Keys page
curl -X POST 'https://<your-sentry-host>/api/<project_id>/store/' \
  -H 'X-Sentry-Auth: Sentry sentry_version=7, sentry_key=<your-public-key>' \
  -H 'Content-Type: application/json' \
  -d '{"level":"error","message":"sentry-on-call test","environment":"production","platform":"python"}'
```

Within ~10 seconds your phone should ring.

## How it works

Sentry's Internal Integrations let you subscribe to `issue` events. Every time a new issue is created, Sentry POSTs a signed JSON payload to your `/sentry` endpoint. This service:

1. Verifies the HMAC-SHA256 signature using `SENTRY_WEBHOOK_SECRET`
2. Reads the event `level`
3. For `error` / `fatal`: calls every number in `ALERT_PHONE_NUMBERS` in parallel via AgentPhone's `POST /v1/calls`. AgentPhone runs an autonomous AI agent that reads the alert out loud and waits for acknowledgment.
4. For everything else: SMS every number via AgentPhone's `POST /v1/messages`.
5. Tracks per-issue last-send time in memory so retries and storms don't blow up your phone.

State is in-memory, so a container restart resets cooldowns. For most setups that's fine.

## Customizing

- **Which levels ring vs text**: change `CALL_LEVELS` at the top of `main.py`.
- **Voice script**: edit `place_call()` to change what the AI says.
- **SMS format**: edit the `sms_body` construction in `sentry_webhook()`.
- **Filter by project or environment**: add a check after the payload is parsed and `return` early if you don't want to page.

## License

MIT. See [LICENSE](LICENSE).

---

Built with [AgentPhone](https://agentphone.ai) (the phone number your AI agent uses), [Sentry](https://sentry.io), and FastAPI.
