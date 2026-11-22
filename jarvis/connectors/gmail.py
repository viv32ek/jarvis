"""100% free direct Gmail API OAuth connector (zero paid middleware)."""

from __future__ import annotations

import base64
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from jarvis.config import Settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GmailClient:
    """Direct Google Gmail API client using local OAuth credentials.json & token.json."""

    def __init__(self, credentials_path: Path, token_path: Path) -> None:
        self.credentials_path = credentials_path
        self.token_path = token_path

    @property
    def is_configured(self) -> bool:
        return self.credentials_path.exists() or self.token_path.exists()

    def _build_service(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Gmail dependencies not installed. "
                "Run: uv pip install --python .venv/bin/python -e '.[gmail]'"
            ) from exc

        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise RuntimeError(
                        f"Missing {self.credentials_path}. Download your free OAuth Desktop client JSON "
                        "from Google Cloud Console and save it as /home/vivek/agy/credentials.json."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds)

    def search(self, query: str = "is:unread", max_results: int = 10) -> list[dict[str, str]]:
        service = self._build_service()
        resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        items = resp.get("messages") or []
        results: list[dict[str, str]] = []
        for item in items:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=item["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )
            headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
            results.append(
                {
                    "id": item["id"],
                    "from": headers.get("from", "(unknown)"),
                    "subject": headers.get("subject", "(no subject)"),
                    "date": headers.get("date", ""),
                    "snippet": msg.get("snippet", ""),
                }
            )
        return results

    def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Create a Gmail draft (safe by default — does not auto-send)."""
        service = self._build_service()
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return service.users().drafts().create(userId="me", body={"message": {"raw": encoded}}).execute()

    def unread_summary(self, limit: int = 8) -> str:
        msgs = self.search(query="is:unread", max_results=limit)
        if not msgs:
            return "No unread emails."
        lines = [f"- **{m['subject']}** — {m['from']}" for m in msgs]
        return f"Unread emails ({len(msgs)} shown):\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    settings = Settings.load()
    client = GmailClient(settings.root_dir / "credentials.json", settings.root_dir / "token.json")

    if not args or args[0] == "unread":
        print(client.unread_summary())
        return 0
    if args[0] == "auth":
        client._build_service()
        print(f"Gmail OAuth complete. Saved token to {client.token_path}")
        return 0
    if args[0] == "search" and len(args) >= 2:
        query = " ".join(args[1:])
        for m in client.search(query=query):
            print(f"- {m['subject']} | {m['from']} | {m['date']}")
        return 0
    print("Usage: python -m jarvis.connectors.gmail [auth|unread|search <query>]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
