import atexit
import os
import random
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeoutError

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
CREATE_RATE_LIMIT_COUNT = 100
CREATE_RATE_LIMIT_SECONDS = 60
ROOM_VISIBILITY_RATE_LIMIT_SECONDS = 5
MAX_ACTIVE_GAMES = 300
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
    configured_origins = get_env_value("CORS_ALLOWED_ORIGINS", get_env_value("FRONTEND_ORIGIN", "http://localhost:5173"))
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
create_attempts = {}
ai_executor = None
ai_executor_lock = threading.Lock()
AI_WORKER_COUNT = max(1, get_env_int("AI_WORKER_COUNT", 1))
AI_QUEUE_CAPACITY = 3
AI_BUSY_MESSAGE = "AI queue is full, try again"
AI_ADMISSION_CAPACITY = AI_WORKER_COUNT + AI_QUEUE_CAPACITY
AI_ADMISSION_STALE_SECONDS = 65
MOVE_ANALYSIS_DEPTH = get_env_int("MOVE_ANALYSIS_DEPTH", 4)
MOVE_ANALYSIS_TIME_LIMIT = get_env_int("MOVE_ANALYSIS_TIME_LIMIT", 30)
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
        "created_at": now,
        "updated_at": now,
    }


def get_human_piece(game):
    return game.get("human_piece", HUMAN)


def get_ai_piece(game):
    return game.get("ai_piece", AI)


def create_multiplayer_game_state(player_id=None, profile_id=None):
    player_id = player_id or uuid.uuid4().hex
    now = time.time()
    return {
        "mode": "multiplayer",
        "public": False,
        "owner_name": "Player",
        "players": {
            player_id: {
                "piece": HUMAN,
                "profile_id": profile_id,
                "display_name": "Player 1",
                "socket_id": request.sid if request else None,
                "connected": True,
                "disconnect_token": None,
            },
        },
        "board": create_board(),
        "difficulty": "multiplayer",
        "status": "waiting",
        "message": "Waiting for Player 2",
        "current_player": HUMAN,
        "disconnect_deadline": None,
        "rematch_requests": set(),
        "move_number": 0,
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
        removed = False
        with get_game_lock(game_id):
            game = get_stored_game(game_id)
            if game is None:
                removed = True
            else:
                ttl = MULTIPLAYER_GAME_TTL_SECONDS if game.get("mode") == "multiplayer" else AI_GAME_TTL_SECONDS
                if now - game.get("updated_at", game.get("created_at", now)) > ttl:
                    with games_lock:
                        if games.get(game_id) is game:
                            games.pop(game_id, None)
                            removed = True
        if removed:
            delete_game_lock(game_id)


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
                removed.append(game_id)

    for game_id in removed[:]:
        did_remove = False
        with get_game_lock(game_id):
            game = get_stored_game(game_id)
            if game is not None and game.get("mode") == "ai" and game.get("socket_id") == socket_id:
                with games_lock:
                    if games.get(game_id) is game:
                        games.pop(game_id, None)
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


def analysis_rating(score_loss):
    if score_loss <= 0:
        return "perfect"
    if score_loss <= 25:
        return "good"
    if score_loss <= 100:
        return "average"
    return "blunder"


def run_move_analysis(moves, minimax_depth, time_limit):
    results = []
    for move in moves:
        move_id = move.get("id")
        board_data = move.get("board_before")
        player_number = move.get("player_number")
        played_column = move.get("column_played")
        if move_id is None or player_number not in (HUMAN, AI) or not isinstance(board_data, list):
            continue

        board = np.array(board_data, dtype=int)
        best_column, scores = get_move_scores(
            board,
            player_number,
            max_depth=minimax_depth,
            time_limit=time_limit,
        )
        if best_column is None or played_column not in scores:
            continue
        played_score = scores[played_column]
        best_score = scores[best_column]
        score_loss = best_score - played_score
        results.append({
            "move_id": move_id,
            "minimax_depth": minimax_depth,
            "played_column": played_column,
            "best_column": best_column,
            "played_score": played_score,
            "best_score": best_score,
            "rating": analysis_rating(score_loss),
        })
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
            rows = get_ai_executor().submit(
                run_move_analysis,
                job["moves"],
                job["minimax_depth"],
                job["time_limit"],
            ).result()
        if not supabase_store.replace_move_analysis(job["game_id"], rows):
            raise RuntimeError("Could not persist move analysis")
        supabase_store.set_game_analysis_status(job["game_id"], "complete")
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
        blocked_by_admissions = bool(
            not ai_active_jobs and move_analysis_job_queue and ai_admission_queue
        )

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
                entry.get("last_checked_at", now) + AI_ADMISSION_STALE_SECONDS
                for entry in ai_admission_queue
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
            1
            for game in games.values()
            if game.get("mode") == "ai" and game.get("status") not in FINISHED_STATUSES
        )


