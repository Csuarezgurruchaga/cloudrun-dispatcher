import json
import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispatcher")

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")

ROUTES: Dict[str, str] = {
    "908255469043841": "https://artuso-chatbot-581915457964.southamerica-west1.run.app/webhook/whatsapp",
    "883679188152252": "https://argenfuego-chatbot-581915457964.southamerica-west1.run.app/webhook/whatsapp",
}


def extract_phone_number_id(payload: Dict[str, Any]) -> str:
    return (
        payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
    )


async def forward_payload(
    url: str,
    body_bytes: bytes,
    signature: Optional[str],
    content_type: Optional[str],
) -> None:
    try:
        headers = {}
        if signature:
            headers["X-Hub-Signature-256"] = signature
        if content_type:
            headers["Content-Type"] = content_type
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, content=body_bytes, headers=headers)
            logger.info("Forwarded payload to %s status=%s", url, response.status_code)
    except Exception as exc:
        logger.exception("Failed to forward payload to %s: %s", url, exc)


@app.get("/webhook")
async def verify_webhook(request: Request) -> PlainTextResponse:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge is not None:
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Webhook verification failed: mode=%s token_match=%s", mode, token == VERIFY_TOKEN)
    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> Dict[str, str]:
    body_bytes = await request.body()
    try:
        payload: Dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
    except Exception as exc:
        logger.exception("Invalid JSON payload: %s", exc)
        return {"status": "ok"}

    try:
        phone_number_id = extract_phone_number_id(payload)
    except Exception as exc:
        logger.exception("Failed to extract phone_number_id: %s", exc)
        return {"status": "ok"}

    target_url = ROUTES.get(str(phone_number_id))
    if not target_url:
        logger.error("No route configured for phone_number_id=%s", phone_number_id)
        return {"status": "ok"}

    signature = request.headers.get("X-Hub-Signature-256")
    content_type = request.headers.get("Content-Type")
    background_tasks.add_task(
        forward_payload,
        target_url,
        body_bytes,
        signature,
        content_type,
    )
    return {"status": "ok"}
