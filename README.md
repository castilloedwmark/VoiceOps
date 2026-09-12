# VoiceOps

VoiceOps is a voice-first incident-response demonstration for the Agents Everywhere hackathon. It combines Twilio ConversationRelay, Cloudflare Tunnel, FastAPI, Hermes reasoning, and a deterministic Python incident core.

Hermes investigates through exactly six allowlisted read/test tools. Python owns session-specific approval, the simulated rollback, and fresh routing, SIP, and synthetic-call verification before an incident can be resolved. The conversational CLI and phone bridge use the same `handle_turn()` workflow.

## Setup

1. Create a Python 3.13 virtual environment in `.venv`.
2. Install dependencies with `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Copy `.env.example` to the ignored local `.env` and supply the required values.

The public HTTPS and WebSocket addresses are operational routing configuration. Keep real addresses only in the local `.env`; committed examples use neutral hostnames.

## Run

From the repository root:

Reset the demonstration to its known-bad starting state:

```powershell
.\.venv\Scripts\python.exe reset_demo.py
```

Start the conversational CLI fallback:

```powershell
.\.venv\Scripts\python.exe voiceops.py
```

Start the phone bridge:

```powershell
.\.venv\Scripts\python.exe -m uvicorn bridge:app --host 127.0.0.1 --port 9001
```

Check the local service at `http://127.0.0.1:9001/health`. A successful health response confirms only the local bridge; the complete phone path also requires Cloudflare, Twilio, ConversationRelay, and Hermes.
