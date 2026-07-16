import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


COMPLETED_STATUSES = {"human_win", "ai_win", "player1_win", "player2_win", "draw"}
GAME_OVER_STATUSES = COMPLETED_STATUSES | {"abandoned"}
MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE = "Move evaluation is temporarily unavailable while the review database is updated."
WINNER_BY_STATUS = {
    "human_win": 1,
    "ai_win": 2,
    "player1_win": 1,
    "player2_win": 2,
}
MOVE_ANALYSIS_FEEDBACK = {
    "blunder": "Blunder",
    "mistake": "Mistake",
    "ok": "OK",
    "great": "Great Move",
}
PUBLIC_REVIEW_MOVE_FIELDS = (
    "id",
    "move_number",
    "player_number",
    "column_played",
    "board_before",
    "board_after",
    "reconstructed",
)

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
        logger.warning("Supabase sync disabled error=%s", error.__class__.__name__)
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
        logger.warning("Supabase sync failed error=%s", error.__class__.__name__)
        return False


def fetch_profile_display_name(profile_id):
    """Return the authoritative public name for an authenticated profile."""
    client = get_client()
    if client is None or not profile_id:
        return None

    response = client.table("profiles").select("username,display_name").eq("id", profile_id).limit(1).execute()
    rows = response.data or []
    if not rows:
        return None
    profile = rows[0]
    return profile.get("display_name") or profile.get("username")


def db_game_id(game_id):
    try:
        return str(uuid.UUID(str(game_id)))
    except (TypeError, ValueError):
        return None


def is_missing_move_analysis_columns(error):
    """Recognize the PostgREST error raised by the pre-worst-move schema."""
    code = str(getattr(error, "code", "") or "")
    message = " ".join(
        (
            str(getattr(error, "message", "") or ""),
            str(error),
        )
    ).lower()
    return (code == "42703" or "42703" in message) and ("worst_column" in message or "worst_score" in message)


def move_analysis_schema_ready(client, game_id):
    """Check required derived-data columns without reading the server-only rating."""
    try:
        (client.table("move_analysis").select("worst_column,worst_score").eq("game_id", game_id).execute())
        return True
    except Exception as error:
        if is_missing_move_analysis_columns(error):
            return False
        raise


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def board_to_json(board):
    if hasattr(board, "astype"):
        return board.astype(int).tolist()
    return board


def next_move_number(game):
    game["move_number"] = game.get("move_number", 0) + 1
    return game["move_number"]


def winner_for_status(status, game=None):
    if game is not None and game.get("mode") != "multiplayer":
        if status == "human_win":
            return game.get("human_piece", 1)
        if status == "ai_win":
            return game.get("ai_piece", 2)
    return WINNER_BY_STATUS.get(status)


def game_payload(game_id, game):
    # Analysis lifecycle fields are deliberately excluded. This payload is
    # reused by ordinary game and membership repairs, which must not erase a
    # completed review. Creation/reset add explicit analysis defaults below.
    status = game.get("status", "playing")
    finished = status in GAME_OVER_STATUSES
    payload = {
        "id": db_game_id(game_id),
        "mode": game.get("mode", "ai"),
        "difficulty": game.get("difficulty"),
        "status": status,
        "winner_player_number": winner_for_status(status, game),
        "final_board": board_to_json(game.get("board")) if finished else None,
        "ended_at": now_iso() if finished else None,
    }
    return {key: value for key, value in payload.items() if value is not None or key != "id"}


def reset_analysis_payload():
    return {
        "analysis_status": "not_requested",
        "analysis_requested_at": None,
        "analysis_completed_at": None,
        "analysis_error": None,
    }


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
                "display_name_snapshot": player.get("display_name") or f"Player {player['piece']}",
            }
            for player in game.get("players", {}).values()
        ]

    human_piece = game.get("human_piece", 1)
    ai_piece = game.get("ai_piece", 2)
    return [
        {
            "game_id": db_id,
            "profile_id": game.get("profile_id"),
            "player_number": human_piece,
            "is_ai": False,
            "ai_difficulty": None,
            "display_name_snapshot": "Player",
        },
        {
            "game_id": db_id,
            "profile_id": None,
            "player_number": ai_piece,
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
        payload = game_payload(game_id, game)
        payload.update(reset_analysis_payload())
        client.table("games").insert(payload).execute()
        player_payloads = game_player_payloads(game_id, game)
        if player_payloads:
            client.table("game_players").insert(player_payloads).execute()

    return execute_safely(action)


def is_configured():
    load_local_env()
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))


