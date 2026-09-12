# VoiceOps

VoiceOps is a voice-first incident-response demonstration for the Agents Everywhere hackathon. It combines Twilio ConversationRelay, Cloudflare Tunnel, FastAPI, Hermes reasoning, and a deterministic Python incident core.

Hermes investigates through exactly six allowlisted read/test tools. Python owns session-specific approval, the simulated rollback, and fresh routing, SIP, and synthetic-call verification before an incident can be resolved. The conversational CLI and phone bridge use the same `handle_turn()` workflow.

## What the demo does

- Investigates the seeded Portland outbound-calling incident using incident, site-health, SIP-metric, change, and synthetic-call evidence.
- Uses Hermes for evidence-based reasoning and concise spoken responses.
- Requires explicit, session-specific human approval before rollback.
- Applies a deterministic simulated rollback and resolves the incident only after fresh routing, SIP, and synthetic-call checks all pass.
- Supports the same workflow through a local CLI and a Twilio ConversationRelay phone call.

The incident data, rollback, SIP metrics, routing state, and synthetic calls are simulations stored in local JSON. VoiceOps does not connect to or modify real voice infrastructure.

## Architecture

```text
Caller -> Twilio ConversationRelay -> FastAPI bridge.py
                                      |
                                      v
                              voiceops.handle_turn()
                               |                 |
                               v                 v
                        Hermes reasoning   Deterministic Python
                                          tools/action/verification
                                                   |
                                                   v
                                      scenario.json working state
```

Hermes may request exactly six read/test tools. It cannot execute rollback or verification directly. Python validates the structured response, enforces the approval gate, performs the simulated mutation once, and controls incident resolution.

## Setup

1. Create a Python 3.13 virtual environment in `.venv`.
2. Install dependencies with `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Copy `.env.example` to the ignored local `.env` and supply the required values.

The public HTTPS and WebSocket addresses are operational routing configuration. Keep real addresses only in the local `.env`; committed examples use neutral hostnames.

Environment variables:

- `HERMES_URL`: OpenAI-compatible Hermes Chat Completions endpoint.
- `HERMES_API_KEY`: local Hermes gateway credential; leave blank only when the gateway configuration permits it.
- `TWILIO_AUTH_TOKEN`: validates Twilio webhook and WebSocket signatures.
- `PUBLIC_BASE_URL`: public HTTPS base URL used for the Twilio voice webhook.
- `PUBLIC_WS_URL`: public WSS URL used by ConversationRelay.

Never commit `.env`, credentials, tunnel configuration, live operational hostnames, phone numbers, logs, or private keys.

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

## Demo

1. Run `reset_demo.py` before each demonstration.
2. Use the CLI or call the configured Twilio number.
3. Report that Portland users cannot place outbound Teams calls and ask VoiceOps to investigate.
4. Wait for the evidence-based rollback proposal.
5. Give a clear yes or no. Only a clear approval in the same session authorizes the pending rollback.
6. On approval, VoiceOps reports the simulated rollback and the fresh three-part recovery result.

For a safe recording, show only concise filtered events for tool requests, pending action, approval, mutation, and verification. Do not display `.env`, live URLs, credentials, phone numbers, raw JSON, or private machine details.

## Limitations

- The scenario and remediation are deterministic simulations for demonstration purposes.
- Hermes, Twilio, ConversationRelay, and the public tunnel are external runtime dependencies.
- The local health endpoint does not prove that the complete external phone path is available.
- Session state is in memory and is cleared when the demo is reset or the process restarts.
