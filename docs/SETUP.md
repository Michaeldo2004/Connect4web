# Setup

## Requirements

- Node.js
- npm
- Python 3.12 recommended

The frontend runs on:

```text
http://localhost:5173
```

Setup screen:

```text
http://localhost:5173/
```

Game screen:

```text
http://localhost:5173/game/{id}
```

Join screen:

```text
http://localhost:5173/join
```

Profile screen:

```text
http://localhost:5173/profiles
```

Completed games can be opened for review at:

```text
http://localhost:5173/game/{gameId}/review
```

Game review displays each persisted board state and move navigation. Only completed games can be reviewed. Invalid review routes show `/404` and return home after three seconds. Completed games can request a shared move evaluation from the review page. The resulting `Turn | Move | Feedback` table shows the server-classified feedback for every turn, highlights the currently selected turn, and is shared by both participants in a multiplayer game. Numerical minimax details and the raw rating are not sent to or rendered by the frontend.

Placeholder legal pages:

```text
http://localhost:5173/tos
http://localhost:5173/privacypolicy
```

The backend runs on:

```text
http://localhost:5000
```

Gameplay uses Socket.IO on the same backend port.

## Backend

From the backend folder:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

If `python` is not on PATH, use your installed Python executable directly:

```powershell
& 'C:\path\to\python.exe' -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Supabase Sync

Create `backend/.env` from `backend/.env.example`:

```text
SUPABASE_URL=
SUPABASE_SECRET_KEY=
SUPABASE_JWT_SECRET=
AUTH_REQUIRED=true
FRONTEND_ORIGIN=http://localhost:5173
CORS_ALLOWED_ORIGINS=http://localhost:5173
BACKEND_HOST=localhost
BACKEND_PORT=5000
AI_WORKER_COUNT=1
MOVE_ANALYSIS_DEPTH=4
MOVE_ANALYSIS_TIME_LIMIT=30
```

For a fresh install, run `docs/supabase_schema.sql` in Supabase before enabling
sync. If the move-analysis tables already exist, run
`docs/migrations/20260714_move_analysis_worst_move_and_ratings.sql` instead.
The migration invalidates legacy derived analysis rows so affected games can
be evaluated again with truthful worst-move data and the current rating rules.
Apply it before exposing move evaluation: it also removes the legacy direct
read policy on `move_analysis`, keeping rating data server-only.

After migration, `GET /api/profile/games/{gameId}/moves` reports
`analysis_available: true` and `analysis_unavailable_reason: null`. Before the
migration, the compatibility fallback still returns board history but disables
evaluation and reports why. `POST /api/profile/games/{gameId}/analysis` will not
queue work against an incompatible schema.

The following optional Supabase SQL Editor check should return both required
columns and no policy rows for `move_analysis`:

```sql
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'move_analysis'
  and column_name in ('worst_column', 'worst_score')
order by column_name;

select policyname, cmd
from pg_policies
where schemaname = 'public'
  and tablename = 'move_analysis';
```

When those env vars are set, Flask writes game rows, player rows, move rows, and final status updates to Supabase. If they are blank, the app runs without database persistence.

## Frontend

From the frontend folder:

```powershell
cd frontend
npm install
npm run dev
```

Vite opens the browser at `http://localhost:5173`. Pick `Vs Player` or a `Vs AI` difficulty, then click `Create game` to create a room and redirect to `/game/{id}`. The `/join` route supports manual room ID joins and refreshable public waiting-room discovery.

Auth uses Supabase Auth. The app has `/login`, `/signup`, and `/profiles` routes, plus a nav popup. Users must be logged in to play or view completed profile games when `AUTH_REQUIRED=true`.

Frontend runtime URLs and paths are read from `frontend/.env`:

```text
VITE_BACKEND_URL=http://localhost:5000
VITE_SOCKET_TRANSPORTS=polling
VITE_SETUP_PATH=/
VITE_GAME_PATH=/game
VITE_JOIN_PATH=/join
VITE_LOGIN_PATH=/login
VITE_SIGNUP_PATH=/signup
VITE_PROFILE_PATH=/profiles
VITE_TOS_PATH=/tos
VITE_PRIVACY_POLICY_PATH=/privacypolicy
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=
```

Local development defaults to Socket.IO long-polling because Werkzeug can log
a spurious 500 when its development WebSocket closes. A deployed WebSocket-
capable server can set `VITE_SOCKET_TRANSPORTS=polling,websocket` to allow the
normal upgrade path.

## Build Check

```powershell
cd frontend
npm run build
```

## Frontend Tests

```powershell
cd frontend
npm test
```

These tests use Node's built-in test runner. They check the UI shell routes, Supabase auth wiring, profile history and review UI, shared evaluator request/poll/render behavior, schema-unavailable handling, REST-only review socket behavior, public room joining, auth UI, footer links, and responsive CSS rules.

## Backend Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

These tests use Flask-SocketIO's in-process test client and do not require the backend server to be running. The Supabase schema tests are static checks against `docs/supabase_schema.sql`.

The command should finish with `OK`. The tests cover AI Socket.IO gameplay, AI worker admission and stale-result protection, AI opening move flow, yellow-token multiplayer starter assignment, two-player rooms, public waiting-room listing and join rejection, turn enforcement, reset/reconnect/rematch/leave behavior, disconnect default wins, env-backed backend config, JWT/Supabase auth checks, completed history and shared review authorization, move evaluator rules and queueing, legacy-schema fallback, the canonical Supabase schema, and persistence payload behavior.
