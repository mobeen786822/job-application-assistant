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
  status text not null default 'shortlisted',
  constraint job_leads_status_check check (status in ('shortlisted', 'generated', 'applied', 'rejected', 'interview')),
  constraint job_leads_recommendation_check check (recommendation in ('APPLY', 'REVIEW', 'SKIP')),
  constraint job_leads_user_hash_unique unique (user_id, job_hash)
);

create index if not exists job_leads_user_score_idx
  on public.job_leads (user_id, score desc, created_at desc);

create index if not exists job_leads_user_status_idx
  on public.job_leads (user_id, status, created_at desc);

alter table public.job_leads enable row level security;

create policy "job_leads_select_own"
  on public.job_leads
  for select
  using (auth.uid() = user_id);

create policy "job_leads_insert_own"
  on public.job_leads
  for insert
  with check (auth.uid() = user_id);

create policy "job_leads_update_own"
  on public.job_leads
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
