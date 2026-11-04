-- Single admin session, sign-out revocation (owner decision, Nov 3, 2026). Run once in the
-- Supabase SQL editor on BOTH Preview and Production before SWJ_ADMIN_PASSWORD is set, per the
-- migration order in lib/admin_auth.py's own docstring.
--
-- The session cookie itself is a stateless, HMAC-signed, sliding-30-day token (lib/admin_auth.py)
-- -- it needs no database row to be issued or checked for expiry/tampering. This ONE table exists
-- solely so "Sign out / Forget this device" can invalidate a specific session server-side,
-- instead of only clearing a browser flag: its nonce is recorded here, and authorized() rejects
-- any token whose nonce appears in this table even if its signature and expiry are still valid.
-- A revoked row older than the session lifetime is pruned on write -- its token would already be
-- expired on its own, so there's nothing left to protect against by keeping it.
--
-- Kept as its own table, not another PRICING_DEFAULTS-style row inside `invoices`, so it never
-- needs to be added to the many invnum exclusion filters scattered across this app's invoice
-- queries, and so it can be locked down with zero anon access at all -- the browser must never
-- be able to read or clear a revocation.
create table if not exists admin_revoked_sessions (
  nonce text primary key,
  revoked_at timestamptz not null default now()
);

alter table admin_revoked_sessions enable row level security;
-- No anon policy at all: only the server (the service-role key, which bypasses RLS) ever reads
-- or writes this table.
