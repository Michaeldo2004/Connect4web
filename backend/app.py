import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS

from ai.minimax import get_best_move
from game.board import COLS, ROWS, check_win, create_board, drop_piece, is_valid_move

HUMAN = 1
AI = 2

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})


def board_to_list(board):
    return board.astype(int).tolist()


def is_draw(board):
    return not np.any(board == 0)


def empty_response():
    return {
        "board": board_to_list(create_board()),
        "status": "playing",
        "aiMove": None,
        "message": "New game started",
    }


def parse_board(data):
    board_data = data.get("board")
    if not isinstance(board_data, list):
        return None

    try:
        board = np.array(board_data, dtype=int)
    except (TypeError, ValueError):
        return None

    if board.shape != (ROWS, COLS):
        return None

    if not np.isin(board, [0, HUMAN, AI]).all():
        return None

    return board


def invalid_move(board, message):
    return jsonify({
        "board": board_to_list(board) if board is not None else board_to_list(create_board()),
        "status": "invalid_move",
        "aiMove": None,
        "message": message,
    }), 400


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/new-game")
def new_game():
    return jsonify(empty_response())


@app.post("/api/move")
def move():
    data = request.get_json(silent=True) or {}
    board = parse_board(data)
    if board is None:
        return invalid_move(None, "Invalid board")

    column = data.get("column")
    if not isinstance(column, int):
        return invalid_move(board, "Invalid column")

    if column < 0 or column >= COLS:
        return invalid_move(board, "Column out of range")

    if check_win(board, HUMAN) or check_win(board, AI) or is_draw(board):
        return invalid_move(board, "Game is already over")

    if not is_valid_move(board, column):
        return invalid_move(board, "Column is full")

    drop_piece(board, column, HUMAN)

    if check_win(board, HUMAN):
        return jsonify({
            "board": board_to_list(board),
            "status": "human_win",
            "aiMove": None,
            "message": "You win",
        })

    if is_draw(board):
        return jsonify({
            "board": board_to_list(board),
            "status": "draw",
            "aiMove": None,
            "message": "Draw",
        })

    ai_move = int(get_best_move(board, AI))
    if not is_valid_move(board, ai_move):
        return invalid_move(board, "AI returned an invalid move")

    drop_piece(board, ai_move, AI)

    if check_win(board, AI):
        status = "ai_win"
        message = "AI wins"
    elif is_draw(board):
        status = "draw"
        message = "Draw"
    else:
        status = "playing"
        message = "Your turn"

    return jsonify({
        "board": board_to_list(board),
        "status": status,
        "aiMove": ai_move,
        "message": message,
    })


if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=True)
