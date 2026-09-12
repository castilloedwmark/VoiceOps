import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from twilio.request_validator import RequestValidator
from voiceops import handle_turn

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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    signature = websocket.headers.get("X-Twilio-Signature", "")

    if not validator.validate(PUBLIC_WS_URL, {}, signature):
        logger.warning("Rejected invalid Twilio WebSocket signature")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info("Accepted Twilio ConversationRelay WebSocket")
    fallback_session_id = f"ws-{uuid.uuid4().hex}"
    session_id = fallback_session_id

    try:
        while True:
            data = await websocket.receive_json()

            message_type = data.get("type")
            logger.info("ConversationRelay message type: %s", message_type)

            if message_type == "setup":
                candidate_session_id = data.get("callSid") or data.get("sessionId")
                if isinstance(candidate_session_id, str) and candidate_session_id.strip():
                    session_id = candidate_session_id.strip()
                else:
                    session_id = fallback_session_id
                logger.info("ConversationRelay session established")
                continue

            if message_type != "prompt":
                continue

            if data.get("last") is not True:
                continue

            user_text = data.get("voicePrompt", "")

            if not user_text:
                continue

            logger.info("Received caller prompt")

            try:
                response_text = await handle_turn(session_id, user_text)
            except Exception:
                logger.exception("VoiceOps handler failed")
                response_text = (
                    "I hit an internal VoiceOps error and could not complete that step. "
                    "Please try again."
                )

            await websocket.send_json({
                "type": "text",
                "token": response_text,
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
