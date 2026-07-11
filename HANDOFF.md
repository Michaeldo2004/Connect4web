# Connect 4 Handoff

## Project context

- Frontend: Vercel-targeted React/Vite app in `frontend/`.
- Backend: Render-targeted Flask-SocketIO app in `backend/`.
- Database: Supabase free tier, used for auth, profiles, completed game history, and move history.
- Expected backend runtime: single Render free instance with roughly one CPU core.
- Active game state is still in backend memory. Supabase stores completed/history data, not live resumable room state.

## Recent changes completed

### Backend

- Hard AI timer changed from `5s` to `4s` in `backend/app.py`.
- Multiplayer `reset_game` is now rejected. Multiplayer rematches should only go through the existing `play_again` two-player vote flow.
- Added regression coverage in `backend/tests/test_socket_game.py` so a live multiplayer match cannot be reset unilaterally.

### Frontend

- Added dark/light theme toggle in the navbar.
- Theme is persisted in `localStorage` using `connect4_theme`.
- Toggle is placed before the account name in the authenticated navbar.
- Added light/dark CSS variable support.
- Added responsive/mobile layout improvements for navbar wrapping, account actions, board layout, profile rows, and public room rows.
- Added skeleton loading states for:
  - account/socket/game loading
  - profile game history loading
  - game review loading
  - public room loading
- Updated frontend hard AI display label to `4s` to match the backend.
- Added frontend static test coverage for theme and skeleton loading states.

## Verification already run

Backend:

```powershell
cd backend
.venv\Scripts\python.exe -m unittest tests.test_socket_game
.venv\Scripts\python.exe -m unittest discover -s tests
```

Result:

- Socket tests passed: `31 tests`
- Full backend tests passed: `68 tests`

Frontend:

```powershell
cd frontend
npm test
npm run build
```

Result:

- Frontend tests passed: `13/13`
- Vite production build passed
- Local Vite dev server was started and responded at `http://localhost:5173`

Note: `npm test` printed a PowerShell npm wrapper `Access is denied` warning after successful test output, but exited with status `0`.

## Important current risks

### AI queue architecture is not implemented

The backend still runs/waits for AI while holding the per-game lock.

Current flow:

```text
player move
  -> lock game
  -> apply human move
  -> run/wait for AI
  -> apply AI move
  -> unlock game
  -> emit update
```

Suggested future flow:

```text
player move
  -> lock game briefly
  -> apply human move
  -> mark ai_thinking
  -> enqueue AI job with game_id + move_number
  -> unlock game
  -> emit "AI is thinking"

AI worker
  -> compute move outside lock
  -> lock game briefly
  -> verify same game_id + move_number
  -> apply AI move or discard stale result
  -> unlock game
  -> emit update
```

Missing pieces:

- bounded AI queue
- queue-full `"AI is busy, try again"` response
- `ai_thinking` state
- move/version validation before applying AI output
- stale AI result discard
- releasing the game lock during CPU search

### Deployment config still needs attention

- Vercel SPA deep links need a rewrite to `index.html`.
- Render should run a production server command, not Flask's debug Werkzeug server.
- Render free can sleep/restart, which will drop in-memory active games.

## Dirty worktree note

The working tree had existing uncommitted changes before the latest handoff work. Do not assume every modified file was changed in the latest pass.

Currently modified areas include backend, frontend, and docs files. Review with:

```powershell
git status --short
git diff -- backend/app.py backend/tests/test_socket_game.py frontend/src/App.jsx frontend/src/styles.css frontend/tests/ui-shell.test.js
```

## Recommended next steps

1. Add Vercel rewrite config for SPA routes.
2. Add Render production start config.
3. Decide whether to implement the simple bounded AI queue.
4. If implementing the queue, keep it single-process/single-worker for Render free first.
5. Later, if scaling beyond one backend instance, move live room coordination to Redis or durable Supabase-backed state.

⚠ Better approach available: store this handoff as an untracked local `HANDOFF.md` for now, then convert the stable parts into docs after deployment choices are final — keeps temporary review notes out of permanent docs until decisions settle — tradeoff is someone must remember to promote the useful parts later.
