import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt

import app as app_module
from app import app, games, socketio


class AuthLayerTests(unittest.TestCase):
    def tearDown(self):
        app.config.pop("AUTH_REQUIRED", None)
        games.clear()

    def make_token(self, user_id="user-123", secret="test-secret"):
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
        with patch.object(app_module, "SUPABASE_JWT_SECRET", "test-secret"):
            auth_context, error = app_module.verify_access_token(self.make_token())

        self.assertIsNone(error)
        self.assertEqual(auth_context["profile_id"], "user-123")
        self.assertEqual(auth_context["email"], "player@example.com")

    def test_verify_access_token_rejects_missing_token(self):
        app.config["AUTH_REQUIRED"] = True
        with patch.object(app_module, "SUPABASE_JWT_SECRET", "test-secret"):
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
            with patch.object(app_module, "SUPABASE_JWT_SECRET", "test-secret"):
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


if __name__ == "__main__":
    unittest.main()
