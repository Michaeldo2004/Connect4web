# Connect 4 Webapp

React frontend with a Flask-SocketIO backend for Human vs. Minimax AI Connect 4 and two-player Socket.IO rooms.

## Docs

- [Setup](docs/SETUP.md)
- [API](docs/API.md)
- [Project Structure](docs/PROJECT_STRUCTURE.md)
- [Supabase Schema](docs/supabase_schema.sql)
- [Move-analysis Migration](docs/migrations/20260714_move_analysis_worst_move_and_ratings.sql)

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
http://localhost:5173/game/{id}
```

Profiles and completed game history are at:

```text
http://localhost:5173/profiles
```

Placeholder legal pages are available at:

```text
http://localhost:5173/tos
http://localhost:5173/privacypolicy
```

The player picks an AI difficulty or `Vs Player`, then `Play` creates a room and redirects to `/game/{id}`.

Completed games appear under `/profiles` and open at
`/game/{gameId}/review`. Participants can request one shared, persisted move
evaluation per game. The review table shows only the turn, move owner, and a
textual feedback label. Numerical minimax scores, best/worst calculations, and
the raw internal rating remain server-side.

The frontend has a Connect 4 themed shell with a top nav, footer, responsive layout, `/login`, `/signup`, and `/profiles` routes, and a signup/login popup. Auth uses Supabase Auth. Usernames allow letters, numbers, and underscores.

## Database Schema

The canonical fresh-install Supabase schema is in:

```text
docs/supabase_schema.sql
```

It defines profiles, games, game players, move history, lazy post-game move analysis, player stats, indexes, triggers, and participant-scoped RLS policies. Raw `move_analysis` rows intentionally have no client read policy.

For a new project, run `docs/supabase_schema.sql`. For an existing database
that predates worst-move analysis, also run
`docs/migrations/20260714_move_analysis_worst_move_and_ratings.sql`. The
migration is safe to rerun, invalidates incompatible derived analysis, and
removes direct client access to `move_analysis` so ratings remain server-only.

Backend Supabase sync is enabled only when these backend environment variables are set:

```text
SUPABASE_URL
SUPABASE_SECRET_KEY
```

Runtime URLs and app paths are configured through `.env` files:

```text
backend/.env: FRONTEND_ORIGIN, CORS_ALLOWED_ORIGINS, BACKEND_HOST, BACKEND_PORT, SUPABASE_JWT_SECRET, AUTH_REQUIRED, AI_WORKER_COUNT, MOVE_ANALYSIS_DEPTH, MOVE_ANALYSIS_TIME_LIMIT
frontend/.env: VITE_BACKEND_URL, VITE_SOCKET_TRANSPORTS, VITE_SETUP_PATH, VITE_GAME_PATH, VITE_JOIN_PATH, VITE_LOGIN_PATH, VITE_SIGNUP_PATH, VITE_PROFILE_PATH, VITE_AI_WAITING_PATH, VITE_TOS_PATH, VITE_PRIVACY_POLICY_PATH, VITE_SUPABASE_URL, VITE_SUPABASE_PUBLISHABLE_KEY
```

Gameplay socket events require a Supabase access token when `AUTH_REQUIRED=true`.

PvP room creation uses a client-generated request ID plus a Socket.IO
reconciliation query. The backend derives ownership from the verified user,
and the client explicitly joins the authoritative room returned for that
user/request pair. Apply
`docs/migrations/20260715_multiplayer_room_requests.sql` to make this recovery
survive backend restarts; without it, the same flow falls back to memory.

When configured, the backend writes game rows, player rows, valid moves, final game status, and requested move analysis to Supabase. The profile page reads completed game history through the backend; completed means a win or draw was recorded. Live Socket.IO game state still runs in memory.

## API / Socket.IO

```text
GET  /api/health
POST /api/new-game
GET  /api/profile/games
GET  /api/profile/games/{gameId}/moves
POST /api/profile/games/{gameId}/analysis
Socket.IO create_game
Socket.IO join_game
Socket.IO create_multiplayer_game
Socket.IO reconcile_multiplayer_creation
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

The backend stores each board in memory by `gameId`; the browser stores `gameId/playerId` locally so refresh can rejoin the same game while the backend is running. Resetting an AI game issues a new `gameId` and redirects the browser to the new `/game/{id}` room.

For two-player rooms, Player 1 creates a room and Player 2 joins by room ID. If either player disconnects, the backend starts a 15-second reconnect countdown. If they do not return, the other player wins by default.

After a two-player match ends, both players can vote `Play again`. The next game starts with a new `gameId` once both players accept. If one player leaves after the match, the other client receives a popup and can return to the main menu.

Every new AI game randomizes the starting side. Multiplayer rooms randomize the starter when the second player joins; that starter is assigned yellow/player 1. Status is green only for `Your turn`; opponent and AI waiting states are red.

## Limitations

- Database persistence is backend-only and optional. If Supabase env vars are missing, gameplay still works without saving.
- Auth requires Supabase env vars on the frontend. The backend verifies tokens with `SUPABASE_JWT_SECRET` when set, or through the configured Supabase client.
- Waiting-room creation can be recovered after a backend restart when the PvP
  request migration is installed. Active matches still require the original
  backend process.
- Free backend hosting limited CPU resources to work with.
- In-memory Socket.IO rooms are not safe for horizontal scaling without a message queue or shared store.
- Move-analysis jobs are coordinated in memory and currently assume one backend process.

## Status

Currently supports Human vs. AI, two-player Socket.IO rooms, authenticated profiles, completed-game review, and shared server-persisted move evaluation.
