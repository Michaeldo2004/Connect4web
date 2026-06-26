import argparse

import numpy as np

from ai.minimax import get_best_move
from ai.online_ai import OnlineAI
from game.board import check_win, create_board, drop_piece, get_board_str, is_valid_move

DIFFICULTIES = {
    "easy": {"depth": 3, "time_limit": 3},
    "medium": {"depth": 5, "time_limit": 3},
    "hard": {"depth": 7, "time_limit": 5},
}


def play_single_game(first_player_ai, difficulty):
    board = create_board()
    turn = 0
    online_ai = OnlineAI()
    transposition_table = {}
    settings = DIFFICULTIES[difficulty]

    while True:
        player = (turn % 2) + 1

        if (first_player_ai == "minimax" and player == 1) or (first_player_ai == "online" and player == 2):
            col, transposition_table = get_best_move(
                board,
                player,
                max_depth=settings["depth"],
                time_limit=settings["time_limit"],
                transposition_table=transposition_table,
                return_table=True,
            )
        else:
            board_str = get_board_str(board)
            col = online_ai.get_best_online_move(board_str, player)

        if col is None or not is_valid_move(board, col):
            return 3 - player

        drop_piece(board, col, player)

        if check_win(board, player):
            return player

        if np.all(board != 0):
            return 0

        turn += 1


def evaluate(rounds):
    results = {
        difficulty: {"minimax_wins": 0, "online_wins": 0, "ties": 0}
        for difficulty in DIFFICULTIES
    }

    for i in range(rounds):
        first_player = "minimax" if i % 2 == 0 else "online"
        print(f"Starting loop {i + 1}/{rounds}...")

        for difficulty in DIFFICULTIES:
            print(f"  Testing {difficulty}...")
            winner = play_single_game(first_player, difficulty)

            if winner == 0:
                results[difficulty]["ties"] += 1
            elif (winner == 1 and first_player == "minimax") or (winner == 2 and first_player == "online"):
                results[difficulty]["minimax_wins"] += 1
            else:
                results[difficulty]["online_wins"] += 1

    print("\n=== Final Results ===")
    for difficulty, result in results.items():
        print(f"\n{difficulty.title()}")
        print(f"Minimax AI Wins: {result['minimax_wins']}")
        print(f"Online AI Wins: {result['online_wins']}")
        print(f"Ties: {result['ties']}")


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate minimax Connect 4 AI against the online AI.")
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Number of loops to run. Each loop tests easy, medium, and hard.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    evaluate(args.rounds)
