# gui/gui.py
import tkinter as tk
from tkinter import messagebox
import numpy as np
from game.board import create_board, drop_piece, is_valid_move, check_win, get_board_str
from ai.minimax import get_best_move  # We'll create this
from ai.online_ai import OnlineAI

ROWS = 6
COLS = 7
SQUARE_SIZE = 100

class Connect4GUI:
    def __init__(self, root, ai_type):
        self.root = root
        self.root.title(f"Connect 4 {ai_type} - Your Turn")
        self.board = create_board()
        self.turn = 1  # Player 1 starts
        self.ai_type = ai_type

        self.canvas = tk.Canvas(self.root, width=COLS*SQUARE_SIZE, height=ROWS*SQUARE_SIZE, bg="blue")
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self.handle_click)
        self.draw_board()

    def draw_board(self):
        self.canvas.delete("all")
        for r in range(ROWS):
            for c in range(COLS):
                x0 = c * SQUARE_SIZE
                y0 = r * SQUARE_SIZE
                x1 = x0 + SQUARE_SIZE
                y1 = y0 + SQUARE_SIZE
                color = "white"
                if self.board[r][c] == 1:
                    color = "yellow"
                elif self.board[r][c] == 2:
                    color = "red"
                self.canvas.create_oval(x0+5, y0+5, x1-5, y1-5, fill=color)

    def handle_click(self, event):
        if self.turn == 1:
            col = event.x // SQUARE_SIZE
            if is_valid_move(self.board, col):
                drop_piece(self.board, col, 1)
                if check_win(self.board, 1):
                    self.draw_board()
                    messagebox.showinfo("Game Over", "You (Yellow) win!")
                    self.root.quit()
                self.root.title("Connect 4 - AI's Turn")
                self.turn = 2
                self.draw_board()
                self.root.after(500, self.ai_move)

    def ai_move(self):
        if self.turn == 2:
            if self.ai_type == "local":
                col = get_best_move(self.board, 2)
            else:
                # i guess we assume it is online
                board_data = get_board_str(self.board)
                # creates an instance of the online stuff to call upon
                online_ai = OnlineAI()
                col = online_ai.get_best_online_move(board_data)

            if is_valid_move(self.board, col):
                drop_piece(self.board, col, 2)
                if check_win(self.board, 2):
                    self.draw_board()
                    messagebox.showinfo("Game Over", "AI (Red) wins!")
                    self.root.quit()
                self.root.title("Connect 4 - Your Turn")
                self.turn = 1
                self.draw_board()

def launch_game(ai_type):
    root = tk.Tk()
    game = Connect4GUI(root, ai_type=ai_type)
    root.mainloop()
