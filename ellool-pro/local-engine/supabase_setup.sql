-- ELLOOL optional media archive. Apply once to the ELLOOL Supabase project.
create table if not exists public.generations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  kind text not null check (kind in ('image','video')),
  prompt text not null,
  final_prompt text,
  file_path text not null,
  created_at timestamptz not null default now()
);

alter table public.generations enable row level security;

drop policy if exists "users read own generations" on public.generations;
create policy "users read own generations"
  on public.generations for select
  using (auth.uid() = user_id);

insert into storage.buckets (id, name, public)
values ('media', 'media', false)
on conflict (id) do nothing;
