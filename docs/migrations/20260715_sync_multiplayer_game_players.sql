create or replace function public.sync_multiplayer_game_players(
  p_game_id uuid,
  p_players jsonb
)
returns void
language plpgsql
security invoker
set search_path = public
as $$
begin
  if jsonb_typeof(p_players) <> 'array' or jsonb_array_length(p_players) <> 2 then
    raise exception 'Exactly two multiplayer participants are required';
  end if;

  delete from public.game_players
  where game_id = p_game_id;

  insert into public.game_players (
    game_id,
    profile_id,
    player_number,
    is_ai,
    ai_difficulty,
    display_name_snapshot
  )
  select
    p_game_id,
    nullif(player->>'profile_id', '')::uuid,
    (player->>'player_number')::int,
    false,
    null,
    player->>'display_name_snapshot'
  from jsonb_array_elements(p_players) as participant(player);
end;
$$;

revoke all on function public.sync_multiplayer_game_players(uuid, jsonb) from public;
revoke all on function public.sync_multiplayer_game_players(uuid, jsonb) from anon;
revoke all on function public.sync_multiplayer_game_players(uuid, jsonb) from authenticated;
grant execute on function public.sync_multiplayer_game_players(uuid, jsonb) to service_role;
