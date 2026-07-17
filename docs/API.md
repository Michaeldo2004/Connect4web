# API

Base backend URL:

```text
http://localhost:5000
```

## Health

```text
GET /api/health
```

Response:

```json
{
  "status": "ok"
}
```

## Profile Games

```text
GET /api/profile/games
```

Requires `Authorization: Bearer <supabase-access-token>`.

Returns games that ended with a win or draw.

Response:

```json
{
  "games": [
    {
      "id": "game-id",
      "mode": "ai",
      "difficulty": "medium",
      "status": "human_win",
      "winnerPlayerNumber": 1,
      "startedAt": "2026-01-01T00:00:00+00:00",
      "endedAt": "2026-01-01T00:10:00+00:00",
      "playerNumber": 1,
      "result": "Win"
    }
  ]
}
```

## Profile Game Review

```text
GET /api/profile/games/{gameId}/moves
```

Requires `Authorization: Bearer <supabase-access-token>`. The authenticated user must be a participant in the game.

Returns the shared analysis state and recorded moves in ascending order. When
analysis is complete, each move includes only its server-classified feedback
label. Numerical minimax values and the raw rating stay server-side. The
`move_analysis` table has no authenticated-client SELECT policy; only the
backend service role reads or writes raw analysis rows.

```json
{
  "analysis_status": "complete",
  "analysis_error": null,
  "analysis_available": true,
  "analysis_unavailable_reason": null,
  "moves": [
    {
      "move_number": 1,
      "player_number": 1,
      "column_played": 3,
      "board_before": [[0, 0, 0, 0, 0, 0, 0]],
      "board_after": [[0, 0, 0, 1, 0, 0, 0]],
      "move_analysis": [
        {
          "feedback": "Great Move"
        }
      ]
    }
  ]
}
```

`analysis_status` is one of `not_requested`, `processing`, `complete`, or
`failed`. `analysis_error` is populated only after a failed job. If an existing
database has not received the required move-analysis migration, the endpoint
still returns board history with `analysis_available: false`; evaluation stays
disabled until the schema update is applied.

Public feedback is one of `Blunder`, `Mistake`, `OK`, or `Great Move`. The
backend maps its internal persisted rating to this label before serialization.
The review table renders only `Turn | Move | Feedback`; it never receives or
renders scores, best/worst columns, minimax depth, score loss, or raw rating.

An unexpected datastore failure returns JSON HTTP 503 instead of a Flask HTML
error page:

```json
{
  "message": "Game review is temporarily unavailable. Please try again.",
  "code": "game_review_unavailable"
}
```

To request move analysis (or safely repeat the request):

```text
POST /api/profile/games/{gameId}/analysis
```

The request uses the same bearer-token and participant checks. It is
idempotent: completed analysis returns `complete`; an existing job returns its
current `queued` or `running` state; and a failed analysis can be requested
again. A newly accepted job returns HTTP 202:

```json
{
  "gameId": "game-id",
  "status": "queued",
  "queuePosition": 1,
  "priority": "move_analysis"
}
```

If the required database migration is missing, the request returns HTTP 503
without queueing a job:

```json
{
  "message": "Move evaluation is temporarily unavailable while the review database is updated.",
  "code": "move_analysis_schema_update_required"
}
```

A repaired/reconstructed move without a persistent move id returns HTTP 422
with code `incomplete_move_history` instead of starting a job that cannot be
saved.

Poll `GET /api/profile/games/{gameId}/moves` for the persisted
`analysis_status`; reload its moves when that status becomes `complete`.

Analysis is game-scoped, so either participant in a multiplayer game sees the
same status and results. The frontend review is available at
`/game/{gameId}/review` and only loads completed games.

## Socket.IO Gameplay

Gameplay uses Socket.IO at:

```text
http://localhost:5000
```

The backend stores each active board in memory by `gameId`. AI game sessions use `localStorage`. Multiplayer sessions use per-tab `sessionStorage` so two browser tabs do not share the same `playerId`.

When `AUTH_REQUIRED=true`, gameplay Socket.IO payloads must include a Supabase JWT access token:

```json
{
  "accessToken": "supabase-access-token"
}
```

