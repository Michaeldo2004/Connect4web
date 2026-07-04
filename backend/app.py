import atexit
import os
import random
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeoutError

import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room

from ai.minimax import get_best_move, normalize_transposition_table
from game.board import COLS, ROWS, check_win, create_board, drop_piece, is_valid_move

HUMAN = 1
AI = 2
DISCONNECT_GRACE_SECONDS = 15
CREATE_RATE_LIMIT_COUNT = 100
CREATE_RATE_LIMIT_SECONDS = 60
MAX_ACTIVE_GAMES = 300
AI_GAME_TTL_SECONDS = 30 * 60
MULTIPLAYER_GAME_TTL_SECONDS = 2 * 60 * 60
FINISHED_STATUSES = {"human_win", "ai_win", "player1_win", "player2_win", "draw"}
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
games_lock = threading.Lock()
create_attempts = {}
ai_executor = None
ai_executor_lock = threading.Lock()
AI_WORKER_COUNT = max(1, min(2, (os.cpu_count() or 2) - 1))


def shutdown_ai_executor():
    global ai_executor
    if ai_executor is not None:
        ai_executor.shutdown(wait=False, cancel_futures=True)
        ai_executor = None


atexit.register(shutdown_ai_executor)


def board_to_list(board):
    return board.astype(int).tolist()


def is_draw(board):
    return not np.any(board == 0)


def parse_difficulty(data):
    difficulty = data.get("difficulty", "medium") if isinstance(data, dict) else "medium"
    if difficulty not in DIFFICULTIES:
        return "medium"
    return difficulty


def empty_response(difficulty="medium"):
    return {
        "board": board_to_list(create_board()),
        "status": "playing",
        "aiMove": None,
        "message": "New game started",
        "difficulty": difficulty,
        "transpositionTable": {},
    }


def create_game_state(difficulty, player_id=None):
    now = time.time()
    return {
        "mode": "ai",
        "player_id": player_id or uuid.uuid4().hex,
        "socket_id": request.sid if request else None,
        "board": create_board(),
        "difficulty": difficulty,
        "status": "playing",
        "message": "Your turn",
        "current_player": random.choice([HUMAN, AI]),
        "transposition_table": {},
        "lock": threading.Lock(),
        "created_at": now,
        "updated_at": now,
    }


def create_multiplayer_game_state(player_id=None):
    player_id = player_id or uuid.uuid4().hex
    now = time.time()
    return {
        "mode": "multiplayer",
        "players": {
            player_id: {
                "piece": HUMAN,
                "socket_id": request.sid if request else None,
                "connected": True,
                "disconnect_token": None,
            },
        },
        "board": create_board(),
        "difficulty": "multiplayer",
        "status": "waiting",
        "message": "Waiting for Player 2",
        "current_player": random.choice([HUMAN, AI]),
        "disconnect_deadline": None,
        "rematch_requests": set(),
        "lock": threading.Lock(),
        "created_at": now,
        "updated_at": now,
    }


def get_game_lock(game):
    if "lock" not in game:
        game["lock"] = threading.Lock()
    return game["lock"]


def mark_game_updated(game):
    game["updated_at"] = time.time()


def request_key():
    return request.remote_addr or request.environ.get("REMOTE_ADDR") or "unknown"


def cleanup_stale_games():
    now = time.time()
    stale_game_ids = []
    with games_lock:
        for game_id, game in games.items():
            ttl = MULTIPLAYER_GAME_TTL_SECONDS if game.get("mode") == "multiplayer" else AI_GAME_TTL_SECONDS
            if now - game.get("updated_at", game.get("created_at", now)) > ttl:
                stale_game_ids.append(game_id)

        for game_id in stale_game_ids:
            games.pop(game_id, None)


def check_create_allowed():
    cleanup_stale_games()
    now = time.time()
    key = request_key()
    attempts = [stamp for stamp in create_attempts.get(key, []) if now - stamp < CREATE_RATE_LIMIT_SECONDS]
    create_attempts[key] = attempts

    if len(attempts) >= CREATE_RATE_LIMIT_COUNT:
        return "Too many games created. Try again later."

    with games_lock:
        if len(games) >= MAX_ACTIVE_GAMES:
            return "Server has too many active games. Try again later."

    attempts.append(now)
    return None


