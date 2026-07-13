import os
import unittest
from unittest.mock import patch

import app as app_module


class BackendConfigTests(unittest.TestCase):
    def test_get_env_value_uses_default_for_blank_value(self):
        with patch.dict(os.environ, {"BACKEND_HOST": ""}):
            self.assertEqual(app_module.get_env_value("BACKEND_HOST", "localhost"), "localhost")

    def test_get_env_int_uses_default_for_invalid_value(self):
        with patch.dict(os.environ, {"BACKEND_PORT": "not-a-port"}):
            self.assertEqual(app_module.get_env_int("BACKEND_PORT", 5000), 5000)

    def test_get_env_bool_reads_auth_required_flag(self):
        with patch.dict(os.environ, {"AUTH_REQUIRED": "false"}):
            self.assertFalse(app_module.get_env_bool("AUTH_REQUIRED", True))

        with patch.dict(os.environ, {"AUTH_REQUIRED": "true"}):
            self.assertTrue(app_module.get_env_bool("AUTH_REQUIRED", False))

    def test_cors_origins_support_comma_separated_values(self):
        origins = "http://localhost:5173, https://connect4.example.com"
        with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": origins}):
            self.assertEqual(
                app_module.get_cors_origins(),
                ["http://localhost:5173", "https://connect4.example.com"],
            )


if __name__ == "__main__":
    unittest.main()
