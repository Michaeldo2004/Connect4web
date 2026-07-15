import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt

import app as app_module
from app import app, games, socketio

TEST_JWT_SECRET = "test-secret-that-is-at-least-32-bytes"


class AuthLayerTests(unittest.TestCase):
    def tearDown(self):
        app.config.pop("AUTH_REQUIRED", None)
        games.clear()
        app_module.reset_ai_job_queue()
        app_module.reset_ai_admission_queue()

    def make_token(self, user_id="user-123", secret=TEST_JWT_SECRET):
        return jwt.encode(
            {
                "sub": user_id,
                "email": "player@example.com",
                "aud": "authenticated",
                "exp": int(time.time()) + 60,
            },
            secret,
            algorithm="HS256",
        )

    def test_verify_access_token_returns_profile_id(self):
        app.config["AUTH_REQUIRED"] = True
        with patch.object(app_module, "SUPABASE_JWT_SECRET", TEST_JWT_SECRET):
            auth_context, error = app_module.verify_access_token(self.make_token())

        self.assertIsNone(error)
        self.assertEqual(auth_context["profile_id"], "user-123")
        self.assertEqual(auth_context["email"], "player@example.com")

    def test_verify_access_token_rejects_missing_token(self):
        app.config["AUTH_REQUIRED"] = True
        with patch.object(app_module, "SUPABASE_JWT_SECRET", TEST_JWT_SECRET):
            auth_context, error = app_module.verify_access_token("")

        self.assertIsNone(auth_context)
        self.assertEqual(error, "Login required")

    def test_verify_access_token_uses_supabase_client_without_jwt_secret(self):
        app.config["AUTH_REQUIRED"] = True
        response = SimpleNamespace(user=SimpleNamespace(id="user-123", email="player@example.com"))
        client = SimpleNamespace(auth=SimpleNamespace(get_user=lambda access_token: response))

        with patch.object(app_module, "SUPABASE_JWT_SECRET", ""):
            with patch.object(app_module.supabase_store, "get_client", return_value=client):
                auth_context, error = app_module.verify_access_token("supabase-access-token")

        self.assertIsNone(error)
        self.assertEqual(auth_context["profile_id"], "user-123")
        self.assertEqual(auth_context["email"], "player@example.com")

    def test_verify_access_token_rejects_when_no_auth_backend_is_configured(self):
        app.config["AUTH_REQUIRED"] = True

        with patch.object(app_module, "SUPABASE_JWT_SECRET", ""):
            with patch.object(app_module.supabase_store, "get_client", return_value=None):
                auth_context, error = app_module.verify_access_token("supabase-access-token")

        self.assertIsNone(auth_context)
        self.assertEqual(error, "Authentication is not configured")

    def test_create_game_rejects_unauthenticated_socket_payload(self):
        app.config["AUTH_REQUIRED"] = True
        client = socketio.test_client(app)
        try:
            with patch.object(app_module, "SUPABASE_JWT_SECRET", TEST_JWT_SECRET):
                client.emit("create_game", {"difficulty": "easy"})
            rejected = next(event for event in client.get_received() if event["name"] == "create_rejected")
            self.assertEqual(rejected["args"][0]["message"], "Login required")
        finally:
            client.disconnect()

    def test_profile_games_endpoint_returns_authenticated_completed_games(self):
        app.config["AUTH_REQUIRED"] = True
        expected_games = [{"id": "game-1", "result": "Win"}]

        with patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)):
            with patch.object(app_module.supabase_store, "fetch_completed_games", return_value=expected_games):
                response = app.test_client().get("/api/profile/games", headers={"Authorization": "Bearer token"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["games"], expected_games)

    def test_profile_games_endpoint_rejects_missing_login(self):
        app.config["AUTH_REQUIRED"] = True

        with patch.object(app_module, "verify_access_token", return_value=(None, "Login required")):
            response = app.test_client().get("/api/profile/games")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["message"], "Login required")

    def test_profile_games_endpoint_returns_consistent_service_error(self):
        app.config["AUTH_REQUIRED"] = True

        with patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)):
            with patch.object(
                app_module.supabase_store,
                "fetch_completed_games",
                side_effect=RuntimeError("database unavailable"),
            ):
                response = app.test_client().get(
                    "/api/profile/games",
                    headers={"Authorization": "Bearer token"},
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "profile_games_unavailable")

    def test_profile_game_moves_endpoint_returns_authenticated_history(self):
        app.config["AUTH_REQUIRED"] = True
        expected_moves = [{"move_number": 1, "player_number": 1, "column_played": 3}]
        expected_review = {
            "moves": expected_moves,
            "analysis_status": "complete",
            "analysis_error": None,
        }

        with patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)):
            with patch.object(
                app_module.supabase_store, "fetch_game_moves", return_value=expected_review
            ) as fetch_moves:
                response = app.test_client().get(
                    "/api/profile/games/game-1/moves",
                    headers={"Authorization": "Bearer token"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["moves"], expected_moves)
        self.assertEqual(response.get_json()["analysis_status"], "complete")
        self.assertIsNone(response.get_json()["analysis_error"])
        fetch_moves.assert_called_once_with("user-123", "game-1")

    def test_profile_game_moves_endpoint_rejects_missing_login(self):
        app.config["AUTH_REQUIRED"] = True

        with patch.object(app_module, "verify_access_token", return_value=(None, "Login required")):
            response = app.test_client().get("/api/profile/games/game-1/moves")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["message"], "Login required")

    def test_profile_game_moves_endpoint_preserves_processing_for_active_job(self):
        app.config["AUTH_REQUIRED"] = True
        review = {
            "moves": [{"move_number": 1, "player_number": 1, "column_played": 3}],
            "analysis_status": "processing",
            "analysis_error": None,
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "player-2"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_moves", return_value=review),
            patch.dict(app_module.analysis_jobs_by_game, {"shared-game": {"state": "running"}}, clear=True),
            patch.object(app_module.supabase_store, "set_game_analysis_status") as set_status,
        ):
            response = app.test_client().get(
                "/api/profile/games/shared-game/moves",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["analysis_status"], "processing")
        self.assertIsNone(response.get_json()["analysis_error"])
        set_status.assert_not_called()

    def test_profile_game_moves_reconciles_orphaned_processing_job(self):
        app.config["AUTH_REQUIRED"] = True
        review = {
            "moves": [{"move_number": 1, "player_number": 1, "column_played": 3}],
            "analysis_status": "processing",
            "analysis_error": None,
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "player-2"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_moves", return_value=review),
            patch.object(app_module.supabase_store, "set_game_analysis_status", return_value=True) as set_status,
        ):
            response = app.test_client().get(
                "/api/profile/games/orphaned-game/moves",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["analysis_status"], "failed")
        self.assertEqual(
            response.get_json()["analysis_error"],
            app_module.MOVE_ANALYSIS_INTERRUPTED_MESSAGE,
        )
        set_status.assert_called_once_with(
            "orphaned-game",
            "failed",
            app_module.MOVE_ANALYSIS_INTERRUPTED_MESSAGE,
        )

    def test_profile_game_moves_endpoint_rejects_inaccessible_game(self):
        app.config["AUTH_REQUIRED"] = True

        with patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)):
            with patch.object(app_module.supabase_store, "fetch_game_moves", return_value=None):
                response = app.test_client().get(
                    "/api/profile/games/game-1/moves",
                    headers={"Authorization": "Bearer token"},
                )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["message"], "Game history not found")

    def test_profile_game_moves_endpoint_returns_json_when_store_is_unavailable(self):
        app.config["AUTH_REQUIRED"] = True

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_moves", side_effect=RuntimeError("database offline")),
        ):
            with self.assertLogs(app.logger, level="WARNING") as captured_logs:
                response = app.test_client().get(
                    "/api/profile/games/game-1/moves",
                    headers={"Authorization": "Bearer token"},
                )

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.is_json)
        self.assertEqual(response.get_json()["code"], "game_review_unavailable")
        self.assertTrue(any("Could not load game review" in message for message in captured_logs.output))

    def test_profile_game_analysis_endpoint_queues_low_priority_job(self):
        app.config["AUTH_REQUIRED"] = True
        app_module.reset_ai_job_queue()
        app_module.reserve_ai_search_slot()
        source = {
            "analysis_status": "not_requested",
            "analysis_error": None,
            "moves": [{"id": 10, "player_number": 1, "column_played": 3, "board_before": []}],
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_analysis_source", return_value=source),
            patch.object(app_module.supabase_store, "set_game_analysis_status", return_value=True),
        ):
            response = app.test_client().post(
                "/api/profile/games/game-1/analysis",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "queued")
        self.assertEqual(response.get_json()["priority"], "move_analysis")

    def test_profile_game_analysis_endpoint_is_idempotent_after_completion(self):
        app.config["AUTH_REQUIRED"] = True
        source = {
            "analysis_status": "complete",
            "analysis_error": None,
            "moves": [{"id": 10}],
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_analysis_source", return_value=source),
            patch.object(app_module, "enqueue_move_analysis") as enqueue_analysis,
            patch.object(app_module.supabase_store, "set_game_analysis_status") as set_status,
        ):
            response = app.test_client().post(
                "/api/profile/games/game-1/analysis",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"gameId": "game-1", "status": "complete"})
        enqueue_analysis.assert_not_called()
        set_status.assert_not_called()

    def test_profile_game_analysis_rejects_old_analysis_schema(self):
        app.config["AUTH_REQUIRED"] = True
        source = {
            "analysis_status": "not_requested",
            "analysis_error": None,
            "analysis_available": False,
            "analysis_unavailable_reason": app_module.supabase_store.MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE,
            "moves": [{"id": 10}],
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_analysis_source", return_value=source),
            patch.object(app_module, "enqueue_move_analysis") as enqueue_analysis,
        ):
            response = app.test_client().post(
                "/api/profile/games/game-1/analysis",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "move_analysis_schema_update_required")
        enqueue_analysis.assert_not_called()

    def test_profile_game_analysis_rejects_reconstructed_move_before_queueing(self):
        app.config["AUTH_REQUIRED"] = True
        source = {
            "analysis_status": "not_requested",
            "analysis_error": None,
            "analysis_available": True,
            "moves": [{"move_number": 2, "reconstructed": True}],
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_analysis_source", return_value=source),
            patch.object(app_module, "enqueue_move_analysis") as enqueue_analysis,
        ):
            response = app.test_client().post(
                "/api/profile/games/game-1/analysis",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["code"], "incomplete_move_history")
        enqueue_analysis.assert_not_called()

    def test_failed_profile_game_analysis_can_be_retried(self):
        app.config["AUTH_REQUIRED"] = True
        app_module.reserve_ai_search_slot()
        source = {
            "analysis_status": "failed",
            "analysis_error": "worker stopped",
            "moves": [{"id": 10, "player_number": 1, "column_played": 3, "board_before": []}],
        }

        with (
            patch.object(app_module, "verify_access_token", return_value=({"profile_id": "user-123"}, None)),
            patch.object(app_module.supabase_store, "fetch_game_analysis_source", return_value=source),
            patch.object(app_module.supabase_store, "set_game_analysis_status", return_value=True) as set_status,
        ):
            response = app.test_client().post(
                "/api/profile/games/game-1/analysis",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "queued")
        set_status.assert_called_once_with("game-1", "processing")


if __name__ == "__main__":
    unittest.main()
