import numpy as np

ROWS = 6
COLS = 7

def create_board():
    return np.zeros((ROWS, COLS), dtype=int)

def print_board(board):
    print(" " + " ".join(map(str, range(1, COLS + 1))))
    for row in board:
        print("|" + "|".join(["O" if x == 1 else "X" if x == 2 else " " for x in row]) + "|")
    print("-" * (2 * COLS + 1))

def is_valid_move(board, col):
    return board[0][col] == 0

def drop_piece(board, col, piece):
    for row in range(ROWS - 1, -1, -1):
        if board[row][col] == 0:
            board[row][col] = piece
            return True
    return False

def check_win(board, piece):
    # Check horizontal locations
    for r in range(ROWS):
        for c in range(COLS - 3):
            if all(board[r][c + i] == piece for i in range(4)):
                return True
    
    # Check vertical locations
    for r in range(ROWS - 3):
        for c in range(COLS):
            if all(board[r + i][c] == piece for i in range(4)):
                return True
    
    # Check positive diagonal locations
    for r in range(ROWS - 3):
        for c in range(COLS - 3):
            if all(board[r + i][c + i] == piece for i in range(4)):
                return True
    
    # Check negative diagonal locations
    for r in range(3, ROWS):
        for c in range(COLS - 3):
            if all(board[r - i][c + i] == piece for i in range(4)):
                return True
    
    return False

def play_game():
    board = create_board()
    game_over = False
    turn = 0
    
    print("Welcome to Connect 4!")
    print_board(board)
    
    while not game_over:
        player = (turn % 2) + 1
        try:
            col = int(input(f"Player {player} (O for 1, X for 2), choose a column (1-{COLS}): ")) - 1
            if col < 0 or col >= COLS or not is_valid_move(board, col):
                print("Invalid move. Try again.")
                continue
        except ValueError:
            print("Please enter a valid column number.")
            continue
        
        drop_piece(board, col, player)
        print_board(board)
        
        if check_win(board, player):
            print(f"Player {player} wins! 🎉")
            game_over = True
        
        turn += 1
        
        if turn == ROWS * COLS and not game_over:
            print("It's a tie!")
            game_over = True

if __name__ == "__main__":
    play_game()
