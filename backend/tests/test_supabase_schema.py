import re
import unittest
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "supabase_schema.sql"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "migrations" / "20260714_move_analysis_worst_move_and_ratings.sql"
)
MULTIPLAYER_RECOVERY_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "migrations" / "20260715_multiplayer_room_requests.sql"
)
FAST_CONNECT_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "migrations" / "20260716_fast_connect_modes.sql"
)


class SupabaseSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = SCHEMA_PATH.read_text(encoding="utf-8")
        cls.migration = MIGRATION_PATH.read_text(encoding="utf-8")
        cls.multiplayer_recovery_migration = MULTIPLAYER_RECOVERY_MIGRATION_PATH.read_text(encoding="utf-8")
        cls.fast_connect_migration = FAST_CONNECT_MIGRATION_PATH.read_text(encoding="utf-8")

    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA_PATH.exists())

    def test_core_tables_are_defined(self):
        for table_name in [
            "profiles",
            "games",
            "multiplayer_room_requests",
            "game_players",
            "game_moves",
            "move_analysis",
            "player_stats",
        ]:
            self.assertIn(f"create table public.{table_name}", self.schema)

    def test_multiplayer_room_requests_are_private_lifecycle_tombstones(self):
        for field in [
            "profile_id uuid not null",
            "request_id text not null",
            "game_id uuid not null",
            "player_id uuid not null",
            "state text not null default 'active'",
            "expires_at timestamptz not null",
            "resolved_at timestamptz",
            "primary key (profile_id, request_id)",
        ]:
            self.assertIn(field, self.schema)
        self.assertRegex(
            self.schema,
            r"state in \('active', 'completed', 'cancelled', 'expired', 'invalid'\)",
        )
        self.assertIn("create unique index multiplayer_room_requests_game_id_unique", self.schema)
        self.assertIn(
            "alter table public.multiplayer_room_requests enable row level security;",
            self.schema,
        )
        self.assertNotRegex(
            self.schema,
            r'create policy "[^"]+"\s+on public\.multiplayer_room_requests',
        )

    def test_multiplayer_recovery_migration_defines_atomic_service_only_rpcs(self):
        self.assertTrue(MULTIPLAYER_RECOVERY_MIGRATION_PATH.exists())
        migration = self.multiplayer_recovery_migration
        self.assertIn("begin;", migration)
        self.assertIn("commit;", migration)
        self.assertIn("create table if not exists public.multiplayer_room_requests", migration)
        self.assertIn("create or replace function public.claim_multiplayer_room_request", migration)
        self.assertIn("pg_advisory_xact_lock", migration)
        self.assertIn("insert into public.games", migration)
        self.assertIn("insert into public.game_players", migration)
        self.assertIn("insert into public.multiplayer_room_requests", migration)
        self.assertIn("create or replace function public.resolve_multiplayer_room_request", migration)
        self.assertIn("set status = 'abandoned'", migration)
        self.assertIn("from public, anon, authenticated", migration)
        self.assertIn("to service_role", migration)

    def test_games_tracks_lazy_analysis_status(self):
        self.assertIn("analysis_status text not null default 'not_requested'", self.schema)
        self.assertIn("analysis_requested_at timestamptz", self.schema)
        self.assertIn("analysis_completed_at timestamptz", self.schema)
        self.assertIn("analysis_error text", self.schema)
        self.assertRegex(
            self.schema,
            r"analysis_status in \('not_requested', 'processing', 'complete', 'failed'\)",
        )

    def test_fast_connect_migration_is_rerunnable_and_updates_durable_creation(self):
        migration = self.fast_connect_migration
        self.assertTrue(FAST_CONNECT_MIGRATION_PATH.exists())
        self.assertIn("begin;", migration)
        self.assertIn("commit;", migration)
        self.assertIn("drop constraint if exists games_difficulty_check", migration)
        self.assertIn("'fast_connect_60', 'fast_connect_30'", migration)
        self.assertIn(
            "drop function if exists public.claim_multiplayer_room_request(uuid, text, uuid, uuid, text)",
            migration,
        )
        self.assertIn("p_difficulty text", migration)
        self.assertIn("game_difficulty text", migration)
        self.assertIn("values (p_game_id, 'multiplayer', p_difficulty, 'waiting')", migration)
        self.assertIn("game_record.difficulty", migration)
        self.assertIn("to service_role", migration)

    def test_username_allows_letters_numbers_and_underscores(self):
        self.assertIn("constraint profiles_username_length check (char_length(username) between 3 and 32)", self.schema)
        self.assertIn("constraint profiles_username_format check (username ~ '^[A-Za-z0-9_]+$')", self.schema)

    def test_game_moves_record_replay_state(self):
        for column_name in [
            "move_number int not null",
            "player_number int not null",
            "profile_id uuid references public.profiles(id) on delete set null",
            "is_ai_move boolean not null default false",
            "column_played int not null",
            "board_before jsonb not null",
            "board_after jsonb not null",
        ]:
            self.assertIn(column_name, self.schema)
        self.assertIn("constraint game_moves_unique_move_number unique (game_id, move_number)", self.schema)

    def test_multiplayer_ownership_is_unique_per_profile_and_game(self):
        self.assertIn("create unique index game_players_unique_profile_per_game", self.schema)
        self.assertIn("on public.game_players (game_id, profile_id)", self.schema)
        self.assertIn("where profile_id is not null", self.schema)

    def test_auth_signup_automatically_creates_profile(self):
        self.assertIn("create or replace function public.handle_new_user()", self.schema)
        self.assertIn("security definer", self.schema)
        self.assertIn("insert into public.profiles (id, username, display_name)", self.schema)
        self.assertIn("create trigger on_auth_user_created", self.schema)
        self.assertIn("after insert on auth.users", self.schema)

    def test_move_analysis_is_bound_to_the_same_game_as_the_move(self):
        self.assertIn("constraint game_moves_unique_id_game unique (id, game_id)", self.schema)
        self.assertIn("constraint move_analysis_move_game_fk foreign key (move_id, game_id)", self.schema)
        self.assertIn("references public.game_moves(id, game_id) on delete cascade", self.schema)
        self.assertIn("constraint move_analysis_unique_depth unique (move_id, minimax_depth)", self.schema)

    def test_move_analysis_records_worst_move_and_uses_current_ratings(self):
        for column_name in [
            "worst_column int not null",
            "worst_score int not null",
        ]:
            self.assertIn(column_name, self.schema)
        self.assertIn(
            "constraint move_analysis_worst_column_check check (worst_column between 0 and 6)",
            self.schema,
        )
        self.assertRegex(
            self.schema,
            r"rating in \('blunder', 'mistake', 'ok', 'great'\)",
        )
        for legacy_rating in ["average", "good", "perfect"]:
            self.assertNotRegex(
                self.schema,
                rf"rating in \([^\)]*'{legacy_rating}'",
            )

    def test_move_analysis_migration_invalidates_legacy_derived_rows(self):
        self.assertTrue(MIGRATION_PATH.exists())
        self.assertIn("add column if not exists worst_column int", self.migration)
        self.assertIn("add column if not exists worst_score int", self.migration)
        self.assertIn(
            "delete from public.move_analysis",
            self.migration,
        )
        self.assertIn(
            "analysis_status = 'not_requested'",
            self.migration,
        )
        self.assertIn("analysis_requested_at = null", self.migration)
        self.assertIn("analysis_completed_at = null", self.migration)
        self.assertIn("analysis_error = null", self.migration)
        self.assertNotIn("coalesce(worst_column, played_column)", self.migration)
        self.assertNotIn("when 'average' then", self.migration)
        self.assertRegex(
            self.migration,
            r"rating in \('blunder', 'mistake', 'ok', 'great'\)",
        )

    def test_move_analysis_migration_is_safe_to_rerun(self):
        self.assertIn("begin;", self.migration)
        self.assertIn("commit;", self.migration)
        self.assertEqual(self.migration.count("add column if not exists"), 2)
        self.assertIn("create temporary table move_analysis_migration_state", self.migration)
        self.assertIn("where needs_reanalysis", self.migration)
        self.assertIn("where worst_column is null", self.migration)
        self.assertIn("or worst_score is null", self.migration)
        self.assertIn(
            "drop constraint if exists move_analysis_rating_check",
            self.migration,
        )
        self.assertIn(
            "drop constraint if exists move_analysis_worst_column_check",
            self.migration,
        )

    def test_move_analysis_has_no_direct_participant_read_policy(self):
        policy_name = "users can read their move analysis"
        self.assertNotIn(f'create policy "{policy_name}"', self.schema)
        self.assertIn(
            f'drop policy if exists "{policy_name}"',
            self.migration,
        )
        self.assertIn("on public.move_analysis;", self.migration)

    def test_rls_is_enabled_and_read_policies_exist(self):
        for table_name in [
            "profiles",
            "games",
            "multiplayer_room_requests",
            "game_players",
            "game_moves",
            "move_analysis",
            "player_stats",
        ]:
            self.assertIn(f"alter table public.{table_name} enable row level security;", self.schema)

        for policy_name in [
            "profiles are readable by authenticated users",
            "users can read their games",
            "users can read their game players",
            "users can read their game moves",
            "users can read their own stats",
        ]:
            self.assertRegex(self.schema, rf'create policy "{re.escape(policy_name)}"')


if __name__ == "__main__":
    unittest.main()
