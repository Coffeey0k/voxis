# Voxis Mobile (Flutter starter)

This directory contains the Flutter Android starter UI in `lib/main.dart`.

## Setup

Install Flutter and Android SDK, then run `flutter doctor`.

From the repository root:
```bash
flutter create --org app.voxis mobile_app
cp mobile/lib/main.dart mobile_app/lib/main.dart
cd mobile_app
flutter pub add http
flutter pub add web_socket_channel
flutter run
```

Android Emulator reaches the host computer via `http://10.0.2.2:8000`. A physical phone needs the host's LAN IP and the same network. HTTP is for local development only; use HTTPS/WSS for real deployments.

The starter does not yet implement push notifications, calls, or end-to-end encryption.
