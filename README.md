# Connect 4 Webapp

React frontend with a Flask-SocketIO backend for Human vs. Minimax AI Connect 4 and two-player websocket rooms.

## Docs

- [Setup](docs/SETUP.md)
- [API](docs/API.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)
- [Supabase Schema Draft](docs/supabase_schema.sql)

## Backend

```powershell
cd backend
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
cd frontend
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

Placeholder legal pages are available at:

```text
http://localhost:5173/tos
http://localhost:5173/privacypolicy
```

The player picks an AI difficulty or `Vs Player`, then `Play` redirects to `/game`.

The frontend has a Connect 4 themed shell with a top nav, footer, responsive layout, `/login` and `/signup` routes, and a signup/login popup. Auth uses Supabase Auth. Usernames allow letters, numbers, and underscores.

## Database Draft

The Supabase schema draft is in:

```text
docs/supabase_schema.sql
```

It defines profiles, games, game players, move history, lazy post-game move analysis, player stats, indexes, triggers, and RLS read policies.

Backend Supabase sync is enabled only when these backend environment variables are set:

```text
SUPABASE_URL
SUPABASE_SECRET_KEY
```

Runtime URLs and app paths are configured through `.env` files:

```text
backend/.env: FRONTEND_ORIGIN, CORS_ALLOWED_ORIGINS, BACKEND_HOST, BACKEND_PORT, SUPABASE_JWT_SECRET, AUTH_REQUIRED
frontend/.env: VITE_BACKEND_URL, VITE_SETUP_PATH, VITE_GAME_PATH, VITE_LOGIN_PATH, VITE_SIGNUP_PATH, VITE_TOS_PATH, VITE_PRIVACY_POLICY_PATH
```

Gameplay socket events require a Supabase access token when `AUTH_REQUIRED=true`.

When configured, the backend writes game rows, player rows, valid moves, and final game status to Supabase. The live websocket game state still runs in memory.

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

- Database persistence is backend-only and optional. If Supabase env vars are missing, gameplay still works without saving.
- Auth requires Supabase env vars on the frontend and `SUPABASE_JWT_SECRET` on the backend.
- Rejoin only works while the backend process stays alive.
- Free backend hosting has no load balancer for horizontally scaling websocket rooms.
- In-memory Socket.IO rooms are not safe for horizontal scaling without a message queue or shared store.

## Status

Currently supports Human vs. AI and two-player websocket rooms. Account-based game history is not implemented.
