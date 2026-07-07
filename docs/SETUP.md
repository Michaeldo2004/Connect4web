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
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\backend
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

## Frontend

From the frontend folder:

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\frontend
npm install
npm run dev
```

Vite opens the browser at `http://localhost:5173`. Pick a difficulty or `Vs Player`, then click `Play` to redirect to `/game`.

The signup/login popup is placeholder-only. Its inputs are locally sanitized, and usernames allow letters, numbers, and underscores.

## Build Check

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\frontend
npm run build
```

## Frontend Tests

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\frontend
npm test
```

These tests use Node's built-in test runner. They statically check the UI shell routes, placeholder auth modal, footer links, and mobile CSS rules.

## Backend Tests

```powershell
cd C:\Users\micha\OneDrive\Desktop\VSC\Connect-4\backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

These tests use Flask-SocketIO's in-process test client and do not require the backend server to be running. The Supabase schema tests are static checks against `docs/supabase_schema.sql`.

Current expected result:

```text
Ran 34 tests
OK
```

The tests cover AI websocket gameplay, randomized starting players, two-player websocket rooms, turn enforcement, reset, reconnect, rematch voting, leave-room behavior, the 15-second disconnect default-win rule, and the database schema draft.
