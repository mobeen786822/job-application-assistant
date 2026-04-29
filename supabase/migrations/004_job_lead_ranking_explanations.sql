alter table public.job_leads
  add column if not exists preference_signals jsonb not null default '[]'::jsonb,
  add column if not exists reasons jsonb not null default '[]'::jsonb;
