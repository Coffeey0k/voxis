# Voxis Next — development MVP for CachyOS + Android

**Verified in this environment:** Python syntax check and one automated API integration test (account creation, direct message, profile, group message, device list, migrations). **Not verified:** PySide6 desktop runtime, Flutter/Dart compilation, APK build, production deployment, E2EE, calls or push notifications. This repository is a development MVP, not a secure production messenger.

## Included now
- FastAPI API, SQLite schema migrations 1–3 and password hashing with scrypt
- PySide6 desktop UI; fixes the crash by adding `sidebar` (a QWidget) instead of `side` (a QVBoxLayout)
- Accounts, direct text messages, basic group chat, profile fields
- Avatar upload API and attachment upload/download API
- Device registry/list/revoke API
- WebSocket message delivery, presence/typing events, read receipts and a minimal call-signaling event hook
- Optional server-side Firebase Cloud Messaging push dispatch (FCM HTTP v1) with generic, privacy-preserving notification text; requires service-account configuration and client token registration
- Flutter starter Android client for login, user/group selection and text messages
- API integration test, PyInstaller desktop build script, and GitHub Actions workflow that builds an installable debug APK artifact

## Important unfinished work
- **E2EE is not implemented.** Message bodies are stored as plaintext. The `encrypted` field is only metadata.
- **Voice/video calls are not implemented.** Signaling hooks do not transmit media. A real implementation needs WebRTC, STUN/TURN, call-state handling, device permissions, and likely an SFU for group calls.
- **Push notifications are only partially integrated.** The server can dispatch FCM notifications when configured, but the mobile client still needs Firebase SDK initialization, runtime permission/token handling, and registration via `POST /api/devices`. Real delivery has not been verified without Firebase credentials.
- Android app currently has a starter UI; it does not yet include all profile/avatar/file/device-management screens.
- The desktop upload flow is present, but a polished attachment download/open UI is unfinished.
- WebSocket auth uses a token in the query string. For production, use TLS and short-lived, single-use WebSocket tickets.
- Do not expose the prototype directly to the public internet.

## CachyOS: install and run

```bash
sudo pacman -S --needed python python-pip python-virtualenv
cd ~/Загрузки/Voxis-next
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-server.txt -r requirements-client.txt
```

Terminal 1 — server:

```bash
source .venv/bin/activate
uvicorn server:app --host 127.0.0.1 --port 8000
```

Terminal 2 — desktop client:

```bash
source .venv/bin/activate
python -m voxis_client.main
```

Health endpoint: http://127.0.0.1:8000/health ; API documentation: http://127.0.0.1:8000/docs

You can also run `./scripts/run-local.sh` after installing the dependencies. To test messaging, register a second user in another client instance.

### Run tests

```bash
pip install -r requirements-test.txt
pytest -q
```

### Build a desktop bundle

```bash
pip install -r requirements-packaging.txt
./packaging/linux/build-desktop.sh
```

The executable bundle will be at `dist/Voxis/Voxis`. PyInstaller builds should be made on the Linux distribution/architecture you intend to target. This environment did not build or runtime-test the bundle.

## Android APK

This workspace has no Flutter/Dart or Android SDK installed, so I cannot honestly attach a compiled APK from this environment. The repository contains a GitHub Actions workflow that builds a **debug-installable APK** once you push the project to a GitHub repository.

1. Create an empty GitHub repository and push this folder.
2. Open **Actions → Build Voxis Android APK → Run workflow** (or push a change under `mobile/`).
3. Open the completed workflow run and download the `voxis-android-debug-apk` artifact.
4. Transfer `app-debug.apk` to the Android phone and install it. Android may ask you to allow installation from that source.

To build locally, install Flutter and Android SDK per <https://docs.flutter.dev/get-started/install>, then:

```bash
flutter doctor
flutter doctor --android-licenses
flutter create --platforms=android --org app.voxis mobile_app
cp mobile/lib/main.dart mobile_app/lib/main.dart
cd mobile_app
flutter pub add http
flutter pub add web_socket_channel
flutter run
flutter build apk --debug
```

Output: `build/app/outputs/flutter-apk/app-debug.apk`.

For the Android Emulator, use `http://10.0.2.2:8000`. A physical phone needs the computer's LAN IP and both devices on the same network; start Uvicorn with `--host 0.0.0.0` for a trusted LAN test and allow port 8000 through the local firewall. HTTP cleartext is enabled only in the generated debug manifest by the workflow. For any real deployment, configure HTTPS/WSS and do not use public HTTP.

## Upgrade from the previous Voxis version

Back up the database and uploads directory before replacing files:

```bash
cp voxis.sqlite3 "voxis-backup-$(date +%F-%H%M%S).sqlite3"
```

Keep the existing database path, or set `VOXIS_DB` to it. Set `VOXIS_UPLOAD_DIR` to a persistent directory. Startup applies additive migrations; still test against a copy of your real database first.

## LAN / VPS deployment notes

LAN development only:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Use persistent paths such as `VOXIS_DB=/var/lib/voxis/voxis.sqlite3` and `VOXIS_UPLOAD_DIR=/var/lib/voxis/uploads`. An example systemd unit is in `packaging/linux/voxis-server.service.example`; review the service user and paths before installing.

For a VPS, use a reverse proxy with valid TLS, expose only 80/443 publicly, firewall the Uvicorn port, configure backups/restore tests and monitoring, rate-limit auth and uploads, validate file contents, and rotate/revoke sessions. This prototype is not ready for public exposure.

## Security architecture plan — not yet implemented

1. Use an audited, cross-platform implementation of a mature protocol (e.g. Signal Protocol for 1:1 messaging; evaluate MLS for groups). Do not invent cryptographic primitives or claim security based on a boolean flag.
2. Generate private identity/session keys on each device; private keys must never be uploaded to the server. Store public identity keys and prekeys server-side, with device verification, rotation and revocation.
3. Encrypt message payloads on clients before API submission. Server must persist ciphertext only. Add replay protection and multi-device session handling.
4. Encrypt attachments locally with per-file keys and transmit keys only inside encrypted messages.
5. Use WebRTC with DTLS-SRTP, authenticated signaling and STUN/TURN for calls; use an SFU for scalable group calls if needed.
6. Add FCM/APNs push delivery with revocable tokens and no plaintext message bodies in notifications.
7. Require cross-platform test vectors, key-change UX, backup/recovery design, independent security review and end-to-end tests before describing Voxis as E2EE.

## Render: постоянное хранение данных

По умолчанию Voxis хранит SQLite-базу в `voxis.sqlite3` рядом с сервером, а загрузки — в каталоге `uploads/`. На хостинге с эфемерной файловой системой эти файлы могут исчезнуть при пересоздании экземпляра или деплое. Сам код не может включить постоянный диск в панели хостинга автоматически.

Для сохранения данных подключи постоянный диск в настройках сервиса Render и задай переменные окружения:

- `VOXIS_DB=/путь/на/постоянном-диске/voxis.sqlite3`
- `VOXIS_UPLOAD_DIR=/путь/на/постоянном-диске/uploads`

Укажи реальные пути из настроек подключённого диска и убедись, что приложение имеет право записи. Перед сменой путей сделай резервную копию существующей базы и каталога загрузок; переменные сами по себе не переносят уже сохранённые файлы. Если постоянный диск недоступен на выбранном тарифе, используй поддерживаемое хранилище базы и объектов вместо локальной файловой системы.

**Важно:** эти настройки защищают только при корректно подключённом постоянном хранилище. После настройки проверь перезапуском сервиса, что тестовое сообщение и вложение остаются на месте.
