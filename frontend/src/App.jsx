import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { io } from "socket.io-client";

const SOCKET_URL = "http://localhost:5000";
const STORAGE_KEY = "connect4_game_session";
const MULTIPLAYER_STORAGE_KEY = "connect4_multiplayer_session";
const PENDING_GAME_KEY = "connect4_pending_game";
const PENDING_MULTIPLAYER_JOIN_KEY = "connect4_pending_multiplayer_join";
const SETUP_PATH = "/";
const GAME_PATH = "/game";
const ROWS = 6;
const COLS = 7;

const PLAYER = 1;
const AI = 2;
const GAME_MODE_AI = "ai";
const GAME_MODE_MULTIPLAYER = "multiplayer";
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

function saveMultiplayerSession(gameId, playerId) {
  window.sessionStorage.setItem(MULTIPLAYER_STORAGE_KEY, JSON.stringify({ gameId, playerId }));
}

function loadMultiplayerSession() {
  try {
    const storedSession = JSON.parse(window.sessionStorage.getItem(MULTIPLAYER_STORAGE_KEY));
    if (storedSession?.gameId && storedSession?.playerId) {
      return storedSession;
    }
  } catch {
    return null;
  }

  return null;
}

function clearMultiplayerSession() {
  window.sessionStorage.removeItem(MULTIPLAYER_STORAGE_KEY);
}

function savePendingGame(mode, difficulty) {
  window.sessionStorage.setItem(PENDING_GAME_KEY, JSON.stringify({ mode, difficulty }));
}

function takePendingGame() {
  try {
    const pendingGame = JSON.parse(window.sessionStorage.getItem(PENDING_GAME_KEY));
    window.sessionStorage.removeItem(PENDING_GAME_KEY);
    if (pendingGame?.mode) {
      return pendingGame;
    }
  } catch {
    window.sessionStorage.removeItem(PENDING_GAME_KEY);
  }

  return null;
}

function savePendingMultiplayerJoin(gameId) {
  window.sessionStorage.setItem(PENDING_MULTIPLAYER_JOIN_KEY, JSON.stringify({ gameId }));
}

function takePendingMultiplayerJoin() {
  try {
    const pendingJoin = JSON.parse(window.sessionStorage.getItem(PENDING_MULTIPLAYER_JOIN_KEY));
    window.sessionStorage.removeItem(PENDING_MULTIPLAYER_JOIN_KEY);
    if (pendingJoin?.gameId) {
      return pendingJoin;
    }
  } catch {
    window.sessionStorage.removeItem(PENDING_MULTIPLAYER_JOIN_KEY);
  }

  return null;
}

function formatDifficulty(difficulty) {
  return difficulty.replace("_", " ");
}

function getCurrentPath() {
  return window.location.pathname === GAME_PATH ? GAME_PATH : SETUP_PATH;
}

