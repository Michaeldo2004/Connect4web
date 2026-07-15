import threading
import unittest
import uuid
from unittest.mock import call, patch

import app as app_module
from app import app, games, socketio


def find_event(client, event_name):
    for event in client.get_received():
        if event["name"] == event_name:
            return event["args"][0]
    return None


def find_events(client, event_name):
    return [event["args"][0] for event in client.get_received() if event["name"] == event_name]


def filter_events(events, event_name):
    return [event["args"][0] for event in events if event["name"] == event_name]


class SocketGameTests(unittest.TestCase):
    def setUp(self):
        app_module.DISCONNECT_GRACE_SECONDS = 0.01
        app.config["AI_SEARCH_INLINE"] = True
        app.config["AUTH_REQUIRED"] = False
        games.clear()
        app_module.reset_ai_job_queue()
        app_module.reset_ai_admission_queue()
        app_module.create_attempts.clear()
        self.supabase_execute_patch = patch.object(app_module.supabase_store, "execute_safely", return_value=False)
        self.supabase_execute_patch.start()
        self.room_claim_patch = patch.object(
            app_module.supabase_store,
            "claim_multiplayer_room_request",
            return_value={"result": "disabled"},
        )
        self.room_fetch_patch = patch.object(
            app_module.supabase_store,
            "fetch_multiplayer_room_request",
            return_value={"result": "disabled"},
        )
        self.room_resolve_patch = patch.object(
            app_module.supabase_store,
            "resolve_multiplayer_room_request",
            return_value={"result": "disabled"},
        )
        self.room_claim_mock = self.room_claim_patch.start()
        self.room_fetch_mock = self.room_fetch_patch.start()
        self.room_resolve_mock = self.room_resolve_patch.start()
        self.client = socketio.test_client(app)

    def tearDown(self):
        if self.client.is_connected():
            self.client.disconnect()
        socketio.sleep(0.02)
        games.clear()
        app_module.reset_ai_job_queue()
        app_module.reset_ai_admission_queue()
        app_module.create_attempts.clear()
        app.config["AI_SEARCH_INLINE"] = False
        app.config.pop("AUTH_REQUIRED", None)
        app_module.DISCONNECT_GRACE_SECONDS = 15
        self.room_resolve_patch.stop()
        self.room_fetch_patch.stop()
        self.room_claim_patch.stop()
        self.supabase_execute_patch.stop()

    def create_game(self, difficulty="very_easy"):
        self.client.emit("create_game", {"difficulty": difficulty})
        payload = find_event(self.client, "game_created")
        self.assertIsNotNone(payload)
        return payload

    def durable_room_record(self, profile_id, request_id, state="active", game_status="waiting", created=False):
        return {
            "profile_id": profile_id,
            "request_id": request_id,
            "game_id": str(uuid.uuid4()),
            "player_id": str(uuid.uuid4()),
            "owner_name": "Durable Player",
            "state": state,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "resolved_at": None,
            "game_mode": "multiplayer",
            "game_status": game_status,
            "player_count": 1,
            "owner_profile_id": profile_id,
            "created": created,
        }

    def test_socket_connects(self):
        self.assertTrue(self.client.is_connected())

    def test_api_move_endpoint_is_removed(self):
        response = app.test_client().post(
            "/api/move",
            json={
                "board": [[0, 0, 0, 0, 0, 0, 0] for _ in range(6)],
                "column": 3,
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_create_game_returns_game_and_player_ids(self):
        payload = self.create_game()
        self.assertIn("gameId", payload)
        self.assertIn("playerId", payload)
        self.assertEqual(payload["status"], "playing")
        self.assertEqual(payload["difficulty"], "very_easy")
        self.assertEqual(payload["currentPlayer"], 1)
        self.assertIn(payload["playerNumber"], [1, 2])
        self.assertIn(payload["aiNumber"], [1, 2])
        self.assertNotEqual(payload["playerNumber"], payload["aiNumber"])
        self.assertEqual(sum(cell != 0 for row in payload["board"] for cell in row), 0)
        self.assertIsNone(payload["aiMove"])

    def test_ai_game_creation_enters_room_before_opening_ai_move(self):
        for starter in [1, 2]:
            games.clear()
            with patch("app.random.choice", return_value=starter):
                self.client.emit("create_game", {"difficulty": "very_easy"})

            events = self.client.get_received()
            created = filter_events(events, "game_created")[0]
            updates = filter_events(events, "board_updated")
            self.assertEqual(created["currentPlayer"], 1)
            self.assertEqual(created["playerNumber"], 1 if starter == 1 else 2)
            self.assertEqual(created["aiNumber"], 2 if starter == 1 else 1)
            self.assertEqual(sum(cell != 0 for row in created["board"] for cell in row), 0)
            self.assertIsNone(created["aiMove"])

            if starter == 1:
                self.assertEqual(created["message"], "Your turn")
                self.assertEqual(updates, [])
            else:
                self.assertEqual(created["message"], "AI is thinking")
                self.assertEqual(len(updates), 1)
                self.assertEqual(updates[0]["currentPlayer"], 2)
                self.assertEqual(updates[0]["playerNumber"], 2)
                self.assertEqual(updates[0]["aiNumber"], 1)
                self.assertEqual(updates[0]["message"], "Your turn")
                self.assertIsNotNone(updates[0]["aiMove"])
                self.assertEqual(sum(cell != 0 for row in updates[0]["board"] for cell in row), 1)
                self.assertEqual(sum(cell == 1 for row in updates[0]["board"] for cell in row), 1)
                self.assertEqual(games[created["gameId"]]["move_number"], 1)

    def test_ai_game_random_start_payload_stays_valid(self):
        for _ in range(8):
            self.client.emit("create_game", {"difficulty": "very_easy"})
            events = self.client.get_received()
            created = filter_events(events, "game_created")[0]
            updates = filter_events(events, "board_updated")
            self.assertEqual(created["currentPlayer"], 1)
            self.assertEqual(sum(cell != 0 for row in created["board"] for cell in row), 0)
            if created["aiNumber"] == 2:
                self.assertEqual(updates, [])
            else:
                self.assertEqual(len(updates), 1)
                self.assertEqual(updates[0]["currentPlayer"], 2)
                self.assertEqual(
                    updates[0]["aiMove"],
                    next(
                        column_index
                        for row in updates[0]["board"]
                        for column_index, cell in enumerate(row)
                        if cell == 1
                    ),
                )

    def test_ai_move_rejected_when_not_human_turn(self):
        created = self.create_game()
        game = games[created["gameId"]]
        game["current_player"] = game["ai_piece"]
        self.client.emit(
            "player_move",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "column": 3,
            },
        )
        invalid = find_event(self.client, "invalid_move")
        self.assertIsNotNone(invalid)
        self.assertEqual(invalid["message"], "Not your turn")

    def test_full_ai_queue_rejects_nonterminal_human_move_without_mutating_the_game(self):
        with patch("app.random.choice", return_value=1):
            created = self.create_game()
        game = games[created["gameId"]]

        with patch.object(app_module, "reserve_ai_search_slot", return_value=False):
            self.client.emit(
                "player_move",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                    "column": 3,
                },
            )

        invalid = find_event(self.client, "invalid_move")
        self.assertIsNotNone(invalid)
        self.assertEqual(invalid["message"], "AI queue is full, try again")
        self.assertEqual(sum(cell != 0 for row in game["board"] for cell in row), 0)
        self.assertEqual(game["move_number"], 0)
        self.assertFalse(game["ai_thinking"])

    def test_ai_queue_allows_three_waiting_jobs_in_fifo_order(self):
        with patch.object(app_module, "AI_WORKER_COUNT", 1):
            active_reservation = app_module.reserve_ai_search_slot()
            queued_reservations = [app_module.reserve_ai_search_slot() for _ in range(3)]
            rejected_reservation = app_module.reserve_ai_search_slot()

            self.assertEqual(active_reservation["state"], "active")
            self.assertEqual([reservation["position"] for reservation in queued_reservations], [1, 2, 3])
            self.assertIsNone(rejected_reservation)

            jobs = [
                {"game_id": f"queued-{index}", "reservation": reservation}
                for index, reservation in enumerate(queued_reservations, start=1)
            ]
            with (
                patch.object(app_module, "queued_game_payload", return_value={}),
                patch.object(app_module.socketio, "emit"),
                patch.object(app_module, "start_reserved_ai_job") as start_job,
            ):
                for job in jobs:
                    app_module.launch_ai_turn(job)
                app_module.finish_ai_job()

        start_job.assert_called_once_with(jobs[0])
        self.assertEqual([job["game_id"] for job in app_module.ai_job_queue], ["queued-2", "queued-3"])

    def test_live_moves_run_before_queued_move_analysis(self):
        active = app_module.reserve_ai_search_slot()
        self.assertEqual(active["state"], "active")

        with (
            patch.object(app_module, "start_move_analysis_job") as start_analysis,
            patch.object(app_module, "start_reserved_ai_job") as start_live,
            patch.object(app_module, "queued_game_payload", return_value={}),
        ):
            analysis = app_module.enqueue_move_analysis("analysis-1", [])
            live_reservation = app_module.reserve_ai_search_slot()
            live_job = {"game_id": "live-1", "reservation": live_reservation}
            app_module.launch_ai_turn(live_job)

            app_module.finish_ai_job()
            start_live.assert_called_once_with(live_job)
            start_analysis.assert_not_called()

            app_module.finish_ai_job()
            start_analysis.assert_called_once_with(analysis)

    def test_running_move_analysis_is_non_preemptive(self):
        with (
            patch.object(app_module, "start_move_analysis_job") as start_analysis,
            patch.object(app_module, "start_reserved_ai_job") as start_live,
            patch.object(app_module, "queued_game_payload", return_value={}),
        ):
            analysis = app_module.enqueue_move_analysis("analysis-1", [])
            start_analysis.assert_called_once_with(analysis)
            self.assertEqual(app_module.ai_running_job_type, "move_analysis")

            reservation = app_module.reserve_ai_search_slot()
            self.assertEqual(reservation["state"], "queued")
            live_job = {"game_id": "live-1", "reservation": reservation}
            app_module.launch_ai_turn(live_job)
            start_live.assert_not_called()

            app_module.finish_ai_job()
            start_live.assert_called_once_with(live_job)

    def test_move_analysis_remains_exclusive_when_more_workers_are_configured(self):
        with patch.object(app_module, "AI_WORKER_COUNT", 2), patch.object(app_module, "start_move_analysis_job"):
            app_module.enqueue_move_analysis("analysis-1", [])
            reservation = app_module.reserve_ai_search_slot()

        self.assertEqual(reservation["state"], "queued")

    def test_analysis_allows_all_four_admitted_games_to_wait_for_live_moves(self):
        with patch.object(app_module, "start_move_analysis_job"):
            app_module.enqueue_move_analysis("analysis-1", [])
            reservations = [app_module.reserve_ai_search_slot() for _ in range(app_module.AI_ADMISSION_CAPACITY)]
            rejected = app_module.reserve_ai_search_slot()

        self.assertTrue(all(reservation["state"] == "queued" for reservation in reservations))
        self.assertIsNone(rejected)

    def test_waiting_admissions_run_before_move_analysis(self):
        with patch.object(app_module, "AI_ADMISSION_CAPACITY", 0):
            admitted, entry, position = app_module.request_ai_admission("profile-1", "medium")
        self.assertFalse(admitted)
        self.assertEqual(position, 1)

        with patch.object(app_module, "start_move_analysis_job") as start_analysis:
            analysis = app_module.enqueue_move_analysis("analysis-1", [])
            start_analysis.assert_not_called()
            self.assertEqual(analysis["state"], "queued")

            self.assertTrue(app_module.cancel_ai_admission("profile-1", entry["queue_id"]))
            start_analysis.assert_called_once_with(analysis)

    def test_move_analysis_builds_scores_and_ratings(self):
        moves = [
            {
                "id": 10,
                "player_number": 1,
                "column_played": 2,
                "board_before": [[0 for _ in range(7)] for _ in range(6)],
            }
        ]
        with patch.object(app_module, "get_move_scores", return_value=(3, 0, {0: -50, 2: 10, 3: 200})):
            rows = app_module.run_move_analysis(moves, 4, 30)

        self.assertEqual(
            rows,
            [
                {
                    "move_id": 10,
                    "minimax_depth": 4,
                    "played_column": 2,
                    "best_column": 3,
                    "worst_column": 0,
                    "played_score": 10,
                    "best_score": 200,
                    "worst_score": -50,
                    "rating": "ok",
                }
            ],
        )

    def test_move_analysis_rating_rules_use_best_then_worst_precedence(self):
        self.assertEqual(app_module.analysis_rating(50, 50, -100), "great")
        self.assertEqual(app_module.analysis_rating(0, 0, 0), "great")
        self.assertEqual(app_module.analysis_rating(-100, 50, -100), "blunder")
        self.assertEqual(app_module.analysis_rating(-10, 50, -100), "mistake")
        self.assertEqual(app_module.analysis_rating(10, 50, -100), "ok")

    def test_move_analysis_fails_instead_of_completing_with_skipped_moves(self):
        reconstructed_move = {
            "move_number": 2,
            "player_number": 2,
            "column_played": 4,
            "board_before": [[0 for _ in range(7)] for _ in range(6)],
            "reconstructed": True,
        }

        with patch.object(app_module, "get_move_scores") as get_scores:
            with self.assertRaisesRegex(ValueError, "Move 2 could not be analyzed"):
                app_module.run_move_analysis([reconstructed_move], 4, 30)

        get_scores.assert_not_called()

    def test_move_analysis_marks_failed_when_complete_status_cannot_persist(self):
        job = {
            "game_id": "analysis-1",
            "moves": [{"id": 10}],
            "minimax_depth": 4,
            "time_limit": 30,
        }
        app_module.analysis_jobs_by_game[job["game_id"]] = job

        with (
            patch.object(app_module, "run_move_analysis", return_value=[{"move_id": 10}]),
            patch.object(
                app_module.supabase_store,
                "replace_move_analysis",
                return_value=True,
            ),
            patch.object(
                app_module.supabase_store,
                "set_game_analysis_status",
                side_effect=[False, True],
            ) as set_status,
            patch.object(app_module, "finish_ai_job"),
        ):
            app_module.complete_move_analysis(job)

        self.assertEqual(
            set_status.call_args_list,
            [
                call("analysis-1", "complete"),
                call("analysis-1", "failed", "Could not complete move analysis"),
            ],
        )
        self.assertNotIn("analysis-1", app_module.analysis_jobs_by_game)

    def test_ai_admission_waiting_room_promotes_front_player(self):
        with patch.object(app_module, "AI_ADMISSION_CAPACITY", 0):
            self.client.emit("create_game", {"difficulty": "medium"})
            waiting = find_event(self.client, "ai_waiting")

        self.assertIsNotNone(waiting)
        self.assertEqual(waiting["position"], 1)
        self.assertEqual(waiting["checkIntervalSeconds"], 20)
        self.assertNotIn("gameId", waiting)

        with patch.object(app_module, "AI_ADMISSION_CAPACITY", 1), patch("app.random.choice", return_value=1):
            self.client.emit(
                "check_ai_waiting",
                {
                    "queueId": waiting["queueId"],
                    "difficulty": "medium",
                },
            )
            created = find_event(self.client, "game_created")

        self.assertIsNotNone(created)
        self.assertEqual(created["difficulty"], "medium")
        self.assertEqual(len(app_module.ai_admission_queue), 0)

    def test_ai_admission_queue_is_unbounded_and_reports_positions(self):
        with patch.object(app_module, "AI_ADMISSION_CAPACITY", 0):
            positions = [app_module.request_ai_admission(f"profile-{index}", "easy")[2] for index in range(1, 11)]
        self.assertEqual(positions, list(range(1, 11)))

    def test_stale_ai_result_is_discarded_after_game_version_changes(self):
        with patch("app.random.choice", return_value=1):
            created = self.create_game()
        game_id = created["gameId"]
        game = games[game_id]

        with app_module.get_game_lock(game_id):
            job, error = app_module.apply_human_and_ai_move(game, 3, game_id)
            self.assertIsNone(error)
            self.assertIsNotNone(job)
            self.assertTrue(game["ai_thinking"])
            game["move_number"] += 1

        with patch.object(app_module, "get_ai_move", return_value=((2, {}), True)):
            app_module.complete_ai_turn(job)

        self.assertEqual(sum(cell != 0 for row in game["board"] for cell in row), 1)
        self.assertTrue(game["ai_thinking"])

    def test_ai_search_runs_without_holding_the_game_lock(self):
        with patch("app.random.choice", return_value=1):
            created = self.create_game()
        game_id = created["gameId"]
        game = games[game_id]
        search_started = threading.Event()
        allow_search_to_finish = threading.Event()

        def blocking_search(*_args):
            search_started.set()
            allow_search_to_finish.wait(timeout=1)
            return (2, {}), True

        app.config["AI_SEARCH_INLINE"] = False
        try:
            with app_module.get_game_lock(game_id):
                job, error = app_module.apply_human_and_ai_move(game, 3, game_id)
                self.assertIsNone(error)
                self.assertIsNotNone(job)

            with patch.object(app_module, "get_ai_move", side_effect=blocking_search):
                app_module.launch_ai_turn(job)
                self.assertTrue(search_started.wait(timeout=1))
                game_lock = app_module.get_game_lock(game_id)
                self.assertTrue(game_lock.acquire(blocking=False))
                game_lock.release()
                allow_search_to_finish.set()
                socketio.sleep(0.02)
        finally:
            app.config["AI_SEARCH_INLINE"] = True

    def test_ai_reset_current_player_matches_opening_board(self):
        created = self.create_game()
        current_game_id = created["gameId"]
        current_player_id = created["playerId"]
        for _ in range(8):
            self.client.emit(
                "reset_game",
                {
                    "gameId": current_game_id,
                    "playerId": current_player_id,
                    "difficulty": "very_easy",
                },
            )
            updates = find_events(self.client, "board_updated")
            reset = updates[0]
            self.assertNotEqual(reset["gameId"], current_game_id)
            self.assertEqual(reset["playerId"], current_player_id)
            self.assertNotIn(current_game_id, games)
            self.assertIn(reset["gameId"], games)
            current_game_id = reset["gameId"]
            pieces = sum(cell != 0 for row in reset["board"] for cell in row)
            self.assertEqual(reset["currentPlayer"], 1)
            self.assertEqual(pieces, 0)
            self.assertIsNone(reset["aiMove"])
            if reset["aiNumber"] == 1:
                self.assertEqual(reset["message"], "AI is thinking")
                self.assertEqual(len(updates), 2)
                self.assertEqual(updates[1]["currentPlayer"], 2)
                self.assertIsNotNone(updates[1]["aiMove"])
                self.assertEqual(sum(cell == 1 for row in updates[1]["board"] for cell in row), 1)
            else:
                self.assertEqual(reset["message"], "Your turn")
                self.assertEqual(len(updates), 1)

    def test_join_game_with_matching_player_id_succeeds(self):
        created = self.create_game()
        second_client = socketio.test_client(app)
        try:
            second_client.emit(
                "join_game",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                },
            )
            joined = find_event(second_client, "game_joined")
            self.assertIsNotNone(joined)
            self.assertEqual(joined["gameId"], created["gameId"])
            self.assertEqual(joined["status"], "playing")
        finally:
            second_client.disconnect()

    def test_join_game_with_wrong_player_id_rejected(self):
        created = self.create_game()
        self.client.emit(
            "join_game",
            {
                "gameId": created["gameId"],
                "playerId": "wrong-player",
            },
        )
        rejected = find_event(self.client, "join_rejected")
        self.assertIsNotNone(rejected)
        self.assertEqual(rejected["gameId"], created["gameId"])

    def test_player_move_updates_board(self):
        created = self.create_game()
        games[created["gameId"]]["current_player"] = games[created["gameId"]]["human_piece"]
        self.client.emit(
            "player_move",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "column": 3,
            },
        )
        updated = find_event(self.client, "board_updated")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "playing")
        self.assertIn(sum(cell != 0 for row in updated["board"] for cell in row), [2, 3])

    def test_invalid_move_rejected(self):
        created = self.create_game()
        self.client.emit(
            "player_move",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "column": 8,
            },
        )
        invalid = find_event(self.client, "invalid_move")
        self.assertIsNotNone(invalid)
        self.assertEqual(invalid["status"], "invalid_move")
        self.assertEqual(invalid["message"], "Column out of range")

    def test_ai_leave_removes_game(self):
        created = self.create_game()
        self.client.emit(
            "leave_game",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
            },
        )
        left = find_event(self.client, "game_left")
        self.assertIsNotNone(left)
        self.assertNotIn(created["gameId"], games)

    def test_ai_disconnect_preserves_game_for_refresh_rejoin(self):
        created = self.create_game()
        self.client.disconnect()
        socketio.sleep(0.02)
        self.assertIn(created["gameId"], games)
        self.assertIsNone(games[created["gameId"]]["socket_id"])

        reconnecting_client = socketio.test_client(app)
        try:
            reconnecting_client.emit(
                "join_game",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                },
            )
            joined = find_event(reconnecting_client, "game_joined")
            self.assertIsNotNone(joined)
            self.assertEqual(joined["gameId"], created["gameId"])
            self.assertEqual(joined["playerId"], created["playerId"])
            self.assertIsNotNone(games[created["gameId"]]["socket_id"])
        finally:
            reconnecting_client.disconnect()

    def test_ai_worker_failure_unlocks_the_human_turn(self):
        with patch("app.random.choice", return_value=1):
            created = self.create_game()
        game = games[created["gameId"]]
        with app_module.get_game_lock(created["gameId"]):
            job, error = app_module.apply_human_and_ai_move(game, 3, created["gameId"])
        self.assertIsNone(error)
        self.assertTrue(game["ai_thinking"])

        with patch.object(app_module, "get_ai_move", side_effect=RuntimeError("worker failed")):
            app_module.complete_ai_turn(job)

        self.assertFalse(game["ai_thinking"])
        self.assertEqual(game["current_player"], game["human_piece"])
        self.assertEqual(game["message"], "AI move failed. Your turn")

    def test_reset_game_clears_board(self):
        created = self.create_game()
        self.client.emit(
            "player_move",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "column": 3,
            },
        )
        find_event(self.client, "board_updated")

        self.client.emit(
            "reset_game",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "difficulty": "hard",
            },
        )
        reset = find_event(self.client, "board_updated")
        self.assertIsNotNone(reset)
        self.assertNotEqual(reset["gameId"], created["gameId"])
        self.assertEqual(reset["playerId"], created["playerId"])
        self.assertNotIn(created["gameId"], games)
        self.assertIn(reset["gameId"], games)
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

    def test_multiplayer_create_ack_contains_created_game(self):
        ack = self.client.emit(
            "create_multiplayer_game",
            {"requestId": "create-request-1"},
            callback=True,
        )
        event = find_event(self.client, "multiplayer_game_created")

        self.assertTrue(ack["ok"])
        self.assertFalse(ack["recovered"])
        self.assertEqual(ack["requestId"], "create-request-1")
        self.assertEqual(ack["gameId"], event["gameId"])
        self.assertEqual(ack["playerId"], event["playerId"])
        self.assertEqual(ack["status"], "waiting")
        self.assertEqual(ack["mode"], "multiplayer")

    def test_multiplayer_create_uses_atomic_durable_claim(self):
        profile_id = str(uuid.uuid4())
        room = self.durable_room_record(profile_id, "durable-create", created=True)
        app.config["AUTH_REQUIRED"] = True

        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": "durable@example.com"}, None),
            ),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "not_found"},
            ),
            patch.object(
                app_module.supabase_store,
                "claim_multiplayer_room_request",
                return_value={"result": "ok", "room": room},
            ) as claim,
            patch.object(app_module.supabase_store, "create_game_record") as legacy_create,
        ):
            ack = self.client.emit(
                "create_multiplayer_game",
                {"requestId": room["request_id"], "accessToken": "token", "ownerName": room["owner_name"]},
                callback=True,
            )

        self.assertTrue(ack["ok"])
        self.assertFalse(ack["recovered"])
        self.assertEqual(ack["gameId"], room["game_id"])
        self.assertEqual(ack["playerId"], room["player_id"])
        self.assertEqual(len(games), 1)
        claim.assert_called_once()
        legacy_create.assert_not_called()

    def test_multiplayer_create_rejects_transient_durable_store_failure(self):
        profile_id = str(uuid.uuid4())
        app.config["AUTH_REQUIRED"] = True
        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": None}, None),
            ),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "error", "code": "persistence_unavailable"},
            ),
            patch.object(app_module.supabase_store, "claim_multiplayer_room_request") as claim,
        ):
            ack = self.client.emit(
                "create_multiplayer_game",
                {"requestId": "transient-failure", "accessToken": "token"},
                callback=True,
            )

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["code"], "persistence_unavailable")
        event = find_event(self.client, "create_rejected")
        self.assertEqual(event["code"], "persistence_unavailable")
        self.assertEqual(event["requestId"], "transient-failure")
        self.assertEqual(games, {})
        claim.assert_not_called()

    def test_multiplayer_create_does_not_reuse_terminal_claim_result(self):
        profile_id = str(uuid.uuid4())
        room = self.durable_room_record(profile_id, "terminal-race", state="completed")
        app.config["AUTH_REQUIRED"] = True
        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": None}, None),
            ),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "not_found"},
            ),
            patch.object(
                app_module.supabase_store,
                "claim_multiplayer_room_request",
                return_value={"result": "ok", "room": room},
            ),
            patch.object(app_module.supabase_store, "create_game_record") as legacy_create,
        ):
            ack = self.client.emit(
                "create_multiplayer_game",
                {"requestId": room["request_id"], "accessToken": "token"},
                callback=True,
            )

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["code"], "creation_request_terminal")
        self.assertEqual(games, {})
        legacy_create.assert_not_called()

    def test_multiplayer_create_falls_back_when_recovery_schema_is_missing(self):
        profile_id = str(uuid.uuid4())
        app.config["AUTH_REQUIRED"] = True
        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": None}, None),
            ),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "schema_missing"},
            ),
            patch.object(app_module.supabase_store, "create_game_record", return_value=True) as legacy_create,
        ):
            ack = self.client.emit(
                "create_multiplayer_game",
                {"requestId": "missing-schema", "accessToken": "token"},
                callback=True,
            )

        self.assertTrue(ack["ok"])
        self.assertEqual(len(games), 1)
        legacy_create.assert_called_once()

    def test_reconcile_restores_durable_waiting_room_then_join_binds_socket(self):
        profile_id = str(uuid.uuid4())
        room = self.durable_room_record(profile_id, "restart-recovery")
        app.config["AUTH_REQUIRED"] = True

        def authenticate(_data):
            return {"profile_id": profile_id, "email": "durable@example.com"}, None

        with (
            patch.object(app_module, "authenticate_payload", side_effect=authenticate),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "ok", "room": room},
            ),
        ):
            ack = self.client.emit(
                "reconcile_multiplayer_creation",
                {"requestId": room["request_id"], "accessToken": "token"},
                callback=True,
            )
            restored_player = games[room["game_id"]]["players"][room["player_id"]]
            self.assertFalse(restored_player["connected"])
            self.assertIsNone(restored_player["socket_id"])

            self.client.emit(
                "join_multiplayer_game",
                {
                    "gameId": room["game_id"],
                    "playerId": room["player_id"],
                    "requestId": room["request_id"],
                    "accessToken": "token",
                },
            )
            joined = find_event(self.client, "multiplayer_game_joined")

        self.assertEqual(
            ack,
            {
                "ok": True,
                "status": "found",
                "requestId": room["request_id"],
                "gameId": room["game_id"],
                "playerId": room["player_id"],
            },
        )
        self.assertEqual(joined["requestId"], room["request_id"])
        self.assertTrue(restored_player["connected"])
        self.assertIsNotNone(restored_player["socket_id"])

    def test_reconcile_rejects_terminal_request_without_reusing_id(self):
        profile_id = str(uuid.uuid4())
        room = self.durable_room_record(profile_id, "cancelled-request", state="cancelled")
        app.config["AUTH_REQUIRED"] = True
        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": None}, None),
            ),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "ok", "room": room},
            ),
        ):
            ack = self.client.emit(
                "reconcile_multiplayer_creation",
                {"requestId": room["request_id"], "accessToken": "token"},
                callback=True,
            )

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["code"], "creation_request_terminal")
        self.assertEqual(games, {})

    def test_reconcile_does_not_recover_in_memory_room_after_it_started(self):
        profile_id = str(uuid.uuid4())
        request_id = "already-started"
        player_id = str(uuid.uuid4())
        game_id = str(uuid.uuid4())
        game = app_module.create_multiplayer_game_state(player_id, profile_id=profile_id, bind_socket=False)
        game.update(
            {
                "status": "playing",
                "creation_request_id": request_id,
                "creator_profile_id": profile_id,
                "creator_player_id": player_id,
            }
        )
        games[game_id] = game
        app.config["AUTH_REQUIRED"] = True

        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": None}, None),
            ),
            patch.object(app_module.supabase_store, "fetch_multiplayer_room_request") as fetch,
        ):
            ack = self.client.emit(
                "reconcile_multiplayer_creation",
                {"requestId": request_id, "accessToken": "token"},
                callback=True,
            )

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["code"], "creation_request_terminal")
        fetch.assert_not_called()
        self.assertFalse(game["players"][player_id]["connected"])

    def test_reconcile_marks_expired_active_request_terminal(self):
        profile_id = str(uuid.uuid4())
        room = self.durable_room_record(profile_id, "expired-request")
        room["expires_at"] = "2000-01-01T00:00:00+00:00"
        app.config["AUTH_REQUIRED"] = True
        with (
            patch.object(
                app_module,
                "authenticate_payload",
                return_value=({"profile_id": profile_id, "email": None}, None),
            ),
            patch.object(
                app_module.supabase_store,
                "fetch_multiplayer_room_request",
                return_value={"result": "ok", "room": room},
            ),
            patch.object(
                app_module.supabase_store,
                "resolve_multiplayer_room_request",
                return_value={"result": "ok", "resolved": True},
            ) as resolve,
        ):
            ack = self.client.emit(
                "reconcile_multiplayer_creation",
                {"requestId": room["request_id"], "accessToken": "token"},
                callback=True,
            )

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["code"], "creation_request_expired")
        resolve.assert_called_once_with(room["game_id"], "expired")
        self.assertEqual(games, {})

    def test_multiplayer_create_retry_rebinds_authenticated_creator(self):
        request_payload = {
            "requestId": "lost-create-response",
            "accessToken": "profile-one-token",
            "ownerName": "Player One",
        }

        def authenticate(data):
            return {"profile_id": data.get("accessToken"), "email": "one@example.com"}, None

        app.config["AUTH_REQUIRED"] = True
        retry_client = None
        with (
            patch.object(app_module, "authenticate_payload", side_effect=authenticate),
            patch.object(app_module.supabase_store, "create_game_record", return_value=False) as create_record,
        ):
            # Simulate a response that the creator never receives. The original
            # socket disconnects before consuming the emitted success event.
            self.client.emit("create_multiplayer_game", request_payload)
            self.assertEqual(len(games), 1)
            original_game_id, original_game = next(iter(games.items()))
            original_player_id = original_game["creator_player_id"]
            original_socket_id = original_game["players"][original_player_id]["socket_id"]
            self.client.disconnect()
            self.assertFalse(original_game["players"][original_player_id]["connected"])

            retry_client = socketio.test_client(app)
            retry_ack = retry_client.emit(
                "create_multiplayer_game",
                request_payload,
                callback=True,
            )
            retry_event = find_event(retry_client, "multiplayer_game_created")
            socketio.emit("recovery_probe", {"gameId": original_game_id}, to=original_game_id)
            recovery_probe = find_event(retry_client, "recovery_probe")
            self.assertEqual(create_record.call_count, 1)

        try:
            self.assertTrue(retry_ack["ok"])
            self.assertTrue(retry_ack["recovered"])
            self.assertEqual(retry_ack["requestId"], request_payload["requestId"])
            self.assertEqual(retry_ack["gameId"], original_game_id)
            self.assertEqual(retry_ack["playerId"], original_player_id)
            self.assertEqual(retry_event["gameId"], original_game_id)
            self.assertEqual(retry_event["playerId"], original_player_id)
            self.assertTrue(retry_event["recovered"])
            self.assertEqual(len(games), 1)
            self.assertTrue(original_game["players"][original_player_id]["connected"])
            self.assertIsNotNone(original_game["players"][original_player_id]["socket_id"])
            self.assertNotEqual(original_game["players"][original_player_id]["socket_id"], original_socket_id)
            self.assertEqual(recovery_probe["gameId"], original_game_id)
        finally:
            if retry_client is not None and retry_client.is_connected():
                retry_client.disconnect()

    def test_multiplayer_create_request_id_is_isolated_by_profile(self):
        def authenticate(data):
            return {"profile_id": data.get("accessToken"), "email": None}, None

        app.config["AUTH_REQUIRED"] = True
        second_client = socketio.test_client(app)
        try:
            with patch.object(app_module, "authenticate_payload", side_effect=authenticate):
                first_ack = self.client.emit(
                    "create_multiplayer_game",
                    {"requestId": "shared-request", "accessToken": "profile-one"},
                    callback=True,
                )
                second_ack = second_client.emit(
                    "create_multiplayer_game",
                    {"requestId": "shared-request", "accessToken": "profile-two"},
                    callback=True,
                )

            self.assertTrue(first_ack["ok"])
            self.assertTrue(second_ack["ok"])
            self.assertFalse(first_ack["recovered"])
            self.assertFalse(second_ack["recovered"])
            self.assertNotEqual(first_ack["gameId"], second_ack["gameId"])
            self.assertNotEqual(first_ack["playerId"], second_ack["playerId"])
            self.assertEqual(len(games), 2)
        finally:
            second_client.disconnect()

    def test_multiplayer_create_rejection_has_useful_ack_and_event(self):
        with patch.object(app_module, "authenticate_payload", return_value=(None, "Invalid session")):
            ack = self.client.emit(
                "create_multiplayer_game",
                {"requestId": "rejected-request", "accessToken": "bad-token"},
                callback=True,
            )
        event = find_event(self.client, "create_rejected")

        self.assertEqual(
            ack,
            {
                "ok": False,
                "requestId": "rejected-request",
                "code": "authentication_failed",
                "message": "Invalid session",
            },
        )
        self.assertEqual(
            event,
            {
                "message": "Invalid session",
                "requestId": "rejected-request",
                "code": "authentication_failed",
            },
        )
        self.assertEqual(games, {})

    def test_multiplayer_create_without_request_id_preserves_legacy_behavior(self):
        first_ack = self.client.emit("create_multiplayer_game", callback=True)
        second_ack = self.client.emit("create_multiplayer_game", callback=True)

        self.assertTrue(first_ack["ok"])
        self.assertTrue(second_ack["ok"])
        self.assertIsNone(first_ack["requestId"])
        self.assertIsNone(second_ack["requestId"])
        self.assertFalse(first_ack["recovered"])
        self.assertFalse(second_ack["recovered"])
        self.assertNotEqual(first_ack["gameId"], second_ack["gameId"])
        self.assertEqual(len(games), 2)

    def test_second_multiplayer_socket_joins_as_player_two(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            self.assertIsNotNone(joined)
            self.assertEqual(joined["gameId"], created["gameId"])
            self.assertIn(joined["playerNumber"], [1, 2])
            self.assertEqual(joined["playersConnected"], 2)
            self.assertEqual(joined["status"], "playing")
            self.assertEqual(joined["currentPlayer"], 1)
            self.assertEqual(joined["message"], "Player 1 turn")
            self.assertEqual(set(joined["playerNames"]), {"1", "2"})
        finally:
            second_client.disconnect()

    def test_public_multiplayer_room_can_be_listed_and_joined_once(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        self.client.emit(
            "set_room_public",
            {
                "gameId": created["gameId"],
                "playerId": created["playerId"],
                "public": True,
            },
        )
        public_update = find_event(self.client, "room_public_updated")
        self.assertTrue(public_update["publicRoom"])

        second_client = socketio.test_client(app)
        third_client = socketio.test_client(app)
        try:
            second_client.emit("list_public_games")
            public_games = find_event(second_client, "public_games")
            self.assertEqual(public_games["games"][0]["gameId"], created["gameId"])

            second_client.emit(
                "join_multiplayer_game",
                {
                    "gameId": created["gameId"],
                    "publicJoin": True,
                },
            )
            joined = find_event(second_client, "multiplayer_game_joined")
            self.assertIsNotNone(joined)
            self.assertFalse(games[created["gameId"]]["public"])

            third_client.emit(
                "join_multiplayer_game",
                {
                    "gameId": created["gameId"],
                    "publicJoin": True,
                },
            )
            rejected = find_event(third_client, "join_rejected")
            self.assertIsNotNone(rejected)
            self.assertEqual(rejected["message"], "Multiplayer game is full")
        finally:
            second_client.disconnect()
            third_client.disconnect()

    def test_room_visibility_changes_are_rate_limited(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        visibility_payload = {
            "gameId": created["gameId"],
            "playerId": created["playerId"],
        }

        self.client.emit("set_room_public", {**visibility_payload, "public": True})
        updated = find_event(self.client, "room_public_updated")
        self.assertTrue(updated["publicRoom"])

        self.client.emit("set_room_public", {**visibility_payload, "public": False})
        rejected = find_event(self.client, "room_public_update_failed")
        self.assertTrue(rejected["publicRoom"])
        self.assertGreater(rejected["retryAfterMs"], 0)

    def test_public_join_rejects_private_waiting_room(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit(
                "join_multiplayer_game",
                {
                    "gameId": created["gameId"],
                    "publicJoin": True,
                },
            )
            rejected = find_event(second_client, "join_rejected")
            self.assertIsNotNone(rejected)
            self.assertEqual(rejected["message"], "Room is no longer public")
            self.assertEqual(len(games[created["gameId"]]["players"]), 1)
        finally:
            second_client.disconnect()

    def test_multiplayer_starter_is_assigned_yellow(self):
        for starter_index in [0, 1]:
            games.clear()
            first_client = socketio.test_client(app)
            second_client = socketio.test_client(app)
            try:
                first_client.emit("create_multiplayer_game")
                created = find_event(first_client, "multiplayer_game_created")
                with patch("app.random.choice", side_effect=lambda player_ids: player_ids[starter_index]):
                    second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
                joined = find_event(second_client, "multiplayer_game_joined")
                self.assertEqual(joined["currentPlayer"], 1)
                self.assertEqual(joined["message"], "Player 1 turn")
                first_update = find_event(first_client, "board_updated")
                self.assertEqual(first_update["currentPlayer"], 1)
                self.assertEqual(first_update["currentPlayer"], joined["currentPlayer"])
                self.assertEqual(first_update["message"], joined["message"])
                self.assertEqual(first_update["playerNumber"], 1 if starter_index == 0 else 2)
                self.assertEqual(joined["playerNumber"], 2 if starter_index == 0 else 1)
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
                self.assertEqual(joined["currentPlayer"], 1)
                self.assertEqual(joined["message"], "Player 1 turn")
            finally:
                first_client.disconnect()
                second_client.disconnect()

    def test_multiplayer_move_updates_both_clients(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            with patch("app.random.choice", return_value=created["playerId"]):
                second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            games[created["gameId"]]["current_player"] = 1
            self.client.get_received()
            second_client.get_received()

            self.client.emit(
                "player_move",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                    "column": 3,
                },
            )
            first_update = find_event(self.client, "board_updated")
            second_update = find_event(second_client, "board_updated")
            self.assertIsNotNone(first_update)
            self.assertIsNotNone(second_update)
            self.assertEqual(first_update["board"], second_update["board"])
            self.assertEqual(first_update["currentPlayer"], 2)
            self.assertEqual(sum(cell != 0 for row in first_update["board"] for cell in row), 1)

            second_client.emit(
                "player_move",
                {
                    "gameId": joined["gameId"],
                    "playerId": joined["playerId"],
                    "column": 4,
                },
            )
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
            with patch("app.random.choice", return_value=created["playerId"]):
                second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            games[created["gameId"]]["current_player"] = 1
            second_client.emit(
                "player_move",
                {
                    "gameId": joined["gameId"],
                    "playerId": joined["playerId"],
                    "column": 3,
                },
            )
            invalid = find_event(second_client, "invalid_move")
            self.assertIsNotNone(invalid)
            self.assertEqual(invalid["message"], "Not your turn")
        finally:
            second_client.disconnect()

    def test_multiplayer_disconnect_defaults_win_after_grace_period(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        with patch("app.random.choice", return_value=created["playerId"]):
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
        with patch("app.random.choice", return_value=created["playerId"]):
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
        joined = find_event(second_client, "multiplayer_game_joined")
        self.client.get_received()

        second_client.disconnect()
        reconnecting_client = socketio.test_client(app)
        try:
            reconnecting_client.emit(
                "join_multiplayer_game",
                {
                    "gameId": joined["gameId"],
                    "playerId": joined["playerId"],
                },
            )
            rejoined = find_event(reconnecting_client, "multiplayer_game_joined")
            self.assertIsNotNone(rejoined)
            self.assertEqual(rejoined["playerNumber"], 2)

            socketio.sleep(0.03)
            game = games[created["gameId"]]
            self.assertEqual(game["status"], "playing")
            self.assertEqual(sum(1 for player in game["players"].values() if player["connected"]), 2)
        finally:
            reconnecting_client.disconnect()

    def test_multiplayer_reconnect_preserves_reassigned_player_number(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            with patch("app.random.choice", return_value=created["playerId"]):
                second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            self.assertEqual(joined["playerNumber"], 2)

            second_client.disconnect()
            reconnecting_client = socketio.test_client(app)
            try:
                reconnecting_client.emit(
                    "join_multiplayer_game",
                    {
                        "gameId": joined["gameId"],
                        "playerId": joined["playerId"],
                    },
                )
                rejoined = find_event(reconnecting_client, "multiplayer_game_joined")
                self.assertEqual(rejoined["playerNumber"], 2)
                self.assertEqual(rejoined["currentPlayer"], 1)
            finally:
                reconnecting_client.disconnect()
        finally:
            if second_client.is_connected():
                second_client.disconnect()

    def test_multiplayer_reset_game_is_rejected_during_live_match(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        second_client = socketio.test_client(app)
        try:
            second_client.emit("join_multiplayer_game", {"gameId": created["gameId"]})
            joined = find_event(second_client, "multiplayer_game_joined")
            self.client.get_received()

            self.client.emit(
                "reset_game",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                },
            )

            invalid = find_event(self.client, "invalid_move")
            self.assertIsNotNone(invalid)
            self.assertEqual(invalid["message"], "Use play again after the multiplayer match ends")
            self.assertIn(created["gameId"], games)
            self.assertEqual(games[created["gameId"]]["status"], "playing")
            self.assertNotIn("board_updated", [event["name"] for event in self.client.get_received()])
            self.assertEqual(joined["gameId"], created["gameId"])
        finally:
            second_client.disconnect()

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

            self.client.emit(
                "play_again",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                },
            )
            vote_update = find_event(second_client, "play_again_updated")
            self.assertIsNotNone(vote_update)
            self.assertEqual(vote_update["playAgainAccepted"], 1)

            second_client.emit(
                "play_again",
                {
                    "gameId": joined["gameId"],
                    "playerId": joined["playerId"],
                },
            )
            reset = find_event(self.client, "board_updated")
            self.assertIsNotNone(reset)
            self.assertNotEqual(reset["gameId"], created["gameId"])
            self.assertNotIn(created["gameId"], games)
            self.assertIn(reset["gameId"], games)
            self.assertEqual(reset["status"], "playing")
            self.assertEqual(reset["currentPlayer"], 1)
            self.assertEqual(reset["message"], "Player 1 turn")
            self.assertEqual(reset["playAgainAccepted"], 0)
            self.assertEqual(sum(cell != 0 for row in reset["board"] for cell in row), 0)
        finally:
            second_client.disconnect()

    def test_multiplayer_play_again_reassigns_yellow_to_starter(self):
        for starter_index in [0, 1]:
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

                first_client.emit(
                    "play_again",
                    {
                        "gameId": created["gameId"],
                        "playerId": created["playerId"],
                    },
                )
                find_event(first_client, "play_again_updated")
                with patch("app.random.choice", side_effect=lambda player_ids: player_ids[starter_index]):
                    second_client.emit(
                        "play_again",
                        {
                            "gameId": joined["gameId"],
                            "playerId": joined["playerId"],
                        },
                    )
                reset = find_event(first_client, "board_updated")
                reset_second = find_event(second_client, "board_updated")
                self.assertNotEqual(reset["gameId"], created["gameId"])
                self.assertEqual(reset_second["gameId"], reset["gameId"])
                self.assertNotIn(created["gameId"], games)
                self.assertIn(reset["gameId"], games)
                self.assertEqual(reset["currentPlayer"], 1)
                self.assertEqual(reset["message"], "Player 1 turn")
                self.assertEqual(reset["playerNumber"], 1 if starter_index == 0 else 2)
                self.assertEqual(reset_second["playerNumber"], 2 if starter_index == 0 else 1)
                self.assertEqual(sum(cell != 0 for row in reset["board"] for cell in row), 0)
            finally:
                first_client.disconnect()
                second_client.disconnect()

    def test_multiplayer_waiting_player_can_leave_room(self):
        self.client.emit("create_multiplayer_game")
        created = find_event(self.client, "multiplayer_game_created")
        with patch.object(
            app_module.supabase_store,
            "resolve_multiplayer_room_request",
            return_value={"result": "ok", "resolved": True},
        ) as resolve:
            self.client.emit(
                "leave_game",
                {
                    "gameId": created["gameId"],
                    "playerId": created["playerId"],
                },
            )
        left = find_event(self.client, "game_left")
        self.assertIsNotNone(left)
        self.assertNotIn(created["gameId"], games)
        resolve.assert_called_once_with(created["gameId"], "cancelled")

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

            second_client.emit(
                "leave_game",
                {
                    "gameId": joined["gameId"],
                    "playerId": joined["playerId"],
                },
            )
            left = find_event(second_client, "game_left")
            other_left = find_event(self.client, "player_left")
            self.assertIsNotNone(left)
            self.assertIsNotNone(other_left)
            self.assertEqual(other_left["message"], f"Player {joined['playerNumber']} left the room")
            self.assertNotIn(created["gameId"], games)
        finally:
            second_client.disconnect()


if __name__ == "__main__":
    unittest.main()