def remove_ai_games_for_sid(socket_id):
    removed = []
    with games_lock:
        for game_id, game in list(games.items()):
            if game.get("mode") == "ai" and game.get("socket_id") == socket_id:
                games.pop(game_id, None)
                removed.append(game_id)
    return removed


def store_game(game_id, game):
    with games_lock:
        games[game_id] = game


def pop_game(game_id):
    with games_lock:
        return games.pop(game_id, None)


def run_best_move(board_data, piece, max_depth, time_limit, transposition_table):
    board = np.array(board_data, dtype=int)
    return get_best_move(
        board,
        piece,
        max_depth=max_depth,
        time_limit=time_limit,
        transposition_table=transposition_table,
        return_table=True,
    )


def get_ai_executor():
    global ai_executor
    with ai_executor_lock:
        if ai_executor is None:
            ai_executor = ProcessPoolExecutor(max_workers=AI_WORKER_COUNT)
        return ai_executor


def get_ai_move(board, settings, transposition_table):
    if app.config.get("AI_SEARCH_INLINE"):
        return run_best_move(board_to_list(board), AI, settings["depth"], settings["time_limit"], transposition_table)

    future = get_ai_executor().submit(
        run_best_move,
        board_to_list(board),
        AI,
        settings["depth"],
        settings["time_limit"],
        transposition_table,
    )
    try:
        return future.result(timeout=settings["time_limit"] + 1)
    except FutureTimeoutError:
        future.cancel()
        return None, transposition_table


def serialize_game(game_id, game, ai_move=None, include_player_id=False, player_id=None):
    payload = {
        "gameId": game_id,
        "board": board_to_list(game["board"]),
        "status": game["status"],
        "message": game["message"],
        "aiMove": ai_move,
        "difficulty": game["difficulty"],
        "mode": game.get("mode", "ai"),
    }

    if "current_player" in game:
        payload["currentPlayer"] = game["current_player"]

    if include_player_id:
        payload["playerId"] = game["player_id"]

    if game.get("mode") == "multiplayer":
        payload["currentPlayer"] = game["current_player"]
        payload["playersConnected"] = sum(1 for player in game["players"].values() if player.get("connected"))
        payload["disconnectDeadline"] = game.get("disconnect_deadline")
        payload["playAgainAccepted"] = len(game.get("rematch_requests", set()))
        if player_id in game["players"]:
            payload["playerId"] = player_id
            payload["playerNumber"] = game["players"][player_id]["piece"]

    return payload


def apply_ai_turn(game):
    board = game["board"]
    difficulty = game["difficulty"]
    transposition_table = normalize_transposition_table(game.get("transposition_table"))
    settings = DIFFICULTIES[difficulty]
    ai_move, transposition_table = get_ai_move(board, settings, transposition_table)

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
        game["current_player"] = HUMAN
        game["message"] = "Your turn"

    mark_game_updated(game)
    return ai_move, None


def start_ai_game_if_needed(game):
    if game.get("current_player") != AI:
        game["message"] = "Your turn"
        return None, None

    return apply_ai_turn(game)


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

    if game.get("mode") == "multiplayer":
        if player_id not in game["players"]:
            return game_id, game, "Player does not have access to this game"
        return game_id, game, None

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

    if game["status"] in FINISHED_STATUSES or check_win(board, HUMAN) or check_win(board, AI) or is_draw(board):
        return None, "Game is already over"

    if game.get("current_player") != HUMAN:
        return None, "Not your turn"

    if not is_valid_move(board, column):
        return None, "Column is full"

    drop_piece(board, column, HUMAN)

    if check_win(board, HUMAN):
        game["status"] = "human_win"
        game["message"] = "You win"
        game["transposition_table"] = transposition_table
        mark_game_updated(game)
        return None, None

    if is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
        game["transposition_table"] = transposition_table
        mark_game_updated(game)
        return None, None

    game["current_player"] = AI
    return apply_ai_turn(game)


