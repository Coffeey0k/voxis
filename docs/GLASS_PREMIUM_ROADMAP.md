# Voxis — Glass Premium product plan

This document is the implementation roadmap for the Android + Linux messenger. It distinguishes shipped prototype behavior from planned work; roadmap items are not claims of completed functionality.

## Product direction
- Visual language: Glass Premium — deep navy / cool porcelain surfaces, restrained violet-blue gradients, translucent-feeling layered panels, rounded corners, clear contrast.
- Theme modes: System, Light, Dark. Theme preference must persist locally.
- Platforms: Flutter Android client and Linux desktop client; shared API contracts and domain behavior.
- Accessibility: readable contrast, scalable text, keyboard navigation on desktop, reduced reliance on blur/transparency.

## Current verified baseline (repository review, 2026-10-13)
- FastAPI + SQLite with additive migrations, password hashing, bearer sessions, direct text messages/history, basic groups, profile fields, attachment endpoints, device registry, WebSocket delivery/signaling hooks.
- Android Flutter starter uses REST for registration/login, user list, direct history and send; refresh is manual.
- Linux PySide6 client exists but is not yet a full synchronized product.
- Existing limitations: no end-to-end encryption, no complete WebRTC calls, no push delivery, no production-grade deployment hardening, Android WebSocket/session persistence and media/device/group screens unfinished.

## Delivery phases

### Phase 0 — Establish a reliable baseline
- Keep database backups and additive migrations; add automated API integration tests for auth, access control, message history, groups and attachment ownership.
- Standardize error payloads, request IDs, pagination and API versioning.
- Add CI for Python tests and Android debug build; add Linux packaging smoke checks.
- Treat the current SQLite deployment as single-instance development mode until concurrency and backup/restore behavior are validated.

### Phase 1 — Shared client foundations
- Introduce API client/service layer, typed DTOs, loading/empty/error states and cancellation/timeouts.
- Persist auth credentials using platform secure storage; add logout/session revoke and device management.
- Add persisted System/Light/Dark theme preference and Glass Premium design tokens.
- Wire WebSocket events with reconnect/backoff, deduplication and REST resync; do not rely on sockets as message storage.

### Phase 2 — Complete reliable messaging
- Stable message IDs, cursor pagination, delivery/read receipts, edit/delete, replies, reactions, typing and presence.
- Search conversations/messages; drafts, pinned/muted/archive states and notification preferences.
- Add integration tests for concurrent sends, reconnect and duplicate event handling.

### Phase 3 — Groups and media
- Group creation, member/role management, invitations and membership checks on every operation.
- Upload validation, size limits, content sniffing, randomized storage names, download authorization and quotas.
- Android/Linux attachment previews and downloads; voice-message recording/playback after permissions and lifecycle tests.

### Phase 4 — Security before public deployment
- HTTPS/WSS, rate limits, account/session revocation, upload abuse protection, secret/config management, structured privacy-safe logs, backups and restore drills.
- Design E2EE around a mature audited protocol and interoperable client libraries; private keys stay on devices. Encrypt attachments client-side.
- Never call the current `encrypted` metadata flag E2EE. Require key-change/device verification UX, test vectors and independent review before making that claim.

### Phase 5 — Calls and notifications
- WebRTC audio/video with DTLS-SRTP, STUN/TURN, permission/lifecycle handling and authenticated signaling; assess an SFU only if group calls are required.
- Push notifications with revocable FCM/APNs tokens; keep message contents out of push payloads by default.
- Test background/foreground transitions and network changes on real devices.

### Phase 6 — Release readiness
- Linux package for target distro, signed/reproducible Android release build, crash diagnostics with consent, privacy policy, migration/backup guide and release checklist.
- Load, security and restore testing; no public release until high-risk issues are resolved.

## Definition of done
A feature is done only when its API authorization rules, client UX, persistence, failure/reconnect behavior and automated tests are present. A successful compile alone is not a product-level acceptance test.
