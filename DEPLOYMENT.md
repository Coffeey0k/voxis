# Voxis API deployment (prototype)

The Android APK is currently a UI demo. The server code in `server.py` implements an API, but it must be running at a reachable address before a phone can use it. The current Flutter screen does not yet call the API.

## Quick local smoke test (CachyOS/Linux)

```bash
sudo pacman -S --needed python python-pip
git clone https://github.com/Coffeey0k/voxis.git
cd voxis
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-server.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

On the same computer, open `http://127.0.0.1:8000/health`. From another device on the same Wi-Fi, use `http://<LAN-IP-OF-COMPUTER>:8000/health`. Find the LAN address with `ip -brief address`.

Do not expose port 8000 directly to the public internet. For remote use, deploy behind HTTPS/WSS using a reverse proxy or managed platform, and configure a persistent volume for the SQLite database and uploaded files.

## API smoke test

Register a test account (password must be at least 10 characters):

```bash
curl -X POST http://127.0.0.1:8000/api/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_user","password":"change-this-password","device_id":"demo-device-01","platform":"linux"}'
```

The response includes a bearer token. Use it for protected endpoints:

```bash
curl http://127.0.0.1:8000/api/me -H 'Authorization: Bearer YOUR_TOKEN'
```

## Important security and reliability work before real users

- Use HTTPS/WSS and never transmit bearer tokens over plain internet HTTP.
- Do not commit `voxis.sqlite3`, WAL/SHM files, tokens, or user uploads. If real user data has ever been committed, remove it from repository history and rotate any exposed credentials.
- Add rate limits, account recovery, session expiry/revocation policy, upload scanning/limits, backups, and structured logging.
- Implement actual client API/WebSocket integration and automated integration tests.
- The `encrypted` message flag is metadata, not end-to-end encryption. Do not claim E2EE until client-side cryptography and key verification are implemented and audited.
- Call signaling is not a complete voice/video calling system.

## Current status

The Android screen and desktop screen are demos. The server API has registration/login, profiles, user listing, groups, message history, attachments and WebSocket notifications, but clients need to be integrated with those endpoints. An APK artifact proves only that the app built, not that end-to-end messaging works.
