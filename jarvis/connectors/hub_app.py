"""Official hub-app connector for Jarvis & Jarvis Code.

Talks ONLY through hub-app's sanctioned interfaces:
  - `node <HUB_APP_DIR>/scripts/issues.mjs --json ...` over `data/issues.sock`
    with `HUB_ISSUES_CLIENT=jarvis` or `jarvis-code`
  - `GET <HUB_APP_URL>/api/livez`

Never reads or writes hub-app's SQLite DBs, auth.json, or systemd units directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.config import Settings


@dataclass(frozen=True)
class HubResult:
    ok: bool
    exit_code: int
    data: Any = None
    message: str = ""


class HubAppClient:
    """Thin, zero-dependency wrapper around hub-app's `hub-issues` CLI and `/api/livez`."""

    def __init__(
        self,
        hub_app_dir: Path,
        hub_app_url: str = "http://127.0.0.1:3100",
        client_name: str = "jarvis",
        timeout: int = 20,
    ) -> None:
        self.hub_app_dir = Path(hub_app_dir).expanduser()
        self.hub_app_url = (hub_app_url or "http://127.0.0.1:3100").rstrip("/")
        self.client_name = client_name
        self.timeout = timeout
        self.cli_script = self.hub_app_dir / "scripts" / "issues.mjs"

    @property
    def is_available(self) -> bool:
        return self.cli_script.exists() and shutil.which("node") is not None

    def livez(self) -> tuple[bool, str]:
        """Check hub-app's public `/api/livez` endpoint."""
        url = f"{self.hub_app_url}/api/livez"
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if resp.status == 200 and body.get("ok") is True:
                    return True, "online"
                return False, f"unexpected response ({resp.status})"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            return False, f"unreachable ({exc})"

    def run_issues(self, *args: str) -> HubResult:
        """Run `hub-issues --json <args>` and interpret its documented exit codes (0..4)."""
        if not self.cli_script.exists():
            return HubResult(False, 4, message=f"hub-issues script not found at {self.cli_script}")
        if shutil.which("node") is None:
            return HubResult(False, 4, message="`node` binary not found in PATH.")

        env = os.environ.copy()
        env["HUB_ISSUES_CLIENT"] = self.client_name
        # Never inherit hub-run internal env vars from a parent process.
        for var in ("HUB_CTX", "HUB_SOCKET", "HUB_BRIDGE_SOCKET", "HUB_TOKEN", "HUB_ISSUES_NO_DEFAULTS"):
            env.pop(var, None)

        cmd = ["node", str(self.cli_script), "--json", *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return HubResult(False, 1, message="hub-issues timed out.")

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = stdout

        if proc.returncode == 0:
            return HubResult(True, 0, data=parsed)

        if proc.returncode == 2:
            approval_id = parsed.get("approvalId") if isinstance(parsed, dict) else None
            msg = f"Queued for Vivek's approval in hub-app" + (f" (approvalId: {approval_id})" if approval_id else ".")
            return HubResult(True, 2, data=parsed, message=msg)

        if proc.returncode == 3:
            detail = (parsed.get("error") if isinstance(parsed, dict) else None) or stderr or stdout
            return HubResult(False, 3, data=parsed, message=f"Refused by hub-app rule: {detail}")

        if proc.returncode == 4:
            detail = stderr or stdout or "tracker unreachable"
            return HubResult(
                False,
                4,
                data=parsed,
                message=(
                    f"hub-app tracker unreachable ({detail}). "
                    f"If not registered yet, run in ~/git/hub-app: `npm run issues:client -- add {self.client_name}`"
                ),
            )

        detail = (parsed.get("error") if isinstance(parsed, dict) else None) or stderr or stdout or f"exit {proc.returncode}"
        return HubResult(False, proc.returncode, data=parsed, message=f"hub-issues error: {detail}")

    # --- Concise formatters for CLI, Web UI, Telegram, and Voice ---

    def decisions_summary(self, project: str | None = None) -> str:
        args = ["questions"]
        if project:
            args.extend(["--project", project.upper()])
        res = self.run_issues(*args)
        if not res.ok:
            return res.message
        items = _extract_list(res.data, "questions", "items")
        if not items:
            return "No open Decisions waiting on you in hub-app."
        lines = [f"Open Decisions ({len(items)}):"]
        for q in items[:10]:
            target = q.get("issueKey") or q.get("epicKey") or q.get("ref") or q.get("id") or "?"
            text = q.get("question") or q.get("text") or ""
            lines.append(f"- [{target}] {text}")
        return "\n".join(lines)

    def qa_ready_summary(self, project: str | None = None) -> str:
        args = ["qa-ready"]
        if project:
            args.extend(["--project", project.upper()])
        res = self.run_issues(*args)
        if not res.ok:
            return res.message
        data = res.data if isinstance(res.data, dict) else {}
        ready = data.get("ready") or []
        needs = data.get("needsQa") or data.get("needs_qa") or []
        lines = [f"QA Queue — Ready for QA Passed: {len(ready)} | Needs QA: {len(needs)}"]
        for item in ready[:6]:
            key = item.get("key") or item.get("id") or "?"
            lines.append(f"  ✓ {key}: {item.get('title', '')}")
        for item in needs[:6]:
            key = item.get("key") or item.get("id") or "?"
            reason = item.get("reason") or item.get("firstReason") or ""
            lines.append(f"  · {key}: {item.get('title', '')}" + (f" ({reason})" if reason else ""))
        return "\n".join(lines)

    def dashboard_summary(self, project: str | None = None) -> str:
        args = ["dashboard"]
        if project:
            args.extend(["--project", project.upper()])
        res = self.run_issues(*args)
        if not res.ok:
            return res.message
        if isinstance(res.data, str):
            return res.data
        data = res.data if isinstance(res.data, dict) else {}
        live_ok, live_msg = self.livez()
        lines = [f"Hub-App Dashboard (livez: {'OK' if live_ok else live_msg})"]
        if "needsYou" in data and isinstance(data["needsYou"], dict):
            ny = data["needsYou"]
            lines.append(
                f"- Needs you: {ny.get('decisions', 0)} decisions, "
                f"{ny.get('approvals', 0)} approvals, {ny.get('readyForQa', 0)} ready for QA"
            )
        if "sprint" in data and isinstance(data["sprint"], dict):
            sp = data["sprint"]
            lines.append(f"- Sprint: {sp.get('name') or sp.get('number') or 'active'} ({sp.get('state', '')})")
        if len(lines) == 1:
            # Generic compact fallback for any dashboard JSON shape
            summary_keys = [f"{k}: {v}" for k, v in list(data.items())[:8] if not isinstance(v, (dict, list))]
            if summary_keys:
                lines.append("- " + " | ".join(summary_keys))
            else:
                lines.append(json.dumps(data, indent=2)[:1200])
        return "\n".join(lines)

    def issue_summary(self, key: str) -> str:
        issue_key = key.strip().upper()
        res = self.run_issues("qa", issue_key)
        if not res.ok:
            res = self.run_issues("get", issue_key, "--relations")
        if not res.ok:
            return res.message
        if isinstance(res.data, str):
            return res.data
        data = res.data if isinstance(res.data, dict) else {}
        issue = data.get("issue") if isinstance(data.get("issue"), dict) else data
        title = issue.get("title") or "(untitled)"
        status = issue.get("status") or issue.get("state") or "?"
        if isinstance(status, dict):
            status = status.get("name") or status.get("role") or "?"
        ac = data.get("acceptanceCriteria") or issue.get("acceptanceCriteria") or []
        done_ac = sum(1 for a in ac if a.get("done"))
        lines = [f"{issue_key}: {title}", f"- Status: {status} | AC: {done_ac}/{len(ac)} done"]
        for idx, a in enumerate(ac[:10], start=1):
            mark = "x" if a.get("done") else " "
            state = a.get("state") or ""
            lines.append(f"  [{mark}] {idx}. {a.get('text', '')}" + (f" ({state})" if state and state != "open" else ""))
        return "\n".join(lines)

    def search_summary(self, query: str, project: str | None = None) -> str:
        args = ["search", query, "--limit", "10"]
        if project:
            args.extend(["--project", project.upper()])
        res = self.run_issues(*args)
        if not res.ok:
            return res.message
        items = _extract_list(res.data, "issues", "items", "results")
        if not items:
            return f"No hub-app issues matching `{query}`."
        lines = [f"Hub search `{query}` ({len(items)} shown):"]
        for item in items[:10]:
            key = item.get("id") or item.get("key") or "?"
            lines.append(f"- {key}: {item.get('title', '')}")
        return "\n".join(lines)

    def lease_summary(self) -> str:
        res = self.run_issues("lease", "status")
        if not res.ok:
            return res.message
        if isinstance(res.data, str):
            return res.data
        data = res.data if isinstance(res.data, dict) else {}
        active = data.get("active") or data.get("held")
        if not active:
            return "Schedlr staging lease: Free (not currently leased)."
        holder = data.get("holder") or data.get("owner") or "unknown"
        expires = data.get("expiresAt") or data.get("expires_at") or ""
        return f"Schedlr staging lease: Held by {holder}" + (f" until {expires}" if expires else "")

    def briefing_snapshot(self) -> str:
        """Compact Hub-App section for Jarvis's morning /briefing."""
        live_ok, live_msg = self.livez()
        lines = [f"- hub-app: {'Online' if live_ok else live_msg}"]
        if not live_ok:
            return "\n".join(lines)

        q_res = self.run_issues("questions")
        if q_res.ok:
            qs = _extract_list(q_res.data, "questions", "items")
            lines.append(f"- Open Decisions: {len(qs)}")
        elif q_res.exit_code == 4:
            lines.append(f"- Tracker: client `{self.client_name}` not registered yet (`npm run issues:client -- add {self.client_name}`)")
            return "\n".join(lines)

        qa_res = self.run_issues("qa-ready")
        if qa_res.ok and isinstance(qa_res.data, dict):
            ready = qa_res.data.get("ready") or []
            needs = qa_res.data.get("needsQa") or qa_res.data.get("needs_qa") or []
            lines.append(f"- QA Queue: {len(ready)} ready for QA Passed, {len(needs)} needs QA")

        lease_res = self.run_issues("lease", "status")
        if lease_res.ok and isinstance(lease_res.data, dict):
            held = lease_res.data.get("active") or lease_res.data.get("held")
            lines.append(f"- Staging Lease: {'Held' if held else 'Free'}")

        return "\n".join(lines)

    def build_work_brief(self, key: str) -> tuple[bool, str]:
        """Fetch full issue context for Jarvis Code `/work <KEY>`."""
        issue_key = key.strip().upper()
        res = self.run_issues("qa", issue_key)
        if not res.ok:
            res = self.run_issues("get", issue_key, "--relations")
        if not res.ok:
            return False, res.message

        payload_str = json.dumps(res.data, indent=2) if not isinstance(res.data, str) else res.data
        if len(payload_str) > 8000:
            payload_str = payload_str[:8000] + "\n... (truncated)"
        brief = (
            f"Work on hub-app issue `{issue_key}`.\n"
            "Rules:\n"
            f"- Use `node {self.cli_script}` (HUB_ISSUES_CLIENT={self.client_name}) for tracker updates.\n"
            "- Never touch hub-app/data/issues.db directly and never set QA Passed (Vivek only).\n"
            "- Run local tests to verify your changes, tick verified AC (`--state verified_tests`), "
            "and report a concise Summary + Changed + Needs you.\n\n"
            f"Issue data ({issue_key}):\n```json\n{payload_str}\n```"
        )
        return True, brief


def _extract_list(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in keys:
            val = data.get(k)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def project_prefix_from_key(issue_key: str) -> str:
    """Extract project prefix from an issue key (e.g. 'UNI-42' -> 'UNI')."""
    cleaned = (issue_key or "").strip().upper()
    return cleaned.split("-", 1)[0] if "-" in cleaned else cleaned


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    settings = Settings.load()
    client = HubAppClient(settings.hub_app_dir, settings.hub_app_url, client_name="jarvis")
    if not args or args[0] == "dashboard":
        print(client.dashboard_summary(args[1] if len(args) > 1 else None))
        return 0
    sub = args[0].lower()
    if sub == "decisions":
        print(client.decisions_summary(args[1] if len(args) > 1 else None))
        return 0
    if sub == "qa":
        print(client.qa_ready_summary(args[1] if len(args) > 1 else None))
        return 0
    if sub == "issue" and len(args) >= 2:
        print(client.issue_summary(args[1]))
        return 0
    if sub == "search" and len(args) >= 2:
        print(client.search_summary(" ".join(args[1:])))
        return 0
    if sub == "lease":
        print(client.lease_summary())
        return 0
    print("Usage: python -m jarvis.connectors.hub_app [dashboard|decisions|qa|issue <KEY>|search <q>|lease]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
