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

The backend stores each active board in memory by `gameId`. The browser stores the returned `playerId` with the `gameId` in `localStorage` so a refresh can rejoin the same board while the backend is still running.

### Client Events

```text
create_game { difficulty }
join_game { gameId, playerId }
player_move { gameId, playerId, column }
reset_game { gameId, playerId, difficulty }
```

### Server Events

```text
game_created { gameId, playerId, board, status, message, difficulty }
game_joined { gameId, board, status, message, difficulty }
board_updated { gameId, board, status, message, aiMove, difficulty }
invalid_move { gameId, board, status, message, difficulty }
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
  "message": "New game started",
  "aiMove": null,
  "difficulty": "medium"
}
```

### `join_game`

Request:

```json
{
  "gameId": "generated-game-id",
  "playerId": "generated-player-id"
}
```

If the `playerId` matches the game, the server emits `game_joined`. If it does not match, the server emits `join_rejected`.

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
human_win
ai_win
draw
invalid_move
```
