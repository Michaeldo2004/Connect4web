begin;

-- IMPORTANT: move analysis is derived data. Legacy rows do not contain enough
-- information to reconstruct the true worst move, and the old ratings are not
-- semantically equivalent to the new ratings. Invalidate those reviews so the
-- application recomputes truthful results instead of exposing fabricated data.
--
-- Record whether this database still has the legacy shape before changing it.
-- This makes rerunning the migration harmless to reviews created afterward.
create temporary table move_analysis_migration_state (
  needs_reanalysis boolean not null
) on commit drop;

insert into move_analysis_migration_state (needs_reanalysis)
select
  not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'move_analysis'
      and column_name = 'worst_column'
  )
  or not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'move_analysis'
      and column_name = 'worst_score'
  )
  or exists (
    select 1
    from public.move_analysis
    where rating in ('average', 'good', 'perfect')
  )
  or exists (
    select 1
    from pg_constraint constraint_record
    join pg_class table_record
      on table_record.oid = constraint_record.conrelid
    join pg_namespace schema_record
      on schema_record.oid = table_record.relnamespace
    where schema_record.nspname = 'public'
      and table_record.relname = 'move_analysis'
      and constraint_record.conname = 'move_analysis_rating_check'
      and pg_get_constraintdef(constraint_record.oid) ~ '(average|good|perfect)'
  );

alter table public.move_analysis
  add column if not exists worst_column int,
  add column if not exists worst_score int;

-- Also recover safely from a manually interrupted or partially applied change.
update move_analysis_migration_state
set needs_reanalysis = true
where exists (
  select 1
  from public.move_analysis
  where worst_column is null
     or worst_score is null
);

alter table public.move_analysis
  drop constraint if exists move_analysis_rating_check;

delete from public.move_analysis
where exists (
  select 1
  from move_analysis_migration_state
  where needs_reanalysis
);

update public.games
set
  analysis_status = 'not_requested',
  analysis_requested_at = null,
  analysis_completed_at = null,
  analysis_error = null
where exists (
  select 1
  from move_analysis_migration_state
  where needs_reanalysis
)
and (
  analysis_status <> 'not_requested'
  or analysis_requested_at is not null
  or analysis_completed_at is not null
  or analysis_error is not null
);

alter table public.move_analysis
  alter column worst_column set not null,
  alter column worst_score set not null;

-- Dropping and recreating named checks keeps this migration safe to rerun.
alter table public.move_analysis
  drop constraint if exists move_analysis_worst_column_check;

alter table public.move_analysis
  add constraint move_analysis_worst_column_check
    check (worst_column between 0 and 6);

alter table public.move_analysis
  add constraint move_analysis_rating_check
    check (rating in ('blunder', 'mistake', 'ok', 'great'));

-- Keep raw analysis rows (including the server-only rating) inaccessible to
-- public clients. The application server returns only the approved fields.
drop policy if exists "users can read their move analysis"
on public.move_analysis;

commit;
