# Project Structure

```text
Connect-4/
|-- backend/
|   |-- ai/
|   |   |-- minimax.py
|   |   `-- online_ai.py
|   |-- game/
|   |   |-- board.py
|   |   `-- game_loop.py
|   |-- gui/
|   |   `-- gui.py
|   |-- tests/
|   |   |-- __init__.py
|   |   |-- test_auth_layer.py
|   |   |-- test_backend_config.py
|   |   |-- test_game_locks.py
|   |   |-- test_minimax_analysis.py
|   |   |-- test_socket_game.py
|   |   |-- test_supabase_store.py
|   |   `-- test_supabase_schema.py
|   |-- app.py
|   |-- connect4.py
|   |-- evaluation.py
|   |-- evaluation_results.json
|   |-- main.py
|   |-- requirements.txt
|   `-- supabase_store.py
|-- docs/
|   |-- migrations/
|   |   `-- 20260714_move_analysis_worst_move_and_ratings.sql
|   |-- API.md
|   |-- PROJECT_STRUCTURE.md
|   |-- SETUP.md
|   `-- supabase_schema.sql
|-- frontend/
|   |-- src/
|   |   |-- App.jsx
|   |   |-- main.jsx
|   |   `-- styles.css
|   |-- tests/
|   |   `-- ui-shell.test.js
|   |-- index.html
|   |-- package-lock.json
|   |-- package.json
|   `-- vite.config.js
|-- .gitignore
`-- README.md
```

## Runtime Split

`frontend/` renders setup at `/` by default, manual/public room joining at `/join`, active games at `/game/{id}`, completed history and profile stats at `/profiles`, and persisted board-state reviews at `/game/{id}/review`. Reviews can request the shared evaluator, poll its persisted status, render `Turn | Move | Feedback`, and highlight the row matching the selected board turn. Profile, review, auth, legal, and not-found routes are REST-only and do not open gameplay Socket.IO connections. The frontend never receives numerical minimax details or the raw server-side rating.

`backend/` owns the Flask health/profile/review APIs, authenticated shared move-analysis requests, the prioritized minimax evaluator queue, server-only ratings, legacy-schema review fallback, Socket.IO AI games, two-player rooms, public waiting-room listing, disconnect timers, rematch voting, leave-room events, board rules, optional Supabase persistence, CLI evaluation files, tests, and Python dependencies.

`docs/supabase_schema.sql` is the canonical fresh-install schema for Supabase auth, profile history, shared game review, and lazy post-game move analysis. Existing installations that predate worst-move fields use `docs/migrations/20260714_move_analysis_worst_move_and_ratings.sql`; the migration invalidates incompatible derived rows and removes direct client access to raw analysis. Runtime sync is backend-only and no-ops when Supabase env vars are missing.
