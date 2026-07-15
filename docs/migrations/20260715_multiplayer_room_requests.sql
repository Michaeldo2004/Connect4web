begin;

-- Persist authenticated PvP creation idempotency without adding transient
-- client request IDs to completed-game records.
create table if not exists public.multiplayer_room_requests (
  profile_id uuid not null references public.profiles(id) on delete cascade,
  request_id text not null,
  game_id uuid not null references public.games(id) on delete cascade,
  player_id uuid not null,
  owner_name text not null,
  state text not null default 'active',
  expires_at timestamptz not null default (now() + interval '2 hours'),
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  primary key (profile_id, request_id),
  constraint multiplayer_room_requests_request_id_length
    check (char_length(request_id) between 1 and 128),
  constraint multiplayer_room_requests_owner_name_length
    check (char_length(owner_name) between 1 and 32),
  constraint multiplayer_room_requests_state_check
    check (state in ('active', 'completed', 'cancelled', 'expired', 'invalid'))
);

create unique index if not exists multiplayer_room_requests_game_id_unique
on public.multiplayer_room_requests (game_id);

alter table public.multiplayer_room_requests enable row level security;

create or replace function public.claim_multiplayer_room_request(
  p_profile_id uuid,
  p_request_id text,
  p_game_id uuid,
  p_player_id uuid,
  p_owner_name text
)
returns table (
  profile_id uuid,
  request_id text,
  game_id uuid,
  player_id uuid,
  owner_name text,
  state text,
  expires_at timestamptz,
  resolved_at timestamptz,
  game_mode text,
  game_status text,
  player_count bigint,
  owner_profile_id uuid,
  created boolean
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  request_record public.multiplayer_room_requests%rowtype;
  was_created boolean := false;
begin
  if p_profile_id is null
     or p_request_id is null
     or char_length(p_request_id) not between 1 and 128
     or p_game_id is null
     or p_player_id is null
     or p_owner_name is null
     or char_length(p_owner_name) not between 1 and 32 then
    raise exception 'invalid multiplayer room request';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(p_profile_id::text || ':' || p_request_id, 0)
  );

  update public.multiplayer_room_requests
  set state = 'expired', resolved_at = coalesce(resolved_at, now())
  where multiplayer_room_requests.profile_id = p_profile_id
    and multiplayer_room_requests.request_id = p_request_id
    and multiplayer_room_requests.state = 'active'
    and multiplayer_room_requests.expires_at <= now();

  update public.games as game_record
  set status = 'abandoned', ended_at = coalesce(game_record.ended_at, now())
  from public.multiplayer_room_requests as room_request
  where room_request.profile_id = p_profile_id
    and room_request.request_id = p_request_id
    and room_request.state = 'expired'
    and game_record.id = room_request.game_id
    and game_record.status = 'waiting';

  select * into request_record
  from public.multiplayer_room_requests as room_request
  where room_request.profile_id = p_profile_id
    and room_request.request_id = p_request_id;

  if not found then
    insert into public.games (id, mode, difficulty, status)
    values (p_game_id, 'multiplayer', 'multiplayer', 'waiting');

    insert into public.game_players (
      game_id, profile_id, player_number, is_ai, ai_difficulty, display_name_snapshot
    ) values (
      p_game_id, p_profile_id, 1, false, null, p_owner_name
    );

    insert into public.multiplayer_room_requests (
      profile_id, request_id, game_id, player_id, owner_name
    ) values (
      p_profile_id, p_request_id, p_game_id, p_player_id, p_owner_name
    ) returning * into request_record;
    was_created := true;
  end if;

  return query
  select
    room_request.profile_id,
    room_request.request_id,
    room_request.game_id,
    room_request.player_id,
    room_request.owner_name,
    room_request.state,
    room_request.expires_at,
    room_request.resolved_at,
    game_record.mode,
    game_record.status,
    (select count(*) from public.game_players as participant where participant.game_id = room_request.game_id),
    (select participant.profile_id from public.game_players as participant
      where participant.game_id = room_request.game_id
      order by participant.created_at asc limit 1),
    was_created
  from public.multiplayer_room_requests as room_request
  join public.games as game_record on game_record.id = room_request.game_id
  where room_request.profile_id = p_profile_id
    and room_request.request_id = p_request_id;
end;
$$;

create or replace function public.resolve_multiplayer_room_request(
  p_game_id uuid,
  p_state text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  changed_count int := 0;
begin
  if p_state not in ('completed', 'cancelled', 'expired', 'invalid') then
    raise exception 'invalid multiplayer room request state';
  end if;

  update public.multiplayer_room_requests
  set state = p_state, resolved_at = coalesce(resolved_at, now())
  where game_id = p_game_id and state = 'active';
  get diagnostics changed_count = row_count;

  if p_state in ('cancelled', 'expired', 'invalid') then
    update public.games
    set status = 'abandoned', ended_at = coalesce(ended_at, now())
    where id = p_game_id and status = 'waiting';
  end if;

  return changed_count > 0 or exists (
    select 1 from public.multiplayer_room_requests
    where game_id = p_game_id and state = p_state
  );
end;
$$;

-- This mapping contains server-issued player credentials. Keep it entirely
-- behind the service-role backend; authenticated browser clients receive no
-- direct table privileges or RLS policy.
revoke all on table public.multiplayer_room_requests from public, anon, authenticated;
revoke all on function public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text)
from public, anon, authenticated;
grant execute on function public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text)
to service_role;
revoke all on function public.resolve_multiplayer_room_request(uuid, text)
from public, anon, authenticated;
grant execute on function public.resolve_multiplayer_room_request(uuid, text)
to service_role;

commit;
