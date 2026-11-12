"""Pair an insole with your OsteoCare account and print its device key.

The website has no "add insole" button yet, so run this once per insole:

    (Easier: open https://<your-backend>/manage in a browser.)

    python scripts/register_device.py --api https://your-backend.onrender.com \
        --supabase https://your-project.supabase.co --anon YOUR_ANON_KEY \
        --email you@example.com --name "My insole"

Use the same email and password you use to sign in on the website.
"""
import argparse
import getpass
import sys

import httpx


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True, help="Backend URL")
    p.add_argument("--supabase", required=True, help="Supabase project URL")
    p.add_argument("--anon", required=True, help="Supabase anon / publishable key")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="My insole")
    p.add_argument("--board", choices=["esp8266", "esp32"], default="esp8266")
    a = p.parse_args()

    password = getpass.getpass("Website password: ")
    r = httpx.post(
        f"{a.supabase.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": a.anon},
        json={"email": a.email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        sys.exit(f"Sign-in failed: {r.text}")
    token = r.json()["access_token"]

    r = httpx.post(
        f"{a.api.rstrip('/')}/api/devices",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": a.name, "board": a.board},
        timeout=15,
    )
    if r.status_code != 201:
        sys.exit(f"Could not pair the insole: {r.status_code} {r.text}")
    d = r.json()
    print(f"\nPaired '{d['name']}' with id {d['id']}")
    print(f"Device key (shown once, put it in the firmware's DEVICE_KEY):\n\n  {d['device_key']}\n")


if __name__ == "__main__":
    main()
