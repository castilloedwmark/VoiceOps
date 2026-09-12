import asyncio
import copy
import json
import logging
import os
import re
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
SCENARIO_PATH = BASE_DIR / "scenario.json"
SEED_PATH = BASE_DIR / "scenario_seed.json"
PROMPT_PATH = BASE_DIR / "voiceops_prompt.md"
SUPPORTED_SITES = ("PDX", "SEA", "DEN")
SUPPORTED_CHANGE_ID = "CHG-1042"
HERMES_MODEL = "hermes-agent"
MAX_TOOL_ROUNDS = 6
MAX_SPOKEN_LENGTH = 600
JSON_CORRECTION = (
    "Your previous response violated the required JSON protocol. Return exactly one "
    "valid JSON object using an allowed response type and no surrounding text."
)

load_dotenv(BASE_DIR / ".env")
HERMES_URL = os.getenv(
    "HERMES_URL",
    "http://127.0.0.1:8642/v1/chat/completions",
)
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")

logger = logging.getLogger("voiceops")
sessions = {}


def _load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_state():
    return _load_json(SCENARIO_PATH)


def _save_state(state):
    descriptor, temporary_name = tempfile.mkstemp(
        dir=BASE_DIR,
        prefix=".scenario-",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.replace(temporary_name, SCENARIO_PATH)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _success(data):
    return {"ok": True, "data": data}


def _error(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def _normalize_site(site):
    if not isinstance(site, str):
        return None
    normalized = site.strip().upper()
    return normalized if normalized in SUPPORTED_SITES else None


def _normalize_change_id(change_id):
    if not isinstance(change_id, str):
        return None
    normalized = change_id.strip().upper()
    return normalized if normalized == SUPPORTED_CHANGE_ID else None


def _invalid_site(site):
    return _error(
        "INVALID_SITE",
        f"Unsupported site {site!r}. Expected one of: {', '.join(SUPPORTED_SITES)}.",
    )


def _invalid_change(change_id):
    return _error(
        "INVALID_CHANGE_ID",
        f"Unsupported change ID {change_id!r}.",
    )


def get_incident():
    state = _load_state()
    return _success(copy.deepcopy(state["incident"]))


def get_voice_health(site):
    site_code = _normalize_site(site)
    if site_code is None:
        return _invalid_site(site)

    state = _load_state()
    site_state = state["sites"][site_code]
    return _success(
        {
            "site": site_code,
            "city": site_state["city"],
            "voice_service": site_state["voice_service"],
            "inbound_pstn": site_state["inbound_pstn"],
            "outbound_pstn": site_state["outbound_pstn"],
            "sbc": copy.deepcopy(site_state["sbc"]),
            "routing": copy.deepcopy(site_state["routing"]),
        }
    )


def get_sip_metrics(site):
    site_code = _normalize_site(site)
    if site_code is None:
        return _invalid_site(site)

    state = _load_state()
    return _success(
        {
            "site": site_code,
            **copy.deepcopy(state["sites"][site_code]["sip"]),
        }
    )


def get_recent_changes(site):
    site_code = _normalize_site(site)
    if site_code is None:
        return _invalid_site(site)

    state = _load_state()
    changes = []
    for change in state["changes"].values():
        if change["site"] == site_code:
            changes.append(
                {
                    "change_id": change["change_id"],
                    "target": change["target"],
                    "site": change["site"],
                    "change_type": change["change_type"],
                    "status": change["status"],
                    "started_at": change["started_at"],
                    "completed_at": change["completed_at"],
                }
            )
    return _success({"site": site_code, "changes": changes})


def get_change_details(change_id):
    normalized = _normalize_change_id(change_id)
    if normalized is None:
        return _invalid_change(change_id)

    state = _load_state()
    return _success(copy.deepcopy(state["changes"][normalized]))


def run_synthetic_test(site):
    site_code = _normalize_site(site)
    if site_code is None:
        return _invalid_site(site)

    state = _load_state()
    state["runtime"]["synthetic_test_sequence"] += 1
    sequence = state["runtime"]["synthetic_test_sequence"]
    run_id = f"SYN-{site_code}-{sequence:04d}"
    test_result = state["synthetic_tests"][site_code]
    test_result["last_run_id"] = run_id
    _save_state(state)

    return _success(
        {
            "site": site_code,
            "run_id": run_id,
            "fresh": True,
            "result": test_result["result"],
            "sip_final_response": test_result["sip_final_response"],
            "failure_reason": test_result["failure_reason"],
        }
    )


def rollback_change(change_id):
    normalized = _normalize_change_id(change_id)
    if normalized is None:
        return _invalid_change(change_id)

    state = _load_state()
    change = state["changes"][normalized]
    if change["status"] != "DEPLOYED":
        return _error(
            "CHANGE_NOT_DEPLOYED",
            f"{normalized} is {change['status']} and cannot be rolled back again.",
        )

    site = state["sites"][change["site"]]
    before = change["before"]
    site["routing"]["active_policy"] = before["policy"]
    site["routing"]["outbound_route_group"] = before["outbound_route_group"]
    site["routing"]["enabled_peers"] = before["enabled_peers"]
    site["voice_service"] = "HEALTHY"
    site["outbound_pstn"] = "HEALTHY"
    site["sip"]["current_failure_rate_pct"] = site["sip"]["baseline_failure_rate_pct"]
    site["sip"]["dominant_failure_code"] = None
    site["sip"]["dominant_failure_reason"] = None

    synthetic = state["synthetic_tests"][change["site"]]
    synthetic["result"] = "SUCCESS"
    synthetic["sip_final_response"] = 200
    synthetic["failure_reason"] = None

    change["status"] = "ROLLED_BACK"
    state["runtime"]["rollback_complete"] = True
    state["runtime"]["rollback_count"] += 1
    _save_state(state)

    return _success(
        {
            "change_id": normalized,
            "site": change["site"],
            "status": change["status"],
            "rollback_complete": True,
            "incident_status": state["incident"]["status"],
        }
    )


def verify_recovery(site):
    site_code = _normalize_site(site)
    if site_code is None:
        return _invalid_site(site)

    state_before_test = _load_state()
    site_state = state_before_test["sites"][site_code]
    expected_routing = state_before_test["changes"][SUPPORTED_CHANGE_ID]["before"]

    routing_check = {
        "passed": (
            site_code == state_before_test["incident"]["site"]
            and site_state["routing"]["active_policy"] == expected_routing["policy"]
            and site_state["routing"]["outbound_route_group"]
            == expected_routing["outbound_route_group"]
            and site_state["routing"]["enabled_peers"] == 2
        ),
        "active_policy": site_state["routing"]["active_policy"],
        "route_group": site_state["routing"]["outbound_route_group"],
        "enabled_peers": site_state["routing"]["enabled_peers"],
    }

    sip_check = {
        "passed": (
            site_state["sip"]["current_failure_rate_pct"]
            <= site_state["sip"]["baseline_failure_rate_pct"]
        ),
        "failure_rate_pct": site_state["sip"]["current_failure_rate_pct"],
        "baseline_failure_rate_pct": site_state["sip"]["baseline_failure_rate_pct"],
    }

    synthetic_result = run_synthetic_test(site_code)
    synthetic_data = synthetic_result["data"]
    synthetic_check = {
        "passed": (
            synthetic_data["fresh"]
            and synthetic_data["result"] == "SUCCESS"
            and synthetic_data["sip_final_response"] == 200
        ),
        "run_id": synthetic_data["run_id"],
        "fresh": synthetic_data["fresh"],
        "result": synthetic_data["result"],
        "sip_final_response": synthetic_data["sip_final_response"],
    }

    state = _load_state()
    passed = (
        state["runtime"]["rollback_complete"]
        and routing_check["passed"]
        and sip_check["passed"]
        and synthetic_check["passed"]
    )
    state["runtime"]["verified"] = passed
    state["incident"]["status"] = "RESOLVED" if passed else "ACTIVE"
    _save_state(state)

    return _success(
        {
            "passed": passed,
            "site": site_code,
            "checks": {
                "routing": routing_check,
                "sip_metrics": sip_check,
                "synthetic_call": synthetic_check,
            },
            "incident_status": state["incident"]["status"],
        }
    )


def reset_demo():
    seed = _load_json(SEED_PATH)
    _save_state(seed)
    sessions.clear()
    return _success(
        {
            "scenario_id": seed["scenario_id"],
            "incident_id": seed["incident"]["incident_id"],
            "incident_status": seed["incident"]["status"],
            "rollback_complete": seed["runtime"]["rollback_complete"],
            "verified": seed["runtime"]["verified"],
        }
    )


class ProtocolError(Exception):
    pass


class MalformedJSONError(ProtocolError):
    pass


def classify_approval(user_text):
    if not isinstance(user_text, str):
        return "AMBIGUOUS"

    normalized = user_text.lower().replace("’", "'").replace("‘", "'")
    normalized = re.sub(r"[^a-z0-9']+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    rejection_patterns = (
        r"\bno\b",
        r"\bcancel\b",
        r"\bstop\b",
        r"\bdon't\b",
        r"\bdo not\b",
        r"\bnever\b",
        r"\bnot proceed\b",
    )
    if any(re.search(pattern, normalized) for pattern in rejection_patterns):
        return "REJECT"

    approval_pattern = r"^(?:yes\b|approve\b|proceed\b|go ahead\b|do it\b|roll it back\b)"
    if re.search(approval_pattern, normalized):
        return "APPROVE"
    return "AMBIGUOUS"


def _get_session(session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A non-empty session ID is required.")
    normalized = session_id.strip()
    if len(normalized) > 160:
        raise ValueError("The session ID is too long.")
    return sessions.setdefault(normalized, {"messages": [], "pending_action": None})


def _safe_spoken_response(value):
    if not isinstance(value, str):
        raise ProtocolError("spoken_response must be a string")
    spoken = value.strip().replace("\u2018", "'").replace("\u2019", "'")
    if not spoken or len(spoken) > MAX_SPOKEN_LENGTH:
        raise ProtocolError("spoken_response is empty or too long")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in spoken):
        raise ProtocolError("spoken_response contains control characters")

    sensitive_values = (
        os.getenv("HERMES_API_KEY", ""),
        os.getenv("TWILIO_AUTH_TOKEN", ""),
    )
    if any(value and len(value) >= 8 and value in spoken for value in sensitive_values):
        raise ProtocolError("spoken_response contains a protected value")
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", spoken):
        raise ProtocolError("spoken_response contains private-key material")
    return spoken


def _require_exact_keys(payload, expected):
    if set(payload) != set(expected):
        raise ProtocolError("response contains missing or unexpected fields")


def _decode_json_response(raw_response):
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise MalformedJSONError("Hermes returned an empty response")
    if len(raw_response) > 12000:
        raise MalformedJSONError("Hermes response is too long")
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as error:
        raise MalformedJSONError("Hermes returned malformed JSON") from error
    if not isinstance(payload, dict):
        raise MalformedJSONError("Hermes response must be one JSON object")
    return payload


def _validate_tool_request(payload):
    _require_exact_keys(payload, {"type", "tool", "arguments"})
    tool_name = payload["tool"]
    arguments = payload["arguments"]
    if not isinstance(tool_name, str) or not isinstance(arguments, dict):
        raise ProtocolError("tool and arguments have invalid types")

    if tool_name == "get_incident":
        if arguments:
            raise ProtocolError("get_incident takes no arguments")
        normalized_arguments = {}
    elif tool_name in {
        "get_voice_health",
        "get_sip_metrics",
        "get_recent_changes",
        "run_synthetic_test",
    }:
        if set(arguments) != {"site"}:
            raise ProtocolError("site tool arguments are invalid")
        site = _normalize_site(arguments["site"])
        if site is None:
            raise ProtocolError("site tool requested an invalid site")
        normalized_arguments = {"site": site}
    elif tool_name == "get_change_details":
        if set(arguments) != {"change_id"}:
            raise ProtocolError("change tool arguments are invalid")
        change_id = _normalize_change_id(arguments["change_id"])
        if change_id is None:
            raise ProtocolError("change tool requested an invalid change ID")
        normalized_arguments = {"change_id": change_id}
    else:
        raise ProtocolError("unknown or forbidden tool")

    return {
        "type": "tool_request",
        "tool": tool_name,
        "arguments": normalized_arguments,
    }


def _validate_action_proposal(payload):
    _require_exact_keys(payload, {"type", "action", "arguments", "spoken_response"})
    if payload["action"] != "rollback_change":
        raise ProtocolError("unsupported action proposal")
    arguments = payload["arguments"]
    if not isinstance(arguments, dict) or set(arguments) != {"change_id"}:
        raise ProtocolError("action arguments are invalid")
    change_id = _normalize_change_id(arguments["change_id"])
    if change_id is None:
        raise ProtocolError("action proposal requested an invalid change ID")

    change_result = get_change_details(change_id)
    if not change_result["ok"] or change_result["data"]["status"] != "DEPLOYED":
        raise ProtocolError("action proposal requested a non-deployed change")

    return {
        "type": "action_proposal",
        "action": "rollback_change",
        "arguments": {"change_id": change_id},
        "site": change_result["data"]["site"],
        "spoken_response": _safe_spoken_response(payload["spoken_response"]),
    }


def _validate_final_response(payload):
    _require_exact_keys(payload, {"type", "spoken_response"})
    return {
        "type": "final_response",
        "spoken_response": _safe_spoken_response(payload["spoken_response"]),
    }


def _validate_protocol_response(payload):
    response_type = payload.get("type")
    if response_type == "tool_request":
        return _validate_tool_request(payload)
    if response_type == "action_proposal":
        return _validate_action_proposal(payload)
    if response_type == "final_response":
        return _validate_final_response(payload)
    raise ProtocolError("unknown response type")


def _execute_tool(tool_name, arguments):
    if tool_name == "get_incident":
        return get_incident()
    if tool_name == "get_voice_health":
        return get_voice_health(arguments["site"])
    if tool_name == "get_sip_metrics":
        return get_sip_metrics(arguments["site"])
    if tool_name == "get_recent_changes":
        return get_recent_changes(arguments["site"])
    if tool_name == "get_change_details":
        return get_change_details(arguments["change_id"])
    if tool_name == "run_synthetic_test":
        return run_synthetic_test(arguments["site"])
    raise ProtocolError("unknown or forbidden tool")


def _messages_for_hermes(session):
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    return [{"role": "system", "content": prompt}, *copy.deepcopy(session["messages"])]


async def _call_hermes(messages):
    if not HERMES_API_KEY:
        raise RuntimeError("HERMES_API_KEY is unavailable")
    headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }
    request_body = {
        "model": HERMES_MODEL,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(HERMES_URL, headers=headers, json=request_body)
        response.raise_for_status()
        response_data = response.json()

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("Hermes response envelope is invalid") from error
    if not isinstance(content, str):
        raise RuntimeError("Hermes response content is invalid")
    return content


async def _request_json_object(session):
    raw_response = await _call_hermes(_messages_for_hermes(session))
    try:
        return _decode_json_response(raw_response), raw_response
    except MalformedJSONError:
        session["messages"].append({"role": "assistant", "content": raw_response})
        session["messages"].append({"role": "user", "content": JSON_CORRECTION})
        corrected_response = await _call_hermes(_messages_for_hermes(session))
        return _decode_json_response(corrected_response), corrected_response


def _append_final_message(session, spoken_response):
    session["messages"].append(
        {
            "role": "assistant",
            "content": json.dumps(
                {"type": "final_response", "spoken_response": spoken_response},
                separators=(",", ":"),
            ),
        }
    )


def _tool_result_message(tool_name, arguments, result):
    return (
        "VOICEOPS_TOOL_RESULT\n"
        f"tool: {tool_name}\n"
        f"arguments: {json.dumps(arguments, separators=(',', ':'))}\n"
        f"result: {json.dumps(result, separators=(',', ':'))}\n\n"
        "Continue the investigation. Return exactly one allowed JSON object."
    )


def _deterministic_action_summary(action_result, verification_result):
    if action_result.get("ok") and verification_result.get("ok"):
        verification = verification_result["data"]
        if verification["passed"]:
            return (
                "The rollback completed. Portland is back on the production route, "
                "SIP failures returned to normal, and the verification call succeeded. "
                "The incident is resolved."
            )
        return (
            "The rollback completed, but recovery did not fully verify. "
            "The incident remains active."
        )
    return "The rollback could not be completed, so no recovery was reported."


async def _summarize_action_result(session, action_result, verification_result):
    authoritative_message = (
        "VOICEOPS_ACTION_RESULT\n"
        f"action_result: {json.dumps(action_result, separators=(',', ':'))}\n"
        f"verification_result: {json.dumps(verification_result, separators=(',', ':'))}\n\n"
        "Summarize only this authoritative result. Return exactly one final_response JSON object."
    )
    session["messages"].append({"role": "user", "content": authoritative_message})
    fallback = _deterministic_action_summary(action_result, verification_result)
    try:
        payload, raw_response = await _request_json_object(session)
        validated = _validate_protocol_response(payload)
        if validated["type"] != "final_response":
            raise ProtocolError("action summary was not a final_response")
        session["messages"].append({"role": "assistant", "content": raw_response})
        return validated["spoken_response"]
    except Exception as error:
        logger.warning("[HERMES] authoritative summary fallback reason=%s", type(error).__name__)
        _append_final_message(session, fallback)
        return fallback


async def _handle_pending_action(session, user_text):
    pending_action = copy.deepcopy(session["pending_action"])
    decision = classify_approval(user_text)
    logger.info("[APPROVAL] %s", decision)

    if decision == "REJECT":
        session["pending_action"] = None
        session["messages"].append({"role": "user", "content": user_text})
        response = (
            f"Cancelled. I did not roll back {pending_action['change_id']}, "
            "and no state was changed."
        )
        _append_final_message(session, response)
        return response

    if decision == "AMBIGUOUS":
        session["messages"].append({"role": "user", "content": user_text})
        response = (
            f"I still need a clear yes or no before I can roll back "
            f"{pending_action['change_id']}."
        )
        _append_final_message(session, response)
        return response

    expected_action = {
        "action": "rollback_change",
        "change_id": SUPPORTED_CHANGE_ID,
        "site": "PDX",
    }
    session["pending_action"] = None
    session["messages"].append({"role": "user", "content": user_text})
    if pending_action != expected_action:
        logger.error("[APPROVAL] invalid pending action rejected")
        response = "The pending action was invalid and was not executed."
        _append_final_message(session, response)
        return response

    action_result = rollback_change(pending_action["change_id"])
    logger.info("[ACTION] rollback %s ok=%s", pending_action["change_id"], action_result["ok"])
    if action_result["ok"]:
        verification_result = verify_recovery(pending_action["site"])
    else:
        verification_result = _error(
            "ACTION_NOT_COMPLETED",
            "Verification was not run because rollback did not complete.",
        )

    if verification_result["ok"]:
        checks = verification_result["data"]["checks"]
        logger.info(
            "[VERIFY] routing=%s sip=%s synthetic=%s",
            checks["routing"]["passed"],
            checks["sip_metrics"]["passed"],
            checks["synthetic_call"]["passed"],
        )
    return await _summarize_action_result(session, action_result, verification_result)


async def handle_turn(session_id, user_text):
    try:
        session = _get_session(session_id)
        if not isinstance(user_text, str) or not user_text.strip():
            return "Please describe the incident or answer the pending approval question."
        caller_text = user_text.strip()
        if len(caller_text) > 4000:
            return "That request is too long. Please provide a shorter incident description."

        if session["pending_action"] is not None:
            return await _handle_pending_action(session, caller_text)

        session["messages"].append({"role": "user", "content": caller_text})
        logger.info("[USER] session=%s", session_id)
        tool_rounds = 0
        force_conclusion = False

        while True:
            payload, raw_response = await _request_json_object(session)
            response = _validate_protocol_response(payload)

            if response["type"] == "tool_request":
                if force_conclusion or tool_rounds >= MAX_TOOL_ROUNDS:
                    raise ProtocolError("Hermes exceeded the tool-round limit")
                tool_result = _execute_tool(response["tool"], response["arguments"])
                tool_rounds += 1
                logger.info(
                    "[HERMES] tool_request %s round=%s",
                    response["tool"],
                    tool_rounds,
                )
                logger.info("[TOOL] %s ok=%s", response["tool"], tool_result["ok"])
                session["messages"].append({"role": "assistant", "content": raw_response})
                session["messages"].append(
                    {
                        "role": "user",
                        "content": _tool_result_message(
                            response["tool"], response["arguments"], tool_result
                        ),
                    }
                )
                if tool_rounds == MAX_TOOL_ROUNDS:
                    session["messages"].append(
                        {
                            "role": "user",
                            "content": (
                                "The six-tool limit has been reached. Use only the evidence "
                                "already retrieved and return a concise action_proposal or "
                                "final_response. Do not request another tool."
                            ),
                        }
                    )
                    force_conclusion = True
                continue

            session["messages"].append({"role": "assistant", "content": raw_response})
            if response["type"] == "action_proposal":
                session["pending_action"] = {
                    "action": response["action"],
                    "change_id": response["arguments"]["change_id"],
                    "site": response["site"],
                }
                logger.info("[STATE] pending_action set change=%s", SUPPORTED_CHANGE_ID)
            return response["spoken_response"]

    except httpx.TimeoutException:
        logger.warning("[HERMES] request timed out")
        return "Hermes did not respond in time. Please try again."
    except httpx.ConnectError:
        logger.warning("[HERMES] connection failed")
        return "Hermes is currently unavailable. Please try again."
    except httpx.HTTPStatusError as error:
        logger.warning("[HERMES] HTTP status=%s", error.response.status_code)
        return "Hermes returned an error. Please try again."
    except (MalformedJSONError, ProtocolError):
        logger.warning("[HERMES] invalid structured response")
        return "I received an invalid investigation response. Please try again."
    except Exception as error:
        logger.error("[VOICEOPS] internal error type=%s", type(error).__name__)
        return "I hit an internal VoiceOps error. Please try again."


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("VoiceOps CLI")
    print("Type 'quit' to exit.")
    while True:
        try:
            user_text = input("\nYou: ").strip()
        except EOFError:
            print()
            break
        if user_text.lower() in {"quit", "exit"}:
            break
        response = asyncio.run(handle_turn("cli-demo", user_text))
        print(f"VoiceOps: {response}")


if __name__ == "__main__":
    main()
