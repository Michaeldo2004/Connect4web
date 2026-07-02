import uuid

import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room

from ai.minimax import get_best_move, normalize_transposition_table
from game.board import COLS, ROWS, check_win, create_board, drop_piece, is_valid_move

HUMAN = 1
AI = 2
DIFFICULTIES = {
    "very_easy": {"depth": 1, "time_limit": 3},
    "easy": {"depth": 2, "time_limit": 3},
    "medium": {"depth": 5, "time_limit": 3},
    "hard": {"depth": 7, "time_limit": 5},
}

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})
socketio = SocketIO(app, cors_allowed_origins="http://localhost:5173", async_mode="threading", manage_session=False)
games = {}


def board_to_list(board):
    return board.astype(int).tolist()


def is_draw(board):
    return not np.any(board == 0)


def parse_difficulty(data):
    difficulty = data.get("difficulty", "medium") if isinstance(data, dict) else "medium"
    if difficulty not in DIFFICULTIES:
        return "medium"
    return difficulty


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


def empty_response(difficulty="medium"):
    return {
        "board": board_to_list(create_board()),
        "status": "playing",
        "aiMove": None,
        "message": "New game started",
        "difficulty": difficulty,
        "transpositionTable": {},
    }


def invalid_move(board, message, difficulty="medium", transposition_table=None):
    return jsonify({
        "board": board_to_list(board) if board is not None else board_to_list(create_board()),
        "status": "invalid_move",
        "aiMove": None,
        "message": message,
        "difficulty": difficulty,
        "transpositionTable": transposition_table or {},
    }), 400


def create_game_state(difficulty, player_id=None):
    return {
        "player_id": player_id or uuid.uuid4().hex,
        "socket_id": request.sid if request else None,
        "board": create_board(),
        "difficulty": difficulty,
        "status": "playing",
        "message": "New game started",
        "transposition_table": {},
    }


def serialize_game(game_id, game, ai_move=None, include_player_id=False):
    payload = {
        "gameId": game_id,
        "board": board_to_list(game["board"]),
        "status": game["status"],
        "message": game["message"],
        "aiMove": ai_move,
        "difficulty": game["difficulty"],
    }

    if include_player_id:
        payload["playerId"] = game["player_id"]

    return payload


def make_socket_error(game_id, game, message):
    if game is None:
        return {
            "gameId": game_id,
            "board": board_to_list(create_board()),
            "status": "invalid_move",
            "message": message,
            "aiMove": None,
            "difficulty": "medium",
        }

    payload = serialize_game(game_id, game)
    payload["status"] = "invalid_move"
    payload["message"] = message
    return payload


def get_authorized_game(data):
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    game = games.get(game_id)

    if game is None:
        return game_id, None, "Game not found"

    if game["player_id"] != player_id:
        return game_id, game, "Player does not have access to this game"

    return game_id, game, None


def apply_human_and_ai_move(game, column):
    board = game["board"]
    difficulty = game["difficulty"]
    transposition_table = normalize_transposition_table(game.get("transposition_table"))

    if not isinstance(column, int):
        return None, "Invalid column"

    if column < 0 or column >= COLS:
        return None, "Column out of range"

    if game["status"] in {"human_win", "ai_win", "draw"} or check_win(board, HUMAN) or check_win(board, AI) or is_draw(board):
        return None, "Game is already over"

    if not is_valid_move(board, column):
        return None, "Column is full"

    drop_piece(board, column, HUMAN)

    if check_win(board, HUMAN):
        game["status"] = "human_win"
        game["message"] = "You win"
        game["transposition_table"] = transposition_table
        return None, None

    if is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
        game["transposition_table"] = transposition_table
        return None, None

    settings = DIFFICULTIES[difficulty]
    ai_move, transposition_table = get_best_move(
        board,
        AI,
        max_depth=settings["depth"],
        time_limit=settings["time_limit"],
        transposition_table=transposition_table,
        return_table=True,
    )

    if ai_move is None or not is_valid_move(board, int(ai_move)):
        game["transposition_table"] = transposition_table
        return None, "AI returned an invalid move"

    ai_move = int(ai_move)
    drop_piece(board, ai_move, AI)
    game["transposition_table"] = transposition_table

    if check_win(board, AI):
        game["status"] = "ai_win"
        game["message"] = "AI wins"
    elif is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
    else:
        game["status"] = "playing"
        game["message"] = "Your turn"

    return ai_move, None


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

    game = {
        "board": board,
        "difficulty": difficulty,
        "status": "playing",
        "message": "Your turn",
        "transposition_table": transposition_table,
    }
    ai_move, error = apply_human_and_ai_move(game, data.get("column"))
    if error:
        return invalid_move(board, error, difficulty, game["transposition_table"])

    return jsonify({
        "board": board_to_list(game["board"]),
        "status": game["status"],
        "aiMove": ai_move,
        "message": game["message"],
        "difficulty": difficulty,
        "transpositionTable": game["transposition_table"],
    })


@socketio.on("create_game")
def socket_create_game(data):
    difficulty = parse_difficulty(data or {})
    game_id = uuid.uuid4().hex
    game = create_game_state(difficulty)
    games[game_id] = game
    join_room(game_id)
    emit("game_created", serialize_game(game_id, game, include_player_id=True))


@socketio.on("join_game")
def socket_join_game(data):
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    game = games.get(game_id)

    if game is None or game["player_id"] != player_id:
        emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
        return

    game["socket_id"] = request.sid
    join_room(game_id)
    emit("game_joined", serialize_game(game_id, game))


@socketio.on("player_move")
def socket_player_move(data):
    game_id, game, error = get_authorized_game(data or {})
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    ai_move, error = apply_human_and_ai_move(game, data.get("column"))
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    emit("board_updated", serialize_game(game_id, game, ai_move=ai_move), to=game_id)


@socketio.on("reset_game")
def socket_reset_game(data):
    game_id, game, error = get_authorized_game(data or {})
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    difficulty = parse_difficulty(data or {})
    game["board"] = create_board()
    game["difficulty"] = difficulty
    game["status"] = "playing"
    game["message"] = "New game started"
    game["transposition_table"] = {}
    game["socket_id"] = request.sid
    emit("board_updated", serialize_game(game_id, game), to=game_id)


if __name__ == "__main__":
    socketio.run(app, host="localhost", port=5000, debug=True, allow_unsafe_werkzeug=True)
