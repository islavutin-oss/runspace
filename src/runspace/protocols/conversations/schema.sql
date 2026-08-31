-- Conversations — the SupabaseConversations adapter's table.
--
-- Storage is abstracted behind the `Conversations` protocol (ADR-0001);
-- Supabase is only one backend. This DDL applies ONLY when a host wires
-- the Supabase adapter. The table name is configurable — the adapter
-- defaults to `conversations_messages`.
--
-- A thread is the (tenant, party_a, party_b) triple, roles fixed. Each
-- host maps the roles to its own domain (buyer↔seller, guest↔
-- restaurant, lead↔agent).
--
-- RLS is host-specific — each product maps party_a / party_b to its own
-- auth identities — so it is NOT included here. Add RLS in the host's
-- own migration.

create table if not exists conversations_messages (
    id          bigserial primary key,
    tenant      text not null default '',
    thread_key  text not null,
    party_a     text not null,
    party_b     text not null,
    sender      text not null check (sender in ('a', 'b', 'system')),
    sender_name text,
    body        text not null,
    meta        jsonb,
    read_at     timestamptz,
    created_at  timestamptz not null default now()
);

create index if not exists conversations_messages_thread_idx
    on conversations_messages (thread_key, created_at);
create index if not exists conversations_messages_party_a_idx
    on conversations_messages (tenant, party_a);
create index if not exists conversations_messages_party_b_idx
    on conversations_messages (tenant, party_b);
