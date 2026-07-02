# Connect 4 Webapp

React frontend with a Flask-SocketIO backend for Human vs. Minimax AI Connect 4 using minimax alpha-beta pruning with iterative deepening.

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

Gameplay uses Socket.IO on the backend port. `GET /api/health` remains available for health checks.

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

## API / Socket.IO

```text
GET  /api/health
Socket.IO create_game
Socket.IO join_game
Socket.IO player_move
Socket.IO reset_game
```

### `player_move`

Socket.IO payload:

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id",
  "column": 3
}
```

The backend stores each board in memory by `gameId`; the browser stores `gameId/playerId` locally so refresh can rejoin the same game while the backend is running.

## Limitations

- using free backend hosting plan: no load balancer for horizontally scaling application

## Status

Currently supports Human vs. AI locally. PvP and account-based game history are planned but not yet implemented.
