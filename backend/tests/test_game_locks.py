import threading
import time
import unittest

import app as app_module


class GameLockTests(unittest.TestCase):
    def tearDown(self):
        app_module.game_locks.clear()

    def test_get_game_lock_returns_same_semaphore_for_same_game_id(self):
        game_id = "test-game-lock-1"
        lock1 = app_module.get_game_lock(game_id)
        lock2 = app_module.get_game_lock(game_id)

        self.assertIs(lock1, lock2)
        self.assertIn(game_id, app_module.game_locks)
        self.assertEqual(app_module.game_locks[game_id], lock1)

    def test_pop_game_removes_game_lock(self):
        game_id = "test-game-lock-2"
        app_module.store_game(game_id, {"mode": "ai"})
        app_module.get_game_lock(game_id)

        self.assertIn(game_id, app_module.game_locks)

        app_module.pop_game(game_id)

        self.assertNotIn(game_id, app_module.game_locks)

    def test_multiple_game_ids_get_distinct_locks(self):
        first_game_id = "test-game-lock-4a"
        second_game_id = "test-game-lock-4b"

        first_lock = app_module.get_game_lock(first_game_id)
        second_lock = app_module.get_game_lock(second_game_id)

        self.assertIsNot(first_lock, second_lock)
        self.assertIn(first_game_id, app_module.game_locks)
        self.assertIn(second_game_id, app_module.game_locks)
        self.assertEqual(app_module.game_locks[first_game_id], first_lock)
        self.assertEqual(app_module.game_locks[second_game_id], second_lock)

    def test_game_lock_is_exclusive_across_threads(self):
        game_id = "test-game-lock-3"
        lock = app_module.get_game_lock(game_id)
        order = []

        def first_worker():
            with lock:
                order.append("first_enter")
                time.sleep(0.05)
                order.append("first_exit")

        def second_worker():
            with lock:
                order.append("second_enter")

        t1 = threading.Thread(target=first_worker)
        t2 = threading.Thread(target=second_worker)

        t1.start()
        time.sleep(0.01)
        t2.start()

        t1.join()
        t2.join()

        self.assertEqual(order, ["first_enter", "first_exit", "second_enter"])
