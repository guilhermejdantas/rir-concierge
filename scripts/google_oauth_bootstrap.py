"""One-time OAuth2 bootstrap for ``GOOGLE_AUTH_MODE=oauth``.

Run this locally, signed in as ``guilherme.dantas.sp@gmail.com``:

    python scripts/google_oauth_bootstrap.py \
        --client ./secrets/oauth_client.json \
        --token  ./secrets/oauth_token.json

It opens a browser, performs the consent flow, and writes a reusable
(refreshable) token file that the container mounts read/write.
"""
from __future__ import annotations

import argparse
import pathlib

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, help="OAuth client secret JSON path.")
    parser.add_argument("--token", required=True, help="Output token JSON path.")
    parser.add_argument("--port", type=int, default=0, help="Local redirect port.")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client, SCOPES)
    creds = flow.run_local_server(port=args.port, prompt="consent")

    out = pathlib.Path(args.token)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token written to {out.resolve()}")


if __name__ == "__main__":
    main()