def prune_ai_admission_queue(now=None):
    now = now or time.time()
    active_entries = [
        entry
        for entry in ai_admission_queue
        if now - entry.get("last_checked_at", now) <= AI_ADMISSION_STALE_SECONDS
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
                if queue_id
                and queued_entry["queue_id"] == queue_id
                and queued_entry.get("profile_id") == profile_id
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

    if game.get("mode") != "multiplayer":
        payload["aiThinking"] = bool(game.get("ai_thinking"))
        payload["aiQueued"] = bool(game.get("ai_queued"))
        payload["aiQueuePosition"] = game.get("ai_queue_position", 0)

    if include_player_id:
        payload["playerId"] = game["player_id"]

    if game.get("mode") == "multiplayer":
        payload["currentPlayer"] = game["current_player"]
        payload["playersConnected"] = sum(1 for player in game["players"].values() if player.get("connected"))
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
    if game.get("status") in FINISHED_STATUSES or game.get("current_player") != get_ai_piece(game) or game.get("ai_thinking"):
        if slot_reserved:
            release_ai_search_slot(slot_reserved)
        return None, None
    reservation = slot_reserved or reserve_ai_search_slot()
    if not reservation:
        return None, AI_BUSY_MESSAGE

    game["ai_thinking"] = True
    game["ai_queued"] = reservation.get("state") == "queued"
    game["ai_queue_position"] = reservation.get("position", 0)
    game["message"] = (
        f"AI queued - position {game['ai_queue_position']}"
        if game["ai_queued"]
        else "AI is thinking"
    )
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
        mark_game_updated(game)
        supabase_store.update_game_record(game_id, game)
        return None, "AI returned an invalid move"

    ai_move = int(ai_move)
    board_before = board_to_list(board)
    drop_piece(board, ai_move, ai_piece)
    if game_id:
        supabase_store.record_move(game_id, game, ai_piece, ai_move, board_before, board_to_list(board), is_ai_move=True)
    game["transposition_table"] = transposition_table

    if check_win(board, ai_piece):
        game["status"] = "ai_win"
        game["message"] = "AI wins"
    elif is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
    else:
        game["status"] = "playing"
        game["current_player"] = human_piece
        game["message"] = "Your turn"

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


def public_game_payload(game_id, game):
    return {
        "gameId": game_id,
        "ownerName": sanitize_public_owner_name(game.get("owner_name")),
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

    if not isinstance(column, int):
        return None, "Invalid column"

    if column < 0 or column >= COLS:
        return None, "Column out of range"

    if game["status"] in FINISHED_STATUSES or check_win(board, human_piece) or check_win(board, ai_piece) or is_draw(board):
        return None, "Game is already over"

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

    board_before = board_to_list(board)
    drop_piece(board, column, human_piece)
    if game_id:
        supabase_store.record_move(game_id, game, human_piece, column, board_before, board_to_list(board))

    if check_win(board, human_piece):
        game["status"] = "human_win"
        game["message"] = "You win"
        game["transposition_table"] = transposition_table
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None, None

    if is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
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
        game["status"] = "player1_win" if player["piece"] == HUMAN else "player2_win"
        game["message"] = f"Player {player['piece']} wins"
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None

    if is_draw(board):
        game["status"] = "draw"
        game["message"] = "Draw"
        mark_game_updated(game)
        if game_id:
            supabase_store.update_game_record(game_id, game)
        return None

    game["current_player"] = AI if player["piece"] == HUMAN else HUMAN
    game["status"] = "playing"
    game["message"] = f"Player {game['current_player']} turn"
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
    player["disconnect_token"] = None
    if all(current_player.get("connected") for current_player in game["players"].values()):
        game["disconnect_deadline"] = None
        if game["status"] == "playing":
            game["message"] = f"Player {game['current_player']} turn"
    mark_game_updated(game)
    print(f"Player {player['piece']} connected to game {game_id}")


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
            game["status"] = "draw"
            game["message"] = "Game abandoned"
            game["disconnect_deadline"] = None
            mark_game_updated(game)
            supabase_store.update_game_record(game_id, game)
            payload = serialize_game(game_id, game)
        else:
            winning_piece = other_player["piece"]
            game["status"] = "player1_win" if winning_piece == HUMAN else "player2_win"
            game["message"] = f"Player {winning_piece} wins by default"
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

    # Repair a participant row if its join-time database write failed. Games
    # remain in server memory after completion, so both players can recover
    # their profile history without replaying the match.
    with games_lock:
        profile_games_to_repair = [
            (game_id, game)
            for game_id, game in games.items()
            if game.get("mode") == "multiplayer"
            and any(player.get("profile_id") == profile_id for player in game.get("players", {}).values())
        ]
    for game_id, game in profile_games_to_repair:
        supabase_store.add_game_player_records(game_id, game)

    return jsonify({"games": supabase_store.fetch_completed_games(profile_id)})


@app.get("/api/profile/games/<game_id>/moves")
def profile_game_moves(game_id):
    auth_context, auth_error = authenticate_request()
    if auth_error:
        return jsonify({"message": auth_error}), 401

    profile_id = auth_context.get("profile_id")
    if not profile_id:
        return jsonify({"message": "Login required"}), 401

    moves = supabase_store.fetch_game_moves(profile_id, game_id)
    if not moves:
        return jsonify({"message": "Game history not found"}), 404

    return jsonify({"moves": moves})


@app.post("/api/profile/games/<game_id>/analysis")
def request_profile_game_analysis(game_id):
    auth_context, auth_error = authenticate_request()
    if auth_error:
        return jsonify({"message": auth_error}), 401
    profile_id = auth_context.get("profile_id")
    if not profile_id:
        return jsonify({"message": "Login required"}), 401

    source = supabase_store.fetch_game_analysis_source(profile_id, game_id)
    if source is None:
        return jsonify({"message": "Completed game history not found"}), 404
    if source["analysis_status"] == "complete":
        return jsonify({"gameId": game_id, "status": "complete"})
    if not source["moves"]:
        return jsonify({"message": "No recorded moves to analyze"}), 422

    with ai_queue_lock:
        existing = analysis_jobs_by_game.get(game_id)
    if existing is None:
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
    return jsonify({
        "gameId": game_id,
        "status": status,
        "queuePosition": position,
        "priority": "move_analysis",
    }), 202


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
    emit("game_created", serialize_game(game_id, game, include_player_id=True))
    emit_ai_turn_if_needed(game_id, game, ai_slot_reserved)


def emit_ai_admission_result(auth_context, difficulty, queue_id=None):
    admitted, entry, position = request_ai_admission(auth_context["profile_id"], difficulty, queue_id)
    if not admitted:
        emit("ai_waiting", {
            "queueId": entry["queue_id"],
            "position": position,
            "difficulty": entry["difficulty"],
            "checkIntervalSeconds": 20,
            "message": "AI player is currently busy right now.",
        })
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
    auth_context, auth_error = authenticate_payload(data or {})
    if auth_error:
        emit("create_rejected", {"message": auth_error})
        return

    error = check_create_allowed()
    if error:
        emit("create_rejected", {"message": error})
        return

    game_id = uuid.uuid4().hex
    player_id = uuid.uuid4().hex
    game = create_multiplayer_game_state(player_id, profile_id=auth_context["profile_id"])
    game["owner_name"] = sanitize_public_owner_name(data.get("ownerName"), auth_context.get("email") or "Player")
    game["players"][player_id]["display_name"] = game["owner_name"]
    store_game(game_id, game)
    supabase_store.create_game_record(game_id, game)
    join_room(game_id)
    print(f"Player 1 connected to game {game_id}")
    emit("multiplayer_game_created", serialize_game(game_id, game, player_id=player_id))


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
        emit("room_public_update_failed", {
            "gameId": game_id,
            "publicRoom": bool((get_stored_game(game_id) or {}).get("public")),
            "message": invalid_payload["message"],
            "retryAfterMs": invalid_payload.get("retryAfterMs", 0),
        })
        return

    confirmed_public = bool(game.get("public"))
    if confirmed_public != requested_public:
        emit("room_public_update_failed", {
            "gameId": game_id,
            "publicRoom": confirmed_public,
            "message": "Room visibility could not be confirmed",
        })
        return

    emit("room_public_updated", {
        "gameId": game_id,
        "publicRoom": confirmed_public,
        "message": "Room is now public" if confirmed_public else "Room is now private",
    })


@socketio.on("join_multiplayer_game")
def socket_join_multiplayer_game(data):
    data = data or {}
    auth_context, auth_error = authenticate_payload(data or {})
    if auth_error:
        emit("join_rejected", {"gameId": None, "message": auth_error})
        return

    game_id = data.get("gameId") if isinstance(data, dict) else None
    player_id = data.get("playerId") if isinstance(data, dict) else None
    if not game_id:
        emit("join_rejected", {"gameId": game_id, "message": "Multiplayer game not found"})
        return

    with get_game_lock(game_id):
        game = get_stored_game(game_id)
        if game is None or game.get("mode") != "multiplayer":
            emit("join_rejected", {"gameId": game_id, "message": "Multiplayer game not found"})
            return

        if player_id in game["players"]:
            if is_auth_required() and game["players"][player_id].get("profile_id") != auth_context["profile_id"]:
                emit("join_rejected", {"gameId": game_id, "message": "Multiplayer game not found"})
                return
            mark_multiplayer_player_connected(game_id, game, player_id)
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = serialize_game(game_id, game)
            is_rejoin = True

        elif len(game["players"]) >= 2:
            emit("join_rejected", {"gameId": game_id, "message": "Multiplayer game is full"})
            return

        elif data.get("publicJoin") and not game.get("public"):
            emit("join_rejected", {"gameId": game_id, "message": "Room is no longer public"})
            return

        else:
            player_id = uuid.uuid4().hex
            game["players"][player_id] = {
                "piece": AI,
                "profile_id": auth_context["profile_id"],
                "display_name": sanitize_public_owner_name(data.get("playerName"), auth_context.get("email") or "Player"),
                "socket_id": request.sid,
                "connected": True,
                "disconnect_token": None,
            }
            assign_multiplayer_pieces(game)
            game["status"] = "playing"
            game["public"] = False
            game["message"] = f"Player {game['current_player']} turn"
            mark_game_updated(game)
            supabase_store.add_game_player_records(game_id, game)
            joined_payload = serialize_game(game_id, game, player_id=player_id)
            updated_payload = None
            is_rejoin = False

    join_room(game_id)
    emit("multiplayer_game_joined", joined_payload)
    if is_rejoin:
        emit("board_updated", updated_payload, to=game_id)
    else:
        print(f"Player 2 connected to game {game_id}")
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
        print(f"Player {player['piece']} disconnected from game {game_id}")

        if len(game["players"]) < 2 or game["status"] in FINISHED_STATUSES:
            game["public"] = False
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
                supabase_store.delete_game_record(game_id)
                pop_game(game_id)
                leave_allowed = True
            elif is_multiplayer_finished(game):
                player_left_payload = {"gameId": game_id, "message": f"Player {player['piece']} left the room"}
                finalize_or_delete_previous_game(game_id, game)
                pop_game(game_id)
                leave_allowed = True
            else:
                invalid_payload = make_socket_error(game_id, game, "Cannot leave during an active multiplayer game")

    if leave_allowed:
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

            if len(game["rematch_requests"]) == 2 and all(player.get("connected") for player in game["players"].values()):
                finalize_or_delete_previous_game(game_id, game)
                reset_multiplayer_game(game)
                new_game_id = replace_game_id(game_id, game)
                move_multiplayer_room(game_id, new_game_id, game)
                supabase_store.create_game_record(new_game_id, game)
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
                payload = serialize_game(new_game_id, new_game, include_player_id=True)

    if error:
        emit("invalid_move", invalid_payload)
        return

    emit("board_updated", payload)
    emit_ai_turn_if_needed(new_game_id, new_game, ai_slot_reserved)


if __name__ == "__main__":
    socketio.run(app, host=BACKEND_HOST, port=BACKEND_PORT, debug=True, allow_unsafe_werkzeug=True)