def is_missing_multiplayer_recovery_schema(error):
    code = str(getattr(error, "code", "") or "")
    message = " ".join(
        (
            str(getattr(error, "message", "") or ""),
            str(error),
        )
    ).lower()
    return code in {"42P01", "42883", "PGRST202", "PGRST205"} or any(
        fragment in message
        for fragment in (
            "multiplayer_room_requests does not exist",
            "could not find the function",
            "could not find the table",
            "undefined table",
            "undefined function",
        )
    )


def is_missing_multiplayer_player_sync_schema(error):
    code = str(getattr(error, "code", "") or "")
    message = " ".join((str(getattr(error, "message", "") or ""), str(error))).lower()
    return (
        code in {"42883", "PGRST202"}
        or "sync_multiplayer_game_players" in message
        and ("could not find" in message or "does not exist" in message or "undefined function" in message)
    )


def multiplayer_recovery_client():
    client = get_client()
    if client is not None:
        return client, None
    if is_configured():
        return None, {"result": "error", "code": "persistence_unavailable"}
    return None, {"result": "disabled"}


def normalize_multiplayer_room_request(row):
    if not isinstance(row, dict):
        return None

    profile_id = db_game_id(row.get("profile_id"))
    game_id = db_game_id(row.get("game_id"))
    player_id = db_game_id(row.get("player_id"))
    request_id = row.get("request_id")
    if profile_id is None or game_id is None or player_id is None or not isinstance(request_id, str):
        return None

    game_data = row.get("games") or {}
    if isinstance(game_data, list):
        game_data = game_data[0] if game_data else {}
    game_mode = row.get("game_mode") or game_data.get("mode")
    game_status = row.get("game_status") or game_data.get("status")

    game_players = game_data.get("game_players") or []
    player_count = row.get("player_count")
    owner_profile_id = row.get("owner_profile_id")
    if player_count is None:
        player_count = len(game_players)
    if owner_profile_id is None and len(game_players) == 1:
        owner_profile_id = game_players[0].get("profile_id")
    owner_profile_id = db_game_id(owner_profile_id)

    return {
        "profile_id": profile_id,
        "request_id": request_id,
        "game_id": game_id,
        "player_id": player_id,
        "owner_name": row.get("owner_name") or "Player",
        "state": row.get("state"),
        "expires_at": row.get("expires_at"),
        "resolved_at": row.get("resolved_at"),
        "game_mode": game_mode,
        "game_status": game_status,
        "player_count": int(player_count or 0),
        "owner_profile_id": owner_profile_id,
        "created": bool(row.get("created")),
    }


def claim_multiplayer_room_request(profile_id, request_id, game_id, player_id, owner_name):
    profile_id = db_game_id(profile_id)
    game_id = db_game_id(game_id)
    player_id = db_game_id(player_id)
    if profile_id is None or game_id is None or player_id is None or not request_id:
        return {"result": "error", "code": "invalid_room_request"}

    client, unavailable = multiplayer_recovery_client()
    if unavailable:
        return unavailable

    try:
        response = client.rpc(
            "claim_multiplayer_room_request",
            {
                "p_profile_id": profile_id,
                "p_request_id": request_id,
                "p_game_id": game_id,
                "p_player_id": player_id,
                "p_owner_name": owner_name or "Player",
            },
        ).execute()
    except Exception as error:
        if is_missing_multiplayer_recovery_schema(error):
            return {"result": "schema_missing"}
        logger.warning("Supabase multiplayer claim failed error=%s", error.__class__.__name__)
        return {"result": "error", "code": "persistence_unavailable"}

    rows = response.data or []
    if isinstance(rows, dict):
        rows = [rows]
    room = normalize_multiplayer_room_request(rows[0] if rows else None)
    if room is None:
        # The transaction may have committed even when the response was lost or
        # malformed. Force a retry with the same idempotency key.
        return {"result": "error", "code": "persistence_unavailable"}
    return {"result": "ok", "room": room}


