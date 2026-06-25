# ai/minimax.py
import numpy as np
from game.board import is_valid_move, drop_piece, create_board, check_win

def get_best_move(board, piece):
    valid_moves = [c for c in range(board.shape[1]) if is_valid_move(board, c)]
    best_score = -float("inf")
    best_col = np.random.choice(valid_moves)
    opponent_piece = 2 if piece == 1 else 1
    for col in valid_moves:
        temp_board = board.copy()
        drop_piece(temp_board, col, piece)
        score = minimax(temp_board, 5, -float("inf"), float("inf"), False, piece, opponent_piece)
        if score > best_score:
            best_score = score
            best_col = col
    return best_col

# def score_position(board, piece):
#     # Very simple scoring: prefer center
#     center_array = [int(i) for i in list(board[:, board.shape[1]//2])]
#     center_score = center_array.count(piece) * 3
#     return center_score

def minimax(board, search_depth, alpha, beta, maximizing_player, piece, opponent_piece):
    valid_moves = [c for c in range(board.shape[1]) if is_valid_move(board, c)]

    #Terminal conditions
    if search_depth == 0:
        return evaluate_board(board, piece)
    elif check_win(board, piece):
        return 10000
    elif check_win(board, opponent_piece):
        return -10000
    elif len(valid_moves) == 0:
        #Draw
        return 0

    if maximizing_player:
        value = -float("inf")
        for col in valid_moves:
            temp_board = board.copy()
            #Check if move is valid
            if drop_piece(temp_board, col, piece):
                score = minimax(temp_board, search_depth -1, alpha, beta, False, piece, opponent_piece)
                value = max(value, score)
                alpha = max(alpha, value)
                #Prune the remaining branches
                if beta <= alpha:
                    break
        return value
    else:
        value = float("inf")
        for col in valid_moves:
            temp_board = board.copy()
            #Check if move is valid
            if drop_piece(temp_board, col, opponent_piece):
                score = minimax(temp_board, search_depth - 1, alpha, beta, True, piece, opponent_piece)
                value = min(value, score)
                beta = min(beta, value)
                #Prune the remaining branches
                if beta <= alpha:
                    break
        return value

def evaluate_board(board, piece):
    score = 0
    if piece == 1:
        opponent_piece = 2
    else:
        opponent_piece = 1
    
    for r in range(board.shape[0]):
        for c in range(board.shape[1]-3):
            window = list(board[r, c:c+4])
            score += evaluate_window(window, piece, opponent_piece)
    
    for r in range(board.shape[0]-3):
        for c in range(board.shape[1]):
            window = list(board[r:r+4, c])
            score += evaluate_window(window, piece, opponent_piece)
    
    for r in range(board.shape[0]-3):
        for c in range(board.shape[1]-3):
            window = [board[r+i, c+i] for i in range(4)]
            score += evaluate_window(window, piece, opponent_piece)
    
    for r in range(3,board.shape[0]):
        for c in range(board.shape[1]-3):
            window = [board[r-i, c+i] for i in range(4)]
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
                