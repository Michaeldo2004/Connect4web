create extension if not exists pgcrypto;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text not null unique,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint profiles_username_length check (char_length(username) between 3 and 32),
  constraint profiles_username_format check (username ~ '^[A-Za-z0-9_]+$')
);

create table public.games (
  id uuid primary key default gen_random_uuid(),
  mode text not null,
  difficulty text,
  status text not null default 'playing',
  winner_player_number int,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  final_board jsonb,
  analysis_status text not null default 'not_requested',
  analysis_requested_at timestamptz,
  analysis_completed_at timestamptz,
  analysis_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint games_mode_check check (mode in ('ai', 'multiplayer')),
  constraint games_difficulty_check check (
    difficulty is null
    or difficulty in ('very_easy', 'easy', 'medium', 'hard', 'multiplayer')
  ),
  constraint games_status_check check (
    status in (
      'waiting',
      'playing',
      'human_win',
      'ai_win',
      'player1_win',
      'player2_win',
      'draw',
      'abandoned'
    )
  ),
  constraint games_winner_player_number_check check (
    winner_player_number is null
    or winner_player_number in (1, 2)
  ),
  constraint games_analysis_status_check check (
    analysis_status in ('not_requested', 'processing', 'complete', 'failed')
  )
);

-- Server-only idempotency records for authenticated PvP room creation. This
-- keeps transient creation identity separate from completed game history.
create table public.multiplayer_room_requests (
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

create table public.game_players (
  id uuid primary key default gen_random_uuid(),
  game_id uuid not null references public.games(id) on delete cascade,
  profile_id uuid references public.profiles(id) on delete set null,
  player_number int not null,
  is_ai boolean not null default false,
  ai_difficulty text,
  display_name_snapshot text,
  created_at timestamptz not null default now(),
  constraint game_players_player_number_check check (player_number in (1, 2)),
  constraint game_players_ai_difficulty_check check (
    ai_difficulty is null
    or ai_difficulty in ('very_easy', 'easy', 'medium', 'hard')
  ),
  constraint game_players_unique_number unique (game_id, player_number)
);

create table public.game_moves (
  id bigint generated always as identity primary key,
  game_id uuid not null references public.games(id) on delete cascade,
  move_number int not null,
  player_number int not null,
  profile_id uuid references public.profiles(id) on delete set null,
  is_ai_move boolean not null default false,
  column_played int not null,
  board_before jsonb not null,
  board_after jsonb not null,
  created_at timestamptz not null default now(),
  constraint game_moves_move_number_check check (move_number > 0),
  constraint game_moves_player_number_check check (player_number in (1, 2)),
  constraint game_moves_column_played_check check (column_played between 0 and 6),
  constraint game_moves_unique_id_game unique (id, game_id),
  constraint game_moves_unique_move_number unique (game_id, move_number)
);

create table public.move_analysis (
  id bigint generated always as identity primary key,
  move_id bigint not null,
  game_id uuid not null references public.games(id) on delete cascade,
  minimax_depth int not null,
  played_column int not null,
  best_column int not null,
  worst_column int not null,
  played_score int not null,
  best_score int not null,
  worst_score int not null,
  score_loss int generated always as (best_score - played_score) stored,
  rating text not null,
  created_at timestamptz not null default now(),
  constraint move_analysis_depth_check check (minimax_depth > 0),
  constraint move_analysis_played_column_check check (played_column between 0 and 6),
  constraint move_analysis_best_column_check check (best_column between 0 and 6),
  constraint move_analysis_worst_column_check check (worst_column between 0 and 6),
  constraint move_analysis_rating_check check (rating in ('blunder', 'mistake', 'ok', 'great')),
  constraint move_analysis_move_game_fk foreign key (move_id, game_id)
    references public.game_moves(id, game_id) on delete cascade,
  constraint move_analysis_unique_depth unique (move_id, minimax_depth)
);

create table public.player_stats (
  profile_id uuid primary key references public.profiles(id) on delete cascade,
  games_played int not null default 0,
  wins int not null default 0,
  losses int not null default 0,
  draws int not null default 0,
  ai_games_played int not null default 0,
  ai_wins int not null default 0,
  multiplayer_games_played int not null default 0,
  multiplayer_wins int not null default 0,
  updated_at timestamptz not null default now(),
  constraint player_stats_nonnegative_check check (
    games_played >= 0
    and wins >= 0
    and losses >= 0
    and draws >= 0
    and ai_games_played >= 0
    and ai_wins >= 0
    and multiplayer_games_played >= 0
    and multiplayer_wins >= 0
  )
);

create index profiles_username_idx on public.profiles (username);
create index games_started_at_idx on public.games (started_at desc);
create index games_analysis_status_idx on public.games (analysis_status);
create unique index multiplayer_room_requests_game_id_unique
on public.multiplayer_room_requests (game_id);
create index game_players_profile_id_idx on public.game_players (profile_id);
create index game_players_game_id_idx on public.game_players (game_id);
create unique index game_players_unique_profile_per_game
on public.game_players (game_id, profile_id)
where profile_id is not null;
create index game_moves_game_id_move_number_idx on public.game_moves (game_id, move_number);
create index move_analysis_game_id_idx on public.move_analysis (game_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create trigger games_set_updated_at
before update on public.games
for each row execute function public.set_updated_at();

create trigger player_stats_set_updated_at
before update on public.player_stats
for each row execute function public.set_updated_at();

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

  select *
  into request_record
  from public.multiplayer_room_requests as room_request
  where room_request.profile_id = p_profile_id
    and room_request.request_id = p_request_id;

  if not found then
    insert into public.games (id, mode, difficulty, status)
    values (p_game_id, 'multiplayer', 'multiplayer', 'waiting');

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
  where game_id = p_game_id
    and state = 'active';
  get diagnostics changed_count = row_count;

  if p_state in ('cancelled', 'expired', 'invalid') then
    update public.games
    set status = 'abandoned', ended_at = coalesce(ended_at, now())
    where id = p_game_id
      and status = 'waiting';
  end if;

  return changed_count > 0 or exists (
    select 1 from public.multiplayer_room_requests
    where game_id = p_game_id and state = p_state
  );
end;
$$;

revoke all on function public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text)
from public, anon, authenticated;
grant execute on function public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text)
to service_role;
revoke all on function public.resolve_multiplayer_room_request(uuid, text)
from public, anon, authenticated;
grant execute on function public.resolve_multiplayer_room_request(uuid, text)
to service_role;

