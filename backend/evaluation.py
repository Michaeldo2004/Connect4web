import numpy as np
from game.board import create_board, drop_piece, is_valid_move, check_win, get_board_str
from ai.minimax import get_best_move
from ai.online_ai import OnlineAI

ROWS = 6
COLS = 7

def play_single_game(first_player_ai):
    board = create_board()
    turn = 0
    online_ai = OnlineAI()

    while True:
        player = (turn % 2) + 1

        if (first_player_ai == "minimax" and player == 1) or (first_player_ai == "online" and player == 2):
            # Our Minimax AI's move
            col = get_best_move(board, player)
        else:
            # Online AI's move
            board_str = get_board_str(board)
            col = online_ai.get_best_online_move(board_str)

        if col is None or not is_valid_move(board, col):
            # Invalid move (online AI failure?), current player loses
            return 3 - player  # Opponent wins

        drop_piece(board, col, player)

        if check_win(board, player):
            return player

        if np.all(board != 0):
            return 0  # Tie

        turn += 1

def evaluate(minimax_first=True):
    minimax_wins = 0
    online_wins = 0
    ties = 0

    for i in range(10):
        print(f"Starting Game {i+1}...")
        first_player = "minimax" if (i % 2 == 0) else "online"
        winner = play_single_game(first_player)

        if winner == 1:
            if first_player == "minimax":
                minimax_wins += 1
            else:
                online_wins += 1
        elif winner == 2:
            if first_player == "online":
                minimax_wins += 1
            else:
                online_wins += 1
        else:
            ties += 1

    print("\n=== Final Results ===")
    print(f"Minimax AI Wins: {minimax_wins}")
    print(f"Online AI Wins: {online_wins}")
    print(f"Ties: {ties}")

if __name__ == "__main__":
    evaluate()
