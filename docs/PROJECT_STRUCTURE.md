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
|   |-- app.py
|   |-- connect4.py
|   |-- evaluation.py
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

`frontend/` renders the Connect 4 board and sends moves to Flask.

`backend/` owns the Flask API, board rules, AI logic, CLI files, and Python dependencies.