def apply_multiplayer_move(game, player_id, column):
    board = game["board"]
    player = game["players"].get(player_id)

    if player is None:
        return "Player does not have access to this game"

    if len(game["players"]) < 2:
        return "Waiting for Player 2"

    if not isinstance(column, int):
        return "Invalid column"

    if column < 0 or column >= COLS:
        return "Column out of range"

    if game["status"] in FINISHED_STATUSES or check_win(board, HUMAN) or check_win(board, AI) or is_draw(board):
        return "Game is already over"

    if any(not current_player.get("connected") for current_player in game["players"].values()):
        return "Waiting for disconnected player"

    if player["piece"] != game["current_player"]:
        return "Not your turn"

    if not is_valid_move(board, column):
        return "Column is full"

    drop_piece(board, column, player["piece"])

    if check_win(board, player["piece"]):
        game["status"] = "player1_win" if player["piece"] == HUMAN else "player2_win"
        game["message"] = f"Player {player['piece']} wins"
        mark_game_updated(game)
        return None

    if is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
        mark_game_updated(game)
        return None

    game["current_player"] = AI if player["piece"] == HUMAN else HUMAN
    game["status"] = "playing"
    game["message"] = f"Player {game['current_player']} turn"
    mark_game_updated(game)
    return None


def find_multiplayer_player_by_sid(socket_id):
    for game_id, game in games.items():
        if game.get("mode") != "multiplayer":
            continue

        for player_id, player in game["players"].items():
            if player.get("socket_id") == socket_id:
                return game_id, game, player_id, player

    return None, None, None, None


def mark_multiplayer_player_connected(game_id, game, player_id):
    player = game["players"][player_id]
    player["socket_id"] = request.sid
    player["connected"] = True
    player["disconnect_token"] = None
    if all(current_player.get("connected") for current_player in game["players"].values()):
        game["disconnect_deadline"] = None
        if game["status"] == "playing":
            game["message"] = f"Player {game['current_player']} turn"
    mark_game_updated(game)
    print(f"Player {player['piece']} connected to game {game_id}")


def start_multiplayer_disconnect_timer(game_id, player_id, disconnect_token):
    socketio.sleep(DISCONNECT_GRACE_SECONDS)
    game = games.get(game_id)
    if game is None or game.get("mode") != "multiplayer":
        return

    with get_game_lock(game):
        player = game["players"].get(player_id)
        if player is None or player.get("connected") or player.get("disconnect_token") != disconnect_token:
            return

        if game["status"] in FINISHED_STATUSES:
            return

        other_players = [other for other in game["players"].values() if other["piece"] != player["piece"]]
        if not other_players:
            return

        other_player = other_players[0]
        if not other_player.get("connected"):
            game["status"] = "draw"
            game["message"] = "Game abandoned"
            game["disconnect_deadline"] = None
            mark_game_updated(game)
            payload = serialize_game(game_id, game)
        else:
            winning_piece = other_player["piece"]
            game["status"] = "player1_win" if winning_piece == HUMAN else "player2_win"
            game["message"] = f"Player {winning_piece} wins by default"
            game["disconnect_deadline"] = None
            mark_game_updated(game)
            payload = serialize_game(game_id, game)
    socketio.emit("board_updated", payload, to=game_id)


def is_multiplayer_finished(game):
    return game["status"] in {"player1_win", "player2_win", "draw"}


def reset_multiplayer_game(game):
    game["board"] = create_board()
    game["status"] = "playing" if len(game["players"]) == 2 else "waiting"
    game["current_player"] = random.choice([HUMAN, AI])
    game["message"] = f"Player {game['current_player']} turn" if len(game["players"]) == 2 else "Waiting for Player 2"
    game["disconnect_deadline"] = None
    game["rematch_requests"] = set()
    mark_game_updated(game)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/new-game")
def new_game():
    data = request.get_json(silent=True) or {}
    difficulty = parse_difficulty(data)
    return jsonify(empty_response(difficulty))


