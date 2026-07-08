import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


FINISHED_STATUSES = {"human_win", "ai_win", "player1_win", "player2_win", "draw", "abandoned"}
WINNER_BY_STATUS = {
    "human_win": 1,
    "ai_win": 2,
    "player1_win": 1,
    "player2_win": 2,
}

_env_loaded = False
_client_checked = False
_client = None


def load_local_env():
    global _env_loaded
    if _env_loaded:
        return

    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

    _env_loaded = True


def get_client():
    global _client_checked, _client
    if _client_checked:
        return _client

    load_local_env()
    url = os.environ.get("SUPABASE_URL")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not secret_key:
        _client_checked = True
        return None

    try:
        from supabase import create_client

        _client = create_client(url, secret_key)
    except Exception as error:
        print(f"Supabase sync disabled: {error.__class__.__name__}")
        _client = None

    _client_checked = True
    return _client


def is_enabled():
    return get_client() is not None


def execute_safely(action):
    client = get_client()
    if client is None:
        return False

    try:
        action(client)
        return True
    except Exception as error:
        print(f"Supabase sync failed: {error.__class__.__name__}")
        return False


def db_game_id(game_id):
    try:
        return str(uuid.UUID(str(game_id)))
    except (TypeError, ValueError):
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def board_to_json(board):
    if hasattr(board, "astype"):
        return board.astype(int).tolist()
    return board


def next_move_number(game):
    game["move_number"] = game.get("move_number", 0) + 1
    return game["move_number"]


def winner_for_status(status):
    return WINNER_BY_STATUS.get(status)


def game_payload(game_id, game):
    status = game.get("status", "playing")
    finished = status in FINISHED_STATUSES
    payload = {
        "id": db_game_id(game_id),
        "mode": game.get("mode", "ai"),
        "difficulty": game.get("difficulty"),
        "status": status,
        "winner_player_number": winner_for_status(status),
        "final_board": board_to_json(game.get("board")) if finished else None,
        "ended_at": now_iso() if finished else None,
        "analysis_status": "not_requested",
        "analysis_requested_at": None,
        "analysis_completed_at": None,
        "analysis_error": None,
    }
    return {key: value for key, value in payload.items() if value is not None or key != "id"}


def game_player_payloads(game_id, game):
    db_id = db_game_id(game_id)
    if db_id is None:
        return []

    if game.get("mode") == "multiplayer":
        return [
            {
                "game_id": db_id,
                "profile_id": player.get("profile_id"),
                "player_number": player["piece"],
                "is_ai": False,
                "ai_difficulty": None,
                "display_name_snapshot": f"Player {player['piece']}",
            }
            for player in game.get("players", {}).values()
        ]

    return [
        {
            "game_id": db_id,
            "profile_id": game.get("profile_id"),
            "player_number": 1,
            "is_ai": False,
            "ai_difficulty": None,
            "display_name_snapshot": "Player",
        },
        {
            "game_id": db_id,
            "profile_id": None,
            "player_number": 2,
            "is_ai": True,
            "ai_difficulty": game.get("difficulty"),
            "display_name_snapshot": f"AI - {game.get('difficulty')}",
        },
    ]


def create_game_record(game_id, game):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    def action(client):
        client.table("games").insert(game_payload(game_id, game)).execute()
        player_payloads = game_player_payloads(game_id, game)
        if player_payloads:
            client.table("game_players").insert(player_payloads).execute()

    return execute_safely(action)


def add_game_player_records(game_id, game):
    player_payloads = game_player_payloads(game_id, game)
    if not player_payloads:
        return False

    def action(client):
        client.table("game_players").upsert(player_payloads, on_conflict="game_id,player_number").execute()
        client.table("games").update(game_payload(game_id, game)).eq("id", db_game_id(game_id)).execute()

    return execute_safely(action)


def update_game_record(game_id, game):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    def action(client):
        payload = game_payload(game_id, game)
        payload.pop("id", None)
        client.table("games").update(payload).eq("id", db_id).execute()

    return execute_safely(action)


def delete_game_record(game_id):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    def action(client):
        client.table("games").delete().eq("id", db_id).execute()

    return execute_safely(action)


def reset_game_record(game_id, game):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    game["move_number"] = 0

    def action(client):
        client.table("game_moves").delete().eq("game_id", db_id).execute()
        payload = game_payload(game_id, game)
        payload.pop("id", None)
        payload["started_at"] = now_iso()
        client.table("games").update(payload).eq("id", db_id).execute()

    return execute_safely(action)


def record_move(game_id, game, player_number, column, board_before, board_after, is_ai_move=False, profile_id=None):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    payload = {
        "game_id": db_id,
        "move_number": next_move_number(game),
        "player_number": player_number,
        "profile_id": profile_id,
        "is_ai_move": is_ai_move,
        "column_played": int(column),
        "board_before": board_before,
        "board_after": board_after,
    }

    def action(client):
        client.table("game_moves").insert(payload).execute()

    return execute_safely(action)
