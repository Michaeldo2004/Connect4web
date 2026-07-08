import os
import unittest
import uuid
from unittest.mock import patch

import numpy as np

import supabase_store


class SupabaseStoreTests(unittest.TestCase):
    def setUp(self):
        supabase_store._client = None
        supabase_store._client_checked = False
        supabase_store._env_loaded = True

    def test_db_game_id_normalizes_hex_uuid(self):
        raw_id = uuid.uuid4().hex
        self.assertEqual(supabase_store.db_game_id(raw_id), str(uuid.UUID(raw_id)))

    def test_store_is_disabled_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            supabase_store._client = None
            supabase_store._client_checked = False
            self.assertIsNone(supabase_store.get_client())
            self.assertFalse(supabase_store.is_enabled())

    def test_game_payload_marks_finished_game(self):
        game_id = uuid.uuid4().hex
        game = {
            "mode": "ai",
            "difficulty": "medium",
            "status": "human_win",
            "board": np.array([[0, 0, 0, 0, 0, 0, 0] for _ in range(6)]),
        }

        payload = supabase_store.game_payload(game_id, game)

        self.assertEqual(payload["id"], str(uuid.UUID(game_id)))
        self.assertEqual(payload["winner_player_number"], 1)
        self.assertEqual(payload["analysis_status"], "not_requested")
        self.assertIsNotNone(payload["ended_at"])
        self.assertEqual(payload["final_board"], [[0, 0, 0, 0, 0, 0, 0] for _ in range(6)])

    def test_ai_game_player_payloads_include_human_and_ai(self):
        game_id = uuid.uuid4().hex
        game = {"mode": "ai", "difficulty": "hard", "profile_id": None}

        players = supabase_store.game_player_payloads(game_id, game)

        self.assertEqual(len(players), 2)
        self.assertEqual(players[0]["player_number"], 1)
        self.assertFalse(players[0]["is_ai"])
        self.assertEqual(players[1]["player_number"], 2)
        self.assertTrue(players[1]["is_ai"])
        self.assertEqual(players[1]["ai_difficulty"], "hard")

    def test_record_move_increments_move_number_even_when_disabled(self):
        game_id = uuid.uuid4().hex
        game = {"move_number": 0}

        with patch.dict(os.environ, {}, clear=True):
            supabase_store._client = None
            supabase_store._client_checked = False
            saved = supabase_store.record_move(
                game_id,
                game,
                1,
                3,
                [[0, 0, 0, 0, 0, 0, 0] for _ in range(6)],
                [[0, 0, 0, 0, 0, 0, 0] for _ in range(6)],
            )

        self.assertFalse(saved)
        self.assertEqual(game["move_number"], 1)


if __name__ == "__main__":
    unittest.main()
