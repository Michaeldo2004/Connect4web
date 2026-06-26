# API

Base URL:

```text
http://localhost:5000/api
```

## GET /health

Returns backend status.

Response:

```json
{
  "status": "ok"
}
```

## POST /new-game

Starts a clean game.

Response:

```json
{
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0]
  ],
  "status": "playing",
  "aiMove": null,
  "message": "New game started",
  "difficulty": "medium",
  "transpositionTable": {}
}
```

## POST /move

Makes a human move, then makes the AI move if the game is still active.

Request:

```json
{
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0]
  ],
  "column": 3,
  "difficulty": "medium",
  "transpositionTable": {}
}
```

Response:

```json
{
  "board": [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 2, 0, 0],
    [0, 0, 0, 1, 0, 0, 0]
  ],
  "status": "playing",
  "aiMove": 4,
  "message": "Your turn",
  "difficulty": "medium",
  "transpositionTable": {}
}
```

## Difficulty Values

```text
easy = depth 3, 3s
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

Invalid move responses return HTTP `400`.
