import time
import unittest
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


if __name__ == "__main__":
    unittest.main()
