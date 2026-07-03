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

## Socket.IO Gameplay

Gameplay uses Socket.IO at:

```text
http://localhost:5000
```

The backend stores each active board in memory by `gameId`. AI game sessions use `localStorage`. Multiplayer sessions use per-tab `sessionStorage` so two browser tabs do not share the same `playerId`.

### Client Events

```text
create_game { difficulty }
join_game { gameId, playerId }
create_multiplayer_game
join_multiplayer_game { gameId, playerId? }
player_move { gameId, playerId, column }
reset_game { gameId, playerId, difficulty? }
play_again { gameId, playerId }
leave_game { gameId, playerId }
```

### Server Events

```text
game_created { gameId, playerId, board, status, message, difficulty, mode }
game_joined { gameId, board, status, message, difficulty, mode }
multiplayer_game_created { gameId, playerId, playerNumber, playersConnected, board, status, message, mode }
multiplayer_game_joined { gameId, playerId, playerNumber, playersConnected, board, status, message, mode }
board_updated { gameId, board, status, message, aiMove, difficulty, mode, currentPlayer?, playersConnected?, disconnectDeadline?, playAgainAccepted? }
play_again_updated { gameId, board, status, message, difficulty, mode, currentPlayer, playersConnected, playAgainAccepted }
player_left { gameId, message }
game_left { gameId }
invalid_move { gameId, board, status, message, difficulty, mode }
join_rejected { gameId, message }
```

### `create_game`

Request:

```json
{
  "difficulty": "medium"
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

AI games randomize the starting side. If AI starts, `game_created` may include an AI opening move and a board with one AI piece already placed.

### `join_game`

Request:

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id"
}
```

If the `playerId` matches the game, the server emits `game_joined`. If it does not match, the server emits `join_rejected`.

## Multiplayer Socket.IO

### `create_multiplayer_game`

Request: no payload.

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
  "currentPlayer": 1
}
```

`currentPlayer` is randomized when the second player joins.

### `join_multiplayer_game`

Player 2 joins with only the room ID:

```json
{
  "gameId": "generated-game-id"
}
```

Reconnect uses the saved player ID:

```json
{
  "gameId": "generated-game-id",
  "playerId": "existing-player-id"
}
```

Response event: `multiplayer_game_joined`

```json
{
  "gameId": "generated-game-id",
  "playerId": "player-two-id",
  "playerNumber": 2,
  "playersConnected": 2,
  "status": "playing",
  "message": "Player 2 turn",
  "difficulty": "multiplayer",
  "mode": "multiplayer",
  "currentPlayer": 2
}
```

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
  "playerId": "generated-player-id"
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

When both players accept, the server resets the board and emits `board_updated` with:

```json
{
  "status": "playing",
  "message": "Player 2 turn",
  "currentPlayer": 2,
  "playAgainAccepted": 0
}
```

The rematch starting player is randomized.

### `leave_game`

AI mode leave is handled on the frontend by clearing local session state and returning to `/`.

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
  "column": 3
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

## REST Fallback

These endpoints are still present as fallback compatibility paths, but React gameplay uses Socket.IO.

```text
POST /api/new-game
POST /api/move
```

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
1 = human
2 = AI
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
