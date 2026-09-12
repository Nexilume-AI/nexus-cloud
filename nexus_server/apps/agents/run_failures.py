"""Caller-safe failure presentation. Codes are evidence; prose is not a classifier.

No raw exception, endpoint, credential or remote action enters recovery metadata.
Actions are UI intents only, never permission grants or automatic task retries.
"""
import re


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) else ""


GROUPS = {
    "agent": {
        "AGENT_RUNTIME_UNAVAILABLE", "AGENT_RUNTIME_CONNECTION_FAILED", "OPENWRT_CONNECTION_LOST",
        "CONTROL_PLANE_UNAVAILABLE", "HANDLER_FAILED", "STREAM_HANDLER_FAILED",
        "AGENT_RUNTIME_NOT_READY", "AGENT_RUNTIME_STREAMING_UNSUPPORTED", "OPENWRT_IPV6_REQUIRED",
        "AGENT_STREAMING_NOT_SUPPORTED", "RUNTIME_UNAVAILABLE", "RUNTIME_NOT_READY",
        "AGENT_RUNTIME_NOT_AVAILABLE", "AGENT_RUNTIME_ERROR", "INTERACTIVE_TRANSPORT_REQUIRED",
        "CANCEL_UNCONFIRMED",
    },
    "cloud": {
        "RUN_CONTEXT_EXCHANGE_FAILED", "RUN_CONTEXT_TRUST_UNAVAILABLE", "RUN_CONTEXT_TLS_FAILED",
        "RUN_CONTEXT_ORIGIN_MISMATCH",
        "WORKER_LOST_OUTCOME_UNKNOWN", "TASK_EXECUTION_FAILED", "TASK_DEADLINE_EXCEEDED",
        "TASK_AUTHORITY_UNAVAILABLE", "LEGACY_TASK_EXPIRED", "CHECKPOINT_UNAVAILABLE",
        "MEMORY_UNAVAILABLE", "INTERACTION_UNAVAILABLE", "BILLING_REPORT_UNAVAILABLE",
        "RUN_DELEGATE_UNAVAILABLE",
    },
    "computer": {
        "COMPUTER_REQUIRED", "COMPUTER_PERMISSION_REQUIRED", "COMPUTER_UNAVAILABLE",
        "COMPUTER_RUNTIME_OFFLINE", "COMPUTER_CAPABILITY_UNAVAILABLE", "WORKSPACE_UNAVAILABLE",
        "WORKSPACE_PERMISSION_REQUIRED", "WORKSPACE_SCOPE_DENIED", "WORKSPACE_PATH_INVALID",
        "WORKSPACE_PATH_FORBIDDEN", "TERMINAL_UNAVAILABLE", "LEGACY_SSH_DISABLED",
        "COMPUTER_RUNTIME_REVOKED",
    },
    "browser": {
        "BROWSER_COMPUTER_REQUIRED", "BROWSER_PERMISSION_REQUIRED", "BROWSER_UNAVAILABLE",
        "BROWSER_TUNNEL_UNAVAILABLE", "BROWSER_SESSION_LOST", "BROWSER_ACTION_FAILED",
        "BROWSER_STALE_OBSERVATION", "BROWSER_BUSY",
    },
    "business": {"BUSINESS_RULE_FAILED", "INVALID_TOOL_ARGUMENTS", "VALIDATION_ERROR"},
    # Mobile remains a distinct dependency rather than being mislabeled Computer.
    "mobile": {
        "MOBILE_REQUIRED", "MOBILE_PERMISSION_REQUIRED", "MOBILE_UNAVAILABLE", "MOBILE_BUSY",
        "MOBILE_ACTION_FAILED", "MOBILE_ACTION_REJECTED", "MOBILE_ACTION_CANCELLED",
    },
}
PRESENTATION = {
    "agent": ("Agent unavailable", "The Agent runtime could not complete this turn.",
        "Check Agent availability. If it stays unavailable, share the failure code and Run ID with its developer.",
        ["check_status", "continue_chat"]),
    "cloud": ("Cloud service interruption", "Cloud could not complete the Run service operation.",
        "Check status again. If the issue persists, share the failure code and Run ID with the Cloud operator.",
        ["check_status"]),
    "computer": ("Computer needs attention", "The attached Computer could not provide the required operation.",
        "Start Nexus Computer Runtime on that device and check its permissions, or choose another Computer.",
        ["manage_computer", "check_status"]),
    "browser": ("Browser needs attention", "The isolated browser on the attached Computer could not complete the action.",
        "Inspect the last Browser frame and revise the instruction. The Agent host browser is not used as a fallback.",
        ["view_browser", "continue_chat"]),
    "business": ("Task could not be completed", "The tool reported an unsuccessful task result.",
        "Review the Agent response and your input, then send a corrected instruction. Reconnecting a device may not help.",
        ["continue_chat"]),
    "mobile": ("Mobile needs attention", "The attached Mobile could not provide the required operation.",
        "Check the phone's connection and approved permissions, or choose another Mobile.",
        ["manage_mobile", "check_status"]),
    "unknown": ("Failure source not confirmed", "This turn failed without a recognized cause code.",
        "Inspect existing results before continuing. Share the failure code and Run ID if assistance is needed.",
        ["check_status", "continue_chat"]),
}