def fetch_multiplayer_room_request(profile_id, request_id):
    profile_id = db_game_id(profile_id)
    if profile_id is None or not request_id:
        return {"result": "error", "code": "invalid_room_request"}

    client, unavailable = multiplayer_recovery_client()
    if unavailable:
        return unavailable

    try:
        response = (
            client.table("multiplayer_room_requests")
            .select(
                "profile_id,request_id,game_id,player_id,owner_name,state,expires_at,resolved_at,"
                "games!inner(mode,status,game_players(player_number,profile_id,is_ai))"
            )
            .eq("profile_id", profile_id)
            .eq("request_id", request_id)
            .execute()
        )
    except Exception as error:
        if is_missing_multiplayer_recovery_schema(error):
            return {"result": "schema_missing"}
        logger.warning("Supabase multiplayer recovery failed error=%s", error.__class__.__name__)
        return {"result": "error", "code": "persistence_unavailable"}

    for row in response.data or []:
        room = normalize_multiplayer_room_request(row)
        if room and room["profile_id"] == profile_id and room["request_id"] == request_id:
            return {"result": "ok", "room": room}
    if response.data:
        return {"result": "error", "code": "persistence_unavailable"}
    return {"result": "not_found"}


def resolve_multiplayer_room_request(game_id, state):
    game_id = db_game_id(game_id)
    if game_id is None or state not in {"completed", "cancelled", "expired", "invalid"}:
        return {"result": "error", "code": "invalid_room_resolution"}

    client, unavailable = multiplayer_recovery_client()
    if unavailable:
        return unavailable
    try:
        response = client.rpc(
            "resolve_multiplayer_room_request",
            {
                "p_game_id": game_id,
                "p_state": state,
            },
        ).execute()
    except Exception as error:
        if is_missing_multiplayer_recovery_schema(error):
            return {"result": "schema_missing"}
        logger.warning("Supabase multiplayer resolution failed error=%s", error.__class__.__name__)
        return {"result": "error", "code": "persistence_unavailable"}
    return {"result": "ok", "resolved": bool(response.data)}


def add_game_player_records(game_id, game):
    player_payloads = game_player_payloads(game_id, game)
    if not player_payloads:
        return False

    def action(client):
        if game.get("mode") == "multiplayer" and len(player_payloads) == 2:
            try:
                client.rpc(
                    "sync_multiplayer_game_players",
                    {
                        "p_game_id": db_game_id(game_id),
                        "p_players": player_payloads,
                    },
                ).execute()
            except Exception as error:
                if not is_missing_multiplayer_player_sync_schema(error):
                    raise
                # Compatibility path for deployments that have not applied the
                # atomic sync migration yet. Clearing the old profile links
                # avoids the unique-profile conflict when starter pieces swap.
                client.table("game_players").update({"profile_id": None}).eq("game_id", db_game_id(game_id)).execute()
                for player_payload in player_payloads:
                    client.table("game_players").upsert(
                        player_payload,
                        on_conflict="game_id,player_number",
                    ).execute()
        else:
            for player_payload in player_payloads:
                client.table("game_players").upsert(player_payload, on_conflict="game_id,player_number").execute()
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


def set_game_analysis_status(game_id, status, error=None):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    payload = {"analysis_status": status, "analysis_error": error}
    if status == "processing":
        payload.update(
            {
                "analysis_requested_at": now_iso(),
                "analysis_completed_at": None,
            }
        )
    elif status in {"complete", "failed"}:
        payload["analysis_completed_at"] = now_iso()

    def action(client):
        client.table("games").update(payload).eq("id", db_id).execute()

    return execute_safely(action)


