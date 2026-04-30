-- Consolidated Supabase schema for Job Application Assistant.
-- Safe to run in the Supabase SQL editor more than once.

create extension if not exists pgcrypto;

create table if not exists public.generations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now(),
  job_title text,
  detected_role_type text,
  status text
);

create index if not exists generations_user_created_at_idx
  on public.generations (user_id, created_at desc);

alter table public.generations enable row level security;

drop policy if exists "generations_select_own" on public.generations;
create policy "generations_select_own"
  on public.generations
  for select
  using (auth.uid() = user_id);

drop policy if exists "generations_insert_own" on public.generations;
create policy "generations_insert_own"
  on public.generations
  for insert
  with check (auth.uid() = user_id);

create table if not exists public.job_leads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  job_hash text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  title text not null,
  company text,
  location text,
  url text,
  platform text,
  description text not null,
  score integer not null default 0,
  recommendation text not null default 'REVIEW',
  detected_role_type text,
  matched_terms jsonb not null default '[]'::jsonb,
  missing_terms jsonb not null default '[]'::jsonb,
  positive_signals jsonb not null default '[]'::jsonb,
  risk_signals jsonb not null default '[]'::jsonb,
  preference_signals jsonb not null default '[]'::jsonb,
  reasons jsonb not null default '[]'::jsonb,
  status text not null default 'shortlisted',
  generated_resume_html text,
  generated_resume_pdf text,
  generated_cover_letter text,
  generated_cover_pdf text,
  constraint job_leads_status_check check (status in ('shortlisted', 'generated', 'applied', 'rejected', 'interview')),
  constraint job_leads_recommendation_check check (recommendation in ('APPLY', 'REVIEW', 'SKIP')),
  constraint job_leads_user_hash_unique unique (user_id, job_hash)
);

alter table public.job_leads
  add column if not exists generated_resume_html text,
  add column if not exists generated_resume_pdf text,
  add column if not exists generated_cover_letter text,
  add column if not exists generated_cover_pdf text,
  add column if not exists preference_signals jsonb not null default '[]'::jsonb,
  add column if not exists reasons jsonb not null default '[]'::jsonb;

create index if not exists job_leads_user_score_idx
  on public.job_leads (user_id, score desc, created_at desc);

create index if not exists job_leads_user_status_idx
  on public.job_leads (user_id, status, created_at desc);

alter table public.job_leads enable row level security;

drop policy if exists "job_leads_select_own" on public.job_leads;
create policy "job_leads_select_own"
  on public.job_leads
  for select
  using (auth.uid() = user_id);

drop policy if exists "job_leads_insert_own" on public.job_leads;
create policy "job_leads_insert_own"
  on public.job_leads
  for insert
  with check (auth.uid() = user_id);

drop policy if exists "job_leads_update_own" on public.job_leads;
create policy "job_leads_update_own"
  on public.job_leads
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists job_leads_set_updated_at on public.job_leads;
create trigger job_leads_set_updated_at
  before update on public.job_leads
  for each row
  execute function public.set_updated_at();
