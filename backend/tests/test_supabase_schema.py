import re
import unittest
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "supabase_schema.sql"


class SupabaseSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = SCHEMA_PATH.read_text(encoding="utf-8")

    def test_schema_file_exists(self):
        self.assertTrue(SCHEMA_PATH.exists())

    def test_core_tables_are_defined(self):
        for table_name in [
            "profiles",
            "games",
            "game_players",
            "game_moves",
            "move_analysis",
            "player_stats",
        ]:
            self.assertIn(f"create table public.{table_name}", self.schema)

    def test_games_tracks_lazy_analysis_status(self):
        self.assertIn("analysis_status text not null default 'not_requested'", self.schema)
        self.assertIn("analysis_requested_at timestamptz", self.schema)
        self.assertIn("analysis_completed_at timestamptz", self.schema)
        self.assertIn("analysis_error text", self.schema)
        self.assertRegex(
            self.schema,
            r"analysis_status in \('not_requested', 'processing', 'complete', 'failed'\)",
        )

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

    def test_rls_is_enabled_and_read_policies_exist(self):
        for table_name in [
            "profiles",
            "games",
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
            "users can read their move analysis",
            "users can read their own stats",
        ]:
            self.assertRegex(self.schema, rf'create policy "{re.escape(policy_name)}"')


if __name__ == "__main__":
    unittest.main()
