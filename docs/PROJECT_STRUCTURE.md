# Project Structure

```text
Connect-4/
|-- backend/
|   |-- ai/
|   |   |-- minimax.py
|   |   `-- online_ai.py
|   |-- game/
|   |   |-- board.py
|   |   `-- game_loop.py
|   |-- gui/
|   |   `-- gui.py
|   |-- tests/
|   |   |-- __init__.py
|   |   `-- test_socket_game.py
|   |-- app.py
|   |-- connect4.py
|   |-- evaluation.py
|   |-- evaluation_results.json
|   |-- main.py
|   `-- requirements.txt
|-- docs/
|   |-- API.md
|   |-- PROJECT_STRUCTURE.md
|   `-- SETUP.md
|-- frontend/
|   |-- src/
|   |   |-- App.jsx
|   |   |-- main.jsx
|   |   `-- styles.css
|   |-- index.html
|   |-- package-lock.json
|   |-- package.json
|   `-- vite.config.js
|-- .gitignore
`-- README.md
```

## Runtime Split

`frontend/` renders setup at `/`, redirects selected games to `/game`, and sends gameplay events to Flask-SocketIO.

`backend/` owns the Flask health API, Socket.IO AI games, two-player rooms, disconnect timers, rematch voting, leave-room events, board rules, AI logic, CLI evaluation files, tests, and Python dependencies.
