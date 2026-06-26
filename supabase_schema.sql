-- QueueStorm Investigator — Supabase Schema
-- Run this SQL in your Supabase project: Dashboard → SQL Editor → New Query

-- Enable UUID generation
create extension if not exists "pgcrypto";

-- Ticket analysis results table
create table if not exists ticket_analyses (
    id               uuid primary key default gen_random_uuid(),
    ticket_id        text not null,
    case_type        text,
    evidence_verdict text,
    severity         text,
    department       text,
    relevant_txn_id  text,
    human_review     boolean default false,
    confidence       float,
    reason_codes     text[],
    created_at       timestamptz default now()
);

-- Index for common query patterns
create index if not exists idx_ticket_analyses_ticket_id    on ticket_analyses(ticket_id);
create index if not exists idx_ticket_analyses_case_type    on ticket_analyses(case_type);
create index if not exists idx_ticket_analyses_severity     on ticket_analyses(severity);
create index if not exists idx_ticket_analyses_human_review on ticket_analyses(human_review);
create index if not exists idx_ticket_analyses_created_at   on ticket_analyses(created_at desc);

-- Row Level Security (RLS)
-- Enable RLS so that only authenticated service role can read/write
alter table ticket_analyses enable row level security;

-- Allow service role to do everything (for the backend)
create policy "service_role_all"
  on ticket_analyses
  for all
  using (true)
  with check (true);

-- Optional: view for human review queue
create or replace view human_review_queue as
  select
    id,
    ticket_id,
    case_type,
    evidence_verdict,
    severity,
    department,
    relevant_txn_id,
    created_at
  from ticket_analyses
  where human_review = true
  order by
    case severity
      when 'critical' then 1
      when 'high'     then 2
      when 'medium'   then 3
      when 'low'      then 4
    end,
    created_at asc;

-- Grant select on the view to anon role (read-only dashboard access)
grant select on human_review_queue to anon;
