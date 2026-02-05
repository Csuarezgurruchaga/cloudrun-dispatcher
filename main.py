import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispatcher")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")

ROUTES: Dict[str, str] = {
    "908255469043841": "https://artuso-chatbot-581915457964.southamerica-west1.run.app/webhook/whatsapp",  # Administracion Artuso
    "883679188152252": "https://argenfuego-chatbot-581915457964.southamerica-west1.run.app/webhook/whatsapp",  # Argenfuego SRL
    "893398707191906": "https://8cc9-200-117-49-5.ngrok-free.app/webhooks/whatsapp",  # ngrok test
}

http_client: Optional[httpx.AsyncClient] = None


def _load_routes_from_env() -> None:
    routes_json = os.getenv("ROUTES_JSON")
    if not routes_json:
        return
    try:
        parsed = json.loads(routes_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ROUTES_JSON is not valid JSON") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    ):
        raise RuntimeError("ROUTES_JSON must be a JSON object of string:string pairs")
    ROUTES.clear()
    ROUTES.update(parsed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    _load_routes_from_env()
    http_client = httpx.AsyncClient(timeout=10.0)
    try:
        yield
    finally:
        client = http_client
        http_client = None
        if client is not None and not client.is_closed:
            await client.aclose()


app = FastAPI(lifespan=lifespan, redirect_slashes=False)


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
        client = http_client
        if client is None or client.is_closed:
            async with httpx.AsyncClient(timeout=10.0) as temp_client:
                response = await temp_client.post(url, content=body_bytes, headers=headers)
        else:
            response = await client.post(url, content=body_bytes, headers=headers)
        logger.info("Forwarded payload to %s status=%s", url, response.status_code)
    except Exception as exc:
        logger.exception("Failed to forward payload to %s: %s", url, exc)


@app.get("/webhook")
@app.get("/webhook/")
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
@app.post("/webhook/")
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
