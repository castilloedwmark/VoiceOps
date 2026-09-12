# VoiceOps

VoiceOps is a voice-first incident-response demonstration for the Agents Everywhere hackathon. This initial baseline preserves the tested Twilio ConversationRelay, Cloudflare Tunnel, FastAPI, and Hermes plumbing. The deterministic incident workflow is added in later milestones.

## Setup

1. Create a Python 3.13 virtual environment in `.venv`.
2. Install dependencies with `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Copy `.env.example` to the ignored local `.env` and supply the required values.

The public HTTPS and WebSocket addresses are operational routing configuration. Keep real addresses only in the local `.env`; committed examples use neutral hostnames.

## Run

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn bridge:app --host 127.0.0.1 --port 9001
```

Check the local service at `http://127.0.0.1:9001/health`. A successful health response confirms only the local bridge; the complete phone path also requires Cloudflare, Twilio, ConversationRelay, and Hermes.
