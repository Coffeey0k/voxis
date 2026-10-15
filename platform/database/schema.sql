CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), phone_e164 text UNIQUE NOT NULL,
 username text UNIQUE, display_name text NOT NULL DEFAULT '', bio text NOT NULL DEFAULT '',
 avatar_key text, last_seen_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth_challenges (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), phone_e164 text NOT NULL, code_hash text NOT NULL,
 expires_at timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8),
 consumed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auth_challenges_phone_idx ON auth_challenges(phone_e164,created_at DESC);
CREATE TABLE IF NOT EXISTS sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash text NOT NULL UNIQUE, device_name text NOT NULL DEFAULT 'Web', expires_at timestamptz NOT NULL,
 revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS devices (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 device_label text NOT NULL DEFAULT 'Unknown device', identity_public_key text, signed_prekey text,
 created_at timestamptz NOT NULL DEFAULT now(), last_active_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chats (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text NOT NULL CHECK(kind IN ('direct','group','channel','secret')),
 title text NOT NULL DEFAULT '', owner_id uuid REFERENCES users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chat_members (
 chat_id uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 role text NOT NULL DEFAULT 'member' CHECK(role IN ('owner','admin','member')), joined_at timestamptz NOT NULL DEFAULT now(),
 muted_until timestamptz, PRIMARY KEY(chat_id,user_id)
);
CREATE INDEX IF NOT EXISTS chat_members_user_idx ON chat_members(user_id,joined_at DESC);
CREATE TABLE IF NOT EXISTS messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), chat_id uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
 sender_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, body text NOT NULL DEFAULT '',
 encrypted boolean NOT NULL DEFAULT false, reply_to_id uuid REFERENCES messages(id) ON DELETE SET NULL,
 forwarded_from_id uuid REFERENCES messages(id) ON DELETE SET NULL, edited_at timestamptz, deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), CHECK(length(body)<=50000)
);
CREATE INDEX IF NOT EXISTS messages_chat_created_idx ON messages(chat_id,created_at DESC) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS attachments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), chat_id uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
 uploader_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, object_key text NOT NULL UNIQUE,
 file_name text NOT NULL, content_type text NOT NULL, size_bytes bigint NOT NULL CHECK(size_bytes>=0),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS message_reactions (
 message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 emoji text NOT NULL CHECK(length(emoji)<=32), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(message_id,user_id,emoji)
);
CREATE TABLE IF NOT EXISTS chat_folders (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name text NOT NULL, chat_ids uuid[] NOT NULL DEFAULT '{}', position integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS contacts (
 user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, contact_user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,contact_user_id), CHECK(user_id<>contact_user_id)
);
CREATE TABLE IF NOT EXISTS blocks (
 blocker_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, blocked_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(blocker_id,blocked_id), CHECK(blocker_id<>blocked_id)
);
CREATE TABLE IF NOT EXISTS stories (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 attachment_key text, caption text NOT NULL DEFAULT '', expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS story_views (
 story_id uuid NOT NULL REFERENCES stories(id) ON DELETE CASCADE, viewer_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 viewed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(story_id,viewer_id)
);
CREATE TABLE IF NOT EXISTS subscriptions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 provider text NOT NULL, provider_subscription_id text, plan text NOT NULL,
 status text NOT NULL CHECK(status IN ('trialing','active','past_due','canceled','expired')),
 current_period_end timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bot_tokens (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name text NOT NULL, token_hash text NOT NULL UNIQUE, revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS calls (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), chat_id uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
 initiator_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE, kind text NOT NULL CHECK(kind IN ('audio','video')),
 status text NOT NULL DEFAULT 'ringing' CHECK(status IN ('ringing','active','ended','missed')),
 started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz
);
CREATE TABLE IF NOT EXISTS push_tokens (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 platform text NOT NULL CHECK(platform IN ('web','ios','android')), token text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now()
);