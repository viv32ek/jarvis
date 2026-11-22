"""100% free direct Home Assistant local REST API connector (zero external deps)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

from jarvis.config import Settings


class HomeAssistantClient:
    """Talks directly to Home Assistant over your local network via Long-Lived Access Token."""

    def __init__(self, base_url: str, token: str, timeout: int = 10) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.is_configured:
            raise RuntimeError(
                "Home Assistant is not configured. Set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN in /home/vivek/agy/.env."
            )
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Home Assistant HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach Home Assistant at {self.base_url}: {exc.reason}") from exc

    def list_states(self, domain: str | None = None) -> list[dict[str, Any]]:
        states = self._request("GET", "/api/states")
        if domain:
            prefix = f"{domain.strip().lower()}."
            return [s for s in states if str(s.get("entity_id", "")).startswith(prefix)]
        return states

    def get_state(self, entity_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/states/{entity_id}")

    def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None) -> Any:
        return self._request("POST", f"/api/services/{domain}/{service}", payload=data or {})

    def turn_on(self, entity_id: str) -> Any:
        domain = entity_id.split(".", 1)[0]
        return self.call_service(domain, "turn_on", {"entity_id": entity_id})

    def turn_off(self, entity_id: str) -> Any:
        domain = entity_id.split(".", 1)[0]
        return self.call_service(domain, "turn_off", {"entity_id": entity_id})

    def toggle(self, entity_id: str) -> Any:
        domain = entity_id.split(".", 1)[0]
        return self.call_service(domain, "toggle", {"entity_id": entity_id})

    def summary(self, domains: tuple[str, ...] = ("light", "switch", "climate")) -> str:
        states = self.list_states()
        lines: list[str] = []
        for dom in domains:
            matching = [s for s in states if str(s.get("entity_id", "")).startswith(f"{dom}.")]
            if not matching:
                continue
            active = [s for s in matching if s.get("state") not in ("off", "unavailable", "unknown")]
            lines.append(f"- {dom}: {len(active)}/{len(matching)} active")
            for item in active[:8]:
                name = (item.get("attributes") or {}).get("friendly_name") or item["entity_id"]
                lines.append(f"  · {name} ({item['entity_id']}): {item.get('state')}")
        return "\n".join(lines) if lines else "No matching Home Assistant entities found."


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    settings = Settings.load()
    client = HomeAssistantClient(settings.home_assistant_url, settings.home_assistant_token)
    if not args or args[0] == "status":
        print(client.summary())
        return 0
    action = args[0].lower()
    if action in ("on", "off", "toggle") and len(args) >= 2:
        entity = args[1]
        getattr(client, f"turn_{action}" if action in ("on", "off") else "toggle")(entity)
        print(f"OK: {action} {entity}")
        return 0
    if action == "state" and len(args) >= 2:
        print(json.dumps(client.get_state(args[1]), indent=2))
        return 0
    print("Usage: python -m jarvis.connectors.home_assistant [status|on <entity>|off <entity>|toggle <entity>|state <entity>]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
