# Connect 4 Webapp

React frontend with a Flask backend for Human vs. Minimax AI Connect 4 using minimax alpha-beta pruning with iterative deepening.

## Docs

- [Setup](docs/SETUP.md)
- [API](docs/API.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)

## Backend

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python backend\app.py
```

Flask runs on:

```text
http://localhost:5000
```

## Frontend

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\frontend
npm install
npm run dev
```

React runs on:

```text
http://localhost:5173
```

## API

```text
GET  /api/health
POST /api/new-game
POST /api/move
```

### `POST /api/move`

Request body:

```json
{
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0]
  ],
  "column": 3,
  "difficulty": "medium",
  "transpositionTable": {}
}
```

## Limitations

- using free backend hosting plan: no load balancer for horizontally scaling application

## Status

Currently supports Human vs. AI locally. PvP and account-based game history are planned but not yet implemented.
