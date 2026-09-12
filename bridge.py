import logging
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from twilio.request_validator import RequestValidator

load_dotenv()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("hermes-bridge")

HERMES_URL = os.getenv(
    "HERMES_URL",
    "http://127.0.0.1:8642/v1/chat/completions",
)
HERMES_API_KEY = os.environ["HERMES_API_KEY"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]

PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")
PUBLIC_WS_URL = os.environ["PUBLIC_WS_URL"]

validator = RequestValidator(TWILIO_AUTH_TOKEN)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "hermes-bridge"
    }


@app.post("/voice")
async def voice(request: Request):
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature", "")

    public_url = f"{PUBLIC_BASE_URL}/voice"

    if not validator.validate(public_url, form, signature):
        logger.warning("Rejected invalid Twilio /voice signature")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    logger.info("Accepted Twilio /voice request")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <ConversationRelay
            url="{PUBLIC_WS_URL}"
            welcomeGreeting="Welcome to VoiceOps, your incident response assistant. Describe the issue you're experiencing, and I'll help investigate."
        />
    </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


async def ask_hermes(message: str) -> str:
    headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "model": "hermes-agent",
        "messages": [
            {
                "role": "user",
                "content": message
            }
        ],
    }

    logger.info("Sending prompt to Hermes")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                HERMES_URL,
                headers=headers,
                json=body,
            )

            response.raise_for_status()
            data = response.json()

        logger.info("Hermes responded successfully")
        return data["choices"][0]["message"]["content"]

    except httpx.ConnectError:
        logger.exception("Unable to connect to Hermes")
        return "Hermes is currently unavailable. Please try again later."

    except httpx.TimeoutException:
        logger.exception("Hermes request timed out")
        return "Hermes did not respond in time. Please try again."

    except httpx.HTTPStatusError as exc:
        logger.exception(
            "Hermes returned HTTP error %s",
            exc.response.status_code
        )
        return "Hermes returned an error. Please try again."

    except Exception:
        logger.exception("Unexpected Hermes error")
        return "An unexpected error occurred while contacting Hermes."


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    signature = websocket.headers.get("X-Twilio-Signature", "")

    if not validator.validate(PUBLIC_WS_URL, {}, signature):
        logger.warning("Rejected invalid Twilio WebSocket signature")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("Accepted Twilio ConversationRelay WebSocket")

    try:
        while True:
            data = await websocket.receive_json()

            message_type = data.get("type")
            logger.info("ConversationRelay message type: %s", message_type)

            if message_type != "prompt":
                continue

            if not data.get("last", True):
                continue

            user_text = data.get("voicePrompt", "")

            if not user_text:
                continue

            logger.info("Received caller prompt")

            hermes_response = await ask_hermes(user_text)

            await websocket.send_json({
                "type": "text",
                "token": hermes_response,
                "last": True
            })

            logger.info("Sent response to ConversationRelay")

    except WebSocketDisconnect:
        logger.info("ConversationRelay WebSocket disconnected")

    except Exception:
        logger.exception("Unexpected WebSocket error")

        try:
            await websocket.send_json({
                "type": "text",
                "token": "The voice service encountered an unexpected error.",
                "last": True
            })
        except Exception:
            pass
