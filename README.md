# Connect 4 Webapp

React frontend with a Flask-SocketIO backend for Human vs. Minimax AI Connect 4 and two-player websocket rooms.

## Docs

- [Setup](docs/SETUP.md)
- [API](docs/API.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)

## Backend

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
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

Setup is at `/`. The active board is at:

```text
http://localhost:5173/game
```

The player picks an AI difficulty or `Vs Player`, then `Play` redirects to `/game`.

## API / Socket.IO

```text
GET  /api/health
Socket.IO create_game
Socket.IO join_game
Socket.IO create_multiplayer_game
Socket.IO join_multiplayer_game
Socket.IO player_move
Socket.IO reset_game
Socket.IO play_again
Socket.IO leave_game
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

For two-player rooms, Player 1 creates a room and Player 2 joins by room ID. If either player disconnects, the backend starts a 15-second reconnect countdown. If they do not return, the other player wins by default.

After a two-player match ends, both players can vote `Play again`. The next game starts once both players accept. If one player leaves after the match, the other client receives a popup and can return to the main menu.

Every new game randomizes the starting side. Status is green only for `Your turn`; opponent and AI waiting states are red.

## Limitations

- No database persistence.
- Rejoin only works while the backend process stays alive.
- Free backend hosting has no load balancer for horizontally scaling websocket rooms.
- In-memory Socket.IO rooms are not safe for horizontal scaling without a message queue or shared store.

## Status

Currently supports Human vs. AI and two-player websocket rooms. Account-based game history is not implemented.