Create, join, move, reset, leave, and rematch events reject missing or invalid tokens.

### Client Events

```text
create_game { difficulty, accessToken }
join_game { gameId, playerId, accessToken }
create_multiplayer_game { ownerName?, requestId?, difficulty?, accessToken }
reconcile_multiplayer_creation { requestId, accessToken }
list_public_games { accessToken }
set_room_public { gameId, playerId, public, accessToken }
join_multiplayer_game { gameId, playerId?, requestId?, publicJoin?, accessToken }
player_move { gameId, playerId, column, accessToken }
reset_game { gameId, playerId, difficulty?, accessToken }
play_again { gameId, playerId, accessToken }
leave_game { gameId, playerId, accessToken }
```

### Server Events

```text
game_created { gameId, playerId, playerNumber, aiNumber, board, status, message, difficulty, mode, currentPlayer, timeBanksMs, activeTimerPlayer, timerRunning, serverTimeMs, endReason }
game_joined { gameId, playerNumber?, aiNumber?, board, status, message, difficulty, mode, currentPlayer, timeBanksMs, activeTimerPlayer, timerRunning, serverTimeMs, endReason }
multiplayer_game_created { gameId, playerId, playerNumber, playersConnected, board, status, message, difficulty, mode, requestId?, recovered?, timeBanksMs, activeTimerPlayer, timerRunning, serverTimeMs, endReason }
multiplayer_game_joined { gameId, playerId, playerNumber, playersConnected, board, status, message, difficulty, mode, requestId?, timeBanksMs, activeTimerPlayer, timerRunning, serverTimeMs, endReason }
board_updated { gameId, playerId?, playerNumber?, aiNumber?, board, status, message, aiMove, aiThinking?, difficulty, mode, currentPlayer?, playersConnected?, disconnectDeadline?, playAgainAccepted?, publicRoom?, timeBanksMs, activeTimerPlayer, timerRunning, serverTimeMs, endReason }
play_again_updated { gameId, board, status, message, difficulty, mode, currentPlayer, playersConnected, playAgainAccepted }
public_games { games: [{ gameId, ownerName, difficulty }] }
player_left { gameId, message }
game_left { gameId }
invalid_move { gameId, board, status, gameStatus, message, difficulty, mode, timeBanksMs, activeTimerPlayer, timerRunning, serverTimeMs, endReason }
join_rejected { gameId, message, requestId? }
create_rejected { message, requestId?, code }
```

`timeBanksMs` maps player numbers (`"1"` and/or `"2"`) to the
server-authoritative remaining milliseconds. `activeTimerPlayer` identifies the
running bank, while `timerRunning` indicates whether the client should animate
it. `serverTimeMs` accompanies each snapshot for synchronization, and
`endReason` is live-only (`connect_four`, `timeout`, `time_tiebreak`,
`disconnect`, `draw`, or `abandoned`). Timer state is not stored in Supabase.
PvP `difficulty` is one of `multiplayer` (90 seconds), `fast_connect_60`, or
`fast_connect_30`. Missing creation difficulty defaults to `multiplayer`;
unknown values are rejected.

PvP creation uses an idempotent command followed by authoritative
reconciliation. The client stores a `requestId` scoped to its authenticated
profile, sends `create_multiplayer_game`, and retains the ID until a correlated
`multiplayer_game_joined` event arrives. A create event or acknowledgement only
causes the client to reconcile; it is not the source of truth for navigation.

Successful acknowledgement:

```json
{
  "ok": true,
  "requestId": "client-generated-uuid",
  "recovered": false,
  "gameId": "generated-game-id",
  "playerId": "generated-player-id",
  "status": "waiting",
  "mode": "multiplayer"
}
```

Rejected acknowledgement:

```json
{
  "ok": false,
  "requestId": "client-generated-uuid",
  "code": "create_rejected",
  "message": "Could not create game"
}
```

After any response loss, timeout, reconnect, or page reload, the client sends:

```json
{
  "requestId": "client-generated-uuid",
  "accessToken": "supabase-access-token"
}
```

to `reconcile_multiplayer_creation`. A found response is:

