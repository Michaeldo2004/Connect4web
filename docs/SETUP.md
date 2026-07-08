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
http://localhost:5173/game
```

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
```

Run `docs/supabase_schema.sql` in Supabase before enabling sync.

When those env vars are set, Flask writes game rows, player rows, move rows, and final status updates to Supabase. If they are blank, the app runs without database persistence.

## Frontend

From the frontend folder:

```powershell
cd frontend
npm install
npm run dev
```

Vite opens the browser at `http://localhost:5173`. Pick a difficulty or `Vs Player`, then click `Play` to redirect to `/game`.

Auth uses Supabase Auth. The app has `/login` and `/signup` routes, plus a nav popup. Users must be logged in to play when `AUTH_REQUIRED=true`.

Frontend runtime URLs and paths are read from `frontend/.env`:

```text
VITE_BACKEND_URL=http://localhost:5000
VITE_SETUP_PATH=/
VITE_GAME_PATH=/game
VITE_LOGIN_PATH=/login
VITE_SIGNUP_PATH=/signup
VITE_TOS_PATH=/tos
VITE_PRIVACY_POLICY_PATH=/privacypolicy
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=
```

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

These tests use Node's built-in test runner. They statically check the UI shell routes, Supabase auth wiring, auth modal, footer links, and mobile CSS rules.

## Backend Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

These tests use Flask-SocketIO's in-process test client and do not require the backend server to be running. The Supabase schema tests are static checks against `docs/supabase_schema.sql`.

Current expected result:

```text
Ran 46 tests
OK
```

The tests cover AI websocket gameplay, randomized starting players, two-player websocket rooms, turn enforcement, reset, reconnect, rematch voting, leave-room behavior, the 15-second disconnect default-win rule, env-backed backend config, JWT auth checks, the database schema draft, and Supabase persistence payload behavior.
