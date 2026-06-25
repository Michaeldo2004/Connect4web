import { useEffect, useMemo, useState } from "react";

const API_URL = "http://localhost:5000/api";
const ROWS = 6;
const COLS = 7;

const statusLabels = {
  loading: "Connecting",
  playing: "Your turn",
  human_win: "You win",
  ai_win: "AI wins",
  draw: "Draw",
  invalid_move: "Invalid move",
  error: "Server error",
};

function emptyBoard() {
  return Array.from({ length: ROWS }, () => Array(COLS).fill(0));
}

function App() {
  const [board, setBoard] = useState(emptyBoard);
  const [status, setStatus] = useState("loading");
  const [message, setMessage] = useState("Starting game");
  const [aiMove, setAiMove] = useState(null);
  const [busy, setBusy] = useState(false);

  const gameOver = useMemo(() => {
    return ["human_win", "ai_win", "draw"].includes(status);
  }, [status]);

  async function requestNewGame() {
    setBusy(true);
    try {
      const response = await fetch(`${API_URL}/new-game`, { method: "POST" });
      const data = await response.json();
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setAiMove(null);
    } catch {
      setStatus("error");
      setMessage("Flask is not responding on localhost:5000");
    } finally {
      setBusy(false);
    }
  }

  async function playColumn(column) {
    if (busy || gameOver || status !== "playing") {
      return;
    }

    setBusy(true);
    try {
      const response = await fetch(`${API_URL}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ board, column }),
      });
      const data = await response.json();
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setAiMove(data.aiMove);
    } catch {
      setStatus("error");
      setMessage("Flask is not responding on localhost:5000");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    requestNewGame();
  }, []);

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <h1>Connect 4</h1>
          <p>{statusLabels[status] || message}</p>
        </div>
        <button type="button" onClick={requestNewGame} disabled={busy}>
          Reset
        </button>
      </section>

      <section className="game-area">
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
            row.map((cell, columnIndex) => (
              <button
                key={`${rowIndex}-${columnIndex}`}
                type="button"
                className={`cell player-${cell}`}
                onClick={() => playColumn(columnIndex)}
                disabled={busy || gameOver || status !== "playing"}
                aria-label={`Row ${rowIndex + 1}, column ${columnIndex + 1}`}
              >
                <span />
              </button>
            )),
          )}
        </div>
      </section>

      <section className="status-panel">
        <div>
          <span>Status</span>
          <strong>{message}</strong>
        </div>
        <div>
          <span>Last AI move</span>
          <strong>{aiMove === null ? "-" : aiMove + 1}</strong>
        </div>
      </section>
    </main>
  );
}

export default App;
