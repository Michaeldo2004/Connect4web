import argparse
import json
from pathlib import Path

import numpy as np

from ai.minimax import get_best_move
from ai.online_ai import OnlineAI
from game.board import check_win, create_board, drop_piece, get_board_str, is_valid_move

DIFFICULTIES = {
    "very_easy": {"depth": 1, "time_limit": 3},
    "easy": {"depth": 2, "time_limit": 3},
    "medium": {"depth": 5, "time_limit": 3},
    "hard": {"depth": 7, "time_limit": 5},
}
RESULT_KEYS = ["games", "minimax_wins", "online_wins", "ties"]
DEFAULT_RESULTS_FILE = Path(__file__).with_name("evaluation_results.json")


def empty_results():
    return {
        difficulty: {key: 0 for key in RESULT_KEYS}
        for difficulty in DIFFICULTIES
    }


def load_results(results_file):
    if not results_file.exists():
        return empty_results()

    try:
        data = json.loads(results_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty_results()

    cumulative = data.get("cumulative", data)
    results = empty_results()
    for difficulty in DIFFICULTIES:
        source = cumulative.get(difficulty, {})
        for key in RESULT_KEYS:
            value = source.get(key, 0)
            if isinstance(value, int) and value >= 0:
                results[difficulty][key] = value

    return results


def save_results(results_file, latest_run, cumulative):
    data = {
        "latest_run": latest_run,
        "cumulative": cumulative,
    }
    results_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def add_results(cumulative, latest_run):
    for difficulty in DIFFICULTIES:
        for key in RESULT_KEYS:
            cumulative[difficulty][key] += latest_run[difficulty][key]


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


def print_results(title, results):
    print(f"\n=== {title} ===")
    for difficulty, result in results.items():
        print(f"\n{difficulty.title()}")
        print(f"Games: {result['games']}")
        print(f"Minimax AI Wins: {result['minimax_wins']}")
        print(f"Online AI Wins: {result['online_wins']}")
        print(f"Ties: {result['ties']}")


def evaluate(rounds, results_file):
    latest_run = empty_results()
    cumulative = load_results(results_file)

    for i in range(rounds):
        first_player = "minimax" if i % 2 == 0 else "online"
        print(f"Starting loop {i + 1}/{rounds}...")

        for difficulty in DIFFICULTIES:
            print(f"  Testing {difficulty}...")
            winner = play_single_game(first_player, difficulty)
            latest_run[difficulty]["games"] += 1

            if winner == 0:
                latest_run[difficulty]["ties"] += 1
            elif (winner == 1 and first_player == "minimax") or (winner == 2 and first_player == "online"):
                latest_run[difficulty]["minimax_wins"] += 1
            else:
                latest_run[difficulty]["online_wins"] += 1

    add_results(cumulative, latest_run)
    save_results(results_file, latest_run, cumulative)
    print_results("Final Results", latest_run)
    print_results("Cumulative Results", cumulative)
    print(f"\nSaved results to {results_file}")


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate minimax Connect 4 AI against the online AI.")
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Number of loops to run. Each loop tests every difficulty.",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        default=DEFAULT_RESULTS_FILE,
        help="JSON file used to store cumulative evaluation results.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    evaluate(args.rounds, args.results_file)
