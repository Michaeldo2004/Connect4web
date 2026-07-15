import unittest
from unittest.mock import patch

import numpy as np

from ai import minimax as minimax_module


class MinimaxAnalysisTests(unittest.TestCase):
    def test_move_scores_choose_center_priority_best_and_worst_on_ties(self):
        board = np.zeros((6, 7), dtype=int)
        # Legal columns are searched in center order: 3, 2, 4, 1, 5, 0, 6.
        scores_in_search_order = [100, 100, 20, 20, 20, -50, -50]

        with patch.object(minimax_module, "minimax", side_effect=scores_in_search_order):
            best_column, worst_column, scores = minimax_module.get_move_scores(board, 1)

        self.assertEqual(best_column, 3)
        self.assertEqual(worst_column, 0)
        self.assertEqual(scores[3], 100)
        self.assertEqual(scores[6], -50)

    def test_all_equal_scores_choose_center_for_both_recommendations(self):
        board = np.zeros((6, 7), dtype=int)

        with patch.object(minimax_module, "minimax", return_value=0):
            best_column, worst_column, scores = minimax_module.get_move_scores(board, 2)

        self.assertEqual(best_column, 3)
        self.assertEqual(worst_column, 3)
        self.assertEqual(set(scores.values()), {0})

    def test_timeout_rescores_every_root_move_with_one_consistent_fallback(self):
        board = np.zeros((6, 7), dtype=int)
        static_scores_in_center_order = [30, 20, 40, 10, 50, 0, 60]

        with patch.object(
            minimax_module,
            "minimax",
            side_effect=[111, minimax_module.SearchTimeout()],
        ) as deep_search, patch.object(
            minimax_module,
            "evaluate_board",
            side_effect=static_scores_in_center_order,
        ) as static_search:
            best_column, worst_column, scores = minimax_module.get_move_scores(board, 1)

        self.assertEqual(deep_search.call_count, 2)
        self.assertEqual(static_search.call_count, 7)
        self.assertEqual(scores[3], 30)
        self.assertNotIn(111, scores.values())
        self.assertEqual(best_column, 6)
        self.assertEqual(worst_column, 0)

    def test_full_board_has_no_detailed_move_scores(self):
        board = np.ones((6, 7), dtype=int)

        self.assertEqual(minimax_module.get_move_scores(board, 1), (None, None, {}))


if __name__ == "__main__":
    unittest.main()
