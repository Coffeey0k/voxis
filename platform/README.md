# Voxis Platform

Voxis Platform is a TypeScript monorepo foundation for a Telegram-inspired messaging service. It includes a React/Vite web client, Express + Socket.IO API, PostgreSQL schema, Redis event adapter, S3-compatible upload signing, Docker Compose, and CI checks.

## Start locally

Requirements: Docker Compose v2 and a recent Node.js 22 installation.

```sh
cd platform
cp .env.example .env
# Replace every development secret before exposing the service.
docker compose up --build
```

Open the web client at http://localhost:8080, API health at http://localhost:4000/api/v1/health/live, and MinIO console at http://localhost:9001. The initial MinIO credentials are local-development defaults; change them before use. The first boot runs `database/schema.sql` on a fresh PostgreSQL volume.

For local TypeScript development, install dependencies from this directory with `npm install`, then run `npm run dev:api` and `npm run dev:web` in separate terminals. For local processes, set `DATABASE_URL`, `REDIS_URL`, and `S3_ENDPOINT` to localhost services and configure Vite's `VITE_API_URL=http://localhost:4000/api/v1` as needed.

## Authentication

The development configuration returns a six-digit OTP in the API response. This is deliberately restricted to non-production mode. To enable SMS delivery, set `OTP_DEV_MODE=false` and configure Twilio credentials. Production mode refuses to start with development secrets or OTP development mode enabled. Never use development OTP mode on a public server.

## Implemented API surface

- `POST /api/v1/auth/request-code`, `POST /api/v1/auth/verify-code`, `POST /api/v1/auth/logout`
- `GET /api/v1/users/me`, `PATCH /api/v1/users/me`, `GET /api/v1/users/search?q=...`
- `GET /api/v1/chats`, `POST /api/v1/chats`
- `GET /api/v1/chats/:id/messages`, `POST /api/v1/chats/:id/messages`
- `PATCH /api/v1/messages/:id`, `DELETE /api/v1/messages/:id`, `POST /api/v1/messages/:id/reactions`
- `POST /api/v1/uploads/presign`, `GET /api/v1/search?q=...`
- `GET /api/v1/health/live`, `GET /api/v1/health/ready`

Socket.IO clients authenticate with `auth: { token }`. After connecting, join authorized chat rooms with `chat:join { chatId }`. Server events include `message:new`, `message:updated`, `message:deleted`, `reaction:updated`, and `typing:changed`. Redis adapter allows room broadcasts between API replicas.

## Data and security notes

- PostgreSQL stores accounts, OTP challenges, sessions, chats, members, messages, attachments, reactions, folders, contacts, blocks, stories, subscriptions, bot token records, calls, and push tokens.
- Upload URLs are short-lived S3 presigned PUT URLs. Create the bucket named by `S3_BUCKET` in MinIO before using uploads; this stack intentionally does not silently expose the bucket publicly.
- The current web client is a functional starter for phone OTP, profile loading, chat list, group/channel creation, message history and realtime text messaging. It is not yet a full Telegram-equivalent client.
- The schema reserves tables for stories, subscriptions, calls, bots, push tokens and device keys; complete API flows and provider integrations for those systems are not yet included.
- This implementation does not yet implement Signal Protocol/libsignal, verified secret chats, self-destructing messages, WebRTC call negotiation, FCM/APNs delivery, payment webhooks, channel broadcast policy, moderation tooling, or bot API endpoints. The `encrypted` message flag is metadata only and must not be represented as end-to-end encryption.
- Do not use this repository for sensitive or production communications without independent security review, backups, observability, provider setup, migrations, abuse controls, and cryptographic protocol implementation.

## Deploy on a VPS

Install Docker Engine and the Compose plugin, clone the repository, enter `platform/`, configure `.env`, and run `docker compose up -d --build`. Restrict PostgreSQL and Redis to the private Docker network, expose only HTTPS through a reverse proxy, set strong unique secrets, configure persistent volumes and backups, and provision the S3 bucket. The included Compose file is for a single-node deployment and does not provide HA or automated TLS.

## Project layout

- `apps/web` — React + TypeScript + Vite responsive web client
- `services/api` — Express API, Socket.IO, PostgreSQL, Redis and S3
- `database/schema.sql` — initial PostgreSQL schema
- `docker-compose.yml` — local stack
