import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import supabase_store


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _table_name):
        return FakeQuery(self.rows)


class RecordingQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def insert(self, payload):
        self.client.operations.append({"table": self.table_name, "action": "insert", "payload": payload})
        return self

    def upsert(self, payload, **kwargs):
        self.client.operations.append({
            "table": self.table_name,
            "action": "upsert",
            "payload": payload,
            "kwargs": kwargs,
        })
        return self

    def update(self, payload):
        self.client.operations.append({"table": self.table_name, "action": "update", "payload": payload})
        return self

    def delete(self):
        self.client.operations.append({"table": self.table_name, "action": "delete"})
        return self

    def eq(self, *args):
        self.client.operations[-1]["eq"] = args
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class RecordingClient:
    def __init__(self):
        self.operations = []

    def table(self, table_name):
        return RecordingQuery(self, table_name)


class MoveHistoryClient:
    def __init__(self, membership, moves):
        self.membership = membership
        self.moves = moves

    def table(self, table_name):
        rows = self.membership if table_name == "game_players" else self.moves
        return FakeQuery(rows)


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

    def test_game_payload_uses_ai_game_human_piece_for_winner(self):
        game_id = uuid.uuid4().hex
        game = {
            "mode": "ai",
            "difficulty": "medium",
            "status": "human_win",
            "human_piece": 2,
            "ai_piece": 1,
            "board": np.array([[0, 0, 0, 0, 0, 0, 0] for _ in range(6)]),
        }

        payload = supabase_store.game_payload(game_id, game)

        self.assertEqual(payload["winner_player_number"], 2)

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

    def test_ai_game_player_payloads_swap_when_ai_starts(self):
        game_id = uuid.uuid4().hex
        game = {"mode": "ai", "difficulty": "hard", "profile_id": "profile-1", "human_piece": 2, "ai_piece": 1}

        players = supabase_store.game_player_payloads(game_id, game)

        human = next(player for player in players if not player["is_ai"])
        ai = next(player for player in players if player["is_ai"])
        self.assertEqual(human["player_number"], 2)
        self.assertEqual(human["profile_id"], "profile-1")
        self.assertEqual(ai["player_number"], 1)

    def test_create_game_record_inserts_ai_game_and_players(self):
        game_id = uuid.uuid4().hex
        game = {"mode": "ai", "difficulty": "hard", "profile_id": "profile-1", "status": "playing"}
        client = RecordingClient()

        with patch.object(supabase_store, "get_client", return_value=client):
            saved = supabase_store.create_game_record(game_id, game)

        self.assertTrue(saved)
        self.assertEqual([(op["table"], op["action"]) for op in client.operations], [
            ("games", "insert"),
            ("game_players", "insert"),
        ])
        player_rows = client.operations[1]["payload"]
        self.assertEqual(len(player_rows), 2)
        self.assertEqual({row["player_number"] for row in player_rows}, {1, 2})
        self.assertEqual(player_rows[0]["profile_id"], "profile-1")
        self.assertTrue(player_rows[1]["is_ai"])
        self.assertTrue(all(row["game_id"] == str(uuid.UUID(game_id)) for row in player_rows))

    def test_add_game_player_records_upserts_multiplayer_players(self):
        game_id = uuid.uuid4().hex
        game = {
            "mode": "multiplayer",
            "difficulty": "multiplayer",
            "status": "playing",
            "players": {
                "first-player": {"piece": 2, "profile_id": "profile-1"},
                "second-player": {"piece": 1, "profile_id": "profile-2"},
            },
        }
        client = RecordingClient()

        with patch.object(supabase_store, "get_client", return_value=client):
            saved = supabase_store.add_game_player_records(game_id, game)

        self.assertTrue(saved)
        self.assertEqual([(op["table"], op["action"]) for op in client.operations], [
            ("game_players", "upsert"),
            ("game_players", "upsert"),
            ("games", "update"),
        ])
        player_rows = [client.operations[0]["payload"], client.operations[1]["payload"]]
        self.assertEqual(client.operations[0]["kwargs"], {"on_conflict": "game_id,player_number"})
        self.assertEqual({row["player_number"] for row in player_rows}, {1, 2})
        self.assertEqual({row["profile_id"] for row in player_rows}, {"profile-1", "profile-2"})
        self.assertTrue(all(row["game_id"] == str(uuid.UUID(game_id)) for row in player_rows))

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

    def test_fetch_completed_games_filters_and_formats_profile_games(self):
        finished_id = str(uuid.uuid4())
        active_id = str(uuid.uuid4())
        rows = [
            {
                "player_number": 1,
                "games": {
                    "id": active_id,
                    "mode": "ai",
                    "difficulty": "medium",
                    "status": "playing",
                    "winner_player_number": None,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "ended_at": None,
                },
            },
            {
                "player_number": 1,
                "games": {
                    "id": finished_id,
                    "mode": "ai",
                    "difficulty": "hard",
                    "status": "human_win",
                    "winner_player_number": 1,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "ended_at": "2026-01-01T00:10:00+00:00",
                },
            },
        ]

        with patch.object(supabase_store, "get_client", return_value=FakeClient(rows)):
            games = supabase_store.fetch_completed_games("profile-1")

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["id"], finished_id)
        self.assertEqual(games[0]["result"], "Win")
        self.assertEqual(games[0]["playerNumber"], 1)

    def test_fetch_completed_games_formats_loss_draw_and_sorts_newest_first(self):
        older_id = str(uuid.uuid4())
        draw_id = str(uuid.uuid4())
        rows = [
            {
                "player_number": 1,
                "games": {
                    "id": older_id,
                    "mode": "multiplayer",
                    "difficulty": "multiplayer",
                    "status": "player2_win",
                    "winner_player_number": 2,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "ended_at": "2026-01-01T00:10:00+00:00",
                },
            },
            {
                "player_number": 2,
                "games": [{
                    "id": draw_id,
                    "mode": "multiplayer",
                    "difficulty": "multiplayer",
                    "status": "draw",
                    "winner_player_number": None,
                    "started_at": "2026-01-02T00:00:00+00:00",
                    "ended_at": "2026-01-02T00:10:00+00:00",
                }],
            },
        ]

        with patch.object(supabase_store, "get_client", return_value=FakeClient(rows)):
            games = supabase_store.fetch_completed_games("profile-1")

        self.assertEqual([game["id"] for game in games], [draw_id, older_id])
        self.assertEqual(games[0]["result"], "Draw")
        self.assertEqual(games[1]["result"], "Loss")

    def test_fetch_game_moves_requires_membership_and_returns_move_rows(self):
        game_id = str(uuid.uuid4())
        moves = [
            {"move_number": 1, "player_number": 1, "column_played": 3, "board_before": [], "board_after": []},
            {"move_number": 2, "player_number": 2, "column_played": 2, "board_before": [], "board_after": []},
        ]
        client = MoveHistoryClient([{"game_id": game_id, "games": {"status": "draw"}}], moves)

        with patch.object(supabase_store, "get_client", return_value=client):
            self.assertEqual(supabase_store.fetch_game_moves("profile-1", game_id), moves)

        unauthorized_client = MoveHistoryClient([], moves)
        with patch.object(supabase_store, "get_client", return_value=unauthorized_client):
            self.assertIsNone(supabase_store.fetch_game_moves("profile-2", game_id))

        active_client = MoveHistoryClient([{"game_id": game_id, "games": {"status": "playing"}}], moves)
        with patch.object(supabase_store, "get_client", return_value=active_client):
            self.assertIsNone(supabase_store.fetch_game_moves("profile-1", game_id))

    def test_move_analysis_status_and_rows_are_persisted(self):
        game_id = str(uuid.uuid4())
        client = RecordingClient()
        rows = [{
            "move_id": 10,
            "minimax_depth": 4,
            "played_column": 2,
            "best_column": 3,
            "played_score": 10,
            "best_score": 200,
            "rating": "blunder",
        }]

        with patch.object(supabase_store, "get_client", return_value=client):
            self.assertTrue(supabase_store.set_game_analysis_status(game_id, "processing"))
            self.assertTrue(supabase_store.replace_move_analysis(game_id, rows))
            self.assertTrue(supabase_store.set_game_analysis_status(game_id, "complete"))

        self.assertEqual([(op["table"], op["action"]) for op in client.operations], [
            ("games", "update"),
            ("move_analysis", "delete"),
            ("move_analysis", "insert"),
            ("games", "update"),
        ])
        inserted = client.operations[2]["payload"][0]
        self.assertEqual(inserted["game_id"], game_id)
        self.assertNotIn("score_loss", inserted)

    def test_repair_move_history_restores_missing_opponent_move(self):
        empty = [[0 for _ in range(7)] for _ in range(6)]
        after_one = [row[:] for row in empty]
        after_one[5][3] = 1
        after_two = [row[:] for row in after_one]
        after_two[5][4] = 2
        after_three = [row[:] for row in after_two]
        after_three[4][3] = 1
        stored_moves = [
            {"move_number": 1, "player_number": 1, "column_played": 3, "board_before": empty, "board_after": after_one},
            {"move_number": 3, "player_number": 1, "column_played": 3, "board_before": after_two, "board_after": after_three},
        ]

        repaired = supabase_store.repair_move_history(stored_moves)

        self.assertEqual([move["move_number"] for move in repaired], [1, 2, 3])
        self.assertEqual(repaired[1]["player_number"], 2)
        self.assertEqual(repaired[1]["column_played"], 4)


if __name__ == "__main__":
    unittest.main()
