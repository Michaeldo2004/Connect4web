import unittest

from app import app, games, socketio


def find_event(client, event_name):
    for event in client.get_received():
        if event["name"] == event_name:
            return event["args"][0]
    return None


class SocketGameTests(unittest.TestCase):
    def setUp(self):
        games.clear()
        self.client = socketio.test_client(app)

    def tearDown(self):
        self.client.disconnect()
        games.clear()

    def create_game(self, difficulty="very_easy"):
        self.client.emit("create_game", {"difficulty": difficulty})
        payload = find_event(self.client, "game_created")
        self.assertIsNotNone(payload)
        return payload

    def test_socket_connects(self):
        self.assertTrue(self.client.is_connected())

    def test_create_game_returns_game_and_player_ids(self):
        payload = self.create_game()
        self.assertIn("gameId", payload)
        self.assertIn("playerId", payload)
        self.assertEqual(payload["status"], "playing")
        self.assertEqual(payload["difficulty"], "very_easy")
        self.assertEqual(sum(cell != 0 for row in payload["board"] for cell in row), 0)

    def test_join_game_with_matching_player_id_succeeds(self):
        created = self.create_game()
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_game", {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
            })
            joined = find_event(second_client, "game_joined")
            self.assertIsNotNone(joined)
            self.assertEqual(joined["gameId"], created["gameId"])
            self.assertEqual(joined["status"], "playing")
        finally:
            second_client.disconnect()

    def test_join_game_with_wrong_player_id_rejected(self):
        created = self.create_game()
        self.client.emit("join_game", {
            "gameId": created["gameId"],
            "playerId": "wrong-player",
        })
        rejected = find_event(self.client, "join_rejected")
        self.assertIsNotNone(rejected)
        self.assertEqual(rejected["gameId"], created["gameId"])

    def test_player_move_updates_board(self):
        created = self.create_game()
        self.client.emit("player_move", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
            "column": 3,
        })
        updated = find_event(self.client, "board_updated")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "playing")
        self.assertEqual(sum(cell != 0 for row in updated["board"] for cell in row), 2)

    def test_invalid_move_rejected(self):
        created = self.create_game()
        self.client.emit("player_move", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
            "column": 8,
        })
        invalid = find_event(self.client, "invalid_move")
        self.assertIsNotNone(invalid)
        self.assertEqual(invalid["status"], "invalid_move")
        self.assertEqual(invalid["message"], "Column out of range")

    def test_reset_game_clears_board(self):
        created = self.create_game()
        self.client.emit("player_move", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
            "column": 3,
        })
        find_event(self.client, "board_updated")

        self.client.emit("reset_game", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
            "difficulty": "hard",
        })
        reset = find_event(self.client, "board_updated")
        self.assertIsNotNone(reset)
        self.assertEqual(reset["status"], "playing")
        self.assertEqual(reset["difficulty"], "hard")
        self.assertEqual(sum(cell != 0 for row in reset["board"] for cell in row), 0)


if __name__ == "__main__":
    unittest.main()