```json
{
  "ok": true,
  "status": "found",
  "requestId": "client-generated-uuid",
  "gameId": "generated-game-id",
  "playerId": "player-one-id"
}
```

The client then emits `join_multiplayer_game` with those authoritative IDs and
the same `requestId`. `status: "not_found"` causes the original create command
to be retried with the same ID. Terminal states such as `cancelled`, `expired`,
`completed`, and `invalid` reject the stale command rather than creating a new
room under the same ID.

The emitted `multiplayer_game_created` and `create_rejected` events remain
available for compatibility. With the
`20260715_multiplayer_room_requests.sql` and
`20260716_fast_connect_modes.sql` migrations installed, the authenticated
profile/request-to-room mapping survives backend restarts. Active boards and
live matches remain in memory; durable hydration is intentionally limited to a
waiting one-player room with no moves.

### `create_game`

Request:

```json
{
  "difficulty": "medium",
  "accessToken": "supabase-access-token"
}
```

Response event: `game_created`

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id",
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0]
  ],
  "status": "playing",
  "message": "Your turn",
  "aiMove": null,
  "difficulty": "medium",
  "mode": "ai",
  "currentPlayer": 1,
  "playerNumber": 1,
  "aiNumber": 2
}
```

AI games randomize the starting side. The first mover is always `1` (yellow). If AI starts, `game_created` returns the empty board with `currentPlayer: 1`, `playerNumber: 2`, `aiNumber: 1`, and `message: "AI is thinking"`, then `board_updated` carries the recorded yellow AI opening move and switches `currentPlayer` to `2`.

### `join_game`

Request:

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id",
  "accessToken": "supabase-access-token"
}
```

If the `playerId` matches the game, the server emits `game_joined`. If it does not match, the server emits `join_rejected`.

## Multiplayer Socket.IO

### `create_multiplayer_game`

Request:

```json
{
  "ownerName": "Player",
  "requestId": "client-generated-uuid",
  "difficulty": "fast_connect_60",
  "accessToken": "supabase-access-token"
}
```

The response event and acknowledgement remain `multiplayer_game_created`, but
the current client reconciles the request and explicitly joins the returned
authoritative room before navigating.

```json
{
  "gameId": "generated-game-id",
  "playerId": "player-one-id",
  "playerNumber": 1,
  "playersConnected": 1,
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0]
  ],
  "status": "waiting",
  "message": "Waiting for Player 2",
  "aiMove": null,
  "difficulty": "fast_connect_60",
  "mode": "multiplayer",
  "currentPlayer": 1,
  "publicRoom": false
}
```

The starter is randomized when the second player joins. The starter is assigned `playerNumber: 1`, which renders as yellow, and `currentPlayer` starts at `1`.

### Public Multiplayer Rooms

Waiting multiplayer rooms start private. Player 1 can toggle public visibility only while the room is waiting for Player 2.

Make a waiting room public:

```json
{
  "gameId": "generated-game-id",
  "playerId": "player-one-id",
  "public": true,
  "accessToken": "supabase-access-token"
}
```

Response event: `board_updated` with `publicRoom: true`.

List joinable public rooms:

```json
{
  "accessToken": "supabase-access-token"
}
```

Response event: `public_games`

```json
{
  "games": [
    {
      "gameId": "generated-game-id",
      "ownerName": "Player",
      "difficulty": "fast_connect_60"
    }
  ]
}
```

The public list is in-memory and intended for a single backend instance. A public room stops being public when Player 2 joins, when the room stops waiting, or when the waiting owner disconnects.

### `join_multiplayer_game`

Player 2 joins with only the room ID:

```json
{
  "gameId": "generated-game-id",
  "accessToken": "supabase-access-token"
}
```

Public-list joins include `publicJoin: true` so the server can reject rooms that stopped being public before the click reached the backend:

```json
{
  "gameId": "generated-game-id",
  "publicJoin": true,
  "accessToken": "supabase-access-token"
}
```

Reconnect uses the saved player ID:

```json
{
  "gameId": "generated-game-id",
  "playerId": "existing-player-id",
  "accessToken": "supabase-access-token"
}
```

Response event: `multiplayer_game_joined`

