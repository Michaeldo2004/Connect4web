begin;

alter table public.games
drop constraint if exists games_difficulty_check;

alter table public.games
add constraint games_difficulty_check check (
  difficulty is null
  or difficulty in (
    'very_easy', 'easy', 'medium', 'hard',
    'multiplayer', 'fast_connect_60', 'fast_connect_30'
  )
);

drop function if exists public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text);
drop function if exists public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text, text);

create function public.claim_multiplayer_room_request(
  p_profile_id uuid,
  p_request_id text,
  p_game_id uuid,
  p_player_id uuid,
  p_owner_name text,
  p_difficulty text
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
  game_difficulty text,
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
     or char_length(p_owner_name) not between 1 and 32
     or p_difficulty not in ('multiplayer', 'fast_connect_60', 'fast_connect_30') then
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

  select *
  into request_record
  from public.multiplayer_room_requests as room_request
  where room_request.profile_id = p_profile_id
    and room_request.request_id = p_request_id;

  if not found then
    insert into public.games (id, mode, difficulty, status)
    values (p_game_id, 'multiplayer', p_difficulty, 'waiting');

    insert into public.game_players (
      game_id,
      profile_id,
      player_number,
      is_ai,
      ai_difficulty,
      display_name_snapshot
    ) values (
      p_game_id,
      p_profile_id,
      1,
      false,
      null,
      p_owner_name
    );

    insert into public.multiplayer_room_requests (
      profile_id,
      request_id,
      game_id,
      player_id,
      owner_name
    ) values (
      p_profile_id,
      p_request_id,
      p_game_id,
      p_player_id,
      p_owner_name
    )
    returning * into request_record;
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
    game_record.difficulty,
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

revoke all on function public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text, text)
from public, anon, authenticated;
grant execute on function public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text, text)
to service_role;

commit;
