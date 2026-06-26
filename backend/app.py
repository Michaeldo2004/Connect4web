import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS

from ai.minimax import get_best_move, normalize_transposition_table
from game.board import COLS, ROWS, check_win, create_board, drop_piece, is_valid_move

HUMAN = 1
AI = 2
DIFFICULTIES = {
    "easy": {"depth": 3, "time_limit": 3},
    "medium": {"depth": 5, "time_limit": 3},
    "hard": {"depth": 7, "time_limit": 5},
}

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})


def board_to_list(board):
    return board.astype(int).tolist()


def is_draw(board):
    return not np.any(board == 0)


def empty_response(difficulty="medium"):
    return {
        "board": board_to_list(create_board()),
        "status": "playing",
        "aiMove": None,
        "message": "New game started",
        "difficulty": difficulty,
        "transpositionTable": {},
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


def parse_difficulty(data):
    difficulty = data.get("difficulty", "medium")
    if difficulty not in DIFFICULTIES:
        return "medium"
    return difficulty


def invalid_move(board, message, difficulty="medium", transposition_table=None):
    return jsonify({
        "board": board_to_list(board) if board is not None else board_to_list(create_board()),
        "status": "invalid_move",
        "aiMove": None,
        "message": message,
        "difficulty": difficulty,
        "transpositionTable": transposition_table or {},
    }), 400


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/new-game")
def new_game():
    data = request.get_json(silent=True) or {}
    difficulty = parse_difficulty(data)
    return jsonify(empty_response(difficulty))


@app.post("/api/move")
def move():
    data = request.get_json(silent=True) or {}
    difficulty = parse_difficulty(data)
    transposition_table = normalize_transposition_table(data.get("transpositionTable"))
    board = parse_board(data)
    if board is None:
        return invalid_move(None, "Invalid board", difficulty, transposition_table)

    column = data.get("column")
    if not isinstance(column, int):
        return invalid_move(board, "Invalid column", difficulty, transposition_table)

    if column < 0 or column >= COLS:
        return invalid_move(board, "Column out of range", difficulty, transposition_table)

    if check_win(board, HUMAN) or check_win(board, AI) or is_draw(board):
        return invalid_move(board, "Game is already over", difficulty, transposition_table)

    if not is_valid_move(board, column):
        return invalid_move(board, "Column is full", difficulty, transposition_table)

    drop_piece(board, column, HUMAN)

    if check_win(board, HUMAN):
        return jsonify({
            "board": board_to_list(board),
            "status": "human_win",
            "aiMove": None,
            "message": "You win",
            "difficulty": difficulty,
            "transpositionTable": transposition_table,
        })

    if is_draw(board):
        return jsonify({
            "board": board_to_list(board),
            "status": "draw",
            "aiMove": None,
            "message": "Draw",
            "difficulty": difficulty,
            "transpositionTable": transposition_table,
        })

    settings = DIFFICULTIES[difficulty]
    ai_move, transposition_table = get_best_move(
        board,
        AI,
        max_depth=settings["depth"],
        time_limit=settings["time_limit"],
        transposition_table=transposition_table,
        return_table=True,
    )
    if ai_move is None:
        return invalid_move(board, "AI could not find a move", difficulty, transposition_table)

    ai_move = int(ai_move)
    if not is_valid_move(board, ai_move):
        return invalid_move(board, "AI returned an invalid move", difficulty, transposition_table)

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
        "difficulty": difficulty,
        "transpositionTable": transposition_table,
    })


if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=True)
