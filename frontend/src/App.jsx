import { useCallback, useEffect, useMemo, useState } from "react";

const API_URL = "http://localhost:5000/api";
const ROWS = 6;
const COLS = 7;

const PLAYER = 1;
const AI = 2;
const DEFAULT_DIFFICULTY = "medium";
const DIFFICULTIES = [
  { key: "easy", label: "Easy", depth: 3, timeLimit: "3s" },
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

function App() {
  const [board, setBoard] = useState(emptyBoard);
  const [status, setStatus] = useState("setup");
  const [message, setMessage] = useState("Choose difficulty");
  const [busy, setBusy] = useState(false);
  const [gameStarted, setGameStarted] = useState(false);
  const [selectedDifficulty, setSelectedDifficulty] = useState(DEFAULT_DIFFICULTY);
  const [transpositionTable, setTranspositionTable] = useState({});
  const [animatedPieces, setAnimatedPieces] = useState([]);
  const [animationRun, setAnimationRun] = useState(0);
  const [playerMoves, setPlayerMoves] = useState([]);
  const [aiMoves, setAiMoves] = useState([]);
  const [winningPieces, setWinningPieces] = useState([]);

  const gameOver = useMemo(() => {
    return ["human_win", "ai_win", "draw"].includes(status);
  }, [status]);

  async function requestNewGame() {
    setBusy(true);
    setGameStarted(true);
    setTranspositionTable({});
    try {
      const response = await fetch(`${API_URL}/new-game`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ difficulty: selectedDifficulty }),
      });
      const data = await response.json();
      setAnimatedPieces([]);
      setWinningPieces([]);
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setSelectedDifficulty(data.difficulty || selectedDifficulty);
      setTranspositionTable(data.transpositionTable || {});
      setPlayerMoves([]);
      setAiMoves([]);
    } catch {
      setStatus("error");
      setMessage("Flask is not responding on localhost:5000");
    } finally {
      setBusy(false);
    }
  }

  function chooseDifficulty(difficulty) {
    if (busy) {
      return;
    }

    setSelectedDifficulty(difficulty);
    setGameStarted(false);
    setBoard(emptyBoard());
    setStatus("setup");
    setMessage("Choose difficulty");
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);
    setTranspositionTable({});
  }

  const playColumn = useCallback(async (column) => {
    if (!gameStarted || busy || gameOver || status !== "playing") {
      return;
    }

    setBusy(true);
    setMessage("AI is thinking...");
    const playerBoard = applyLocalMove(board, column, PLAYER);
    const playerPieces = findChangedPieces(board, playerBoard);
    setAnimatedPieces(playerPieces);
    setAnimationRun((currentRun) => currentRun + 1);
    setBoard(playerBoard);
    setPlayerMoves((currentMoves) => [column, ...currentMoves]);

    try {
      const response = await fetch(`${API_URL}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          board,
          column,
          difficulty: selectedDifficulty,
          transpositionTable,
        }),
      });
      const data = await response.json();
      const changedPieces = findChangedPieces(playerBoard, data.board);
      const aiColumn = findMoveColumn(playerBoard, data.board, AI);
      const winningLine = findWinningPieces(data.board);
      setAnimatedPieces(changedPieces);
      setWinningPieces(winningLine);
      setAnimationRun((currentRun) => currentRun + 1);
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setSelectedDifficulty(data.difficulty || selectedDifficulty);
      setTranspositionTable(data.transpositionTable || {});
      if (aiColumn !== null) {
        setAiMoves((currentMoves) => [aiColumn, ...currentMoves]);
      }
    } catch {
      setBoard(board);
      setAnimatedPieces([]);
      setWinningPieces([]);
      setPlayerMoves((currentMoves) => currentMoves.slice(1));
      setStatus("error");
      setMessage("Flask is not responding on localhost:5000");
    } finally {
      setBusy(false);
    }
  }, [board, busy, gameOver, gameStarted, selectedDifficulty, status, transpositionTable]);

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
                <strong>AI - {selectedDifficulty}</strong>
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
