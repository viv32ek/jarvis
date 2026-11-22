#!/usr/bin/env python3
"""Programmatic deployment guardrail for Jarvis Code Mode + Antigravity PreToolUse hook."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

# Patterns that indicate a staging/production deployment or release action.
DEPLOY_COMMAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "deployment CLI/script",
        re.compile(
            r"(?:^|[;&|]\s*)(?:\./)?deploy(?:\.sh|\.py)?\b"
            r"|\b(deploy|redeploy|release)\b.*\b(stag(e|ing)|prod(uction)?|live)\b"
            r"|\b(stag(e|ing)|prod(uction)?)\b.*\b(deploy|redeploy|release)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "git push to protected/release branch",
        re.compile(
            r"\bgit\s+push\b.*\b(stag(e|ing)|prod(uction)?|main|master|release)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Kubernetes / Helm rollout",
        re.compile(
            r"\b(kubectl\s+(apply|rollout|delete|patch|scale)|helm\s+(upgrade|install|rollback))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Terraform / Ansible infrastructure apply",
        re.compile(
            r"\b(terraform\s+(apply|destroy)|ansible-playbook)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Cloud / PaaS deploy",
        re.compile(
            r"\b(fly(ctl)?\s+deploy|vercel\s+--prod|netlify\s+deploy\s+--prod|gcloud\s+.*\bdeploy\b|aws\s+.*\bdeploy\b)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Docker staging/prod rollout",
        re.compile(
            r"\b(docker\s+stack\s+deploy|docker[\s-]+compose\b.*\b(stag(e|ing)|prod(uction)?)\b.*\bup\b)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Direct AWS ECS staging scaling (use hub-issues lease instead)",
        re.compile(r"\baws\s+ecs\s+update-service\b", re.IGNORECASE),
    ),
    (
        "Direct hub-app database/socket/auth access (use hub-issues CLI instead)",
        re.compile(
            r"hub-app/data/(issues\.db|hub\.db|auth\.json|issue-clients\.json|issues\.sock)",
            re.IGNORECASE,
        ),
    ),
]

DEPLOY_INTENT_PATTERN = re.compile(
    r"\b(deploy|push|release|rollout|roll\s+out|ship)\b.*\b(stag(e|ing)|prod(uction)?)\b"
    r"|\b(stag(e|ing)|prod(uction)?)\b.*\b(deploy|deployment|release|rollout)\b",
    re.IGNORECASE,
)


def check_command(command: str) -> tuple[bool, str | None]:
    """Return (blocked, reason) for a shell command string."""
    cmd = (command or "").strip()
    if not cmd:
        return False, None
    for label, pattern in DEPLOY_COMMAND_PATTERNS:
        if pattern.search(cmd):
            return (
                True,
                f"DEPLOYMENT GUARDRAIL: Blocked {label} command (`{cmd}`). "
                "Staging and production deployments always require explicit user permission.",
            )
    return False, None


def is_deploy_intent(user_text: str) -> bool:
    """Return True if the user message appears to request a staging/prod deployment."""
    return bool(DEPLOY_INTENT_PATTERN.search(user_text or ""))


def evaluate_hook_payload(payload: dict[str, Any], approved: bool = False) -> dict[str, Any]:
    """Evaluate an Antigravity PreToolUse hook payload and return the hook decision JSON."""
    if approved:
        return {"decision": "allow"}

    tool_call = payload.get("toolCall") or {}
    args = tool_call.get("args") or {}
    command = str(args.get("CommandLine") or args.get("command") or "")
    blocked, reason = check_command(command)
    if blocked:
        return {"decision": "deny", "reason": reason}
    return {"decision": "allow"}


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"decision": "allow"}))
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "allow"}))
        return 0

    approved = os.environ.get("JARVIS_DEPLOY_APPROVED") == "1"
    result = evaluate_hook_payload(payload, approved=approved)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