def replace_move_analysis(game_id, rows):
    db_id = db_game_id(game_id)
    if db_id is None:
        return False

    payloads = [
        {
            "move_id": row["move_id"],
            "game_id": db_id,
            "minimax_depth": row["minimax_depth"],
            "played_column": row["played_column"],
            "best_column": row["best_column"],
            "worst_column": row["worst_column"],
            "played_score": row["played_score"],
            "best_score": row["best_score"],
            "worst_score": row["worst_score"],
            "rating": row["rating"],
        }
        for row in rows
    ]

    def action(client):
        client.table("move_analysis").delete().eq("game_id", db_id).execute()
        if payloads:
            client.table("move_analysis").insert(payloads).execute()

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
        payload.update(reset_analysis_payload())
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
        "profile_id": None if game.get("mode") == "multiplayer" else profile_id,
        "is_ai_move": is_ai_move,
        "column_played": int(column),
        "board_before": board_before,
        "board_after": board_after,
    }

    def action(client):
        client.table("game_moves").insert(payload).execute()

    return execute_safely(action)


def completed_game_result(game_data, player_number):
    status = game_data.get("status")
    winner = game_data.get("winner_player_number")
    if status == "draw":
        return "Draw"
    if winner is None:
        return "Unknown"
    return "Win" if winner == player_number else "Loss"


def inferred_move(move_number, board_before, board_after):
    if not isinstance(board_before, list) or not isinstance(board_after, list):
        return None
    changes = []
    for row_index, row in enumerate(board_after):
        if row_index >= len(board_before) or not isinstance(row, list) or not isinstance(board_before[row_index], list):
            return None
        for column_index, value in enumerate(row):
            previous = board_before[row_index][column_index] if column_index < len(board_before[row_index]) else None
            if value != previous:
                changes.append((column_index, value))
    if len(changes) != 1 or changes[0][1] not in (1, 2):
        return None
    column, player_number = changes[0]
    return {
        "move_number": move_number,
        "player_number": player_number,
        "column_played": column,
        "board_before": board_before,
        "board_after": board_after,
        "reconstructed": True,
    }


def repair_move_history(moves, final_board=None):
    repaired = []
    for move in sorted(moves or [], key=lambda item: item.get("move_number", 0)):
        expected = (repaired[-1]["move_number"] + 1) if repaired else 1
        if move.get("move_number") == expected + 1:
            previous_board = repaired[-1]["board_after"] if repaired else move.get("board_before")
            recovered = inferred_move(expected, previous_board, move.get("board_before"))
            if recovered:
                repaired.append(recovered)
        repaired.append(move)
    if repaired and final_board is not None:
        recovered = inferred_move(repaired[-1]["move_number"] + 1, repaired[-1].get("board_after"), final_board)
        if recovered:
            repaired.append(recovered)
    return repaired


def public_review_move(move, include_analysis=True):
    """Return a review move without exposing evaluator inputs or raw ratings."""
    public_move = {field: move[field] for field in PUBLIC_REVIEW_MOVE_FIELDS if field in move}
    if not include_analysis:
        return public_move

    nested_analysis = move.get("move_analysis") or []
    if isinstance(nested_analysis, dict):
        nested_analysis = [nested_analysis]

    public_move["move_analysis"] = []
    for analysis in nested_analysis:
        if not isinstance(analysis, dict):
            continue
        feedback = MOVE_ANALYSIS_FEEDBACK.get(analysis.get("rating"))
        if feedback:
            public_move["move_analysis"].append({"feedback": feedback})
    return public_move


