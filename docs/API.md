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
create_multiplayer_game { ownerName?, accessToken }
list_public_games { accessToken }
set_room_public { gameId, playerId, public, accessToken }
join_multiplayer_game { gameId, playerId?, publicJoin?, accessToken }
player_move { gameId, playerId, column, accessToken }
reset_game { gameId, playerId, difficulty?, accessToken }
play_again { gameId, playerId, accessToken }
leave_game { gameId, playerId, accessToken }
```

### Server Events

```text
game_created { gameId, playerId, board, status, message, difficulty, mode }
game_joined { gameId, board, status, message, difficulty, mode }
multiplayer_game_created { gameId, playerId, playerNumber, playersConnected, board, status, message, mode }
multiplayer_game_joined { gameId, playerId, playerNumber, playersConnected, board, status, message, mode }
board_updated { gameId, playerId?, board, status, message, aiMove, difficulty, mode, currentPlayer?, playersConnected?, disconnectDeadline?, playAgainAccepted?, publicRoom? }
play_again_updated { gameId, board, status, message, difficulty, mode, currentPlayer, playersConnected, playAgainAccepted }
public_games { games: [{ gameId, ownerName }] }
player_left { gameId, message }
game_left { gameId }
invalid_move { gameId, board, status, message, difficulty, mode }
join_rejected { gameId, message }
create_rejected { message }
```

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
  "currentPlayer": 1
}
```

AI games randomize the starting side. If AI starts, `game_created` returns the empty board with `currentPlayer: 2` and `message: "AI is thinking"`, then `board_updated` carries the recorded AI opening move.

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
  "accessToken": "supabase-access-token"
}
```

Response event: `multiplayer_game_created`

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
  "difficulty": "multiplayer",
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
      "ownerName": "Player"
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

React gameplay uses Socket.IO. REST exposes `GET /api/health`, `GET /api/profile/games`, and `POST /api/new-game`.

## Difficulty Values

```text
very_easy = depth 1, 3s
easy = depth 2, 3s
medium = depth 5, 3s
hard = depth 7, 5s
```

## Board Values

```text
0 = empty
1 = yellow / human / player 1
2 = red / AI / player 2
```

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
