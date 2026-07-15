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


class MissingMoveAnalysisColumnError(Exception):
    code = "42703"
    message = "column move_analysis_1.worst_column does not exist"


class RecordingQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def insert(self, payload):
        self.client.operations.append({"table": self.table_name, "action": "insert", "payload": payload})
        return self

    def upsert(self, payload, **kwargs):
        self.client.operations.append(
            {
                "table": self.table_name,
                "action": "upsert",
                "payload": payload,
                "kwargs": kwargs,
            }
        )
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


class RpcClient:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = []

    def rpc(self, function_name, payload):
        self.calls.append((function_name, payload))
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=self.data)


class MissingRecoveryRpcError(Exception):
    code = "PGRST202"
    message = "Could not find the function public.claim_multiplayer_room_request"


class MissingRecoveryTableError(Exception):
    code = "PGRST205"
    message = "Could not find the table public.multiplayer_room_requests in the schema cache"


class TransientRecoveryRpcError(Exception):
    code = "503"
    message = "Database connection timed out while executing request"


class MoveHistoryClient:
    def __init__(self, membership, moves, missing_analysis_columns=False):
        self.membership = membership
        self.moves = moves
        self.missing_analysis_columns = missing_analysis_columns
        self.selections = {}
        self.selection_history = []

    def table(self, table_name):
        rows = self.membership if table_name == "game_players" else self.moves
        client = self

        class MoveHistoryQuery(FakeQuery):
            def select(self, selection):
                client.selections[table_name] = selection
                client.selection_history.append((table_name, selection))
                if (
                    client.missing_analysis_columns
                    and table_name in {"game_moves", "move_analysis"}
                    and ("worst_column" in selection or "worst_score" in selection)
                ):
                    raise MissingMoveAnalysisColumnError()
                return self

        return MoveHistoryQuery(rows)


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
        self.assertNotIn("analysis_status", payload)
        self.assertNotIn("analysis_error", payload)
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
        self.assertEqual(
            [(op["table"], op["action"]) for op in client.operations],
            [
                ("games", "insert"),
                ("game_players", "insert"),
            ],
        )
        player_rows = client.operations[1]["payload"]
        self.assertEqual(len(player_rows), 2)
        self.assertEqual({row["player_number"] for row in player_rows}, {1, 2})
        self.assertEqual(player_rows[0]["profile_id"], "profile-1")
        self.assertTrue(player_rows[1]["is_ai"])
        self.assertTrue(all(row["game_id"] == str(uuid.UUID(game_id)) for row in player_rows))
        self.assertEqual(client.operations[0]["payload"]["analysis_status"], "not_requested")
        self.assertIsNone(client.operations[0]["payload"]["analysis_error"])

    def test_atomic_multiplayer_room_claim_uses_canonical_uuid_payload(self):
        profile_id = str(uuid.uuid4())
        game_id = uuid.uuid4().hex
        player_id = uuid.uuid4().hex
        row = {
            "profile_id": profile_id,
            "request_id": "request-1",
            "game_id": str(uuid.UUID(game_id)),
            "player_id": str(uuid.UUID(player_id)),
            "owner_name": "Player One",
            "state": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "resolved_at": None,
            "game_mode": "multiplayer",
            "game_status": "waiting",
            "player_count": 1,
            "owner_profile_id": profile_id,
            "created": True,
        }
        client = RpcClient([row])

        with patch.object(supabase_store, "get_client", return_value=client):
            result = supabase_store.claim_multiplayer_room_request(
                profile_id,
                "request-1",
                game_id,
                player_id,
                "Player One",
            )

        self.assertEqual(result, {"result": "ok", "room": row})
        self.assertEqual(
            client.calls,
            [
                (
                    "claim_multiplayer_room_request",
                    {
                        "p_profile_id": profile_id,
                        "p_request_id": "request-1",
                        "p_game_id": str(uuid.UUID(game_id)),
                        "p_player_id": str(uuid.UUID(player_id)),
                        "p_owner_name": "Player One",
                    },
                )
            ],
        )

    def test_multiplayer_claim_falls_back_only_for_missing_rpc(self):
        profile_id = str(uuid.uuid4())
        game_id = str(uuid.uuid4())
        player_id = str(uuid.uuid4())

        with patch.object(supabase_store, "get_client", return_value=RpcClient(error=MissingRecoveryRpcError())):
            missing = supabase_store.claim_multiplayer_room_request(
                profile_id, "request-1", game_id, player_id, "Player"
            )
        with patch.object(supabase_store, "get_client", return_value=RpcClient(error=TransientRecoveryRpcError())):
            transient = supabase_store.claim_multiplayer_room_request(
                profile_id, "request-1", game_id, player_id, "Player"
            )

        self.assertEqual(missing, {"result": "schema_missing"})
        self.assertEqual(transient, {"result": "error", "code": "persistence_unavailable"})

    def test_missing_recovery_table_is_a_schema_fallback(self):
        self.assertTrue(supabase_store.is_missing_multiplayer_recovery_schema(MissingRecoveryTableError()))

    def test_empty_atomic_claim_response_keeps_request_retryable(self):
        profile_id = str(uuid.uuid4())
        with patch.object(supabase_store, "get_client", return_value=RpcClient([])):
            result = supabase_store.claim_multiplayer_room_request(
                profile_id,
                "request-1",
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "Player",
            )

        self.assertEqual(result, {"result": "error", "code": "persistence_unavailable"})

    def test_configured_but_unavailable_client_is_retryable_persistence_error(self):
        with (
            patch.object(supabase_store, "get_client", return_value=None),
            patch.object(
                supabase_store,
                "is_configured",
                return_value=True,
            ),
        ):
            client, unavailable = supabase_store.multiplayer_recovery_client()

        self.assertIsNone(client)
        self.assertEqual(unavailable, {"result": "error", "code": "persistence_unavailable"})

    def test_fetch_multiplayer_room_request_returns_lifecycle_and_owner_shape(self):
        profile_id = str(uuid.uuid4())
        game_id = str(uuid.uuid4())
        player_id = str(uuid.uuid4())
        rows = [
            {
                "profile_id": profile_id,
                "request_id": "request-1",
                "game_id": game_id,
                "player_id": player_id,
                "owner_name": "Player One",
                "state": "active",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "resolved_at": None,
                "games": {
                    "mode": "multiplayer",
                    "status": "waiting",
                    "game_players": [{"player_number": 1, "profile_id": profile_id, "is_ai": False}],
                },
            }
        ]

        with patch.object(supabase_store, "get_client", return_value=FakeClient(rows)):
            result = supabase_store.fetch_multiplayer_room_request(profile_id, "request-1")

        self.assertEqual(result["result"], "ok")
        self.assertEqual(result["room"]["game_id"], game_id)
        self.assertEqual(result["room"]["player_id"], player_id)
        self.assertEqual(result["room"]["state"], "active")
        self.assertEqual(result["room"]["player_count"], 1)
        self.assertEqual(result["room"]["owner_profile_id"], profile_id)

    def test_resolve_multiplayer_room_request_calls_server_only_rpc(self):
        game_id = str(uuid.uuid4())
        client = RpcClient(True)
        with patch.object(supabase_store, "get_client", return_value=client):
            result = supabase_store.resolve_multiplayer_room_request(game_id, "cancelled")

        self.assertEqual(result, {"result": "ok", "resolved": True})
        self.assertEqual(
            client.calls,
            [
                (
                    "resolve_multiplayer_room_request",
                    {
                        "p_game_id": game_id,
                        "p_state": "cancelled",
                    },
                )
            ],
        )

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
        self.assertEqual(
            [(op["table"], op["action"]) for op in client.operations],
            [
                ("game_players", "upsert"),
                ("game_players", "upsert"),
                ("games", "update"),
            ],
        )
        player_rows = [client.operations[0]["payload"], client.operations[1]["payload"]]
        self.assertEqual(client.operations[0]["kwargs"], {"on_conflict": "game_id,player_number"})
        self.assertEqual({row["player_number"] for row in player_rows}, {1, 2})
        self.assertEqual({row["profile_id"] for row in player_rows}, {"profile-1", "profile-2"})
        self.assertTrue(all(row["game_id"] == str(uuid.UUID(game_id)) for row in player_rows))
        game_update = client.operations[2]["payload"]
        self.assertNotIn("analysis_status", game_update)
        self.assertNotIn("analysis_error", game_update)

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
                "games": [
                    {
                        "id": draw_id,
                        "mode": "multiplayer",
                        "difficulty": "multiplayer",
                        "status": "draw",
                        "winner_player_number": None,
                        "started_at": "2026-01-02T00:00:00+00:00",
                        "ended_at": "2026-01-02T00:10:00+00:00",
                    }
                ],
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
            {
                "move_number": 1,
                "player_number": 1,
                "column_played": 3,
                "board_before": [],
                "board_after": [],
                "move_analysis": [
                    {
                        "rating": "great",
                        "played_score": 80,
                        "best_score": 80,
                        "worst_score": -800,
                        "best_column": 3,
                        "worst_column": 0,
                    }
                ],
            },
            {
                "move_number": 2,
                "player_number": 2,
                "column_played": 2,
                "board_before": [],
                "board_after": [],
                "move_analysis": {"rating": "mistake", "played_score": -20},
            },
        ]
        membership = [
            {
                "game_id": game_id,
                "games": {
                    "status": "draw",
                    "analysis_status": "complete",
                    "analysis_error": None,
                },
            }
        ]
        client = MoveHistoryClient(membership, moves)

        with patch.object(supabase_store, "get_client", return_value=client):
            self.assertEqual(
                supabase_store.fetch_game_moves("profile-1", game_id),
                {
                    "moves": [
                        {
                            "move_number": 1,
                            "player_number": 1,
                            "column_played": 3,
                            "board_before": [],
                            "board_after": [],
                            "move_analysis": [{"feedback": "Great Move"}],
                        },
                        {
                            "move_number": 2,
                            "player_number": 2,
                            "column_played": 2,
                            "board_before": [],
                            "board_after": [],
                            "move_analysis": [{"feedback": "Mistake"}],
                        },
                    ],
                    "analysis_status": "complete",
                    "analysis_error": None,
                    "analysis_available": True,
                    "analysis_unavailable_reason": None,
                },
            )
        analysis_selection = client.selections["game_moves"]
        self.assertIn("worst_column", analysis_selection)
        self.assertIn("worst_score", analysis_selection)
        self.assertIn("rating", analysis_selection)
        self.assertNotIn("played_score", analysis_selection)
        self.assertNotIn("best_score", analysis_selection)

        unauthorized_client = MoveHistoryClient([], moves)
        with patch.object(supabase_store, "get_client", return_value=unauthorized_client):
            self.assertIsNone(supabase_store.fetch_game_moves("profile-2", game_id))

        active_client = MoveHistoryClient([{"game_id": game_id, "games": {"status": "playing"}}], moves)
        with patch.object(supabase_store, "get_client", return_value=active_client):
            self.assertIsNone(supabase_store.fetch_game_moves("profile-1", game_id))

    def test_public_review_move_maps_feedback_without_leaking_raw_analysis(self):
        expected_feedback = {
            "blunder": "Blunder",
            "mistake": "Mistake",
            "ok": "OK",
            "great": "Great Move",
        }

        for rating, feedback in expected_feedback.items():
            with self.subTest(rating=rating):
                public_move = supabase_store.public_review_move(
                    {
                        "id": 10,
                        "move_number": 1,
                        "player_number": 1,
                        "column_played": 3,
                        "move_analysis": [
                            {
                                "rating": rating,
                                "played_score": -100,
                                "best_score": 500,
                                "worst_score": -100,
                                "played_column": 3,
                                "best_column": 4,
                                "worst_column": 0,
                                "minimax_depth": 4,
                            }
                        ],
                    }
                )

                self.assertEqual(public_move["move_analysis"], [{"feedback": feedback}])
                serialized = repr(public_move)
                for private_field in (
                    "rating",
                    "score",
                    "played_column",
                    "best_column",
                    "worst_column",
                    "minimax_depth",
                ):
                    self.assertNotIn(private_field, serialized)

    def test_fetch_game_moves_falls_back_to_history_when_analysis_schema_is_old(self):
        game_id = str(uuid.uuid4())
        moves = [
            {
                "id": 10,
                "move_number": 1,
                "player_number": 1,
                "column_played": 3,
                "board_before": [],
                "board_after": [],
            }
        ]
        membership = [
            {
                "game_id": game_id,
                "games": {
                    "status": "draw",
                    "analysis_status": "complete",
                    "analysis_error": None,
                },
            }
        ]
        client = MoveHistoryClient(membership, moves, missing_analysis_columns=True)

        with patch.object(supabase_store, "get_client", return_value=client):
            review = supabase_store.fetch_game_moves("profile-1", game_id)

        self.assertEqual(review["moves"], moves)
        self.assertFalse(review["analysis_available"])
        self.assertEqual(
            review["analysis_unavailable_reason"],
            supabase_store.MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE,
        )
        self.assertTrue(any("worst_column" in selection for _, selection in client.selection_history))
        self.assertEqual(
            client.selections["game_moves"], "id,move_number,player_number,column_played,board_before,board_after"
        )

    def test_fetch_game_analysis_source_reports_old_analysis_schema(self):
        game_id = str(uuid.uuid4())
        moves = [{"id": 10, "move_number": 1, "player_number": 1, "column_played": 3}]
        membership = [
            {
                "game_id": game_id,
                "games": {
                    "status": "draw",
                    "analysis_status": "not_requested",
                    "analysis_error": None,
                },
            }
        ]
        client = MoveHistoryClient(membership, moves, missing_analysis_columns=True)

        with patch.object(supabase_store, "get_client", return_value=client):
            source = supabase_store.fetch_game_analysis_source("profile-1", game_id)

        self.assertFalse(source["analysis_available"])
        self.assertEqual(
            source["analysis_unavailable_reason"],
            supabase_store.MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE,
        )

    def test_move_analysis_status_and_rows_are_persisted(self):
        game_id = str(uuid.uuid4())
        client = RecordingClient()
        rows = [
            {
                "move_id": 10,
                "minimax_depth": 4,
                "played_column": 2,
                "best_column": 3,
                "worst_column": 0,
                "played_score": 10,
                "best_score": 200,
                "worst_score": -800,
                "rating": "blunder",
            }
        ]

        with patch.object(supabase_store, "get_client", return_value=client):
            self.assertTrue(supabase_store.set_game_analysis_status(game_id, "processing"))
            self.assertTrue(supabase_store.replace_move_analysis(game_id, rows))
            self.assertTrue(supabase_store.set_game_analysis_status(game_id, "complete"))

        self.assertEqual(
            [(op["table"], op["action"]) for op in client.operations],
            [
                ("games", "update"),
                ("move_analysis", "delete"),
                ("move_analysis", "insert"),
                ("games", "update"),
            ],
        )
        inserted = client.operations[2]["payload"][0]
        self.assertEqual(inserted["game_id"], game_id)
        self.assertEqual(inserted["worst_column"], 0)
        self.assertEqual(inserted["worst_score"], -800)
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
            {
                "move_number": 3,
                "player_number": 1,
                "column_played": 3,
                "board_before": after_two,
                "board_after": after_three,
            },
        ]

        repaired = supabase_store.repair_move_history(stored_moves)

        self.assertEqual([move["move_number"] for move in repaired], [1, 2, 3])
        self.assertEqual(repaired[1]["player_number"], 2)
        self.assertEqual(repaired[1]["column_played"], 4)


if __name__ == "__main__":
    unittest.main()
