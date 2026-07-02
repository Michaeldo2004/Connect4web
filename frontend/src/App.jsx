import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { io } from "socket.io-client";

const SOCKET_URL = "http://localhost:5000";
const STORAGE_KEY = "connect4_game_session";
const ROWS = 6;
const COLS = 7;

const PLAYER = 1;
const AI = 2;
const DEFAULT_DIFFICULTY = "medium";
const MIN_AI_RESPONSE_MS = 2000;
const DIFFICULTIES = [
  { key: "very_easy", label: "Very Easy", depth: 1, timeLimit: "3s" },
  { key: "easy", label: "Easy", depth: 2, timeLimit: "3s" },
  { key: "medium", label: "Medium", depth: 5, timeLimit: "3s" },
  { key: "hard", label: "Hard", depth: 7, timeLimit: "5s" },
];

function emptyBoard() {
  return Array.from({ length: ROWS }, () => Array(COLS).fill(0));
}

function findChangedPieces(previousBoard, nextBoard) {
  const changedPieces = [];

  for (let row = 0; row < ROWS; row += 1) {
    for (let column = 0; column < COLS; column += 1) {
      if (previousBoard[row][column] !== nextBoard[row][column] && nextBoard[row][column] !== 0) {
        changedPieces.push(`${row}-${column}`);
      }
    }
  }

  return changedPieces;
}

function findMoveColumn(previousBoard, nextBoard, piece) {
  for (let row = 0; row < ROWS; row += 1) {
    for (let column = 0; column < COLS; column += 1) {
      if (previousBoard[row][column] !== nextBoard[row][column] && nextBoard[row][column] === piece) {
        return column;
      }
    }
  }

  return null;
}

function findWinningPieces(currentBoard) {
  const directions = [
    [0, 1],
    [1, 0],
    [1, 1],
    [1, -1],
  ];

  for (let row = 0; row < ROWS; row += 1) {
    for (let column = 0; column < COLS; column += 1) {
      const piece = currentBoard[row][column];
      if (piece === 0) {
        continue;
      }

      for (const [rowStep, columnStep] of directions) {
        const pieces = [];

        for (let offset = 0; offset < 4; offset += 1) {
          const nextRow = row + rowStep * offset;
          const nextColumn = column + columnStep * offset;

          if (
            nextRow < 0 ||
            nextRow >= ROWS ||
            nextColumn < 0 ||
            nextColumn >= COLS ||
            currentBoard[nextRow][nextColumn] !== piece
          ) {
            break;
          }

          pieces.push(`${nextRow}-${nextColumn}`);
        }

        if (pieces.length === 4) {
          return pieces;
        }
      }
    }
  }

  return [];
}

function applyLocalMove(currentBoard, column, piece) {
  const nextBoard = currentBoard.map((row) => [...row]);

  for (let row = ROWS - 1; row >= 0; row -= 1) {
    if (nextBoard[row][column] === 0) {
      nextBoard[row][column] = piece;
      return nextBoard;
    }
  }

  return currentBoard;
}

function wait(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function saveSession(gameId, playerId) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ gameId, playerId }));
}

function loadSession() {
  try {
    const storedSession = JSON.parse(window.localStorage.getItem(STORAGE_KEY));
    if (storedSession?.gameId && storedSession?.playerId) {
      return storedSession;
    }
  } catch {
    return null;
  }

  return null;
}

function clearSession() {
  window.localStorage.removeItem(STORAGE_KEY);
}

function formatDifficulty(difficulty) {
  return difficulty.replace("_", " ");
}