def describe_failure(code):
    code = safe_code(code) or "UNKNOWN_FAILURE"
    domain = next((name for name, codes in GROUPS.items() if code in codes), "unknown")
    title, message, hint, actions = PRESENTATION[domain]
    if code in {"HANDLER_FAILED", "STREAM_HANDLER_FAILED"}:
        title, message = "Agent handler failed", "The Agent's implementation raised an error."
    elif code == "CANCEL_UNCONFIRMED":
        title, message = "Agent cancellation unconfirmed", "Nexus access was revoked, but the Agent has not acknowledged stopping."
        hint = "Check external work before sending another instruction. Ask the Agent developer to verify it stopped."
        actions = ["check_status"]
    elif code == "TASK_DEADLINE_EXCEEDED":
        title, message = "Run deadline exceeded", "Cloud stopped waiting because this turn exceeded its execution deadline."
        hint = "Inspect existing outputs and external work, then use a smaller task or ask the operator to review the execution limit."
    elif code == "WORKER_LOST_OUTCOME_UNKNOWN":
        title, message = "Cloud worker disconnected", "The worker lost its execution lease; the external outcome could not be confirmed."
        hint = "Check existing results and ask the Cloud operator to restore the worker. This task was not automatically replayed."
    elif code in {"OPENWRT_CONNECTION_LOST", "AGENT_RUNTIME_CONNECTION_FAILED"}:
        message = "The Agent connection was lost."
    elif code in {"RUN_CONTEXT_TRUST_UNAVAILABLE", "RUN_CONTEXT_TLS_FAILED"}:
        title = "Cloud trust could not be verified"
        hint = "Ask the operator to repair Cloud trust delivery or certificates. Do not disable TLS verification."
    elif code == "RUN_CONTEXT_ORIGIN_MISMATCH":
        title = "Cloud callback configuration mismatch"
        hint = "Ask the operator to align the Agent's managed Cloud trust and callback addresses. Do not bypass origin checks."
    elif code == "RUN_CONTEXT_EXCHANGE_FAILED":
        hint = "Ask the operator to check Cloud reachability and Run Context exchange. Do not reuse the one-time exchange token."
    elif code == "COMPUTER_RUNTIME_OFFLINE":
        title, message = "Computer is offline", "Nexus Computer Runtime is not connected to Cloud."
    elif code == "BROWSER_SESSION_LOST":
        title, message = "Browser session ended", "The previous isolated Browser session is no longer available."
        hint = "Inspect the saved frame, then start a new Run for a fresh browser. Previous page state and cookies cannot be restored."
        actions = ["view_browser", "new_run"]
    elif code in {"BROWSER_COMPUTER_REQUIRED", "BROWSER_PERMISSION_REQUIRED", "BROWSER_UNAVAILABLE", "BROWSER_TUNNEL_UNAVAILABLE"}:
        hint = "Check the attached Computer's browser availability and browser.control permission. Install a supported browser or change Computer."
        actions = ["manage_computer", "check_status"]
    elif code == "MOBILE_ACTION_REJECTED":
        title, message = "Mobile action declined", "The requested action was not approved on the attached Mobile."
        hint = "Review the requested action and send a revised instruction. The Mobile connection is still available."
        actions = ["continue_chat"]
    elif code == "MOBILE_ACTION_CANCELLED":
        title, message = "Mobile action cancelled", "The requested Mobile action ended before completion."
        hint = "Check the current phone state, then send the instruction again if the action is still needed."
        actions = ["check_status", "continue_chat"]
    # Transport errors and generic handler failures do not prove that external
    # side effects failed. Never label a replay safe based only on this code.
    unknown_outcome = domain not in {"business"} and code not in {
        "BROWSER_COMPUTER_REQUIRED", "BROWSER_PERMISSION_REQUIRED", "COMPUTER_REQUIRED",
        "COMPUTER_PERMISSION_REQUIRED", "MOBILE_REQUIRED", "MOBILE_PERMISSION_REQUIRED",
        "MOBILE_ACTION_REJECTED",
    }
    return {"domain": domain, "code": code, "title": title, "message": message,
        "recovery_hint": hint, "actions": actions, "outcome_unknown": unknown_outcome,
        "automatic_retry": False}


def failure_payload(run):
    # Historic events/results must not overwrite a recovered current turn.
    if getattr(run, "status", "") != "failed":
        return None
    task = getattr(run, "execution_task", None)
    code = getattr(task, "error_code", "")
    if code in {"", "MCP_TASK_FAILED", "UNKNOWN_FAILURE"}:
        # Recover explicit codes retained in legacy structured error envelopes.
        # Never infer a cause from an arbitrary message or a historical event.
        result = getattr(task, "result_json", None)
        if isinstance(result, dict):
            data = result.get("data")
            data = data if isinstance(data, dict) else {}
            gateway = data.get("gatewayBody")
            gateway = gateway if isinstance(gateway, dict) else {}
            code = safe_code(gateway.get("code")) or safe_code(data.get("code")) or safe_code(result.get("code")) or code
    return describe_failure(code)