function App() {
  const [routePath, setRoutePath] = useState(getCurrentPath);
  const [board, setBoard] = useState(emptyBoard);
  const [status, setStatus] = useState("setup");
  const [message, setMessage] = useState(() => (getCurrentPath() === GAME_PATH ? "Loading game..." : "Choose difficulty"));
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
  const [gameMode, setGameMode] = useState(GAME_MODE_AI);
  const [selectedSetupMode, setSelectedSetupMode] = useState(GAME_MODE_AI);
  const [playerNumber, setPlayerNumber] = useState(null);
  const [playersConnected, setPlayersConnected] = useState(0);
  const [currentPlayer, setCurrentPlayer] = useState(PLAYER);
  const [joinGameId, setJoinGameId] = useState("");
  const [disconnectDeadline, setDisconnectDeadline] = useState(null);
  const [disconnectSecondsLeft, setDisconnectSecondsLeft] = useState(null);
  const [playAgainAccepted, setPlayAgainAccepted] = useState(0);
  const [playAgainRequested, setPlayAgainRequested] = useState(false);
  const [otherPlayerLeftMessage, setOtherPlayerLeftMessage] = useState("");

  const boardRef = useRef(board);
  const pendingMoveRef = useRef(null);

  const gameOver = useMemo(() => {
    return ["human_win", "ai_win", "player1_win", "player2_win", "draw"].includes(status);
  }, [status]);

  useEffect(() => {
    boardRef.current = board;
  }, [board]);

  const redirectTo = useCallback((path, replace = false) => {
    if (window.location.pathname !== path) {
      window.location[replace ? "replace" : "assign"](path);
      return;
    }
    setRoutePath(path);
  }, []);

  useEffect(() => {
    function handlePopState() {
      setRoutePath(getCurrentPath());
    }

    window.addEventListener("popstate", handlePopState);

    return () => {
      window.removeEventListener("popstate", handlePopState);
    };
  }, []);

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
    setGameMode(GAME_MODE_AI);
    setSelectedSetupMode(GAME_MODE_AI);
    setPlayerNumber(null);
    setPlayersConnected(0);
    setCurrentPlayer(PLAYER);
    setDisconnectDeadline(null);
    setDisconnectSecondsLeft(null);
    setPlayAgainAccepted(0);
    setPlayAgainRequested(false);
    setOtherPlayerLeftMessage("");
    setBusy(false);
  }, []);

  const applyServerBoard = useCallback(async (data) => {
    const pendingMove = pendingMoveRef.current;
    if (pendingMove && data.mode !== GAME_MODE_MULTIPLAYER) {
      const elapsed = Date.now() - pendingMove.startedAt;
      if (elapsed < MIN_AI_RESPONSE_MS) {
        await wait(MIN_AI_RESPONSE_MS - elapsed);
      }
    }

    const previousBoard = pendingMove?.board || boardRef.current;
    const changedPieces = findChangedPieces(previousBoard, data.board);
    const playerColumn = findMoveColumn(previousBoard, data.board, PLAYER);
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
    setGameMode(data.mode || GAME_MODE_AI);
    setCurrentPlayer(data.currentPlayer || PLAYER);
    setPlayersConnected(data.playersConnected || 0);
    setDisconnectDeadline(data.disconnectDeadline || null);
    setPlayAgainAccepted(data.playAgainAccepted || 0);
    if (data.status === "playing") {
      setPlayAgainRequested(false);
    }
    setGameStarted(true);
    setBusy(false);

    if (data.mode === GAME_MODE_MULTIPLAYER && playerColumn !== null) {
      setPlayerMoves((currentMoves) => [playerColumn, ...currentMoves]);
    }

    if (aiColumn !== null) {
      setAiMoves((currentMoves) => [aiColumn, ...currentMoves]);
    }
  }, []);

  useEffect(() => {
    const nextSocket = io(SOCKET_URL, { transports: ["websocket"] });
    setSocketClient(nextSocket);

    function handleConnect() {
      const pendingGame = takePendingGame();
      if (pendingGame) {
        clearSession();
        clearMultiplayerSession();
        if (pendingGame.mode === GAME_MODE_MULTIPLAYER) {
          nextSocket.emit("create_multiplayer_game");
          return;
        }

        nextSocket.emit("create_game", { difficulty: pendingGame.difficulty || DEFAULT_DIFFICULTY });
        return;
      }

      const pendingMultiplayerJoin = takePendingMultiplayerJoin();
      if (pendingMultiplayerJoin) {
        clearSession();
        clearMultiplayerSession();
        nextSocket.emit("join_multiplayer_game", { gameId: pendingMultiplayerJoin.gameId });
        return;
      }

      const multiplayerSession = loadMultiplayerSession();
      if (multiplayerSession) {
        nextSocket.emit("join_multiplayer_game", multiplayerSession);
        return;
      }

      const storedSession = loadSession();
      if (storedSession) {
        nextSocket.emit("join_game", storedSession);
      }
    }

    function handleGameCreated(data) {
      saveSession(data.gameId, data.playerId);
      setGameId(data.gameId);
      setPlayerId(data.playerId);
      setGameMode(data.mode || GAME_MODE_AI);
      setPlayerNumber(null);
      setPlayersConnected(data.playersConnected || 0);
      setCurrentPlayer(data.currentPlayer || PLAYER);
      setDisconnectDeadline(data.disconnectDeadline || null);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
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
      redirectTo(GAME_PATH);
    }

    function handleGameJoined(data) {
      const storedSession = loadSession();
      setGameId(data.gameId);
      setPlayerId(storedSession?.playerId || null);
      setGameMode(data.mode || GAME_MODE_AI);
      setPlayerNumber(data.playerNumber || null);
      setPlayersConnected(data.playersConnected || 0);
      setCurrentPlayer(data.currentPlayer || PLAYER);
      setDisconnectDeadline(data.disconnectDeadline || null);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
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
      redirectTo(GAME_PATH, true);
    }

    function handleMultiplayerGameStarted(data) {
      clearSession();
      saveMultiplayerSession(data.gameId, data.playerId);
      setGameId(data.gameId);
      setPlayerId(data.playerId);
      setPlayerNumber(data.playerNumber);
      setPlayersConnected(data.playersConnected || 0);
      setCurrentPlayer(data.currentPlayer || PLAYER);
      setDisconnectDeadline(data.disconnectDeadline || null);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
      setGameMode(GAME_MODE_MULTIPLAYER);
      setAnimatedPieces([]);
      setWinningPieces([]);
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setPlayerMoves([]);
      setAiMoves([]);
      setGameStarted(true);
      setBusy(false);
      redirectTo(GAME_PATH);
      console.log(`Player ${data.playerNumber} connected`);
    }

    function handleJoinRejected(data) {
      clearSession();
      clearMultiplayerSession();
      clearLocalGame();
      setMessage(data?.message || "Game not found");
      redirectTo(SETUP_PATH, true);
    }

    async function handleBoardUpdated(data) {
      await applyServerBoard(data);
    }

    function handlePlayAgainUpdated(data) {
      setMessage(data.message);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
      setPlayersConnected(data.playersConnected || 0);
      setDisconnectDeadline(data.disconnectDeadline || null);
    }

    function handlePlayerLeft(data) {
      clearSession();
      clearMultiplayerSession();
      setOtherPlayerLeftMessage(data?.message || "The other player left the room");
    }

    function handleGameLeft() {
      clearSession();
      clearMultiplayerSession();
      redirectTo(SETUP_PATH, true);
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
    nextSocket.on("multiplayer_game_created", handleMultiplayerGameStarted);
    nextSocket.on("multiplayer_game_joined", handleMultiplayerGameStarted);
    nextSocket.on("join_rejected", handleJoinRejected);
    nextSocket.on("board_updated", handleBoardUpdated);
    nextSocket.on("play_again_updated", handlePlayAgainUpdated);
    nextSocket.on("player_left", handlePlayerLeft);
    nextSocket.on("game_left", handleGameLeft);
    nextSocket.on("invalid_move", handleInvalidMove);
    nextSocket.on("connect_error", () => {
      setStatus("error");
      setMessage("Flask SocketIO is not responding on localhost:5000");
      setBusy(false);
    });

    return () => {
      nextSocket.disconnect();
    };
  }, [applyServerBoard, clearLocalGame, redirectTo]);

  function startMultiplayerGame() {
    if (!socketClient?.connected) {
      setStatus("error");
      setMessage("Flask SocketIO is not responding on localhost:5000");
      return;
    }

    clearSession();
    clearMultiplayerSession();
    pendingMoveRef.current = null;
    setGameMode(GAME_MODE_MULTIPLAYER);
    setPlayerNumber(null);
    setCurrentPlayer(PLAYER);
    setBoard(emptyBoard());
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);
    setGameId(null);
    setPlayerId(null);
    setStatus("waiting");
    setMessage("Creating multiplayer room...");
    setGameStarted(false);
    setBusy(true);
    socketClient.emit("create_multiplayer_game");
  }

  function joinMultiplayerGame() {
    const requestedGameId = joinGameId.trim();
    if (!requestedGameId || !socketClient?.connected) {
      setStatus("error");
      setMessage(!requestedGameId ? "Enter a room ID" : "Flask SocketIO is not responding on localhost:5000");
      return;
    }

    clearSession();
    clearMultiplayerSession();
    savePendingMultiplayerJoin(requestedGameId);
    window.location.assign(GAME_PATH);
  }

  function requestNewGame() {
    if (gameMode === GAME_MODE_MULTIPLAYER) {
      if (gameId && playerId) {
        setBusy(true);
        setAnimatedPieces([]);
        setWinningPieces([]);
        setPlayerMoves([]);
        setAiMoves([]);
        socketClient.emit("reset_game", { gameId, playerId });
      }
      return;
    }

    if (!gameStarted && selectedSetupMode === GAME_MODE_MULTIPLAYER) {
      savePendingGame(GAME_MODE_MULTIPLAYER, selectedDifficulty);
      window.location.assign(GAME_PATH);
      return;
    }

    if (!gameStarted) {
      savePendingGame(GAME_MODE_AI, selectedDifficulty);
      window.location.assign(GAME_PATH);
      return;
    }

    if (!socketClient?.connected) {
      setStatus("error");
      setMessage("Flask SocketIO is not responding on localhost:5000");
      return;
    }

    if (gameStarted && gameId && playerId) {
      setBusy(true);
      setAnimatedPieces([]);
      setWinningPieces([]);
      setPlayerMoves([]);
      setAiMoves([]);
      socketClient.emit("reset_game", { gameId, playerId, difficulty: selectedDifficulty });
      return;
    }

    setBusy(true);
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);
    socketClient.emit("create_game", { difficulty: selectedDifficulty });
  }

  function chooseDifficulty(difficulty) {
    if (busy) {
      return;
    }

    clearSession();
    clearMultiplayerSession();
    clearLocalGame();
    setSelectedDifficulty(difficulty);
    setSelectedSetupMode(GAME_MODE_AI);
  }

  function chooseMultiplayerMode() {
    if (busy) {
      return;
    }

    clearSession();
    clearMultiplayerSession();
    clearLocalGame();
    setSelectedSetupMode(GAME_MODE_MULTIPLAYER);
  }

  const playColumn = useCallback((column) => {
    if (!gameStarted || busy || gameOver || status !== "playing") {
      return;
    }

    if (gameMode === GAME_MODE_MULTIPLAYER) {
      if (!socketClient?.connected || !gameId || !playerId || playerNumber !== currentPlayer) {
        return;
      }

      setBusy(true);
      setMessage("Waiting for server...");
      socketClient.emit("player_move", { gameId, playerId, column });
      return;
    }

    if (!socketClient?.connected || !gameId || !playerId) {
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
  }, [board, busy, currentPlayer, gameId, gameMode, gameOver, gameStarted, playerId, playerNumber, socketClient, status]);

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

  useEffect(() => {
    if (!disconnectDeadline) {
      setDisconnectSecondsLeft(null);
      return undefined;
    }

    function updateCountdown() {
      setDisconnectSecondsLeft(Math.max(0, Math.ceil((disconnectDeadline - Date.now()) / 1000)));
    }

    updateCountdown();
    const timerId = window.setInterval(updateCountdown, 250);

    return () => {
      window.clearInterval(timerId);
    };
  }, [disconnectDeadline]);

  function returnToMainMenu() {
    clearSession();
    clearMultiplayerSession();
    window.location.replace(SETUP_PATH);
  }

  function leaveGame() {
    if (gameMode !== GAME_MODE_MULTIPLAYER) {
      returnToMainMenu();
      return;
    }

    if (!socketClient?.connected || !gameId || !playerId) {
      returnToMainMenu();
      return;
    }

    setBusy(true);
    socketClient.emit("leave_game", { gameId, playerId });
  }

  function requestPlayAgain() {
    if (!socketClient?.connected || !gameId || !playerId || gameMode !== GAME_MODE_MULTIPLAYER || !gameOver) {
      return;
    }

    setPlayAgainRequested(true);
    socketClient.emit("play_again", { gameId, playerId });
  }

  const canDropPiece =
    gameStarted &&
    !busy &&
    !gameOver &&
    status === "playing" &&
    (gameMode !== GAME_MODE_MULTIPLAYER || (playerNumber === currentPlayer && playersConnected === 2));
  const showingSetup = routePath !== GAME_PATH;
  let displayMessage = message;
  if (!gameOver && canDropPiece) {
    displayMessage = "Your turn";
  } else if (!gameOver && gameStarted && gameMode === GAME_MODE_MULTIPLAYER && playersConnected === 2) {
    displayMessage = "Other player's turn";
  } else if (!gameOver && gameStarted && gameMode === GAME_MODE_AI && busy) {
    displayMessage = "AI is thinking";
  }

  if (disconnectDeadline && disconnectSecondsLeft !== null && playersConnected < 2 && !gameOver) {
    displayMessage = `${message} Waiting ${disconnectSecondsLeft} seconds.`;
  }

  const statusClassName = !gameOver && canDropPiece ? "turn-status your-turn" : "turn-status waiting-turn";
  const canLeaveGame =
    gameStarted &&
    !showingSetup &&
    (gameMode !== GAME_MODE_MULTIPLAYER || (status === "waiting" && playersConnected === 1) || gameOver);
  const canRequestPlayAgain = gameStarted && gameMode === GAME_MODE_MULTIPLAYER && gameOver;
  const playerMoveLabel = gameMode === GAME_MODE_MULTIPLAYER ? "Your moves" : "Your moves";
  const opponentMoveLabel = gameMode === GAME_MODE_MULTIPLAYER ? "Other player's moves" : "AI moves";
  const setupView = (
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
      <button
        className={`vs-player-button${selectedSetupMode === GAME_MODE_MULTIPLAYER ? " selected" : ""}`}
        type="button"
        onClick={chooseMultiplayerMode}
        disabled={busy}
      >
        Vs Player
      </button>
      <div className="multiplayer-join">
        <input
          type="text"
          value={joinGameId}
          onChange={(event) => setJoinGameId(event.target.value)}
          placeholder="Room ID"
          aria-label="Room ID"
          disabled={busy}
        />
        <button type="button" onClick={joinMultiplayerGame} disabled={busy}>
          Join
        </button>
      </div>
    </section>
  );
  const loadingView = (
    <section className="difficulty-panel">
      <strong>{message}</strong>
    </section>
  );
  const gameView = (
    <>
      <section className="status-panel">
        <div>
          <span>Status</span>
          <strong className={statusClassName}>{displayMessage}</strong>
        </div>
        <div>
          <span>Game Mode</span>
          <strong>
            {gameMode === GAME_MODE_MULTIPLAYER
              ? `Vs Player - Player ${playerNumber || "-"}`
              : `AI - ${formatDifficulty(selectedDifficulty)}`}
          </strong>
        </div>
        <div>
          <span>Room</span>
          <strong>{gameId || "-"}</strong>
        </div>
      </section>

      <section className="play-layout">
        <aside className="move-column">
          <span>{playerMoveLabel}</span>
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
                disabled={!canDropPiece}
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
                    disabled={!canDropPiece}
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
          <span>{opponentMoveLabel}</span>
          {aiMoves.length === 0 ? (
            <strong>-</strong>
          ) : (
            aiMoves.map((move, index) => <strong key={`ai-${index}`}>{move + 1}</strong>)
          )}
        </aside>
      </section>
    </>
  );

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <h1>Connect 4</h1>
        </div>
        <div className="topbar-actions">
          {gameStarted && gameMode !== GAME_MODE_MULTIPLAYER ? (
            <button type="button" onClick={requestNewGame} disabled={busy}>
              Reset
            </button>
          ) : null}
          {canRequestPlayAgain ? (
            <button type="button" onClick={requestPlayAgain} disabled={busy || playAgainRequested}>
              Play again {playAgainAccepted}/2
            </button>
          ) : null}
          {canLeaveGame ? (
            <button type="button" onClick={leaveGame} disabled={busy}>
              Leave
            </button>
          ) : null}
        </div>
      </section>

      <section className="game-area">
        {showingSetup ? setupView : gameStarted ? gameView : loadingView}
      </section>
      {otherPlayerLeftMessage ? (
        <div className="modal-backdrop" role="presentation">
          <section className="leave-modal" role="dialog" aria-modal="true" aria-labelledby="leave-modal-title">
            <h2 id="leave-modal-title">Player left</h2>
            <p>{otherPlayerLeftMessage}</p>
            <button type="button" onClick={returnToMainMenu}>
              Main menu
            </button>
          </section>
        </div>
      ) : null}
    </main>
  );
}

export default App;
