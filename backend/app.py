import atexit
import os
import random
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

import jwt
import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

import supabase_store
from ai.minimax import get_best_move, get_move_scores, normalize_transposition_table
from game.board import COLS, ROWS, check_win, create_board, drop_piece, is_valid_move

HUMAN = 1
AI = 2
DISCONNECT_GRACE_SECONDS = 15
AI_GAME_TIME_BANK_MS = 90_000
MULTIPLAYER_TIME_BANKS_MS = {
    "multiplayer": 90_000,
    "fast_connect_60": 60_000,
    "fast_connect_30": 30_000,
}
FAST_CONNECT_DIFFICULTIES = {"fast_connect_60", "fast_connect_30"}
CREATE_RATE_LIMIT_COUNT = 100
CREATE_RATE_LIMIT_SECONDS = 60
ROOM_VISIBILITY_RATE_LIMIT_SECONDS = 5
MAX_ACTIVE_GAMES = 300
MULTIPLAYER_CREATE_REQUEST_ID_MAX_LENGTH = 128
AI_GAME_TTL_SECONDS = 30 * 60
MULTIPLAYER_GAME_TTL_SECONDS = 2 * 60 * 60
FINISHED_STATUSES = {"human_win", "ai_win", "player1_win", "player2_win", "draw"}
COMPLETED_STATUSES = {"human_win", "ai_win", "player1_win", "player2_win", "draw"}
DIFFICULTIES = {
    "very_easy": {"depth": 1, "time_limit": 3},
    "easy": {"depth": 2, "time_limit": 3},
    "medium": {"depth": 5, "time_limit": 3},
    "hard": {"depth": 7, "time_limit": 4},
}
supabase_store.load_local_env()


def get_env_value(name, default):
    value = os.environ.get(name, "").strip()
    return value or default


def get_env_int(name, default):
    try:
        return int(get_env_value(name, str(default)))
    except ValueError:
        return default


def get_env_bool(name, default):
    value = get_env_value(name, str(default)).lower()
    return value in {"1", "true", "yes", "on"}


def get_cors_origins():
    configured_origins = get_env_value(
        "CORS_ALLOWED_ORIGINS", get_env_value("FRONTEND_ORIGIN", "http://localhost:5173")
    )
    return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]


app = Flask(__name__)
CORS_ALLOWED_ORIGINS = get_cors_origins()
BACKEND_HOST = get_env_value("BACKEND_HOST", "localhost")
BACKEND_PORT = get_env_int("BACKEND_PORT", 5000)
SUPABASE_JWT_SECRET = get_env_value("SUPABASE_JWT_SECRET", "")
AUTH_REQUIRED = get_env_bool("AUTH_REQUIRED", True)
CORS(app, resources={r"/api/*": {"origins": CORS_ALLOWED_ORIGINS}})
socketio = SocketIO(app, cors_allowed_origins=CORS_ALLOWED_ORIGINS, async_mode="threading", manage_session=False)
games = {}
games_lock = threading.Lock()
multiplayer_create_lock = threading.Lock()
create_attempts = {}
create_attempts_lock = threading.Lock()
ai_executor = None
ai_executor_lock = threading.Lock()
AI_WORKER_COUNT = max(1, get_env_int("AI_WORKER_COUNT", 1))
AI_QUEUE_CAPACITY = 3
AI_BUSY_MESSAGE = "AI queue is full, try again"
AI_ADMISSION_CAPACITY = AI_WORKER_COUNT + AI_QUEUE_CAPACITY
AI_ADMISSION_STALE_SECONDS = 65
MOVE_ANALYSIS_DEPTH = get_env_int("MOVE_ANALYSIS_DEPTH", 4)
MOVE_ANALYSIS_TIME_LIMIT = get_env_int("MOVE_ANALYSIS_TIME_LIMIT", 30)
MOVE_ANALYSIS_INTERRUPTED_MESSAGE = "Move evaluation was interrupted. Request it again."
GAME_REVIEW_UNAVAILABLE_MESSAGE = "Game review is temporarily unavailable. Please try again."
game_locks = {}
game_locks_guard = threading.Lock()
ai_queue_lock = threading.RLock()
ai_job_queue = deque()
move_analysis_job_queue = deque()
ai_active_jobs = 0
ai_queue_reservations = 0
ai_running_job_type = None
analysis_jobs_by_game = {}
analysis_admission_wakeup_scheduled = False
ai_admission_lock = ai_queue_lock
ai_admission_queue = deque()
ai_admissions_in_progress = 0


def get_game_lock(game_id):
    with game_locks_guard:
        lock = game_locks.get(game_id)
        if lock is None:
            lock = threading.Semaphore(1)
            game_locks[game_id] = lock
        return lock


def delete_game_lock(game_id):
    with game_locks_guard:
        game_locks.pop(game_id, None)


def get_stored_game(game_id):
    with games_lock:
        return games.get(game_id)


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


def parse_multiplayer_difficulty(data):
    difficulty = data.get("difficulty") if isinstance(data, dict) else None
    if difficulty in {None, ""}:
        return "multiplayer", None
    if difficulty not in MULTIPLAYER_TIME_BANKS_MS:
        return None, "Unknown multiplayer mode"
    return difficulty, None


def multiplayer_time_bank_ms(difficulty):
    return MULTIPLAYER_TIME_BANKS_MS.get(difficulty, MULTIPLAYER_TIME_BANKS_MS["multiplayer"])


def is_auth_required():
    return app.config.get("AUTH_REQUIRED", AUTH_REQUIRED)


def get_auth_field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def auth_context_from_user(user):
    profile_id = get_auth_field(user, "id")
    if not profile_id:
        return None, "Invalid session"
    return {"profile_id": profile_id, "email": get_auth_field(user, "email")}, None


def auth_context_from_claims(claims):
    profile_id = claims.get("sub")
    if not profile_id:
        return None, "Invalid session"
    return {"profile_id": profile_id, "email": claims.get("email")}, None


def verify_access_token_with_supabase(access_token):
    client = supabase_store.get_client()
    if client is None:
        return None, "Authentication is not configured"

    try:
        response = client.auth.get_user(access_token)
    except Exception:
        return None, "Invalid session"

    return auth_context_from_user(get_auth_field(response, "user"))


