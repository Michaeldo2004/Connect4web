# game/board.py
import numpy as np

ROWS = 6
COLS = 7

def create_board():
    return np.zeros((ROWS, COLS), dtype=int)

def is_valid_move(board, col):
    return board[0][col] == 0

def drop_piece(board, col, piece):
    for row in range(ROWS-1, -1, -1):
        if board[row][col] == 0:
            board[row][col] = piece
            return True
    return False

def check_win(board, piece):
    # Check horizontal locations
    for r in range(ROWS):
        for c in range(COLS-3):
            if all(board[r][c+i] == piece for i in range(4)):
                return True

    # Check vertical locations
    for r in range(ROWS-3):
        for c in range(COLS):
            if all(board[r+i][c] == piece for i in range(4)):
                return True

    # Check positively sloped diagonals
    for r in range(ROWS-3):
        for c in range(COLS-3):
            if all(board[r+i][c+i] == piece for i in range(4)):
                return True

    # Check negatively sloped diagonals
    for r in range(3, ROWS):
        for c in range(COLS-3):
            if all(board[r-i][c+i] == piece for i in range(4)):
                return True

    return False

def get_board_str(board):
    # converts each row into str then combines all the rows into one big string
    return ''.join([''.join(map(str, row)) for row in board])