-- Auth users and application profiles must remain one-to-one. Preserve a
-- valid requested username, but fall back to the user's UUID when metadata is
-- malformed or the requested username is already taken.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  preferred_username text;
begin
  preferred_username := left(
    regexp_replace(
      coalesce(
        nullif(new.raw_user_meta_data ->> 'username', ''),
        split_part(coalesce(new.email, ''), '@', 1),
        'Player'
      ),
      '[^A-Za-z0-9_]',
      '',
      'g'
    ),
    32
  );

  if char_length(preferred_username) < 3 then
    preferred_username := replace(new.id::text, '-', '');
  end if;

  begin
    insert into public.profiles (id, username, display_name)
    values (
      new.id,
      preferred_username,
      coalesce(nullif(new.raw_user_meta_data ->> 'username', ''), preferred_username)
    )
    on conflict (id) do nothing;
  exception when unique_violation then
    insert into public.profiles (id, username, display_name)
    values (
      new.id,
      replace(new.id::text, '-', ''),
      coalesce(nullif(new.raw_user_meta_data ->> 'username', ''), 'Player')
    )
    on conflict (id) do nothing;
  end;

  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

create or replace function public.is_game_participant(target_game_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.game_players
    where game_players.game_id = target_game_id
      and game_players.profile_id = auth.uid()
  );
$$;

alter table public.profiles enable row level security;
alter table public.games enable row level security;
alter table public.multiplayer_room_requests enable row level security;
alter table public.game_players enable row level security;
alter table public.game_moves enable row level security;
alter table public.move_analysis enable row level security;
alter table public.player_stats enable row level security;

-- No client policy is defined for multiplayer_room_requests. The service-role
-- backend derives profile_id from the verified access token and is the only
-- component allowed to read or write idempotency mappings.
revoke all on table public.multiplayer_room_requests from public, anon, authenticated;

create policy "profiles are readable by authenticated users"
on public.profiles
for select
to authenticated
using (true);

create policy "users can insert their own profile"
on public.profiles
for insert
to authenticated
with check (id = auth.uid());

create policy "users can update their own profile"
on public.profiles
for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

create policy "users can read their games"
on public.games
for select
to authenticated
using (public.is_game_participant(id));

create policy "users can read their game players"
on public.game_players
for select
to authenticated
using (public.is_game_participant(game_id));

create policy "users can read their game moves"
on public.game_moves
for select
to authenticated
using (public.is_game_participant(game_id));

create policy "users can read their own stats"
on public.player_stats
for select
to authenticated
using (profile_id = auth.uid());
