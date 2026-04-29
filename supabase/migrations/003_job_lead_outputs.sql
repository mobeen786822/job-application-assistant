alter table public.job_leads
  add column if not exists generated_resume_html text,
  add column if not exists generated_resume_pdf text,
  add column if not exists generated_cover_letter text,
  add column if not exists generated_cover_pdf text;