@socketio.on("create_game")
def socket_create_game(data):
    error = check_create_allowed()
    if error:
        emit("create_rejected", {"message": error})
        return

    remove_ai_games_for_sid(request.sid)
    difficulty = parse_difficulty(data or {})
    game_id = uuid.uuid4().hex
    game = create_game_state(difficulty)
    ai_move, error = start_ai_game_if_needed(game)
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    store_game(game_id, game)
    join_room(game_id)
    emit("game_created", serialize_game(game_id, game, ai_move=ai_move, include_player_id=True))


@socketio.on("join_game")
def socket_join_game(data):
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    game = games.get(game_id)

    if game is not None and game.get("mode") == "multiplayer":
        with get_game_lock(game):
            if player_id not in game["players"]:
                emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
                return

            mark_multiplayer_player_connected(game_id, game, player_id)
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = serialize_game(game_id, game)
        join_room(game_id)
        emit("game_joined", joined_payload)
        emit("board_updated", updated_payload, to=game_id)
        return

    if game is None or game["player_id"] != player_id:
        emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
        return

    with get_game_lock(game):
        game["socket_id"] = request.sid
        mark_game_updated(game)
        joined_payload = serialize_game(game_id, game)
    join_room(game_id)
    emit("game_joined", joined_payload)


@socketio.on("create_multiplayer_game")
def socket_create_multiplayer_game():
    error = check_create_allowed()
    if error:
        emit("create_rejected", {"message": error})
        return

    game_id = uuid.uuid4().hex
    player_id = uuid.uuid4().hex
    game = create_multiplayer_game_state(player_id)
    store_game(game_id, game)
    join_room(game_id)
    print(f"Player 1 connected to game {game_id}")
    emit("multiplayer_game_created", serialize_game(game_id, game, player_id=player_id))


@socketio.on("join_multiplayer_game")
def socket_join_multiplayer_game(data):
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    game = games.get(game_id)

    if game is None or game.get("mode") != "multiplayer":
        emit("join_rejected", {"gameId": game_id, "message": "Multiplayer game not found"})
        return

    with get_game_lock(game):
        if player_id in game["players"]:
            mark_multiplayer_player_connected(game_id, game, player_id)
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = serialize_game(game_id, game)
            join_room(game_id)
            emit("multiplayer_game_joined", joined_payload)
            emit("board_updated", updated_payload, to=game_id)
            return

        if len(game["players"]) >= 2:
            emit("join_rejected", {"gameId": game_id, "message": "Multiplayer game is full"})
            return

        player_id = uuid.uuid4().hex
        game["players"][player_id] = {
            "piece": AI,
            "socket_id": request.sid,
            "connected": True,
            "disconnect_token": None,
        }
        game["status"] = "playing"
        game["message"] = f"Player {game['current_player']} turn"
        mark_game_updated(game)
        joined_payload = serialize_game(game_id, game, player_id=player_id)
        updated_payload = serialize_game(game_id, game)
    join_room(game_id)
    print(f"Player 2 connected to game {game_id}")
    emit("multiplayer_game_joined", joined_payload)
    emit("board_updated", updated_payload, to=game_id)


@socketio.on("disconnect")
def socket_disconnect():
    removed_games = remove_ai_games_for_sid(request.sid)
    if removed_games:
        return

    game_id, game, player_id, player = find_multiplayer_player_by_sid(request.sid)
    if game is None:
        return

    with get_game_lock(game):
        player["connected"] = False
        player["socket_id"] = None
        player["disconnect_token"] = uuid.uuid4().hex
        print(f"Player {player['piece']} disconnected from game {game_id}")

        if len(game["players"]) < 2 or game["status"] in FINISHED_STATUSES:
            mark_game_updated(game)
            return

        game["disconnect_deadline"] = int((time.time() + DISCONNECT_GRACE_SECONDS) * 1000)
        game["message"] = f"Player {player['piece']} disconnected."
        mark_game_updated(game)
        payload = serialize_game(game_id, game)
        disconnect_token = player["disconnect_token"]
    socketio.emit("board_updated", payload, to=game_id)
    socketio.start_background_task(start_multiplayer_disconnect_timer, game_id, player_id, disconnect_token)


