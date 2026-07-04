import unittest
from unittest.mock import patch

import app as app_module
from app import app, games, socketio


def find_event(client, event_name):
    for event in client.get_received():
        if event["name"] == event_name:
            return event["args"][0]
    return None


def find_events(client, event_name):
    return [event["args"][0] for event in client.get_received() if event["name"] == event_name]


class SocketGameTests(unittest.TestCase):
    def setUp(self):
        app_module.DISCONNECT_GRACE_SECONDS = 0.01
        app.config["AI_SEARCH_INLINE"] = True
        games.clear()
        app_module.create_attempts.clear()
        self.client = socketio.test_client(app)

    def tearDown(self):
        if self.client.is_connected():
            self.client.disconnect()
        socketio.sleep(0.02)
        games.clear()
        app_module.create_attempts.clear()
        app.config["AI_SEARCH_INLINE"] = False
        app_module.DISCONNECT_GRACE_SECONDS = 15

    def create_game(self, difficulty="very_easy"):
        self.client.emit("create_game", {"difficulty": difficulty})
        payload = find_event(self.client, "game_created")
        self.assertIsNotNone(payload)
        return payload

    def test_socket_connects(self):
        self.assertTrue(self.client.is_connected())

    def test_api_move_endpoint_is_removed(self):
        response = app.test_client().post("/api/move", json={
            "board": [[0, 0, 0, 0, 0, 0, 0] for _ in range(6)],
            "column": 3,
        })
        self.assertEqual(response.status_code, 404)

    def test_create_game_returns_game_and_player_ids(self):
        payload = self.create_game()
        self.assertIn("gameId", payload)
        self.assertIn("playerId", payload)
        self.assertEqual(payload["status"], "playing")
        self.assertEqual(payload["difficulty"], "very_easy")
        self.assertIn(payload["currentPlayer"], [1, 2])
        self.assertIn(sum(cell != 0 for row in payload["board"] for cell in row), [0, 1])

    def test_ai_game_current_player_matches_opening_board(self):
        for starter in [1, 2]:
            games.clear()
            with patch("app.random.choice", return_value=starter):
                payload = self.create_game()
            pieces = sum(cell != 0 for row in payload["board"] for cell in row)
            self.assertEqual(payload["currentPlayer"], 1)
            self.assertEqual(pieces, 1 if starter == 2 else 0)
            self.assertEqual(payload["message"], "Your turn")
            self.assertEqual(payload["aiMove"] is not None, starter == 2)

    def test_ai_game_random_start_payload_stays_valid(self):
        for _ in range(8):
            payload = self.create_game()
            pieces = sum(cell != 0 for row in payload["board"] for cell in row)
            self.assertEqual(payload["currentPlayer"], 1)
            self.assertIn(pieces, [0, 1])
            if pieces == 1:
                self.assertEqual(payload["aiMove"], next(
                    column_index
                    for row in payload["board"]
                    for column_index, cell in enumerate(row)
                    if cell == 2
                ))

    def test_ai_move_rejected_when_not_human_turn(self):
        created = self.create_game()
        game = games[created["gameId"]]
        game["current_player"] = 2
        self.client.emit("player_move", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
            "column": 3,
        })
        invalid = find_event(self.client, "invalid_move")
        self.assertIsNotNone(invalid)
        self.assertEqual(invalid["message"], "Not your turn")

    def test_ai_reset_current_player_matches_opening_board(self):
        created = self.create_game()
        for _ in range(8):
            self.client.emit("reset_game", {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "difficulty": "very_easy",
            })
            reset = find_event(self.client, "board_updated")
            pieces = sum(cell != 0 for row in reset["board"] for cell in row)
            self.assertEqual(reset["currentPlayer"], 1)
            self.assertIn(pieces, [0, 1])
            if pieces == 1:
                self.assertIsNotNone(reset["aiMove"])

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
        games[created["gameId"]]["current_player"] = 1
        self.client.emit("player_move", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
            "column": 3,
        })
        updated = find_event(self.client, "board_updated")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "playing")
        self.assertIn(sum(cell != 0 for row in updated["board"] for cell in row), [2, 3])

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

    def test_ai_leave_removes_game(self):
        created = self.create_game()
        self.client.emit("leave_game", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
        })
        left = find_event(self.client, "game_left")
        self.assertIsNotNone(left)
        self.assertNotIn(created["gameId"], games)

    def test_ai_disconnect_removes_game(self):
        created = self.create_game()
        self.client.disconnect()
        socketio.sleep(0.02)
        self.assertNotIn(created["gameId"], games)

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
        self.assertIn(reset["currentPlayer"], [1, 2])
        self.assertIn(sum(cell != 0 for row in reset["board"] for cell in row), [0, 1])

    def test_create_multiplayer_game_returns_player_one(self):
        self.client.emit("create_multiplayer_game")
        payload = find_event(self.client, "multiplayer_game_created")
        self.assertIsNotNone(payload)
        self.assertIn("gameId", payload)
        self.assertIn("playerId", payload)
        self.assertEqual(payload["mode"], "multiplayer")
        self.assertEqual(payload["playerNumber"], 1)
        self.assertEqual(payload["playersConnected"], 1)
        self.assertEqual(payload["status"], "waiting")

    def test_second_multiplayer_socket_joins_as_player_two(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            self.assertIsNotNone(joined)
            self.assertEqual(joined["gameId"], created["gameId"])
            self.assertEqual(joined["playerNumber"], 2)
            self.assertEqual(joined["playersConnected"], 2)
            self.assertEqual(joined["status"], "playing")
            self.assertIn(joined["currentPlayer"], [1, 2])
            self.assertEqual(joined["message"], f"Player {joined['currentPlayer']} turn")
        finally:
            second_client.disconnect()

    def test_multiplayer_randomized_start_payload_is_consistent(self):
        for starter in [1, 2]:
            games.clear()
            first_client = socketio.test_client(app)
            second_client = socketio.test_client(app)
            try:
                with patch("app.random.choice", return_value=starter):
                    first_client.emit("create_multiplayer_game")
                created = find_event(first_client, "multiplayer_game_created")
                self.assertEqual(created["currentPlayer"], starter)
                second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
                joined = find_event(second_client, "multiplayer_game_joined")
                self.assertEqual(joined["currentPlayer"], starter)
                self.assertEqual(joined["message"], f"Player {joined['currentPlayer']} turn")
                first_update = find_event(first_client, "board_updated")
                self.assertEqual(first_update["currentPlayer"], joined["currentPlayer"])
                self.assertEqual(first_update["message"], joined["message"])
            finally:
                first_client.disconnect()
                second_client.disconnect()

    def test_multiplayer_random_start_payload_stays_valid(self):
        for _ in range(8):
            games.clear()
            first_client = socketio.test_client(app)
            second_client = socketio.test_client(app)
            try:
                first_client.emit("create_multiplayer_game")
                created = find_event(first_client, "multiplayer_game_created")
                second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
                joined = find_event(second_client, "multiplayer_game_joined")
                self.assertIn(joined["currentPlayer"], [1, 2])
                self.assertEqual(joined["message"], f"Player {joined['currentPlayer']} turn")
            finally:
                first_client.disconnect()
                second_client.disconnect()

    def test_multiplayer_move_updates_both_clients(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            games[created["gameId"]]["current_player"] = 1
            self.client.get_received()
            second_client.get_received()

            self.client.emit("player_move", {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "column": 3,
            })
            first_update = find_event(self.client, "board_updated")
            second_update = find_event(second_client, "board_updated")
            self.assertIsNotNone(first_update)
            self.assertIsNotNone(second_update)
            self.assertEqual(first_update["board"], second_update["board"])
            self.assertEqual(first_update["currentPlayer"], 2)
            self.assertEqual(sum(cell != 0 for row in first_update["board"] for cell in row), 1)

            second_client.emit("player_move", {
                "gameId": joined["gameId"],
                "playerId": joined["playerId"],
                "column": 4,
            })
            second_move = find_event(self.client, "board_updated")
            self.assertIsNotNone(second_move)
            self.assertEqual(second_move["currentPlayer"], 1)
            self.assertEqual(sum(cell != 0 for row in second_move["board"] for cell in row), 2)
        finally:
            second_client.disconnect()

    def test_multiplayer_rejects_wrong_turn(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            games[created["gameId"]]["current_player"] = 1
            second_client.emit("player_move", {
                "gameId": joined["gameId"],
                "playerId": joined["playerId"],
                "column": 3,
            })
            invalid = find_event(second_client, "invalid_move")
            self.assertIsNotNone(invalid)
            self.assertEqual(invalid["message"], "Not your turn")
        finally:
            second_client.disconnect()

    def test_multiplayer_disconnect_defaults_win_after_grace_period(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
        find_event(second_client, "multiplayer_game_joined")
        self.client.get_received()

        second_client.disconnect()
        socketio.sleep(0.03)

        updates = find_events(self.client, "board_updated")
        self.assertGreaterEqual(len(updates), 2)
        self.assertEqual(updates[-1]["status"], "player1_win")
        self.assertEqual(updates[-1]["message"], "Player 1 wins by default")
        self.assertEqual(updates[-1]["playersConnected"], 1)

    def test_multiplayer_both_disconnects_abandons_game(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
        find_event(second_client, "multiplayer_game_joined")

        self.client.disconnect()
        second_client.disconnect()
        socketio.sleep(0.03)

        game = games[created["gameId"]]
        self.assertEqual(game["status"], "draw")
        self.assertEqual(game["message"], "Game abandoned")
        self.assertEqual(sum(1 for player in game["players"].values() if player["connected"]), 0)

    def test_multiplayer_reconnect_cancels_default_win(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
        joined = find_event(second_client, "multiplayer_game_joined")
        self.client.get_received()

        second_client.disconnect()
        reconnecting_client = socketio.test_client(app)
        try:
            reconnecting_client.emit("join_multiplayer_game", {
                "gameId": joined["gameId"],
                "playerId": joined["playerId"],
            })
            rejoined = find_event(reconnecting_client, "multiplayer_game_joined")
            self.assertIsNotNone(rejoined)
            self.assertEqual(rejoined["playerNumber"], 2)

            socketio.sleep(0.03)
            game = games[created["gameId"]]
            self.assertEqual(game["status"], "playing")
            self.assertEqual(sum(1 for player in game["players"].values() if player["connected"]), 2)
        finally:
            reconnecting_client.disconnect()

    def test_multiplayer_play_again_resets_after_both_players_accept(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            game = games[created["gameId"]]
            game["status"] = "player1_win"
            game["message"] = "Player 1 wins"
            self.client.get_received()
            second_client.get_received()

            self.client.emit("play_again", {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
            })
            vote_update = find_event(second_client, "play_again_updated")
            self.assertIsNotNone(vote_update)
            self.assertEqual(vote_update["playAgainAccepted"], 1)

            second_client.emit("play_again", {
                "gameId": joined["gameId"],
                "playerId": joined["playerId"],
            })
            reset = find_event(self.client, "board_updated")
            self.assertIsNotNone(reset)
            self.assertEqual(reset["status"], "playing")
            self.assertIn(reset["currentPlayer"], [1, 2])
            self.assertEqual(reset["message"], f"Player {reset['currentPlayer']} turn")
            self.assertEqual(reset["playAgainAccepted"], 0)
            self.assertEqual(sum(cell != 0 for row in reset["board"] for cell in row), 0)
        finally:
            second_client.disconnect()

    def test_multiplayer_play_again_randomized_start_payload_is_consistent(self):
        for starter in [1, 2]:
            games.clear()
            first_client = socketio.test_client(app)
            second_client = socketio.test_client(app)
            try:
                first_client.emit("create_multiplayer_game")
                created = find_event(first_client, "multiplayer_game_created")
                second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
                joined = find_event(second_client, "multiplayer_game_joined")
                game = games[created["gameId"]]
                game["status"] = "player2_win"
                game["message"] = "Player 2 wins"
                first_client.get_received()
                second_client.get_received()

                first_client.emit("play_again", {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                })
                find_event(first_client, "play_again_updated")
                with patch("app.random.choice", return_value=starter):
                    second_client.emit("play_again", {
                        "gameId": joined["gameId"],
                        "playerId": joined["playerId"],
                    })
                reset = find_event(first_client, "board_updated")
                self.assertEqual(reset["currentPlayer"], starter)
                self.assertEqual(reset["message"], f"Player {starter} turn")
                self.assertEqual(sum(cell != 0 for row in reset["board"] for cell in row), 0)
            finally:
                first_client.disconnect()
                second_client.disconnect()

    def test_multiplayer_waiting_player_can_leave_room(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        self.client.emit("leave_game", {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
        })
        left = find_event(self.client, "game_left")
        self.assertIsNotNone(left)
        self.assertNotIn(created["gameId"], games)

    def test_multiplayer_finished_leave_notifies_other_player(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            game = games[created["gameId"]]
            game["status"] = "player2_win"
            game["message"] = "Player 2 wins"
            self.client.get_received()
            second_client.get_received()

            second_client.emit("leave_game", {
                "gameId": joined["gameId"],
                "playerId": joined["playerId"],
            })
            left = find_event(second_client, "game_left")
            other_left = find_event(self.client, "player_left")
            self.assertIsNotNone(left)
            self.assertIsNotNone(other_left)
            self.assertEqual(other_left["message"], "Player 2 left the room")
            self.assertNotIn(created["gameId"], games)
        finally:
            second_client.disconnect()


if __name__ == "__main__":
    unittest.main()
