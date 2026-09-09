"""Send simulated inbound WhatsApp webhook payloads to a running concierge.

Lets you exercise the full inbound path (location pin, list reply, quick
check-in button, "atrasar 15m" text) without a real WhatsApp channel.

Usage:
    python scripts/simulate_webhook.py --url http://localhost:8000 location \
        --lat -22.9761 --lng -43.3948
    python scripts/simulate_webhook.py list-reply --id checkin_palco_sunset
    python scripts/simulate_webhook.py text --body "atrasar 15m"
    python scripts/simulate_webhook.py button --id delay_15
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

_FROM = "5521999990000"
_PHONE_ID = "SIMULATED_PHONE_ID"


def _meta_envelope(message: dict) -> dict:
    """Wrap a single Meta message object in the full webhook envelope."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": _FROM,
                                "phone_number_id": _PHONE_ID,
                            },
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def _post(url: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/webhook/whatsapp",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - local dev tool
        print(resp.status, resp.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    sub = parser.add_subparsers(dest="kind", required=True)

    p_loc = sub.add_parser("location", help="Static location pin.")
    p_loc.add_argument("--lat", type=float, required=True)
    p_loc.add_argument("--lng", type=float, required=True)

    p_list = sub.add_parser("list-reply", help="Interactive list menu selection.")
    p_list.add_argument("--id", required=True, help="Row id, e.g. checkin_palco_sunset")

    p_btn = sub.add_parser("button", help="Interactive reply button.")
    p_btn.add_argument("--id", required=True, help="Button id, e.g. delay_15")

    p_txt = sub.add_parser("text", help="Free-text message.")
    p_txt.add_argument("--body", required=True)

    args = parser.parse_args()

    if args.kind == "location":
        msg = {
            "from": _FROM,
            "id": "wamid.SIM.location",
            "type": "location",
            "location": {"latitude": args.lat, "longitude": args.lng},
        }
    elif args.kind == "list-reply":
        msg = {
            "from": _FROM,
            "id": "wamid.SIM.list",
            "type": "interactive",
            "interactive": {
                "type": "list_reply",
                "list_reply": {"id": args.id, "title": args.id},
            },
        }
    elif args.kind == "button":
        msg = {
            "from": _FROM,
            "id": "wamid.SIM.btn",
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": args.id, "title": args.id},
            },
        }
    elif args.kind == "text":
        msg = {
            "from": _FROM,
            "id": "wamid.SIM.text",
            "type": "text",
            "text": {"body": args.body},
        }
    else:  # pragma: no cover - argparse guards this
        parser.error(f"unknown kind {args.kind}")
        sys.exit(2)

    _post(args.url, _meta_envelope(msg))


if __name__ == "__main__":
    main()