def verify_access_token(access_token):
    if not is_auth_required():
        return {"profile_id": None, "email": None}, None

    if not access_token:
        return None, "Login required"

    if not SUPABASE_JWT_SECRET:
        return verify_access_token_with_supabase(access_token)

    try:
        claims = jwt.decode(access_token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
    except jwt.InvalidAudienceError:
        try:
            claims = jwt.decode(
                access_token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None, "Invalid session"
    except jwt.PyJWTError:
        return None, "Invalid session"

    return auth_context_from_claims(claims)


def authenticate_payload(data):
    access_token = data.get("accessToken") if isinstance(data, dict) else None
    return verify_access_token(access_token)


def authenticate_request():
    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.removeprefix("Bearer ").strip()
    return verify_access_token(access_token)


def empty_response(difficulty="medium"):
    return {
        "board": board_to_list(create_board()),
        "status": "playing",
        "aiMove": None,
        "message": "New game started",
        "difficulty": difficulty,
        "transpositionTable": {},
    }


def create_game_state(difficulty, player_id=None, profile_id=None):
    now = time.time()
    starter = random.choice([HUMAN, AI])
    human_piece = HUMAN if starter == HUMAN else AI
    ai_piece = AI if starter == HUMAN else HUMAN
    return {
        "mode": "ai",
        "player_id": player_id or uuid.uuid4().hex,
        "profile_id": profile_id,
        "socket_id": request.sid if request else None,
        "board": create_board(),
        "difficulty": difficulty,
        "status": "playing",
        "message": "AI is thinking" if starter == AI else "Your turn",
        "current_player": HUMAN,
        "human_piece": human_piece,
        "ai_piece": ai_piece,
        "transposition_table": {},
        "ai_thinking": False,
        "move_number": 0,
        "time_banks_ms": {human_piece: AI_GAME_TIME_BANK_MS},
        "timer_active_player": None,
        "timer_started_at_ms": None,
        "timer_generation": 0,
        "end_reason": None,
        "created_at": now,
        "updated_at": now,
    }


def get_human_piece(game):
    return game.get("human_piece", HUMAN)


def get_ai_piece(game):
    return game.get("ai_piece", AI)


def create_multiplayer_game_state(
    player_id=None,
    profile_id=None,
    bind_socket=True,
    difficulty="multiplayer",
):
    if difficulty not in MULTIPLAYER_TIME_BANKS_MS:
        raise ValueError("Unknown multiplayer mode")
    player_id = player_id or uuid.uuid4().hex
    now = time.time()
    time_bank_ms = multiplayer_time_bank_ms(difficulty)
    return {
        "mode": "multiplayer",
        "public": False,
        "owner_name": "Player",
        "players": {
            player_id: {
                "piece": HUMAN,
                "profile_id": profile_id,
                "display_name": "Player 1",
                "socket_id": request.sid if bind_socket and request else None,
                "connected": bool(bind_socket),
                "ready": False,
                "disconnect_token": None,
            },
        },
        "board": create_board(),
        "difficulty": difficulty,
        "status": "waiting",
        "message": "Waiting for Player 2",
        "current_player": HUMAN,
        "disconnect_deadline": None,
        "rematch_requests": set(),
        "move_number": 0,
        "time_banks_ms": {HUMAN: time_bank_ms, AI: time_bank_ms},
        "timer_active_player": None,
        "timer_started_at_ms": None,
        "timer_generation": 0,
        "end_reason": None,
        "created_at": now,
        "updated_at": now,
    }


def assign_multiplayer_pieces(game, starter_player_id=None):
    player_ids = list(game.get("players", {}).keys())
    if len(player_ids) < 2:
        game["current_player"] = HUMAN
        return

    starter_id = starter_player_id if starter_player_id in game["players"] else random.choice(player_ids)
    if starter_id not in game["players"]:
        starter_id = player_ids[0]
    for current_player_id, player in game["players"].items():
        player["piece"] = HUMAN if current_player_id == starter_id else AI
    game["current_player"] = HUMAN


def mark_game_updated(game):
    game["updated_at"] = time.time()


def current_time_ms():
    return time.time() * 1000


def timer_banks_snapshot(game, now_ms=None):
    banks = {
        int(player_number): max(0, int(remaining_ms))
        for player_number, remaining_ms in game.get("time_banks_ms", {}).items()
    }
    active_player = game.get("timer_active_player")
    started_at_ms = game.get("timer_started_at_ms")
    if active_player in banks and started_at_ms is not None and game.get("status") == "playing":
        elapsed_ms = max(0, (now_ms if now_ms is not None else current_time_ms()) - started_at_ms)
        banks[active_player] = max(0, int(banks[active_player] - elapsed_ms))
    return banks


def consume_active_timer(game, now_ms=None):
    active_player = game.get("timer_active_player")
    started_at_ms = game.get("timer_started_at_ms")
    banks = game.get("time_banks_ms", {})
    if active_player not in banks or started_at_ms is None:
        return active_player, None

    now_ms = now_ms if now_ms is not None else current_time_ms()
    elapsed_ms = max(0, now_ms - started_at_ms)
    remaining_ms = max(0.0, banks[active_player] - elapsed_ms)
    banks[active_player] = remaining_ms
    game["timer_started_at_ms"] = now_ms
    return active_player, remaining_ms


def cancel_game_timer_task(game):
    timer_task = game.pop("timer_task", None)
    if timer_task is not None:
        timer_task.cancel()


def pause_game_timer(game, now_ms=None):
    cancel_game_timer_task(game)
    active_player, remaining_ms = consume_active_timer(game, now_ms)
    game["timer_active_player"] = None
    game["timer_started_at_ms"] = None
    game["timer_generation"] = game.get("timer_generation", 0) + 1
    return active_player, remaining_ms


def multiplayer_name_for_piece(game, piece):
    for player in game.get("players", {}).values():
        if player.get("piece") == piece:
            return player.get("display_name") or f"Player {piece}"
    return f"Player {piece}"


def complete_game_on_timeout(game_id, game, timed_out_player):
    cancel_game_timer_task(game)
    banks = game.get("time_banks_ms", {})
    if timed_out_player in banks:
        banks[timed_out_player] = 0
    game["timer_active_player"] = None
    game["timer_started_at_ms"] = None
    game["timer_generation"] = game.get("timer_generation", 0) + 1
    game["end_reason"] = "timeout"

    if game.get("mode") == "multiplayer":
        winning_piece = AI if timed_out_player == HUMAN else HUMAN
        game["status"] = "player1_win" if winning_piece == HUMAN else "player2_win"
        game["message"] = f"{multiplayer_name_for_piece(game, winning_piece)} won by timer"
    else:
        game["status"] = "ai_win"
        game["message"] = "AI won by timer"

    mark_game_updated(game)
    if game_id:
        supabase_store.update_game_record(game_id, game)


def expire_active_timer_if_needed(game_id, game, now_ms=None):
    active_player, remaining_ms = consume_active_timer(game, now_ms)
    if active_player is None or remaining_ms is None or remaining_ms > 0:
        return False
    complete_game_on_timeout(game_id, game, active_player)
    return True


def schedule_game_timer_task(game_id, game, generation, player_number, delay_ms):
    if not app.config.get("GAME_CLOCK_TASKS_ENABLED", True):
        return
    timer_task = threading.Timer(
        max(0, delay_ms) / 1000,
        run_game_timer,
        args=(game_id, game, generation, player_number, 0),
    )
    timer_task.daemon = True
    game["timer_task"] = timer_task
    timer_task.start()


def run_game_timer(game_id, expected_game, expected_generation, expected_player, delay_ms):
    if delay_ms > 0:
        socketio.sleep(delay_ms / 1000)
    payload = None
    reschedule_ms = None
    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if (
            game is None
            or game is not expected_game
            or game.get("status") != "playing"
            or game.get("timer_generation") != expected_generation
            or game.get("timer_active_player") != expected_player
        ):
            return

        game.pop("timer_task", None)
        if expire_active_timer_if_needed(game_id, game):
            payload = serialize_game(game_id, game)
        else:
            reschedule_ms = timer_banks_snapshot(game).get(expected_player, 0)

    if payload is not None:
        socketio.emit("board_updated", payload, to=game_id)
    elif reschedule_ms is not None:
        schedule_game_timer_task(
            game_id,
            expected_game,
            expected_generation,
            expected_player,
            reschedule_ms,
        )


def start_game_timer(game_id, game, player_number):
    if game.get("status") != "playing" or player_number not in game.get("time_banks_ms", {}):
        return

    if game.get("timer_active_player") == player_number and game.get("timer_started_at_ms") is not None:
        return

    pause_game_timer(game)
    remaining_ms = game["time_banks_ms"][player_number]
    if remaining_ms <= 0:
        complete_game_on_timeout(game_id, game, player_number)
        return

    game["timer_active_player"] = player_number
    game["timer_started_at_ms"] = current_time_ms()
    game["timer_generation"] = game.get("timer_generation", 0) + 1
    generation = game["timer_generation"]
    schedule_game_timer_task(game_id, game, generation, player_number, remaining_ms)


def finish_game(game, status, message, end_reason):
    pause_game_timer(game)
    game["status"] = status
    game["message"] = message
    game["end_reason"] = end_reason


def request_key():
    return request.remote_addr or request.environ.get("REMOTE_ADDR") or "unknown"


def cleanup_stale_games():
    now = time.time()
    stale_game_ids = []
    with games_lock:
        for game_id, game in games.items():
            ttl = MULTIPLAYER_GAME_TTL_SECONDS if game.get("mode") == "multiplayer" else AI_GAME_TTL_SECONDS
            durable_expires_at = game.get("durable_expires_at")
            if (durable_expires_at is not None and durable_expires_at <= now) or now - game.get(
                "updated_at", game.get("created_at", now)
            ) > ttl:
                stale_game_ids.append(game_id)

    for game_id in stale_game_ids:
        removed = False
        removed_game = None
        with get_game_lock(game_id):
            game = get_stored_game(game_id)
            if game is None:
                removed = True
            else:
                ttl = MULTIPLAYER_GAME_TTL_SECONDS if game.get("mode") == "multiplayer" else AI_GAME_TTL_SECONDS
                durable_expires_at = game.get("durable_expires_at")
                if (durable_expires_at is not None and durable_expires_at <= now) or now - game.get(
                    "updated_at", game.get("created_at", now)
                ) > ttl:
                    with games_lock:
                        if games.get(game_id) is game:
                            games.pop(game_id, None)
                            cancel_game_timer_task(game)
                            removed = True
                            removed_game = game
        if removed:
            if (
                removed_game is not None
                and removed_game.get("mode") == "multiplayer"
                and removed_game.get("status") == "waiting"
                and removed_game.get("creation_request_id")
            ):
                supabase_store.resolve_multiplayer_room_request(game_id, "expired")
            delete_game_lock(game_id)


def check_create_allowed():
    cleanup_stale_games()
    now = time.time()
    key = request_key()
    # This deployment is intentionally single-process. Protect the in-memory
    # rate limiter from concurrent Socket.IO threads without adding Redis.
    with create_attempts_lock:
        attempts = [stamp for stamp in create_attempts.get(key, []) if now - stamp < CREATE_RATE_LIMIT_SECONDS]
        if len(attempts) >= CREATE_RATE_LIMIT_COUNT:
            create_attempts[key] = attempts
            return "Too many games created. Try again later."

        with games_lock:
            if len(games) >= MAX_ACTIVE_GAMES:
                return "Server has too many active games. Try again later."

        attempts.append(now)
        create_attempts[key] = attempts
    return None


def authenticated_display_name(auth_context):
    """Resolve room names from trusted profile data, never client payloads."""
    profile_id = auth_context.get("profile_id")
    try:
        profile_name = supabase_store.fetch_profile_display_name(profile_id)
    except Exception as error:
        app.logger.warning(
            "Could not load multiplayer display name profile_id=%s error=%s",
            profile_id,
            error.__class__.__name__,
        )
        profile_name = None
    email_name = (auth_context.get("email") or "").partition("@")[0]
    return sanitize_public_owner_name(profile_name, email_name or "Player")


def remove_ai_games_for_sid(socket_id):
    removed = []
    with games_lock:
        for game_id, game in list(games.items()):
            if game.get("mode") == "ai" and game.get("socket_id") == socket_id:
                removed.append(game_id)

    for game_id in removed[:]:
        did_remove = False
        with get_game_lock(game_id):
            game = get_stored_game(game_id)
            if game is not None and game.get("mode") == "ai" and game.get("socket_id") == socket_id:
                with games_lock:
                    if games.get(game_id) is game:
                        games.pop(game_id, None)
                        cancel_game_timer_task(game)
                        did_remove = True
        if did_remove:
            delete_game_lock(game_id)
        else:
            removed.remove(game_id)
    return removed


def store_game(game_id, game):
    with games_lock:
        games[game_id] = game


def replace_game_id(old_game_id, game):
    new_game_id = uuid.uuid4().hex
    with games_lock:
        games.pop(old_game_id, None)
        games[new_game_id] = game
    with game_locks_guard:
        if old_game_id in game_locks:
            game_locks[new_game_id] = game_locks.pop(old_game_id)
    return new_game_id


def pop_game(game_id):
    with games_lock:
        game = games.pop(game_id, None)
    if game is not None:
        cancel_game_timer_task(game)
    delete_game_lock(game_id)
    return game


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


def analysis_rating(played_score, best_score, worst_score):
    # A single legal move (and any all-equal position) is both best and worst.
    # Best-score precedence keeps that forced move from being called a blunder.
    if played_score == best_score:
        return "great"
    if played_score == worst_score:
        return "blunder"
    if played_score < 0:
        return "mistake"
    return "ok"


def run_move_analysis(moves, minimax_depth, time_limit):
    if not moves:
        raise ValueError("No recorded moves to analyze")

    results = []
    for move_index, move in enumerate(moves, start=1):
        if not isinstance(move, dict):
            raise ValueError(f"Move {move_index} could not be analyzed")

        move_number = move.get("move_number", move_index)
        move_id = move.get("id")
        board_data = move.get("board_before")
        player_number = move.get("player_number")
        played_column = move.get("column_played")
        if move_id is None or player_number not in (HUMAN, AI) or not isinstance(board_data, list):
            raise ValueError(f"Move {move_number} could not be analyzed")

        try:
            board = np.array(board_data, dtype=int)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Move {move_number} has an invalid board") from error
        if board.shape != (ROWS, COLS) or not np.isin(board, (0, HUMAN, AI)).all():
            raise ValueError(f"Move {move_number} has an invalid board")

        best_column, worst_column, scores = get_move_scores(
            board,
            player_number,
            max_depth=minimax_depth,
            time_limit=time_limit,
        )
        if best_column is None or worst_column is None or played_column not in scores:
            raise ValueError(f"Move {move_number} could not be analyzed")
        played_score = scores[played_column]
        best_score = scores[best_column]
        worst_score = scores[worst_column]
        results.append(
            {
                "move_id": move_id,
                "minimax_depth": minimax_depth,
                "played_column": played_column,
                "best_column": best_column,
                "worst_column": worst_column,
                "played_score": played_score,
                "best_score": best_score,
                "worst_score": worst_score,
                "rating": analysis_rating(played_score, best_score, worst_score),
            }
        )
    return results


def get_ai_executor():
    global ai_executor
    with ai_executor_lock:
        if ai_executor is None:
            ai_executor = ProcessPoolExecutor(max_workers=AI_WORKER_COUNT)
        return ai_executor


def reserve_ai_search_slot():
    global ai_active_jobs, ai_queue_reservations, ai_running_job_type
    with ai_queue_lock:
        if ai_running_job_type != "move_analysis" and ai_active_jobs < AI_WORKER_COUNT:
            ai_active_jobs += 1
            ai_running_job_type = "live_move"
            return {"state": "active", "position": 0}
        queue_capacity = AI_ADMISSION_CAPACITY if ai_running_job_type == "move_analysis" else AI_QUEUE_CAPACITY
        if len(ai_job_queue) + ai_queue_reservations < queue_capacity:
            ai_queue_reservations += 1
            return {
                "state": "queued",
                "position": len(ai_job_queue) + ai_queue_reservations,
            }
    return None


def release_ai_search_slot(reservation):
    global ai_queue_reservations
    if not reservation:
        return
    if reservation.get("state") == "queued":
        with ai_queue_lock:
            ai_queue_reservations = max(0, ai_queue_reservations - 1)
        return
    finish_ai_job()


def queued_game_payload(job, position=None, active=False):
    with get_game_lock(job["game_id"]):
        game = get_stored_game(job["game_id"])
        if (
            game is None
            or game is not job["expected_game"]
            or not game.get("ai_thinking")
            or game.get("move_number") != job["move_number"]
        ):
            return None
        game["ai_queued"] = not active
        game["ai_queue_position"] = 0 if active else position
        game["message"] = "AI is thinking" if active else f"AI queued - position {position}"
        mark_game_updated(game)
        return serialize_game(job["game_id"], game)


def start_reserved_ai_job(job):
    if app.config.get("AI_SEARCH_INLINE"):
        complete_ai_turn(job)
        return
    socketio.start_background_task(complete_ai_turn, job)


def launch_ai_turn(job):
    global ai_queue_reservations
    reservation = job.get("reservation") or {"state": "active", "position": 0}
    if reservation.get("state") == "queued":
        with ai_queue_lock:
            ai_queue_reservations = max(0, ai_queue_reservations - 1)
            ai_job_queue.append(job)
            position = len(ai_job_queue)
        payload = queued_game_payload(job, position=position)
        if payload is not None:
            socketio.emit("board_updated", payload, to=job["game_id"])
        dispatch_ai_scheduler()
        return
    start_reserved_ai_job(job)


def start_move_analysis_job(job):
    if app.config.get("AI_SEARCH_INLINE"):
        complete_move_analysis(job)
        return
    socketio.start_background_task(complete_move_analysis, job)


def enqueue_move_analysis(game_id, moves):
    with ai_queue_lock:
        existing = analysis_jobs_by_game.get(game_id)
        if existing is not None:
            return existing
        job = {
            "game_id": game_id,
            "moves": moves,
            "minimax_depth": MOVE_ANALYSIS_DEPTH,
            "time_limit": MOVE_ANALYSIS_TIME_LIMIT,
            "state": "queued",
        }
        analysis_jobs_by_game[game_id] = job
        move_analysis_job_queue.append(job)
    dispatch_ai_scheduler()
    return job


def complete_move_analysis(job):
    try:
        if app.config.get("AI_SEARCH_INLINE"):
            rows = run_move_analysis(job["moves"], job["minimax_depth"], job["time_limit"])
        else:
            # Intentionally no Future timeout/cancellation: analysis is non-preemptive.
            rows = (
                get_ai_executor()
                .submit(
                    run_move_analysis,
                    job["moves"],
                    job["minimax_depth"],
                    job["time_limit"],
                )
                .result()
            )
        if not supabase_store.replace_move_analysis(job["game_id"], rows):
            raise RuntimeError("Could not persist move analysis")
        if not supabase_store.set_game_analysis_status(job["game_id"], "complete"):
            raise RuntimeError("Could not complete move analysis")
    except Exception as error:
        supabase_store.set_game_analysis_status(job["game_id"], "failed", str(error)[:500])
    finally:
        with ai_queue_lock:
            analysis_jobs_by_game.pop(job["game_id"], None)
        finish_ai_job()


def dispatch_ai_scheduler(release_running=False):
    """Run the highest-priority eligible job without preempting current work."""
    global ai_active_jobs, ai_running_job_type
    next_job = None
    next_job_type = None
    queued_jobs = []
    blocked_by_admissions = False
    with ai_queue_lock:
        if release_running:
            ai_active_jobs = max(0, ai_active_jobs - 1)
            ai_running_job_type = "live_move" if ai_active_jobs else None
        if ai_job_queue and ai_active_jobs < AI_WORKER_COUNT:
            next_job = ai_job_queue.popleft()
            next_job_type = "live_move"
            queued_jobs = list(ai_job_queue)
        elif ai_active_jobs == 0 and (
            not ai_queue_reservations
            and not ai_admission_queue
            and not ai_admissions_in_progress
            and move_analysis_job_queue
        ):
            next_job = move_analysis_job_queue.popleft()
            next_job_type = "move_analysis"
        if next_job is not None:
            ai_active_jobs += 1
            ai_running_job_type = next_job_type
            if next_job_type == "move_analysis":
                next_job["state"] = "running"
        blocked_by_admissions = bool(not ai_active_jobs and move_analysis_job_queue and ai_admission_queue)

    if blocked_by_admissions:
        schedule_analysis_admission_wakeup()

    for position, queued_job in enumerate(queued_jobs, start=1):
        payload = queued_game_payload(queued_job, position=position)
        if payload is not None:
            socketio.emit("board_updated", payload, to=queued_job["game_id"])

    if next_job_type == "live_move":
        payload = queued_game_payload(next_job, active=True)
        if payload is not None:
            socketio.emit("board_updated", payload, to=next_job["game_id"])
            start_reserved_ai_job(next_job)
        else:
            dispatch_ai_scheduler(release_running=True)
    elif next_job_type == "move_analysis":
        start_move_analysis_job(next_job)


def finish_ai_job():
    dispatch_ai_scheduler(release_running=True)


def schedule_analysis_admission_wakeup():
    global analysis_admission_wakeup_scheduled
    with ai_queue_lock:
        if analysis_admission_wakeup_scheduled:
            return
        analysis_admission_wakeup_scheduled = True
    socketio.start_background_task(wake_analysis_after_stale_admissions)


def wake_analysis_after_stale_admissions():
    global analysis_admission_wakeup_scheduled
    while True:
        with ai_queue_lock:
            if not move_analysis_job_queue or not ai_admission_queue:
                analysis_admission_wakeup_scheduled = False
                return
            now = time.time()
            next_expiry = min(
                entry.get("last_checked_at", now) + AI_ADMISSION_STALE_SECONDS for entry in ai_admission_queue
            )
        socketio.sleep(max(0.05, next_expiry - time.time() + 0.01))
        with ai_queue_lock:
            prune_ai_admission_queue()
        dispatch_ai_scheduler()


def reset_ai_job_queue():
    global ai_active_jobs, ai_queue_reservations, ai_running_job_type, analysis_admission_wakeup_scheduled
    with ai_queue_lock:
        ai_job_queue.clear()
        move_analysis_job_queue.clear()
        analysis_jobs_by_game.clear()
        ai_active_jobs = 0
        ai_queue_reservations = 0
        ai_running_job_type = None
        analysis_admission_wakeup_scheduled = False


def active_ai_game_count():
    with games_lock:
        return sum(
            1 for game in games.values() if game.get("mode") == "ai" and game.get("status") not in FINISHED_STATUSES
        )


def prune_ai_admission_queue(now=None):
    now = now or time.time()
    active_entries = [
        entry for entry in ai_admission_queue if now - entry.get("last_checked_at", now) <= AI_ADMISSION_STALE_SECONDS
    ]
    ai_admission_queue.clear()
    ai_admission_queue.extend(active_entries)


def request_ai_admission(profile_id, difficulty, queue_id=None):
    global ai_admissions_in_progress
    now = time.time()
    with ai_admission_lock:
        prune_ai_admission_queue(now)
        entry = next(
            (
                queued_entry
                for queued_entry in ai_admission_queue
                if queue_id and queued_entry["queue_id"] == queue_id and queued_entry.get("profile_id") == profile_id
            ),
            None,
        )
        if entry is None and profile_id:
            entry = next(
                (queued_entry for queued_entry in ai_admission_queue if queued_entry.get("profile_id") == profile_id),
                None,
            )
        if entry is not None:
            entry["last_checked_at"] = now
            entry["difficulty"] = difficulty or entry["difficulty"]

        has_capacity = (
            ai_running_job_type != "move_analysis"
            and active_ai_game_count() + ai_admissions_in_progress < AI_ADMISSION_CAPACITY
        )
        is_front = not ai_admission_queue or entry is ai_admission_queue[0]
        if has_capacity and is_front:
            if entry is not None:
                ai_admission_queue.remove(entry)
            ai_admissions_in_progress += 1
            return True, None, 0

        if entry is None:
            entry = {
                "queue_id": uuid.uuid4().hex,
                "profile_id": profile_id,
                "difficulty": difficulty,
                "last_checked_at": now,
            }
            ai_admission_queue.append(entry)
        return False, entry, list(ai_admission_queue).index(entry) + 1


def finish_ai_admission():
    global ai_admissions_in_progress
    with ai_admission_lock:
        ai_admissions_in_progress = max(0, ai_admissions_in_progress - 1)
    dispatch_ai_scheduler()


def cancel_ai_admission(profile_id, queue_id):
    removed = False
    with ai_admission_lock:
        entry = next(
            (
                queued_entry
                for queued_entry in ai_admission_queue
                if queued_entry["queue_id"] == queue_id and queued_entry.get("profile_id") == profile_id
            ),
            None,
        )
        if entry is not None:
            ai_admission_queue.remove(entry)
            removed = True
    if removed:
        dispatch_ai_scheduler()
    return removed


def reset_ai_admission_queue():
    global ai_admissions_in_progress
    with ai_admission_lock:
        ai_admission_queue.clear()
        ai_admissions_in_progress = 0


def get_ai_move(board_data, settings, transposition_table, ai_piece=AI):
    if app.config.get("AI_SEARCH_INLINE"):
        return run_best_move(board_data, ai_piece, settings["depth"], settings["time_limit"], transposition_table), True

    future = get_ai_executor().submit(
        run_best_move,
        board_data,
        ai_piece,
        settings["depth"],
        settings["time_limit"],
        transposition_table,
    )
    try:
        return future.result(timeout=settings["time_limit"] + 1), True
    except FutureTimeoutError:
        # A running process cannot be canceled. Keep its slot reserved until it exits.
        future.add_done_callback(lambda _future: finish_ai_job())
        return (None, transposition_table), False
    except Exception:
        return (None, transposition_table), True


def serialize_game(game_id, game, ai_move=None, include_player_id=False, player_id=None):
    server_time_ms = current_time_ms()
    timer_banks = timer_banks_snapshot(game, server_time_ms)
    active_timer_player = game.get("timer_active_player")
    payload = {
        "gameId": game_id,
        "board": board_to_list(game["board"]),
        "status": game["status"],
        "message": game["message"],
        "aiMove": ai_move,
        "difficulty": game["difficulty"],
        "mode": game.get("mode", "ai"),
        "timeBanksMs": {str(player_number): remaining_ms for player_number, remaining_ms in timer_banks.items()},
        "activeTimerPlayer": active_timer_player,
        "timerRunning": bool(
            game.get("status") == "playing"
            and active_timer_player in timer_banks
            and game.get("timer_started_at_ms") is not None
        ),
        "serverTimeMs": int(server_time_ms),
        "endReason": game.get("end_reason"),
    }

    if "current_player" in game:
        payload["currentPlayer"] = game["current_player"]

    if game.get("mode") != "multiplayer":
        payload["aiThinking"] = bool(game.get("ai_thinking"))
        payload["aiQueued"] = bool(game.get("ai_queued"))
        payload["aiQueuePosition"] = game.get("ai_queue_position", 0)

    if include_player_id:
        payload["playerId"] = game["player_id"]

    if game.get("mode") == "multiplayer":
        payload["currentPlayer"] = game["current_player"]
        payload["playersConnected"] = sum(
            1 for player in game["players"].values() if player.get("connected") and player.get("ready")
        )
        payload["disconnectDeadline"] = game.get("disconnect_deadline")
        payload["playAgainAccepted"] = len(game.get("rematch_requests", set()))
        payload["publicRoom"] = bool(game.get("public"))
        payload["playerNames"] = {
            str(player["piece"]): player.get("display_name") or f"Player {player['piece']}"
            for player in game["players"].values()
        }
        if player_id in game["players"]:
            payload["playerId"] = player_id
            payload["playerNumber"] = game["players"][player_id]["piece"]
    else:
        payload["playerNumber"] = get_human_piece(game)
        payload["aiNumber"] = get_ai_piece(game)

    return payload


def prepare_ai_turn(game_id, game, expected_game=None, slot_reserved=False):
    if game is None or (expected_game is not None and game is not expected_game):
        if slot_reserved:
            release_ai_search_slot(slot_reserved)
        return None, None
    if (
        game.get("status") in FINISHED_STATUSES
        or game.get("current_player") != get_ai_piece(game)
        or game.get("ai_thinking")
    ):
        if slot_reserved:
            release_ai_search_slot(slot_reserved)
        return None, None
    reservation = slot_reserved or reserve_ai_search_slot()
    if not reservation:
        return None, AI_BUSY_MESSAGE

    game["ai_thinking"] = True
    game["ai_queued"] = reservation.get("state") == "queued"
    game["ai_queue_position"] = reservation.get("position", 0)
    game["message"] = f"AI queued - position {game['ai_queue_position']}" if game["ai_queued"] else "AI is thinking"
    mark_game_updated(game)
    supabase_store.update_game_record(game_id, game)
    return {
        "game_id": game_id,
        "expected_game": game,
        "move_number": game["move_number"],
        "board": board_to_list(game["board"]),
        "settings": DIFFICULTIES[game["difficulty"]],
        "ai_piece": get_ai_piece(game),
        "transposition_table": normalize_transposition_table(game.get("transposition_table")),
        "reservation": reservation,
    }, None


def apply_ai_result(game, game_id, ai_move, transposition_table):
    board = game["board"]
    ai_piece = get_ai_piece(game)
    human_piece = get_human_piece(game)
    game["ai_thinking"] = False
    game["ai_queued"] = False
    game["ai_queue_position"] = 0

    if ai_move is None or not is_valid_move(board, int(ai_move)):
        game["transposition_table"] = transposition_table
        game["current_player"] = human_piece
        game["message"] = "AI move failed. Your turn"
        start_game_timer(game_id, game, human_piece)
        mark_game_updated(game)
        supabase_store.update_game_record(game_id, game)
        return None, "AI returned an invalid move"

    ai_move = int(ai_move)
    board_before = board_to_list(board)
    drop_piece(board, ai_move, ai_piece)
    if game_id:
        supabase_store.record_move(
            game_id, game, ai_piece, ai_move, board_before, board_to_list(board), is_ai_move=True
        )
    game["transposition_table"] = transposition_table

    if check_win(board, ai_piece):
        finish_game(game, "ai_win", "AI wins", "connect_four")
    elif is_draw(board):
        finish_game(game, "draw", "Draw", "draw")
    else:
        game["status"] = "playing"
        game["current_player"] = human_piece
        game["message"] = "Your turn"
        start_game_timer(game_id, game, human_piece)

    mark_game_updated(game)
    if game_id:
        supabase_store.update_game_record(game_id, game)
    return ai_move, None


def complete_ai_turn(job):
    release_slot = True
    payload = None
    try:
        (ai_move, transposition_table), release_slot = get_ai_move(
            job["board"],
            job["settings"],
            job["transposition_table"],
            job["ai_piece"],
        )
        with get_game_lock(job["game_id"]):
            game = get_stored_game(job["game_id"])
            if (
                game is None
                or game is not job["expected_game"]
                or not game.get("ai_thinking")
                or game.get("move_number") != job["move_number"]
            ):
                return

            ai_move, error = apply_ai_result(game, job["game_id"], ai_move, transposition_table)
            payload = serialize_game(job["game_id"], game, ai_move=ai_move)
    except Exception:
        # Never leave a game permanently locked in an AI-thinking state when
        # a worker fails unexpectedly. The human can continue or refresh and
        # rejoin the same routed game.
        with get_game_lock(job["game_id"]):
            game = get_stored_game(job["game_id"])
            if (
                game is not None
                and game is job["expected_game"]
                and game.get("ai_thinking")
                and game.get("move_number") == job["move_number"]
            ):
                game["ai_thinking"] = False
                game["ai_queued"] = False
                game["ai_queue_position"] = 0
                game["current_player"] = get_human_piece(game)
                game["message"] = "AI move failed. Your turn"
                start_game_timer(job["game_id"], game, game["current_player"])
                mark_game_updated(game)
                supabase_store.update_game_record(job["game_id"], game)
                payload = serialize_game(job["game_id"], game)
    finally:
        if release_slot:
            finish_ai_job()

    if payload is not None:
        socketio.emit("board_updated", payload, to=job["game_id"])


def emit_ai_turn_if_needed(game_id, expected_game=None, slot_reserved=False):
    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        job, error = prepare_ai_turn(game_id, game, expected_game, slot_reserved)
        if error:
            payload = make_socket_error(game_id, game, error)
        elif job is not None:
            payload = serialize_game(game_id, game)

    if error:
        socketio.emit("invalid_move", payload, to=game_id)
        return
    if job is None:
        return
    if app.config.get("AI_SEARCH_INLINE"):
        launch_ai_turn(job)
        return
    socketio.emit("board_updated", payload, to=game_id)
    launch_ai_turn(job)


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
    payload["gameStatus"] = game["status"]
    payload["status"] = "invalid_move"
    payload["message"] = message
    return payload


def get_authorized_game(data):
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        return game_id, None, auth_error

    game = get_stored_game(game_id)
    error = validate_game_access(game_id, game, player_id, auth_context)
    return game_id, game, error


def validate_game_access(game_id, game, player_id, auth_context):
    if game is None:
        return "Game not found"

    if game.get("mode") == "multiplayer":
        if player_id not in game["players"]:
            return "Player does not have access to this game"
        if is_auth_required() and game["players"][player_id].get("profile_id") != auth_context["profile_id"]:
            return "Player does not have access to this game"
        return None

    if game["player_id"] != player_id:
        return "Player does not have access to this game"

    if is_auth_required() and game.get("profile_id") != auth_context["profile_id"]:
        return "Player does not have access to this game"

    return None


def sanitize_public_owner_name(value, fallback="Player"):
    if not isinstance(value, str):
        return fallback
    owner_name = " ".join(value.strip().split())
    if not owner_name:
        return fallback
    return owner_name[:32]


def parse_multiplayer_create_request_id(data):
    if not isinstance(data, dict) or "requestId" not in data or data.get("requestId") is None:
        return None, None

    request_id = data.get("requestId")
    if not isinstance(request_id, str):
        return None, "Invalid request ID"

    request_id = request_id.strip()
    if not request_id or len(request_id) > MULTIPLAYER_CREATE_REQUEST_ID_MAX_LENGTH:
        return None, "Invalid request ID"
    return request_id, None


def find_multiplayer_creation(profile_id, request_id):
    if profile_id is None or request_id is None:
        return None, None

    with games_lock:
        game_items = list(games.items())

    for game_id, game in game_items:
        if (
            game.get("mode") == "multiplayer"
            and game.get("creation_request_id") == request_id
            and game.get("creator_profile_id") == profile_id
        ):
            return game_id, game.get("creator_player_id")
    return None, None


def multiplayer_create_rejection(message, request_id=None, code="create_rejected"):
    event_payload = {"message": message, "code": code}
    if request_id is not None:
        event_payload["requestId"] = request_id
    emit("create_rejected", event_payload)
    return {
        "ok": False,
        "requestId": request_id,
        "code": code,
        "message": message,
    }


def multiplayer_create_success(game_payload, request_id=None, recovered=False):
    event_payload = dict(game_payload)
    if request_id is not None:
        event_payload.update({"requestId": request_id, "recovered": recovered})
    emit("multiplayer_game_created", event_payload)
    return {
        "ok": True,
        "requestId": request_id,
        "recovered": recovered,
        **game_payload,
    }


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def durable_multiplayer_room_error(room, profile_id, request_id):
    expected_profile_id = supabase_store.db_game_id(profile_id)
    if (
        not isinstance(room, dict)
        or expected_profile_id is None
        or room.get("profile_id") != expected_profile_id
        or room.get("request_id") != request_id
    ):
        return "creation_request_invalid", "Stored multiplayer room request is invalid"

    state = room.get("state")
    if state == "expired":
        return "creation_request_expired", "This multiplayer room request has expired"
    if state == "invalid":
        return "creation_request_invalid", "Stored multiplayer room request is invalid"
    if state != "active":
        return "creation_request_terminal", "This multiplayer room request is no longer active"

    expires_at = parse_timestamp(room.get("expires_at"))
    if expires_at is None:
        return "creation_request_invalid", "Stored multiplayer room request is invalid"
    if expires_at <= time.time():
        return "creation_request_expired", "This multiplayer room request has expired"

    if (
        room.get("game_mode") != "multiplayer"
        or room.get("game_difficulty") not in MULTIPLAYER_TIME_BANKS_MS
        or room.get("game_status") != "waiting"
        or room.get("player_count") != 1
        or room.get("owner_profile_id") != expected_profile_id
        or supabase_store.db_game_id(room.get("game_id")) is None
        or supabase_store.db_game_id(room.get("player_id")) is None
    ):
        return "creation_request_invalid", "Stored multiplayer room request is invalid"
    return None, None


def rebuild_durable_multiplayer_room(room, bind_socket):
    game_id = supabase_store.db_game_id(room.get("game_id"))
    player_id = supabase_store.db_game_id(room.get("player_id"))
    profile_id = supabase_store.db_game_id(room.get("profile_id"))
    if game_id is None or player_id is None or profile_id is None:
        return None, None, None

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if game is None:
            game = create_multiplayer_game_state(
                player_id,
                profile_id=profile_id,
                bind_socket=bind_socket,
                difficulty=room["game_difficulty"],
            )
            game["owner_name"] = sanitize_public_owner_name(room.get("owner_name"))
            game["players"][player_id]["display_name"] = game["owner_name"]
            game["creation_request_id"] = room["request_id"]
            game["creator_profile_id"] = profile_id
            game["creator_player_id"] = player_id
            game["durable_expires_at"] = parse_timestamp(room.get("expires_at"))
            store_game(game_id, game)
        else:
            player = game.get("players", {}).get(player_id)
            if (
                game.get("mode") != "multiplayer"
                or game.get("creation_request_id") != room.get("request_id")
                or game.get("creator_profile_id") != profile_id
                or player is None
                or player.get("profile_id") != profile_id
            ):
                return None, None, None
            if bind_socket:
                mark_multiplayer_player_connected(game_id, game, player_id)
    return game_id, player_id, game


def recover_in_memory_multiplayer_room(profile_id, request_id, bind_socket):
    game_id, player_id = find_multiplayer_creation(profile_id, request_id)
    if not game_id or not player_id:
        return None, None, None

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        player = (game or {}).get("players", {}).get(player_id)
        if (
            game is None
            or game.get("mode") != "multiplayer"
            or game.get("status") != "waiting"
            or len(game.get("players", {})) != 1
            or (game.get("durable_expires_at") is not None and game.get("durable_expires_at") <= time.time())
            or game.get("creation_request_id") != request_id
            or game.get("creator_profile_id") != profile_id
            or player is None
            or player.get("profile_id") != profile_id
        ):
            return None, None, None
        if bind_socket:
            mark_multiplayer_player_connected(game_id, game, player_id)
    return game_id, player_id, game


def prepare_durable_multiplayer_room(room, profile_id, request_id, bind_socket):
    error_code, error_message = durable_multiplayer_room_error(room, profile_id, request_id)
    if error_code:
        if error_code == "creation_request_expired":
            supabase_store.resolve_multiplayer_room_request(room.get("game_id"), "expired")
        elif error_code == "creation_request_invalid":
            supabase_store.resolve_multiplayer_room_request(room.get("game_id"), "invalid")
        return None, None, None, error_code, error_message

    game_id, player_id, game = rebuild_durable_multiplayer_room(room, bind_socket)
    if game is None:
        supabase_store.resolve_multiplayer_room_request(room.get("game_id"), "invalid")
        return (
            None,
            None,
            None,
            "creation_request_invalid",
            "Stored multiplayer room request is invalid",
        )
    return game_id, player_id, game, None, None


def reconcile_multiplayer_rejection(request_id, code, message):
    return {
        "ok": False,
        "status": "rejected",
        "requestId": request_id,
        "code": code,
        "message": message,
    }


def multiplayer_join_rejection(game_id, message, request_id=None):
    payload = {"gameId": game_id, "message": message}
    if request_id is not None:
        payload["requestId"] = request_id
    emit("join_rejected", payload)


def public_game_payload(game_id, game):
    return {
        "gameId": game_id,
        "ownerName": sanitize_public_owner_name(game.get("owner_name")),
        "difficulty": game.get("difficulty", "multiplayer"),
    }


def list_public_games_payload():
    with games_lock:
        game_items = list(games.items())

    public_games = []
    for game_id, game in game_items:
        if (
            game.get("mode") == "multiplayer"
            and game.get("public")
            and game.get("status") == "waiting"
            and len(game.get("players", {})) == 1
        ):
            public_games.append(public_game_payload(game_id, game))
    return public_games


def apply_human_and_ai_move(game, column, game_id=None):
    board = game["board"]
    human_piece = get_human_piece(game)
    ai_piece = get_ai_piece(game)
    transposition_table = normalize_transposition_table(game.get("transposition_table"))
    move_received_at_ms = current_time_ms()

    if (
        game["status"] in FINISHED_STATUSES
        or check_win(board, human_piece)
        or check_win(board, ai_piece)
        or is_draw(board)
    ):
        return None, "Game is already over"

    if expire_active_timer_if_needed(game_id, game, move_received_at_ms):
        return None, None

    if not isinstance(column, int):
        return None, "Invalid column"

    if column < 0 or column >= COLS:
        return None, "Column out of range"

    if game.get("current_player") != human_piece:
        return None, "Not your turn"

    if not is_valid_move(board, column):
        return None, "Column is full"

    preview_board = board.copy()
    drop_piece(preview_board, column, human_piece)
    needs_ai_turn = not check_win(preview_board, human_piece) and not is_draw(preview_board)
    ai_reservation = reserve_ai_search_slot() if needs_ai_turn else None
    if needs_ai_turn and not ai_reservation:
        return None, AI_BUSY_MESSAGE

    pause_game_timer(game, move_received_at_ms)
    board_before = board_to_list(board)
    drop_piece(board, column, human_piece)
    if game_id:
        supabase_store.record_move(game_id, game, human_piece, column, board_before, board_to_list(board))

    if check_win(board, human_piece):
        finish_game(game, "human_win", "You win", "connect_four")
        game["transposition_table"] = transposition_table
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None, None

    if is_draw(board):
        finish_game(game, "draw", "Draw", "draw")
        game["transposition_table"] = transposition_table
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None, None

    game["current_player"] = ai_piece
    job, error = prepare_ai_turn(game_id, game, slot_reserved=ai_reservation)
    if error:
        return None, error
    return job, None


def apply_multiplayer_move(game, player_id, column, game_id=None):
    board = game["board"]
    player = game["players"].get(player_id)
    move_received_at_ms = current_time_ms()

    if player is None:
        return "Player does not have access to this game"

    if len(game["players"]) < 2:
        return "Waiting for Player 2"

    if game["status"] != "playing":
        return "Waiting for both players to enter the game"

    if expire_active_timer_if_needed(game_id, game, move_received_at_ms):
        return None

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

    pause_game_timer(game, move_received_at_ms)
    board_before = board_to_list(board)
    drop_piece(board, column, player["piece"])
    if game_id:
        # Re-sync both memberships before every move. This repairs a transient
        # join-time database failure and ensures both profiles own the same
        # completed game and can read the full shared move history.
        supabase_store.add_game_player_records(game_id, game)
        supabase_store.record_move(
            game_id,
            game,
            player["piece"],
            column,
            board_before,
            board_to_list(board),
            profile_id=player.get("profile_id"),
        )

    if check_win(board, player["piece"]):
        finish_game(
            game,
            "player1_win" if player["piece"] == HUMAN else "player2_win",
            f"{multiplayer_name_for_piece(game, player['piece'])} wins",
            "connect_four",
        )
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None

    if is_draw(board):
        player_one_time = game["time_banks_ms"][HUMAN]
        player_two_time = game["time_banks_ms"][AI]
        if player_one_time == player_two_time:
            finish_game(game, "draw", "Draw", "draw")
        else:
            winning_piece = HUMAN if player_one_time > player_two_time else AI
            finish_game(
                game,
                "player1_win" if winning_piece == HUMAN else "player2_win",
                f"{multiplayer_name_for_piece(game, winning_piece)} won by timer tiebreak",
                "time_tiebreak",
            )
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None

    game["current_player"] = AI if player["piece"] == HUMAN else HUMAN
    game["status"] = "playing"
    game["message"] = f"Player {game['current_player']} turn"
    start_game_timer(game_id, game, game["current_player"])
    mark_game_updated(game)
    if game_id:
        supabase_store.update_game_record(game_id, game)
    return None


def find_multiplayer_player_by_sid(socket_id):
    with games_lock:
        game_items = list(games.items())

    for game_id, game in game_items:
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
    player["ready"] = False
    player["disconnect_token"] = None
    if all(current_player.get("connected") for current_player in game["players"].values()):
        game["disconnect_deadline"] = None
        if game["status"] == "playing":
            game["message"] = f"Player {game['current_player']} turn"
    mark_game_updated(game)
    app.logger.info("Player reconnected game_id=%s player_number=%s", game_id, player["piece"])


def start_multiplayer_disconnect_timer(game_id, player_id, disconnect_token):
    socketio.sleep(DISCONNECT_GRACE_SECONDS)

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if game is None or game.get("mode") != "multiplayer":
            return

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
            finish_game(game, "draw", "Game abandoned", "abandoned")
            game["disconnect_deadline"] = None
            mark_game_updated(game)
            supabase_store.update_game_record(game_id, game)
            payload = serialize_game(game_id, game)
        else:
            winning_piece = other_player["piece"]
            finish_game(
                game,
                "player1_win" if winning_piece == HUMAN else "player2_win",
                f"Player {winning_piece} wins by default",
                "disconnect",
            )
            game["disconnect_deadline"] = None
            mark_game_updated(game)
            supabase_store.update_game_record(game_id, game)
            payload = serialize_game(game_id, game)
    socketio.emit("board_updated", payload, to=game_id)


def is_multiplayer_finished(game):
    return game["status"] in {"player1_win", "player2_win", "draw"}


def is_completed_game(game):
    return game["status"] in COMPLETED_STATUSES


def finalize_or_delete_previous_game(game_id, game):
    if is_completed_game(game):
        supabase_store.update_game_record(game_id, game)
    else:
        supabase_store.delete_game_record(game_id)


def reset_multiplayer_game(game):
    now = time.time()
    game["board"] = create_board()
    game["status"] = "playing" if len(game["players"]) == 2 else "waiting"
    game["public"] = False
    assign_multiplayer_pieces(game)
    game["message"] = f"Player {game['current_player']} turn" if len(game["players"]) == 2 else "Waiting for Player 2"
    game["disconnect_deadline"] = None
    game["rematch_requests"] = set()
    game["move_number"] = 0
    time_bank_ms = multiplayer_time_bank_ms(game.get("difficulty"))
    game["time_banks_ms"] = {HUMAN: time_bank_ms, AI: time_bank_ms}
    game["timer_active_player"] = None
    game["timer_started_at_ms"] = None
    game["timer_generation"] = game.get("timer_generation", 0) + 1
    game["end_reason"] = None
    game["created_at"] = now
    mark_game_updated(game)


def move_multiplayer_room(old_game_id, new_game_id, game):
    for player in game.get("players", {}).values():
        socket_id = player.get("socket_id")
        if not socket_id:
            continue
        socketio.server.enter_room(socket_id, new_game_id, namespace="/")
        socketio.server.leave_room(socket_id, old_game_id, namespace="/")


def emit_multiplayer_board_update(game_id, game):
    for player_id, player in game.get("players", {}).items():
        socket_id = player.get("socket_id")
        if socket_id:
            socketio.emit("board_updated", serialize_game(game_id, game, player_id=player_id), to=socket_id)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/new-game")
def new_game():
    data = request.get_json(silent=True) or {}
    difficulty = parse_difficulty(data)
    return jsonify(empty_response(difficulty))


@app.get("/api/profile/games")
def profile_games():
    auth_context, auth_error = authenticate_request()
    if auth_error:
        return jsonify({"message": auth_error}), 401

    profile_id = auth_context.get("profile_id")
    if not profile_id:
        return jsonify({"message": "Login required"}), 401

    try:
        # Repair a participant row if its join-time database write failed.
        with games_lock:
            profile_games_to_repair = [
                (game_id, game)
                for game_id, game in games.items()
                if game.get("mode") == "multiplayer"
                and any(player.get("profile_id") == profile_id for player in game.get("players", {}).values())
            ]
        for game_id, game in profile_games_to_repair:
            supabase_store.add_game_player_records(game_id, game)

        completed_games = supabase_store.fetch_completed_games(profile_id)
    except Exception as error:
        app.logger.warning(
            "Could not load profile games profile_id=%s error=%s",
            profile_id,
            error.__class__.__name__,
        )
        return jsonify(
            {
                "message": GAME_REVIEW_UNAVAILABLE_MESSAGE,
                "code": "profile_games_unavailable",
            }
        ), 503

    return jsonify({"games": completed_games})


@app.get("/api/profile/games/<game_id>/moves")
def profile_game_moves(game_id):
    auth_context, auth_error = authenticate_request()
    if auth_error:
        return jsonify({"message": auth_error}), 401

    profile_id = auth_context.get("profile_id")
    if not profile_id:
        return jsonify({"message": "Login required"}), 401

    try:
        review = supabase_store.fetch_game_moves(profile_id, game_id)
    except Exception as error:
        app.logger.warning("Could not load game review (%s)", error.__class__.__name__)
        return jsonify(
            {
                "message": GAME_REVIEW_UNAVAILABLE_MESSAGE,
                "code": "game_review_unavailable",
            }
        ), 503
    if review is None or not review["moves"]:
        return jsonify({"message": "Game history not found"}), 404

    if review.get("analysis_available", True) and review.get("analysis_status") == "processing":
        with ai_queue_lock:
            if game_id not in analysis_jobs_by_game:
                # Analysis jobs are intentionally in-memory in this single-process
                # deployment. Reconcile a status orphaned by a restart or failed
                # final status write so clients stop polling and can retry.
                supabase_store.set_game_analysis_status(
                    game_id,
                    "failed",
                    MOVE_ANALYSIS_INTERRUPTED_MESSAGE,
                )
                review["analysis_status"] = "failed"
                review["analysis_error"] = MOVE_ANALYSIS_INTERRUPTED_MESSAGE

    return jsonify(review)


@app.post("/api/profile/games/<game_id>/analysis")
def request_profile_game_analysis(game_id):
    auth_context, auth_error = authenticate_request()
    if auth_error:
        return jsonify({"message": auth_error}), 401
    profile_id = auth_context.get("profile_id")
    if not profile_id:
        return jsonify({"message": "Login required"}), 401

    try:
        source = supabase_store.fetch_game_analysis_source(profile_id, game_id)
    except Exception as error:
        app.logger.warning("Could not load move-analysis source (%s)", error.__class__.__name__)
        return jsonify(
            {
                "message": GAME_REVIEW_UNAVAILABLE_MESSAGE,
                "code": "game_review_unavailable",
            }
        ), 503
    if source is None:
        return jsonify({"message": "Completed game history not found"}), 404
    if not source.get("analysis_available", True):
        return jsonify(
            {
                "message": source.get("analysis_unavailable_reason")
                or supabase_store.MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE,
                "code": "move_analysis_schema_update_required",
            }
        ), 503
    if source["analysis_status"] == "complete":
        return jsonify({"gameId": game_id, "status": "complete"})
    if not source["moves"]:
        return jsonify({"message": "No recorded moves to analyze"}), 422
    if any(move.get("id") is None for move in source["moves"]):
        return jsonify(
            {
                "message": "Move evaluation is unavailable because this game has incomplete move history.",
                "code": "incomplete_move_history",
            }
        ), 422

    with ai_queue_lock:
        existing = analysis_jobs_by_game.get(game_id)
        if existing is None:
            # Keep the persisted processing state and in-memory registration
            # atomic with respect to orphan detection in the review endpoint.
            if not supabase_store.set_game_analysis_status(game_id, "processing"):
                return jsonify({"message": "Could not queue move analysis"}), 503
            job = enqueue_move_analysis(game_id, source["moves"])
        else:
            job = existing

    with ai_queue_lock:
        position = 0
        if job.get("state") == "queued" and job in move_analysis_job_queue:
            position = list(move_analysis_job_queue).index(job) + 1
        status = job.get("state", "queued")
    return jsonify(
        {
            "gameId": game_id,
            "status": status,
            "queuePosition": position,
            "priority": "move_analysis",
        }
    ), 202


def create_admitted_ai_game(auth_context, difficulty):
    remove_ai_games_for_sid(request.sid)
    game_id = uuid.uuid4().hex
    game = create_game_state(difficulty, profile_id=auth_context["profile_id"])
    ai_starts = game["current_player"] == get_ai_piece(game)
    ai_slot_reserved = reserve_ai_search_slot() if ai_starts else False
    if ai_starts and not ai_slot_reserved:
        emit("create_rejected", {"message": AI_BUSY_MESSAGE})
        return
    supabase_store.create_game_record(game_id, game)
    store_game(game_id, game)
    join_room(game_id)
    if not ai_starts:
        start_game_timer(game_id, game, get_human_piece(game))
    emit("game_created", serialize_game(game_id, game, include_player_id=True))
    emit_ai_turn_if_needed(game_id, game, ai_slot_reserved)


def emit_ai_admission_result(auth_context, difficulty, queue_id=None):
    admitted, entry, position = request_ai_admission(auth_context["profile_id"], difficulty, queue_id)
    if not admitted:
        emit(
            "ai_waiting",
            {
                "queueId": entry["queue_id"],
                "position": position,
                "difficulty": entry["difficulty"],
                "checkIntervalSeconds": 20,
                "message": "AI player is currently busy right now.",
            },
        )
        return

    try:
        create_admitted_ai_game(auth_context, difficulty)
    finally:
        finish_ai_admission()


@socketio.on("create_game")
def socket_create_game(data):
    data = data or {}
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("create_rejected", {"message": auth_error})
        return

    error = check_create_allowed()
    if error:
        emit("create_rejected", {"message": error})
        return

    emit_ai_admission_result(auth_context, parse_difficulty(data))


@socketio.on("check_ai_waiting")
def socket_check_ai_waiting(data):
    data = data or {}
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("create_rejected", {"message": auth_error})
        return
    queue_id = data.get("queueId") if isinstance(data, dict) else None
    if not queue_id:
        emit("create_rejected", {"message": "AI waiting room not found"})
        return
    emit_ai_admission_result(auth_context, parse_difficulty(data), queue_id)


@socketio.on("cancel_ai_waiting")
def socket_cancel_ai_waiting(data):
    data = data or {}
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("create_rejected", {"message": auth_error})
        return
    queue_id = data.get("queueId") if isinstance(data, dict) else None
    if queue_id:
        cancel_ai_admission(auth_context["profile_id"], queue_id)
    emit("ai_waiting_cancelled", {"queueId": queue_id})


@socketio.on("join_game")
def socket_join_game(data):
    data = data or {}
    auth_context, auth_error = authenticate_payload(data or {})
    if auth_error:
        emit("join_rejected", {"gameId": None, "message": auth_error})
        return

    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    if not game_id:
        emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if game is None:
            emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
            return

        if game.get("mode") == "multiplayer":
            access_error = validate_game_access(game_id, game, player_id, auth_context)
            if access_error:
                emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
                return
            mark_multiplayer_player_connected(game_id, game, player_id)
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = serialize_game(game_id, game)
            join_event = "multiplayer"
        else:
            access_error = validate_game_access(game_id, game, player_id, auth_context)
            if access_error:
                emit("join_rejected", {"gameId": game_id, "message": "Game not found or player does not match"})
                return
            game["socket_id"] = request.sid
            mark_game_updated(game)
            joined_payload = serialize_game(game_id, game, include_player_id=True)
            updated_payload = None
            join_event = "ai"

    join_room(game_id)
    emit("game_joined", joined_payload)
    if join_event == "multiplayer":
        emit("board_updated", updated_payload, to=game_id)
    return


@socketio.on("create_multiplayer_game")
def socket_create_multiplayer_game(data=None):
    data = data or {}
    request_id, request_id_error = parse_multiplayer_create_request_id(data)
    difficulty, difficulty_error = parse_multiplayer_difficulty(data)
    auth_context, auth_error = authenticate_payload(data or {})
    if auth_error:
        return multiplayer_create_rejection(auth_error, request_id, "authentication_failed")
    if request_id_error:
        return multiplayer_create_rejection(request_id_error, None, "invalid_request")
    if difficulty_error:
        return multiplayer_create_rejection(difficulty_error, request_id, "invalid_multiplayer_mode")

    profile_id = auth_context["profile_id"]
    game = None
    game_id = None
    player_id = None
    game_payload = None
    recovered = False
    created = False
    fallback_persistence = False

    # Serialize the request-id lookup and insertion so two simultaneous retries
    # cannot create separate rooms. Per-game state is still mutated under that
    # game's lock, and games_lock is never held while acquiring a game lock.
    with multiplayer_create_lock:
        cleanup_stale_games()
        in_memory_creation_id, _ = find_multiplayer_creation(profile_id, request_id)
        game_id, player_id, game = recover_in_memory_multiplayer_room(profile_id, request_id, True)
        if game is not None:
            game_payload = serialize_game(game_id, game, player_id=player_id)
            recovered = True
        elif in_memory_creation_id is not None:
            return multiplayer_create_rejection(
                "This multiplayer room request is no longer active",
                request_id,
                "creation_request_terminal",
            )

        if game is None:
            owner_name = authenticated_display_name(auth_context)
            persistence_result = {"result": "disabled"}
            if request_id is not None and profile_id is not None:
                persistence_result = supabase_store.fetch_multiplayer_room_request(profile_id, request_id)
                if persistence_result.get("result") == "ok":
                    room = persistence_result["room"]
                    game_id, player_id, game, error_code, error_message = prepare_durable_multiplayer_room(
                        room,
                        profile_id,
                        request_id,
                        True,
                    )
                    if error_code:
                        return multiplayer_create_rejection(error_message, request_id, error_code)
                    game_payload = serialize_game(game_id, game, player_id=player_id)
                    recovered = True
                elif persistence_result.get("result") == "error":
                    return multiplayer_create_rejection(
                        "Multiplayer room persistence is temporarily unavailable. Try again.",
                        request_id,
                        persistence_result.get("code") or "persistence_unavailable",
                    )
                elif persistence_result.get("result") == "schema_missing" and difficulty in FAST_CONNECT_DIFFICULTIES:
                    return multiplayer_create_rejection(
                        "Fast Connect requires the latest Supabase multiplayer migration.",
                        request_id,
                        "fast_connect_schema_update_required",
                    )

            if game is None:
                error = check_create_allowed()
                if error:
                    return multiplayer_create_rejection(error, request_id)

                proposed_game_id = str(uuid.uuid4())
                proposed_player_id = str(uuid.uuid4())
                if (
                    request_id is not None
                    and profile_id is not None
                    and persistence_result.get("result") == "not_found"
                ):
                    claim_result = supabase_store.claim_multiplayer_room_request(
                        profile_id,
                        request_id,
                        proposed_game_id,
                        proposed_player_id,
                        owner_name,
                        difficulty,
                    )
                    if claim_result.get("result") == "ok":
                        room = claim_result["room"]
                        game_id, player_id, game, error_code, error_message = prepare_durable_multiplayer_room(
                            room,
                            profile_id,
                            request_id,
                            True,
                        )
                        if error_code:
                            return multiplayer_create_rejection(error_message, request_id, error_code)
                        game_payload = serialize_game(game_id, game, player_id=player_id)
                        created = bool(room.get("created"))
                        recovered = not created
                    elif claim_result.get("result") == "error":
                        return multiplayer_create_rejection(
                            "Multiplayer room persistence is temporarily unavailable. Try again.",
                            request_id,
                            claim_result.get("code") or "persistence_unavailable",
                        )
                    elif claim_result.get("result") == "schema_missing" and difficulty in FAST_CONNECT_DIFFICULTIES:
                        return multiplayer_create_rejection(
                            "Fast Connect requires the latest Supabase multiplayer migration.",
                            request_id,
                            "fast_connect_schema_update_required",
                        )
                    else:
                        persistence_result = claim_result

                if game is None:
                    # Supabase is disabled or the optional recovery migration is
                    # not installed. Preserve the original in-memory behavior.
                    game_id = proposed_game_id
                    player_id = proposed_player_id
                    game = create_multiplayer_game_state(
                        player_id,
                        profile_id=profile_id,
                        difficulty=difficulty,
                    )
                    game["owner_name"] = owner_name
                    game["players"][player_id]["display_name"] = owner_name
                    if request_id is not None and profile_id is not None:
                        game["creation_request_id"] = request_id
                        game["creator_profile_id"] = profile_id
                        game["creator_player_id"] = player_id
                    game_payload = serialize_game(game_id, game, player_id=player_id)
                    store_game(game_id, game)
                    created = True
                    fallback_persistence = True

    if fallback_persistence:
        supabase_store.create_game_record(game_id, game)
    join_room(game_id)
    if created:
        app.logger.info("Multiplayer player connected game_id=%s player_number=1", game_id)
    return multiplayer_create_success(game_payload, request_id, recovered)


@socketio.on("reconcile_multiplayer_creation")
def socket_reconcile_multiplayer_creation(data=None):
    data = data or {}
    request_id, request_id_error = parse_multiplayer_create_request_id(data)
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        return reconcile_multiplayer_rejection(request_id, "authentication_failed", auth_error)
    if request_id_error or request_id is None:
        return reconcile_multiplayer_rejection(None, "invalid_request", request_id_error or "Invalid request ID")

    profile_id = auth_context["profile_id"]
    with multiplayer_create_lock:
        cleanup_stale_games()
        in_memory_creation_id, _ = find_multiplayer_creation(profile_id, request_id)
        game_id, player_id, game = recover_in_memory_multiplayer_room(profile_id, request_id, False)
        if game is not None:
            return {
                "ok": True,
                "status": "found",
                "requestId": request_id,
                "gameId": game_id,
                "playerId": player_id,
            }
        if in_memory_creation_id is not None:
            return reconcile_multiplayer_rejection(
                request_id,
                "creation_request_terminal",
                "This multiplayer room request is no longer active",
            )

        persistence_result = supabase_store.fetch_multiplayer_room_request(profile_id, request_id)
        if persistence_result.get("result") in {"disabled", "schema_missing", "not_found"}:
            return {"ok": True, "status": "not_found", "requestId": request_id}
        if persistence_result.get("result") == "error":
            return reconcile_multiplayer_rejection(
                request_id,
                persistence_result.get("code") or "persistence_unavailable",
                "Multiplayer room persistence is temporarily unavailable. Try again.",
            )

        room = persistence_result["room"]
        game_id, player_id, game, error_code, error_message = prepare_durable_multiplayer_room(
            room,
            profile_id,
            request_id,
            False,
        )
        if error_code:
            return reconcile_multiplayer_rejection(request_id, error_code, error_message)
        return {
            "ok": True,
            "status": "found",
            "requestId": request_id,
            "gameId": game_id,
            "playerId": player_id,
        }


@socketio.on("list_public_games")
def socket_list_public_games(data=None):
    auth_context, auth_error = authenticate_payload(data or {})
    if auth_error:
        emit("public_games", {"games": []})
        return

    emit("public_games", {"games": list_public_games_payload()})


@socketio.on("set_room_public")
def socket_set_room_public(data):
    data = data or {}
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    requested_public = bool(data.get("public")) if isinstance(data, dict) else False
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("room_public_update_failed", {"gameId": game_id, "message": auth_error})
        return
    if not game_id:
        emit("room_public_update_failed", {"gameId": game_id, "message": "Game not found"})
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        error = validate_game_access(game_id, game, player_id, auth_context)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        elif game.get("mode") != "multiplayer" or game.get("status") != "waiting" or len(game.get("players", {})) != 1:
            invalid_payload = make_socket_error(game_id, game, "Only waiting rooms can be public")
            error = "invalid"
        else:
            player = game["players"].get(player_id)
            if player is None or player.get("piece") != HUMAN:
                invalid_payload = make_socket_error(game_id, game, "Only the room owner can make it public")
                error = "invalid"
            else:
                now = time.time()
                last_changed_at = game.get("public_changed_at", 0)
                retry_after = ROOM_VISIBILITY_RATE_LIMIT_SECONDS - (now - last_changed_at)
                if retry_after > 0:
                    invalid_payload = make_socket_error(
                        game_id,
                        game,
                        f"Please wait {max(1, int(retry_after + 0.999))} seconds before changing room visibility",
                    )
                    invalid_payload["retryAfterMs"] = max(1, int(retry_after * 1000))
                    error = "rate_limited"
                else:
                    game["public"] = requested_public
                    game["public_changed_at"] = now
                    mark_game_updated(game)
                    error = None

    if error:
        emit(
            "room_public_update_failed",
            {
                "gameId": game_id,
                "publicRoom": bool((get_stored_game(game_id) or {}).get("public")),
                "message": invalid_payload["message"],
                "retryAfterMs": invalid_payload.get("retryAfterMs", 0),
            },
        )
        return

    confirmed_public = bool(game.get("public"))
    if confirmed_public != requested_public:
        emit(
            "room_public_update_failed",
            {
                "gameId": game_id,
                "publicRoom": confirmed_public,
                "message": "Room visibility could not be confirmed",
            },
        )
        return

    emit(
        "room_public_updated",
        {
            "gameId": game_id,
            "publicRoom": confirmed_public,
            "message": "Room is now public" if confirmed_public else "Room is now private",
        },
    )


@socketio.on("join_multiplayer_game")
def socket_join_multiplayer_game(data):
    data = data or {}
    request_id, request_id_error = parse_multiplayer_create_request_id(data)
    game_id = data.get("gameId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data or {})
    if auth_error:
        multiplayer_join_rejection(game_id, auth_error, request_id)
        return
    if request_id_error:
        multiplayer_join_rejection(game_id, request_id_error, None)
        return

    player_id = data.get("playerId") if isinstance(data, dict) else None
    if not game_id:
        multiplayer_join_rejection(game_id, "Multiplayer game not found", request_id)
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if game is None or game.get("mode") != "multiplayer":
            multiplayer_join_rejection(game_id, "Multiplayer game not found", request_id)
            return

        if player_id in game["players"]:
            if is_auth_required() and game["players"][player_id].get("profile_id") != auth_context["profile_id"]:
                multiplayer_join_rejection(game_id, "Multiplayer game not found", request_id)
                return
            mark_multiplayer_player_connected(game_id, game, player_id)
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = serialize_game(game_id, game)
            is_rejoin = True

        elif len(game["players"]) >= 2:
            multiplayer_join_rejection(game_id, "Multiplayer game is full", request_id)
            return

        elif data.get("publicJoin") and not game.get("public"):
            multiplayer_join_rejection(game_id, "Room is no longer public", request_id)
            return

        else:
            player_id = str(uuid.uuid4())
            game["players"][player_id] = {
                "piece": AI,
                "profile_id": auth_context["profile_id"],
                "display_name": authenticated_display_name(auth_context),
                "socket_id": request.sid,
                "connected": True,
                "ready": False,
                "disconnect_token": None,
            }
            assign_multiplayer_pieces(game)
            game["status"] = "waiting"
            game["public"] = False
            game["message"] = "Waiting for both players to enter the game"
            mark_game_updated(game)
            supabase_store.add_game_player_records(game_id, game)
            if game.get("creation_request_id"):
                supabase_store.resolve_multiplayer_room_request(game_id, "completed")
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = None
            is_rejoin = False

        if request_id is not None:
            joined_payload["requestId"] = request_id

    join_room(game_id)
    emit("multiplayer_game_joined", joined_payload)
    if is_rejoin:
        emit("board_updated", updated_payload, to=game_id)
    else:
        app.logger.info("Multiplayer player connected game_id=%s player_number=2", game_id)
        emit_multiplayer_board_update(game_id, game)


@socketio.on("multiplayer_player_ready")
def socket_multiplayer_player_ready(data):
    data = data or {}
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("invalid_move", make_socket_error(game_id, None, auth_error))
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        error = validate_game_access(game_id, game, player_id, auth_context)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        elif game.get("mode") != "multiplayer":
            error = "Not a multiplayer game"
            invalid_payload = make_socket_error(game_id, game, error)
        else:
            player = game["players"][player_id]
            if player.get("socket_id") != request.sid:
                error = "Player connection changed"
                invalid_payload = make_socket_error(game_id, game, error)
            else:
                error = None
                player["ready"] = True
                if (
                    len(game["players"]) == 2
                    and all(current.get("connected") and current.get("ready") for current in game["players"].values())
                    and game["status"] == "waiting"
                ):
                    game["status"] = "playing"
                    game["message"] = f"Player {game['current_player']} turn"
                    game["disconnect_deadline"] = None
                    start_game_timer(game_id, game, game["current_player"])
                    supabase_store.update_game_record(game_id, game)
                elif game["status"] == "waiting":
                    game["message"] = (
                        "Waiting for Player 2"
                        if len(game["players"]) < 2
                        else "Waiting for both players to enter the game"
                    )
                mark_game_updated(game)

    if error:
        emit("invalid_move", invalid_payload)
        return
    emit_multiplayer_board_update(game_id, game)


@socketio.on("disconnect")
def socket_disconnect():
    with games_lock:
        disconnected_ai_games = [
            (game_id, game)
            for game_id, game in games.items()
            if game.get("mode") == "ai" and game.get("socket_id") == request.sid
        ]
    if disconnected_ai_games:
        for game_id, expected_game in disconnected_ai_games:
            with get_game_lock(game_id):
                game = get_stored_game(game_id)
                if game is expected_game and game.get("socket_id") == request.sid:
                    game["socket_id"] = None
                    mark_game_updated(game)
        return

    game_id, game, player_id, player = find_multiplayer_player_by_sid(request.sid)
    if game is None:
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if game is None or game.get("mode") != "multiplayer":
            return
        player = game["players"].get(player_id)
        if player is None or player.get("socket_id") != request.sid:
            return

        player["connected"] = False
        player["socket_id"] = None
        player["disconnect_token"] = uuid.uuid4().hex
        app.logger.info("Player disconnected game_id=%s player_number=%s", game_id, player["piece"])

        if len(game["players"]) < 2 or game["status"] in FINISHED_STATUSES:
            game["public"] = False
            mark_game_updated(game)
            return

        if game["status"] != "playing":
            player["ready"] = False
            game["message"] = "Waiting for both players to enter the game"
            game["disconnect_deadline"] = None
            mark_game_updated(game)
            emit_multiplayer_board_update(game_id, game)
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
    data = data or {}
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("invalid_move", make_socket_error(game_id, None, auth_error))
        return
    if not game_id:
        emit("invalid_move", make_socket_error(game_id, None, "Game not found"))
        return

    player_left_payload = None
    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        error = validate_game_access(game_id, game, player_id, auth_context)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
            leave_allowed = False
        elif game.get("mode") != "multiplayer":
            finalize_or_delete_previous_game(game_id, game)
            pop_game(game_id)
            leave_allowed = True
        else:
            leave_allowed = False

        player_id = data.get("playerId")
        if not leave_allowed and game is not None and game.get("mode") == "multiplayer" and error is None:
            player = game["players"].get(player_id)
            if game["status"] == "waiting" and len(game["players"]) == 1:
                resolution = supabase_store.resolve_multiplayer_room_request(game_id, "cancelled")
                if resolution.get("result") == "error":
                    invalid_payload = make_socket_error(
                        game_id,
                        game,
                        "Could not cancel room right now. Try again.",
                    )
                else:
                    game["status"] = "abandoned"
                    game["message"] = "Room cancelled"
                    game["public"] = False
                    mark_game_updated(game)
                    if resolution.get("result") in {"disabled", "schema_missing"}:
                        supabase_store.update_game_record(game_id, game)
                    pop_game(game_id)
                    leave_allowed = True
            elif is_multiplayer_finished(game):
                player_left_payload = {"gameId": game_id, "message": f"Player {player['piece']} left the room"}
                finalize_or_delete_previous_game(game_id, game)
                player["left"] = True
                player["connected"] = False
                player["socket_id"] = None
                player["disconnect_token"] = None
                game["public"] = False
                mark_game_updated(game)
                if all(current_player.get("left") for current_player in game["players"].values()):
                    pop_game(game_id)
                leave_allowed = True
            else:
                invalid_payload = make_socket_error(game_id, game, "Cannot leave during an active multiplayer game")

    if leave_allowed:
        leave_room(game_id)
        if player_left_payload is not None:
            socketio.emit("player_left", player_left_payload, to=game_id, skip_sid=request.sid)
        emit("game_left", {"gameId": game_id})
        return

    emit("invalid_move", invalid_payload)


@socketio.on("play_again")
def socket_play_again(data):
    data = data or {}
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("invalid_move", make_socket_error(game_id, None, auth_error))
        return
    if not game_id:
        emit("invalid_move", make_socket_error(game_id, None, "Game not found"))
        return

    event_name = None
    emit_target = game_id
    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        error = validate_game_access(game_id, game, player_id, auth_context)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        elif game.get("mode") != "multiplayer" or not is_multiplayer_finished(game):
            invalid_payload = make_socket_error(game_id, game, "Play again is only available after a multiplayer match")
            error = "invalid"
        else:
            error = None

        if error:
            pass
        else:
            player_id = data.get("playerId")
            game["rematch_requests"].add(player_id)
            mark_game_updated(game)

            if len(game["rematch_requests"]) == 2 and all(
                player.get("connected") for player in game["players"].values()
            ):
                finalize_or_delete_previous_game(game_id, game)
                reset_multiplayer_game(game)
                new_game_id = replace_game_id(game_id, game)
                move_multiplayer_room(game_id, new_game_id, game)
                supabase_store.create_game_record(new_game_id, game)
                start_game_timer(new_game_id, game, game["current_player"])
                reset_game_id = new_game_id
                reset_game = game
                event_name = "board_updated"
            else:
                payload = serialize_game(game_id, game)
                event_name = "play_again_updated"

    if event_name == "board_updated":
        emit_multiplayer_board_update(reset_game_id, reset_game)
        return

    if event_name == "play_again_updated":
        socketio.emit(event_name, payload, to=emit_target)
        return

    emit("invalid_move", invalid_payload)


@socketio.on("player_move")
def socket_player_move(data):
    data = data or {}
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("invalid_move", make_socket_error(game_id, None, auth_error))
        return
    if not game_id:
        emit("invalid_move", make_socket_error(game_id, None, "Game not found"))
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        error = validate_game_access(game_id, game, player_id, auth_context)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        elif game.get("mode") == "multiplayer":
            error = apply_multiplayer_move(game, player_id, data.get("column"), game_id)
            if error:
                invalid_payload = make_socket_error(game_id, game, error)
            else:
                payload = serialize_game(game_id, game)
                emit_to_room = True
        else:
            ai_job, error = apply_human_and_ai_move(game, data.get("column"), game_id)
            if error:
                invalid_payload = make_socket_error(game_id, game, error)
            else:
                payload = serialize_game(game_id, game)
                emit_to_room = False

    if error:
        emit("invalid_move", invalid_payload)
        return

    if emit_to_room:
        emit("board_updated", payload, to=game_id)
    else:
        if ai_job is not None and app.config.get("AI_SEARCH_INLINE"):
            launch_ai_turn(ai_job)
            return
        emit("board_updated", payload)
        if ai_job is not None:
            launch_ai_turn(ai_job)


@socketio.on("reset_game")
def socket_reset_game(data):
    data = data or {}
    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    auth_context, auth_error = authenticate_payload(data)
    if auth_error:
        emit("invalid_move", make_socket_error(game_id, None, auth_error))
        return
    if not game_id:
        emit("invalid_move", make_socket_error(game_id, None, "Game not found"))
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        error = validate_game_access(game_id, game, player_id, auth_context)
        if error:
            invalid_payload = make_socket_error(game_id, game, error)
        elif game.get("mode") == "multiplayer":
            invalid_payload = make_socket_error(
                game_id,
                game,
                "Use play again after the multiplayer match ends",
            )
            error = invalid_payload["message"]
        else:
            difficulty = parse_difficulty(data or {})
            previous_player_id = game["player_id"]
            previous_profile_id = game.get("profile_id")
            new_game = create_game_state(difficulty, player_id=previous_player_id, profile_id=previous_profile_id)
            ai_starts = new_game["current_player"] == get_ai_piece(new_game)
            ai_slot_reserved = reserve_ai_search_slot() if ai_starts else False
            if ai_starts and not ai_slot_reserved:
                invalid_payload = make_socket_error(game_id, game, AI_BUSY_MESSAGE)
                error = invalid_payload["message"]
            else:
                finalize_or_delete_previous_game(game_id, game)
                new_game_id = replace_game_id(game_id, new_game)
                supabase_store.create_game_record(new_game_id, new_game)
                leave_room(game_id)
                join_room(new_game_id)
                if not ai_starts:
                    start_game_timer(new_game_id, new_game, get_human_piece(new_game))
                payload = serialize_game(new_game_id, new_game, include_player_id=True)

    if error:
        emit("invalid_move", invalid_payload)
        return

    emit("board_updated", payload)
    emit_ai_turn_if_needed(new_game_id, new_game, ai_slot_reserved)


if __name__ == "__main__":
    socketio.run(app, host=BACKEND_HOST, port=BACKEND_PORT, debug=True, allow_unsafe_werkzeug=True)
