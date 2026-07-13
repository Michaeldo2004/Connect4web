import time

from game.board import check_win, drop_piece, get_board_str, is_valid_move

CENTER_ORDER = [3, 2, 4, 1, 5, 0, 6]
MAX_TRANSPOSITION_ENTRIES = 12000


class SearchTimeout(Exception):
    pass


def normalize_transposition_table(transposition_table):
    if not isinstance(transposition_table, dict):
        return {}

    normalized_table = {}
    items = list(transposition_table.items())[-MAX_TRANSPOSITION_ENTRIES:]
    for key, value in items:
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
            normalized_table[key] = float(value)

    return normalized_table


def trim_transposition_table(transposition_table):
    if len(transposition_table) <= MAX_TRANSPOSITION_ENTRIES:
        return transposition_table

    return dict(list(transposition_table.items())[-MAX_TRANSPOSITION_ENTRIES:])


def get_best_move(board, piece, max_depth=5, time_limit=3, transposition_table=None, return_table=False):
    transposition_table = normalize_transposition_table(transposition_table)
    valid_moves = ordered_moves(board)

    if not valid_moves:
        return (None, transposition_table) if return_table else None

    best_col = valid_moves[0]
    opponent_piece = 2 if piece == 1 else 1
    deadline = time.monotonic() + time_limit

    for depth in range(1, max_depth + 1):
        try:
            best_col = search_best_move(
                board,
                piece,
                opponent_piece,
                depth,
                deadline,
                transposition_table,
            )
        except SearchTimeout:
            break

    transposition_table = trim_transposition_table(transposition_table)
    return (int(best_col), transposition_table) if return_table else int(best_col)


def get_move_scores(board, piece, max_depth=4, time_limit=30):
    """Return fixed-depth minimax scores for every legal move in a position."""
    valid_moves = ordered_moves(board)
    if not valid_moves:
        return None, {}

    opponent_piece = 2 if piece == 1 else 1
    deadline = time.monotonic() + time_limit
    transposition_table = {}
    scores = {}
    for col in valid_moves:
        temp_board = board.copy()
        drop_piece(temp_board, col, piece)
        try:
            score = minimax(
                temp_board,
                max(0, max_depth - 1),
                -float("inf"),
                float("inf"),
                False,
                piece,
                opponent_piece,
                deadline,
                transposition_table,
            )
        except SearchTimeout:
            # A completed analysis job must produce a result for every move.
            # Static scoring is a deterministic fallback if its total budget expires.
            score = evaluate_board(temp_board, piece)
        scores[int(col)] = int(score)

    best_col = max(valid_moves, key=lambda col: scores[int(col)])
    return int(best_col), scores


def search_best_move(board, piece, opponent_piece, search_depth, deadline, transposition_table):
    check_time(deadline)
    valid_moves = ordered_moves(board)
    best_score = -float("inf")
    best_col = valid_moves[0]
    alpha = -float("inf")
    beta = float("inf")

    for col in valid_moves:
        check_time(deadline)
        temp_board = board.copy()
        drop_piece(temp_board, col, piece)
        score = minimax(
            temp_board,
            search_depth - 1,
            alpha,
            beta,
            False,
            piece,
            opponent_piece,
            deadline,
            transposition_table,
        )

        if score > best_score:
            best_score = score
            best_col = col

        alpha = max(alpha, best_score)

    return best_col


def minimax(board, search_depth, alpha, beta, maximizing_player, piece, opponent_piece, deadline, transposition_table):
    check_time(deadline)
    cache_key = get_cache_key(board, search_depth, maximizing_player, piece)
    cached_score = transposition_table.get(cache_key)
    if cached_score is not None:
        return cached_score

    valid_moves = ordered_moves(board)
    terminal_score = get_terminal_score(board, search_depth, piece, opponent_piece, valid_moves)
    if terminal_score is not None:
        transposition_table[cache_key] = terminal_score
        return terminal_score

    if search_depth == 0:
        score = evaluate_board(board, piece)
        transposition_table[cache_key] = score
        return score

    cutoff = False
    if maximizing_player:
        value = -float("inf")
        for col in valid_moves:
            check_time(deadline)
            temp_board = board.copy()
            if drop_piece(temp_board, col, piece):
                score = minimax(
                    temp_board,
                    search_depth - 1,
                    alpha,
                    beta,
                    False,
                    piece,
                    opponent_piece,
                    deadline,
                    transposition_table,
                )
                value = max(value, score)
                alpha = max(alpha, value)
                if beta <= alpha:
                    cutoff = True
                    break
    else:
        value = float("inf")
        for col in valid_moves:
            check_time(deadline)
            temp_board = board.copy()
            if drop_piece(temp_board, col, opponent_piece):
                score = minimax(
                    temp_board,
                    search_depth - 1,
                    alpha,
                    beta,
                    True,
                    piece,
                    opponent_piece,
                    deadline,
                    transposition_table,
                )
                value = min(value, score)
                beta = min(beta, value)
                if beta <= alpha:
                    cutoff = True
                    break

    if not cutoff:
        transposition_table[cache_key] = value

    return value


def ordered_moves(board):
    valid_moves = [col for col in range(board.shape[1]) if is_valid_move(board, col)]
    return [col for col in CENTER_ORDER if col in valid_moves]


def get_cache_key(board, search_depth, maximizing_player, piece):
    return f"{get_board_str(board)}:{search_depth}:{int(maximizing_player)}:{piece}"


def check_time(deadline):
    if time.monotonic() >= deadline:
        raise SearchTimeout


def get_terminal_score(board, search_depth, piece, opponent_piece, valid_moves):
    if check_win(board, piece):
        return 100000 + search_depth
    if check_win(board, opponent_piece):
        return -100000 - search_depth
    if not valid_moves:
        return 0
    return None


def evaluate_board(board, piece):
    score = 0
    if piece == 1:
        opponent_piece = 2
    else:
        opponent_piece = 1

    for r in range(board.shape[0]):
        for c in range(board.shape[1] - 3):
            window = list(board[r, c:c + 4])
            score += evaluate_window(window, piece, opponent_piece)

    for r in range(board.shape[0] - 3):
        for c in range(board.shape[1]):
            window = list(board[r:r + 4, c])
            score += evaluate_window(window, piece, opponent_piece)

    for r in range(board.shape[0] - 3):
        for c in range(board.shape[1] - 3):
            window = [board[r + i, c + i] for i in range(4)]
            score += evaluate_window(window, piece, opponent_piece)

    for r in range(3, board.shape[0]):
        for c in range(board.shape[1] - 3):
            window = [board[r - i, c + i] for i in range(4)]
            score += evaluate_window(window, piece, opponent_piece)

    return score


def evaluate_window(window, piece, opponent_piece):
    score = 0
    piece_count = window.count(piece)
    empty_count = window.count(0)
    opponent_count = window.count(opponent_piece)

    if piece_count == 4:
        score += 100
    elif piece_count == 3 and empty_count == 1:
        score += 80
    elif piece_count == 2 and empty_count == 2:
        score += 18
    if opponent_count == 3 and empty_count == 1:
        score -= 800
    elif opponent_count == 2 and empty_count == 2:
        score -= 10

    return score
