# Project Structure

```text
Connect-4/
├── ai/
│   ├── minimax.py
│   └── online_ai.py
├── backend/
│   └── app.py
├── docs/
│   ├── API.md
│   ├── PROJECT_STRUCTURE.md
│   └── SETUP.md
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   └── styles.css
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── game/
│   ├── board.py
│   └── game_loop.py
├── README.md
└── requirements.txt
```

## Runtime Split

`frontend/` renders the Connect 4 board and sends moves to Flask.

`backend/app.py` validates moves, updates the board, calls the Minimax AI, and returns the updated game state.

`game/board.py` owns the board rules.

`ai/minimax.py` owns the local AI move selection.

The old Tkinter and CLI files are no longer the launch path.

