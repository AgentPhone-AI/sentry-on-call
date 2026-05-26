import asyncio
import hashlib
import hmac
import json
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Request

AGENTPHONE_BASE = "https://api.agentphone.ai"
CALL_LEVELS = {"fatal", "error"}

app = FastAPI()

LAST_SENT: dict[str, float] = {}


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HTTPException(status_code=503, detail=f"missing env var: {name}")
    return value


def parse_numbers(raw: str) -> list[str]:
    nums = [n.strip() for n in raw.split(",") if n.strip()]
    if not nums:
        raise HTTPException(status_code=503, detail="ALERT_PHONE_NUMBERS is empty")
    return nums


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@app.get("/health")
def health():
    return {"ok": True, "configured": all(os.environ.get(k) for k in (
        "AGENTPHONE_API_KEY", "AGENTPHONE_AGENT_ID", "ALERT_PHONE_NUMBERS", "SENTRY_WEBHOOK_SECRET"
    ))}


@app.post("/sentry")
async def sentry_webhook(request: Request):
    secret = require("SENTRY_WEBHOOK_SECRET")
    api_key = require("AGENTPHONE_API_KEY")
    agent_id = require("AGENTPHONE_AGENT_ID")
    numbers = parse_numbers(require("ALERT_PHONE_NUMBERS"))

    body = await request.body()
    signature = request.headers.get("sentry-hook-signature", "")
    resource = request.headers.get("sentry-hook-resource", "")

    if not verify_signature(body, signature, secret):
        raise HTTPException(status_code=401, detail="bad signature")

    payload = json.loads(body)
    action_str = payload.get("action")

    if resource == "installation":
        return {"ok": True, "skipped": "installation"}
    if resource == "issue" and action_str != "created":
        return {"ok": True, "skipped": f"issue.{action_str}"}

    data = payload.get("data", {})
    event = data.get("event", {}) or {}
    issue = data.get("issue", {}) or {}

    level = (event.get("level") or issue.get("level") or "info").lower()
    project = event.get("project_slug") or issue.get("project", {}).get("slug") or "unknown"
    environment = event.get("environment") or "unknown"
    title = event.get("title") or issue.get("title") or "unknown error"
    url = issue.get("web_url") or event.get("web_url") or ""

    is_call = level in CALL_LEVELS
    channel = "call" if is_call else "sms"
    cooldown_minutes = int(os.environ.get(
        "CALL_COOLDOWN_MINUTES" if is_call else "SMS_COOLDOWN_MINUTES",
        "30" if is_call else "15",
    ))

    issue_key = str(issue.get("id") or title)
    rate_key = f"{issue_key}:{channel}"
    now = time.time()
    last = LAST_SENT.get(rate_key, 0)
    if now - last < cooldown_minutes * 60:
        return {
            "ok": True,
            "skipped": "rate-limited",
            "channel": channel,
            "issue_id": issue_key,
            "seconds_until_unlock": int(cooldown_minutes * 60 - (now - last)),
        }

    async with httpx.AsyncClient(timeout=15) as client:
        if is_call:
            tasks = [
                place_call(client, api_key, agent_id, n, level, project, environment, title)
                for n in numbers
            ]
        else:
            sms_body = f"[{level.upper()}] {project} ({environment}): {title}"
            if url:
                sms_body = f"{sms_body}\n{url}"
            tasks = [
                send_sms(client, api_key, agent_id, n, sms_body)
                for n in numbers
            ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = [r for r in results if isinstance(r, Exception)]
    if len(errors) == len(results):
        raise HTTPException(status_code=502, detail=f"all sends failed: {errors[0]!r}")

    LAST_SENT[rate_key] = now

    return {
        "ok": True,
        "level": level,
        "action": channel,
        "recipients": len(numbers),
        "failed": len(errors),
    }


async def send_sms(client, api_key, agent_id, to_number, body_text):
    r = await client.post(
        f"{AGENTPHONE_BASE}/v1/messages",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "agent_id": agent_id,
            "to_number": to_number,
            "body": body_text,
        },
    )
    r.raise_for_status()


async def place_call(client, api_key, agent_id, to_number, level, project, environment, title):
    system_prompt = (
        f"You are calling an on-call engineer with an urgent production alert. "
        f"Be brief and serious, no chit-chat. "
        f"A {level} just fired in the {project} project in {environment}. "
        f"The error title is: {title}. "
        f"Tell them to check Sentry for details. Wait for acknowledgment, then end the call."
    )
    greeting = f"Urgent production alert. A {level} in {project}: {title}."
    r = await client.post(
        f"{AGENTPHONE_BASE}/v1/calls",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "agentId": agent_id,
            "toNumber": to_number,
            "systemPrompt": system_prompt,
            "initialGreeting": greeting,
        },
    )
    r.raise_for_status()
