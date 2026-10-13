# Voxis: local API on CachyOS + Android

This guide runs the API on your CachyOS computer and connects the Android debug APK to it over your home Wi-Fi.

## 1. Start the API on CachyOS

Open a terminal and run:

```bash
sudo pacman -S --needed git python python-pip
git clone https://github.com/Coffeey0k/voxis.git
cd voxis
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-server.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

Keep this terminal open while using Voxis. The first startup creates/updates the SQLite database. The API should print that it is running on port 8000.

If you already cloned the repository earlier, don't clone it again. Instead run `cd /path/to/voxis`, then `git pull`, and continue from creating/activating the virtual environment.

## 2. Verify the API on the computer

In a second terminal:

```bash
curl -i http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/meta
```

The health response should contain `"ok":true`. You can also open `http://127.0.0.1:8000/docs` in a browser on the computer.

## 3. Find the computer's Wi-Fi IP

Run:

```bash
ip -brief address
```

Find the active Wi-Fi interface (often named `wlan0`, `wlp...`) and its private IPv4 address, usually something like `192.168.1.25/24`. Do not use `127.0.0.1` on a physical phone: that points to the phone itself.

If CachyOS firewall rules block local connections, allow TCP port 8000 only on your trusted home LAN using your firewall's normal configuration. Do not disable the firewall wholesale.

## 4. Build the updated Android APK from GitHub

The repository's GitHub Actions workflow starts when `mobile/**` or the Android workflow changes. Open:

https://github.com/Coffeey0k/voxis/actions/workflows/android-apk.yml

Open the newest successful **Build Voxis Android APK** run, download the `voxis-android-debug-apk` artifact, extract it, and install `app-debug.apk` on your phone. If the run is still in progress or failed, wait for/fix the run before installing. The APK is a debug build, not a Play Store release.

## 5. Connect Android to the local API

- Keep the computer and phone on the same trusted Wi-Fi network.
- Open Voxis on the phone.
- In **API server URL**, enter the computer's address, for example `http://192.168.1.25:8000` (replace the example with your actual IP).
- Tap **Register**, choose a username of 3–24 characters using letters/numbers/dot/dash/underscore, and use a password of at least 10 characters.
- To message someone, register a second account from another device (or log out and register a different username). Then select that person under **Conversation** and send a message.
- To read the latest history, tap the refresh icon. Messages are saved by the API in the server's SQLite database.

For the Android emulator running on the same computer, use `http://10.0.2.2:8000` instead of the LAN IP. A physical Android phone should use the computer's LAN IP.

## 6. Quick API test without Android (optional)

```bash
curl -X POST http://127.0.0.1:8000/api/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_user","password":"change-this-password","device_id":"demo-device-01","platform":"linux"}'
```

Copy the returned token and replace `YOUR_TOKEN` here:

```bash
curl http://127.0.0.1:8000/api/me -H 'Authorization: Bearer YOUR_TOKEN'
curl http://127.0.0.1:8000/api/users -H 'Authorization: Bearer YOUR_TOKEN'
```

Use a unique test username. The registration command returns HTTP 409 if that username already exists.

## Current capabilities and limitations

The Android app now uses the API for registration/login, user listing, direct-message history, and sending messages. Refresh users/history manually with the refresh controls. Real-time WebSocket updates, secure token persistence, groups, file attachments, push notifications, and calls are not yet wired into this Android screen.

The local server is intended for development on a trusted home network only. The Android debug build permits plain HTTP for this test setup. Do not expose port 8000 to the public internet or use real/private conversations over plain HTTP. For use outside the home, first set up HTTPS/WSS, secure deployment, backups, rate limiting, and operational monitoring.

The API's `encrypted` field is metadata, not end-to-end encryption. Do not describe Voxis as E2EE until client-side cryptography and key verification are implemented and audited. Voice/video call signaling is not a complete calling system.
