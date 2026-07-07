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
  played_score int not null,
  best_score int not null,
  score_loss int generated always as (best_score - played_score) stored,
  rating text not null,
  created_at timestamptz not null default now(),
  constraint move_analysis_depth_check check (minimax_depth > 0),
  constraint move_analysis_played_column_check check (played_column between 0 and 6),
  constraint move_analysis_best_column_check check (best_column between 0 and 6),
  constraint move_analysis_rating_check check (rating in ('blunder', 'average', 'good', 'perfect')),
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
create index game_players_profile_id_idx on public.game_players (profile_id);
create index game_players_game_id_idx on public.game_players (game_id);
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
alter table public.game_players enable row level security;
alter table public.game_moves enable row level security;
alter table public.move_analysis enable row level security;
alter table public.player_stats enable row level security;

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

create policy "users can read their move analysis"
on public.move_analysis
for select
to authenticated
using (public.is_game_participant(game_id));

create policy "users can read their own stats"
on public.player_stats
for select
to authenticated
using (profile_id = auth.uid());
