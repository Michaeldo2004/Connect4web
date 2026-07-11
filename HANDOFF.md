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

### Game Review

- Profile rows now use `Review Game` instead of displaying the room ID.
- Completed games open at `/game/{gameId}/review`; inaccessible, incomplete, missing, or empty-history reviews redirect to `/404`, then return home after three seconds.
- The authenticated review endpoint returns only a participant's completed move history in chronological order.
- Review includes board navigation, player-colored move chips in ten-move rows, drop animations, final winning-line highlighting, and game status/mode/room/move-order metadata.
- Move evaluation is intentionally not implemented yet.

### Live AI Scheduling

- AI calculations now run outside per-game locks.
- `aiThinking` is included in AI-game `board_updated` payloads while a search is active.
- A non-blocking worker-slot semaphore prevents app-level executor backlog. A non-terminal move is rejected with `AI is busy, try again` before board mutation when every AI worker is occupied.
- AI results are applied only when the same in-memory game object and `move_number` are still current; stale results are discarded.
- A timed-out process keeps its slot until it actually exits, preventing the executor from silently accepting excess work.

## Verification already run

Backend:

```powershell
cd backend
.venv\Scripts\python.exe -m unittest tests.test_socket_game
.venv\Scripts\python.exe -m unittest discover -s tests
```

Result:

- Socket tests passed: `34 tests`
- Full backend tests passed: `71 tests`

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

### Postgame Evaluation Architecture Is Not Implemented

Do not reuse the live Socket.IO AI executor for postgame move evaluation. On a one-CPU Render instance, a long evaluation can still delay active games even though it no longer holds a game lock.

Recommended design:

```text
completed game
  -> insert/upsert durable Supabase analysis_jobs row

separate one-worker analysis service
  -> atomically claim one job with a lease
  -> replay game_moves in order
  -> score legal moves from each board_before with a pure minimax wrapper
  -> upsert move_analysis by move_id + minimax_depth
  -> save progress, retry count, and final status
```

- Keep analysis durable and idempotent so Render restarts do not lose work.
- Keep the analysis service separate from the live Socket.IO service.
- For rating precedence, use: highest score = `best`; otherwise lowest negative = `blunder`; other negative = `mistake`; remaining non-best nonnegative = `good`.
- A future `analysis_jobs` table needs status, requested depth, current move cursor, attempts, lease expiry, and error fields.

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
3. Add the durable postgame evaluation job/worker design when move ratings are ready.
4. If live AI demand exceeds worker slots, decide whether a bounded live queue is preferable to the current reject-and-retry behavior.
5. Later, if scaling beyond one backend instance, move live room coordination to Redis or durable Supabase-backed state.

⚠ Better approach available: store this handoff as an untracked local `HANDOFF.md` for now, then convert the stable parts into docs after deployment choices are final — keeps temporary review notes out of permanent docs until decisions settle — tradeoff is someone must remember to promote the useful parts later.