function App() {
  const [board, setBoard] = useState(emptyBoard);
  const [status, setStatus] = useState("setup");
  const [message, setMessage] = useState("Choose difficulty");
  const [busy, setBusy] = useState(false);
  const [gameStarted, setGameStarted] = useState(false);
  const [selectedDifficulty, setSelectedDifficulty] = useState(DEFAULT_DIFFICULTY);
  const [animatedPieces, setAnimatedPieces] = useState([]);
  const [animationRun, setAnimationRun] = useState(0);
  const [playerMoves, setPlayerMoves] = useState([]);
  const [aiMoves, setAiMoves] = useState([]);
  const [winningPieces, setWinningPieces] = useState([]);
  const [socketClient, setSocketClient] = useState(null);
  const [gameId, setGameId] = useState(null);
  const [playerId, setPlayerId] = useState(null);

  const boardRef = useRef(board);
  const pendingMoveRef = useRef(null);

  const gameOver = useMemo(() => {
    return ["human_win", "ai_win", "draw"].includes(status);
  }, [status]);

  useEffect(() => {
    boardRef.current = board;
  }, [board]);

  const clearLocalGame = useCallback(() => {
    pendingMoveRef.current = null;
    setAnimatedPieces([]);
    setWinningPieces([]);
    setBoard(emptyBoard());
    setStatus("setup");
    setMessage("Choose difficulty");
    setPlayerMoves([]);
    setAiMoves([]);
    setGameId(null);
    setPlayerId(null);
    setBusy(false);
  }, []);

  const applyServerBoard = useCallback(async (data) => {
    const pendingMove = pendingMoveRef.current;
    if (pendingMove) {
      const elapsed = Date.now() - pendingMove.startedAt;
      if (elapsed < MIN_AI_RESPONSE_MS) {
        await wait(MIN_AI_RESPONSE_MS - elapsed);
      }
    }

    const previousBoard = pendingMove?.board || boardRef.current;
    const changedPieces = findChangedPieces(previousBoard, data.board);
    const aiColumn = findMoveColumn(previousBoard, data.board, AI);
    const winningLine = findWinningPieces(data.board);

    pendingMoveRef.current = null;
    setAnimatedPieces(changedPieces);
    setWinningPieces(winningLine);
    setAnimationRun((currentRun) => currentRun + 1);
    setBoard(data.board);
    setStatus(data.status);
    setMessage(data.message);
    setSelectedDifficulty(data.difficulty || DEFAULT_DIFFICULTY);
    setGameStarted(true);
    setBusy(false);

    if (aiColumn !== null) {
      setAiMoves((currentMoves) => [aiColumn, ...currentMoves]);
    }
  }, []);

  useEffect(() => {
    const nextSocket = io(SOCKET_URL, { transports: ["websocket"] });
    setSocketClient(nextSocket);

    function handleConnect() {
      const storedSession = loadSession();
      if (storedSession) {
        nextSocket.emit("join_game", storedSession);
      }
    }

    function handleGameCreated(data) {
      saveSession(data.gameId, data.playerId);
      setGameId(data.gameId);
      setPlayerId(data.playerId);
      setAnimatedPieces([]);
      setWinningPieces([]);
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setSelectedDifficulty(data.difficulty || DEFAULT_DIFFICULTY);
      setPlayerMoves([]);
      setAiMoves([]);
      setGameStarted(true);
      setBusy(false);
    }

    function handleGameJoined(data) {
      const storedSession = loadSession();
      setGameId(data.gameId);
      setPlayerId(storedSession?.playerId || null);
      setAnimatedPieces([]);
      setWinningPieces(findWinningPieces(data.board));
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setSelectedDifficulty(data.difficulty || DEFAULT_DIFFICULTY);
      setPlayerMoves([]);
      setAiMoves([]);
      setGameStarted(true);
      setBusy(false);
    }

    function handleJoinRejected(data) {
      clearSession();
      clearLocalGame();
      setMessage(data?.message || "Game not found");
    }

    async function handleBoardUpdated(data) {
      await applyServerBoard(data);
    }

    function handleInvalidMove(data) {
      const hadPendingMove = Boolean(pendingMoveRef.current);
      pendingMoveRef.current = null;
      setAnimatedPieces([]);
      setWinningPieces(findWinningPieces(data.board));
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setBusy(false);
      if (hadPendingMove) {
        setPlayerMoves((currentMoves) => currentMoves.slice(1));
      }
    }

    nextSocket.on("connect", handleConnect);
    nextSocket.on("game_created", handleGameCreated);
    nextSocket.on("game_joined", handleGameJoined);
    nextSocket.on("join_rejected", handleJoinRejected);
    nextSocket.on("board_updated", handleBoardUpdated);
    nextSocket.on("invalid_move", handleInvalidMove);
    nextSocket.on("connect_error", () => {
      setStatus("error");
      setMessage("Flask SocketIO is not responding on localhost:5000");
      setBusy(false);
    });

    return () => {
      nextSocket.disconnect();
    };
  }, [applyServerBoard, clearLocalGame]);

  function requestNewGame() {
    if (!socketClient?.connected) {
      setStatus("error");
      setMessage("Flask SocketIO is not responding on localhost:5000");
      return;
    }

    setBusy(true);
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);

    if (gameStarted && gameId && playerId) {
      socketClient.emit("reset_game", { gameId, playerId, difficulty: selectedDifficulty });
      return;
    }

    socketClient.emit("create_game", { difficulty: selectedDifficulty });
  }

  function chooseDifficulty(difficulty) {
    if (busy) {
      return;
    }

    clearSession();
    clearLocalGame();
    setSelectedDifficulty(difficulty);
  }

  const playColumn = useCallback((column) => {
    if (!socketClient?.connected || !gameId || !playerId || !gameStarted || busy || gameOver || status !== "playing") {
      return;
    }

    setBusy(true);
    setMessage("AI is thinking...");
    const playerBoard = applyLocalMove(board, column, PLAYER);
    const playerPieces = findChangedPieces(board, playerBoard);
    pendingMoveRef.current = {
      board: playerBoard,
      startedAt: Date.now(),
    };
    setAnimatedPieces(playerPieces);
    setAnimationRun((currentRun) => currentRun + 1);
    setBoard(playerBoard);
    setPlayerMoves((currentMoves) => [column, ...currentMoves]);
    socketClient.emit("player_move", { gameId, playerId, column });
  }, [board, busy, gameId, gameOver, gameStarted, playerId, socketClient, status]);

  useEffect(() => {
    function handleKeyDown(event) {
      const numberRowMatch = event.code.match(/^Digit([1-7])$/);
      const numpadMatch = event.code.match(/^Numpad([1-7])$/);
      const columnKey = numberRowMatch?.[1] || numpadMatch?.[1];

      if (!columnKey || event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }

      event.preventDefault();
      playColumn(Number(columnKey) - 1);
    }

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [playColumn]);

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <h1>Connect 4</h1>
        </div>
        <button type="button" onClick={requestNewGame} disabled={busy || !gameStarted}>
          Reset
        </button>
      </section>

      <section className="game-area">
        {!gameStarted ? (
          <section className="difficulty-panel">
            <div className="difficulty-tabs" role="tablist" aria-label="Difficulty">
              {DIFFICULTIES.map((difficulty) => (
                <button
                  key={difficulty.key}
                  type="button"
                  role="tab"
                  aria-selected={selectedDifficulty === difficulty.key}
                  className={`difficulty-tab difficulty-${difficulty.key}${selectedDifficulty === difficulty.key ? " selected" : ""}`}
                  onClick={() => chooseDifficulty(difficulty.key)}
                  disabled={busy}
                >
                  <span>{difficulty.label}</span>
                </button>
              ))}
            </div>
            <button className="play-button" type="button" onClick={requestNewGame} disabled={busy}>
              Play
            </button>
          </section>
        ) : (
          <>
            <section className="status-panel">
              <div>
                <span>Status</span>
                <strong>{message}</strong>
              </div>
              <div>
                <span>Game Mode</span>
                <strong>AI - {formatDifficulty(selectedDifficulty)}</strong>
              </div>
            </section>

            <section className="play-layout">
              <aside className="move-column">
                <span>Player</span>
                {playerMoves.length === 0 ? (
                  <strong>-</strong>
                ) : (
                  playerMoves.map((move, index) => <strong key={`player-${index}`}>{move + 1}</strong>)
                )}
              </aside>

              <div className="board-area">
                <div className="column-controls" aria-label="Choose a column">
                  {Array.from({ length: COLS }, (_, column) => (
                    <button
                      key={column}
                      type="button"
                      onClick={() => playColumn(column)}
                      disabled={busy || gameOver || status !== "playing"}
                      aria-label={`Drop in column ${column + 1}`}
                    >
                      {column + 1}
                    </button>
                  ))}
                </div>

                <div className="board" role="grid" aria-label="Connect 4 board">
                  {board.flatMap((row, rowIndex) =>
                    row.map((cell, columnIndex) => {
                      const pieceKey = `${rowIndex}-${columnIndex}`;
                      const isDropping = animatedPieces.includes(pieceKey);
                      const isWinning = winningPieces.includes(pieceKey);

                      return (
                        <button
                          key={pieceKey}
                          type="button"
                          className={`cell player-${cell}${isDropping ? " dropping" : ""}${isWinning ? " winning" : ""}`}
                          style={isDropping ? { "--drop-start": `-${(rowIndex + 1) * 115}%` } : undefined}
                          onClick={() => playColumn(columnIndex)}
                          disabled={busy || gameOver || status !== "playing"}
                          aria-label={`Row ${rowIndex + 1}, column ${columnIndex + 1}`}
                        >
                          <span key={isDropping ? `drop-${animationRun}` : "piece"} />
                        </button>
                      );
                    }),
                  )}
                </div>
              </div>

              <aside className="move-column">
                <span>AI</span>
                {aiMoves.length === 0 ? (
                  <strong>-</strong>
                ) : (
                  aiMoves.map((move, index) => <strong key={`ai-${index}`}>{move + 1}</strong>)
                )}
              </aside>
            </section>
          </>
        )}
      </section>
    </main>
  );
}

export default App;
