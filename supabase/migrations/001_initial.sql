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

create policy "generations_select_own"
  on public.generations
  for select
  using (auth.uid() = user_id);

create policy "generations_insert_own"
  on public.generations
  for insert
  with check (auth.uid() = user_id);