def fetch_completed_games(profile_id):
    client = get_client()
    if client is None or not profile_id:
        return []

    response = (
        client.table("game_players")
        .select(
            "player_number,is_ai,ai_difficulty,display_name_snapshot,"
            "games(id,mode,difficulty,status,winner_player_number,started_at,ended_at,"
            "game_players(player_number,display_name_snapshot,profiles(username,display_name)))"
        )
        .eq("profile_id", profile_id)
        .execute()
    )

    completed_games = []
    for row in response.data or []:
        game_data = row.get("games") or {}
        if isinstance(game_data, list):
            game_data = game_data[0] if game_data else {}
        if game_data.get("status") not in COMPLETED_STATUSES:
            continue

        player_number = row.get("player_number")
        game_players = game_data.get("game_players") or []
        player_names = {}
        for game_player in game_players:
            profile = game_player.get("profiles") or {}
            if isinstance(profile, list):
                profile = profile[0] if profile else {}
            display_name = (
                profile.get("display_name") or profile.get("username") or game_player.get("display_name_snapshot")
            )
            if game_player.get("player_number") and display_name:
                player_names[str(game_player["player_number"])] = display_name
        completed_games.append(
            {
                "id": game_data.get("id"),
                "mode": game_data.get("mode"),
                "difficulty": game_data.get("difficulty"),
                "status": game_data.get("status"),
                "winnerPlayerNumber": game_data.get("winner_player_number"),
                "startedAt": game_data.get("started_at"),
                "endedAt": game_data.get("ended_at"),
                "playerNumber": player_number,
                "playerNames": player_names,
                "winnerName": player_names.get(str(game_data.get("winner_player_number"))),
                "result": completed_game_result(game_data, player_number),
            }
        )

    return sorted(completed_games, key=lambda game: game.get("endedAt") or game.get("startedAt") or "", reverse=True)


def fetch_game_moves(profile_id, game_id):
    db_id = db_game_id(game_id)
    client = get_client()
    if client is None or not profile_id or db_id is None:
        return None

    membership = (
        client.table("game_players")
        .select("game_id,games!inner(status,final_board,winner_player_number,analysis_status,analysis_error)")
        .eq("profile_id", profile_id)
        .eq("game_id", db_id)
        .execute()
    )
    if not membership.data:
        return None

    game_data = membership.data[0].get("games") or {}
    if isinstance(game_data, list):
        game_data = game_data[0] if game_data else {}
    if game_data.get("status") not in COMPLETED_STATUSES:
        return None

    move_fields = "id,move_number,player_number,column_played,board_before,board_after"
    analysis_available = True
    try:
        response = (
            client.table("game_moves")
            .select(f"{move_fields},move_analysis(rating,worst_column,worst_score)")
            .eq("game_id", db_id)
            .order("move_number")
            .execute()
        )
    except Exception as error:
        if not is_missing_move_analysis_columns(error):
            raise
        # Existing deployments can still show a useful board/history while the
        # migration is pending. Do not return legacy analysis because its rating
        # and worst-move semantics are not equivalent to the current evaluator.
        analysis_available = False
        response = client.table("game_moves").select(move_fields).eq("game_id", db_id).order("move_number").execute()
    repaired_moves = repair_move_history(response.data or [], game_data.get("final_board"))
    return {
        "moves": [public_review_move(move, include_analysis=analysis_available) for move in repaired_moves],
        "analysis_status": game_data.get("analysis_status") or "not_requested",
        "analysis_error": game_data.get("analysis_error"),
        "analysis_available": analysis_available,
        "analysis_unavailable_reason": None if analysis_available else MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE,
    }


def fetch_game_analysis_source(profile_id, game_id):
    db_id = db_game_id(game_id)
    client = get_client()
    if client is None or not profile_id or db_id is None:
        return None

    membership = (
        client.table("game_players")
        .select("game_id,games!inner(status,final_board,analysis_status,analysis_error)")
        .eq("profile_id", profile_id)
        .eq("game_id", db_id)
        .execute()
    )
    if not membership.data:
        return None
    game_data = membership.data[0].get("games") or {}
    if isinstance(game_data, list):
        game_data = game_data[0] if game_data else {}
    if game_data.get("status") not in COMPLETED_STATUSES:
        return None

    analysis_available = move_analysis_schema_ready(client, db_id)

    response = (
        client.table("game_moves")
        .select("id,move_number,player_number,column_played,board_before,board_after")
        .eq("game_id", db_id)
        .order("move_number")
        .execute()
    )
    return {
        "analysis_status": game_data.get("analysis_status") or "not_requested",
        "analysis_error": game_data.get("analysis_error"),
        "analysis_available": analysis_available,
        "analysis_unavailable_reason": None if analysis_available else MOVE_ANALYSIS_SCHEMA_UPDATE_MESSAGE,
        "moves": repair_move_history(response.data or [], game_data.get("final_board")),
    }