@socketio.on("leave_game")
def socket_leave_game(data):
    game_id, game, error = get_authorized_game(data or {})
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    if game.get("mode") != "multiplayer":
        pop_game(game_id)
        emit("game_left", {"gameId": game_id})
        return

    with get_game_lock(game):
        player_id = data.get("playerId")
        player = game["players"].get(player_id)
        if player is None:
            emit("invalid_move", make_socket_error(game_id, game, "Player does not have access to this game"))
            return

        if game["status"] == "waiting" and len(game["players"]) == 1:
            pop_game(game_id)
            emit("game_left", {"gameId": game_id})
            return

        if is_multiplayer_finished(game):
            left_payload = {"gameId": game_id, "message": f"Player {player['piece']} left the room"}
            pop_game(game_id)
            socketio.emit("player_left", left_payload, to=game_id, skip_sid=request.sid)
            emit("game_left", {"gameId": game_id})
            return

    emit("invalid_move", make_socket_error(game_id, game, "Cannot leave during an active multiplayer game"))


@socketio.on("play_again")
def socket_play_again(data):
    game_id, game, error = get_authorized_game(data or {})
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    if game.get("mode") != "multiplayer" or not is_multiplayer_finished(game):
        emit("invalid_move", make_socket_error(game_id, game, "Play again is only available after a multiplayer match"))
        return

    with get_game_lock(game):
        player_id = data.get("playerId")
        game["rematch_requests"].add(player_id)
        mark_game_updated(game)

        if len(game["rematch_requests"]) == 2 and all(player.get("connected") for player in game["players"].values()):
            reset_multiplayer_game(game)
            payload = serialize_game(game_id, game)
            event_name = "board_updated"
        else:
            payload = serialize_game(game_id, game)
            event_name = "play_again_updated"

    socketio.emit(event_name, payload, to=game_id)


@socketio.on("player_move")
def socket_player_move(data):
    game_id, game, error = get_authorized_game(data or {})
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    if game.get("mode") == "multiplayer":
        with get_game_lock(game):
            error = apply_multiplayer_move(game, data.get("playerId"), data.get("column"))
            if error:
                invalid_payload = make_socket_error(game_id, game, error)
            else:
                payload = serialize_game(game_id, game)
        if error:
            emit("invalid_move", invalid_payload)
            return

        emit("board_updated", payload, to=game_id)
        return

    with get_game_lock(game):
        ai_move, error = apply_human_and_ai_move(game, data.get("column"))
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        else:
            payload = serialize_game(game_id, game, ai_move=ai_move)

    if error:
        emit("invalid_move", invalid_payload)
        return

    emit("board_updated", payload, to=game_id)


@socketio.on("reset_game")
def socket_reset_game(data):
    game_id, game, error = get_authorized_game(data or {})
    if error:
        emit("invalid_move", make_socket_error(game_id, game, error))
        return

    if game.get("mode") == "multiplayer":
        with get_game_lock(game):
            reset_multiplayer_game(game)
            payload = serialize_game(game_id, game)
        emit("board_updated", payload, to=game_id)
        return

    with get_game_lock(game):
        difficulty = parse_difficulty(data or {})
        game["board"] = create_board()
        game["difficulty"] = difficulty
        game["status"] = "playing"
        game["current_player"] = random.choice([HUMAN, AI])
        game["message"] = "Your turn"
        game["transposition_table"] = {}
        game["socket_id"] = request.sid
        mark_game_updated(game)
        ai_move, error = start_ai_game_if_needed(game)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        else:
            payload = serialize_game(game_id, game, ai_move=ai_move)

    if error:
        emit("invalid_move", invalid_payload)
        return

    emit("board_updated", payload, to=game_id)


if __name__ == "__main__":
    socketio.run(app, host="localhost", port=5000, debug=True, allow_unsafe_werkzeug=True)