```json
{
  "gameId": "generated-game-id",
  "playerId": "player-two-id",
  "playerNumber": 1,
  "playersConnected": 2,
  "status": "playing",
  "message": "Player 1 turn",
  "difficulty": "multiplayer",
  "mode": "multiplayer",
  "currentPlayer": 1
}
```

The joining player can be assigned `playerNumber: 1` or `playerNumber: 2`. Player 1 is always yellow and always starts the current multiplayer game.

Rooms with two assigned players reject additional joins. Reconnects require one of the existing saved `playerId` values.

### Multiplayer Disconnect Rule

If a multiplayer user disconnects, the server emits `board_updated` with a disconnect message and `disconnectDeadline`. The frontend uses that deadline to show a live countdown. If the same `playerId` reconnects before the timer expires, the timer is canceled. If not, the remaining player wins by default.

Default-win message:

```text
Player 1 wins by default
Player 2 wins by default
```

### `play_again`

Available only after a multiplayer match ends.

Request:

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id",
  "accessToken": "supabase-access-token"
}
```

When one player accepts, the server emits `play_again_updated`:

```json
{
  "gameId": "generated-game-id",
  "status": "player1_win",
  "message": "Player 1 wins",
  "playAgainAccepted": 1,
  "playersConnected": 2,
  "mode": "multiplayer"
}
```

When both players accept, the server creates a new game id, resets the board, and emits `board_updated` with:

```json
{
  "gameId": "new-generated-game-id",
  "playerId": "same-player-id",
  "status": "playing",
  "message": "Player 1 turn",
  "currentPlayer": 1,
  "mode": "multiplayer",
  "playAgainAccepted": 0
}
```

The rematch starting player is randomized, then assigned `playerNumber: 1` so yellow always starts. Clients replace the URL with `/game/{newId}`.

### `leave_game`

AI mode leave removes the server-side game and returns `game_left`.

Multiplayer leave is accepted by the backend only when:

```text
the room is waiting with one connected player
the multiplayer match has ended
```

If a player leaves after a multiplayer match ends, the remaining player receives:

```json
{
  "gameId": "generated-game-id",
  "message": "Player 2 left the room"
}
```

### `player_move`

Request:

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id",
  "column": 3,
  "accessToken": "supabase-access-token"
}
```

Response event: `board_updated`

```json
{
  "gameId": "generated-game-id",
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 2, 0, 0, 0],
    [0, 0, 0, 1, 0, 0, 0]
  ],
  "status": "playing",
  "message": "Your turn",
  "aiMove": 3,
  "difficulty": "medium"
}
```

Invalid moves emit `invalid_move`.

## REST

React gameplay uses Socket.IO. REST exposes:

```text
GET  /api/health
POST /api/new-game
GET  /api/profile/games
GET  /api/profile/games/{gameId}/moves
POST /api/profile/games/{gameId}/analysis
```

## Difficulty Values

```text
very_easy = depth 1, 3s
easy = depth 2, 3s
medium = depth 5, 3s
hard = depth 7, 4s
```

### AI Turn Scheduling

AI calculations run outside the per-game lock. While an AI move is calculating, `board_updated` includes `aiThinking: true` and the board remains available for reconnects.

The backend admits at most one active calculation per configured AI worker. A non-terminal human move is rejected with `AI is busy, try again` before changing the board when all worker slots are occupied. Results are applied only when the original `gameId` and `move_number` still match; stale results are discarded.

## Board Values

```text
0 = empty
1 = yellow / player 1
2 = red / player 2
```

In AI games, `playerNumber` is the human piece and `aiNumber` is the AI piece. These can swap when the AI starts. In multiplayer games, `playerNumber` is the local player's assigned piece.

## Status Values

```text
playing
waiting
human_win
ai_win
player1_win
player2_win
draw
invalid_move
```

Frontend status text is green only when the local player can move and displays `Your turn`. Opponent turns and AI thinking states display in red.

Profile history treats win and draw statuses as completed games: `human_win`, `ai_win`, `player1_win`, `player2_win`, and `draw`. The frontend displays `human_win` as `Player Wins` in profile history.
