import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "@supabase/supabase-js";
import { io } from "socket.io-client";

const STORAGE_KEY = "connect4_game_session";
const MULTIPLAYER_STORAGE_KEY = "connect4_multiplayer_session";
const PENDING_GAME_KEY = "connect4_pending_game";
const PENDING_MULTIPLAYER_JOIN_KEY = "connect4_pending_multiplayer_join";
const AI_WAITING_KEY = "connect4_ai_waiting";
const THEME_KEY = "connect4_theme";
const SOCKET_URL = getEnvString("VITE_BACKEND_URL", "http://localhost:5000").replace(/\/+$/, "");
const SOCKET_TRANSPORTS = getSocketTransports();
const SETUP_PATH = getEnvRoute("VITE_SETUP_PATH", "/");
const GAME_PATH = getEnvRoute("VITE_GAME_PATH", "/game");
const JOIN_PATH = getEnvRoute("VITE_JOIN_PATH", "/join");
const LOGIN_PATH = getEnvRoute("VITE_LOGIN_PATH", "/login");
const SIGNUP_PATH = getEnvRoute("VITE_SIGNUP_PATH", "/signup");
const PROFILE_PATH = getEnvRoute("VITE_PROFILE_PATH", "/profiles");
const AI_WAITING_PATH = getEnvRoute("VITE_AI_WAITING_PATH", "/ai/waiting");
const GAME_REVIEW_PATH = `${GAME_PATH}/review`;
const NOT_FOUND_PATH = "/404";
const TOS_PATH = getEnvRoute("VITE_TOS_PATH", "/tos");
const PRIVACY_POLICY_PATH = getEnvRoute("VITE_PRIVACY_POLICY_PATH", "/privacypolicy");
const APP_PATHS = new Set([SETUP_PATH, GAME_PATH, JOIN_PATH, LOGIN_PATH, SIGNUP_PATH, PROFILE_PATH, AI_WAITING_PATH, GAME_REVIEW_PATH, NOT_FOUND_PATH, TOS_PATH, PRIVACY_POLICY_PATH]);
const SOCKET_PATHS = new Set([SETUP_PATH, GAME_PATH, JOIN_PATH, AI_WAITING_PATH]);
const ROWS = 6;
const COLS = 7;
const MOVE_ANALYSIS_POLL_MS = 2000;
const MOVE_ANALYSIS_ACTIVE_STATUSES = new Set(["queued", "running", "processing"]);
const MOVE_ANALYSIS_COMPLETE_STATUSES = new Set(["complete", "evaluated"]);
const MULTIPLAYER_CREATE_TIMEOUT_MS = 10000;

const PLAYER = 1;
const AI = 2;
const GAME_MODE_AI = "ai";
const GAME_MODE_MULTIPLAYER = "multiplayer";
const DEFAULT_DIFFICULTY = "medium";
const MIN_AI_RESPONSE_MS = 2000;
const DIFFICULTIES = [
  { key: "very_easy", label: "Very Easy", hint: "Learn the board", depth: 1, timeLimit: "3s" },
  { key: "easy", label: "Easy", hint: "A relaxed match", depth: 2, timeLimit: "3s" },
  { key: "medium", label: "Medium", hint: "A fair challenge", depth: 5, timeLimit: "3s" },
  { key: "hard", label: "Hard", hint: "Plan every move", depth: 7, timeLimit: "4s" },
];
const USERNAME_MAX_LENGTH = 32;
const EMAIL_MAX_LENGTH = 254;
const PASSWORD_MAX_LENGTH = 128;
const SUPABASE_URL = getEnvString("VITE_SUPABASE_URL", "");
const SUPABASE_PUBLISHABLE_KEY = getEnvString("VITE_SUPABASE_PUBLISHABLE_KEY", "");
const supabaseClient = createAuthClient();

function getEnvString(name, fallback) {
  const value = import.meta.env[name];
  if (typeof value !== "string" || value.trim() === "") {
    return fallback;
  }

  return value.trim();
}

function getEnvRoute(name, fallback) {
  const value = getEnvString(name, fallback);
  const path = value.startsWith("/") ? value : `/${value}`;
  if (path === "/") {
    return path;
  }

  return path.replace(/\/+$/, "");
}

function getSocketTransports() {
  const configured = getEnvString("VITE_SOCKET_TRANSPORTS", "polling")
    .split(",")
    .map((transport) => transport.trim().toLowerCase())
    .filter((transport, index, transports) => (
      ["polling", "websocket"].includes(transport) && transports.indexOf(transport) === index
    ));
  return configured.length > 0 ? configured : ["polling"];
}

async function readJsonResponse(response) {
  const body = await response.text();
  if (!body) {
    return {};
  }

  try {
    return JSON.parse(body);
  } catch {
    return {};
  }
}

function createAuthClient() {
  if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
    return null;
  }

  return createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);
}

function getInitialTheme() {
  const storedTheme = window.localStorage.getItem(THEME_KEY);
  if (storedTheme === "dark" || storedTheme === "light") {
    return storedTheme;
  }

  return window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light";
}

function SkeletonBlock({ className = "" }) {
  return <span className={`skeleton-block ${className}`.trim()} aria-hidden="true" />;
}

function GameLoadingSkeleton({ message }) {
  return (
    <section className="difficulty-panel loading-panel" aria-busy="true">
      <SkeletonBlock className="skeleton-title" />
      <SkeletonBlock className="skeleton-line skeleton-line-wide" />
      <div className="skeleton-board" aria-hidden="true">
        {Array.from({ length: ROWS * COLS }, (_, index) => (
          <span key={index} />
        ))}
      </div>
      <span className="sr-only">{message}</span>
    </section>
  );
}

function ProfileLoadingSkeleton() {
  return (
    <section className="profile-skeleton" aria-label="Loading completed games" aria-busy="true">
      {Array.from({ length: 3 }, (_, index) => (
        <article className="profile-game-row skeleton-row" key={index}>
          <SkeletonBlock />
          <SkeletonBlock />
          <SkeletonBlock />
          <SkeletonBlock className="skeleton-button" />
        </article>
      ))}
    </section>
  );
}

function PublicRoomsSkeleton() {
  return (
    <div className="public-games-list" aria-label="Loading public rooms" aria-busy="true">
      {Array.from({ length: 2 }, (_, index) => (
        <div className="public-game-row skeleton-row" key={index}>
          <SkeletonBlock />
          <SkeletonBlock className="skeleton-button" />
        </div>
      ))}
    </div>
  );
}

function emptyBoard() {
  return Array.from({ length: ROWS }, () => Array(COLS).fill(0));
}

function sanitizeRoomIdInput(value) {
  return value.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
}

function sanitizeUsernameInput(value) {
  return value.replace(/[^A-Za-z0-9_]/g, "").slice(0, USERNAME_MAX_LENGTH);
}

function sanitizeEmailInput(value) {
  return value.replace(/[\s<>"]/g, "").slice(0, EMAIL_MAX_LENGTH);
}

function sanitizePasswordInput(value) {
  return value.replace(/[\r\n]/g, "").slice(0, PASSWORD_MAX_LENGTH);
}

function isTextEntryTarget(target) {
  const tagName = target?.tagName?.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable;
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

function splitIntoRows(items, rowSize) {
  const rows = [];
  for (let index = 0; index < items.length; index += rowSize) {
    rows.push(items.slice(index, index + rowSize));
  }
  return rows;
}

function normalizeMoveAnalysisStatus(status) {
  if (typeof status !== "string" || status.trim() === "") {
    return "not_requested";
  }

  return status.trim().toLowerCase();
}

function isMoveAnalysisActive(status) {
  return MOVE_ANALYSIS_ACTIVE_STATUSES.has(normalizeMoveAnalysisStatus(status));
}

function isMoveAnalysisComplete(status) {
  return MOVE_ANALYSIS_COMPLETE_STATUSES.has(normalizeMoveAnalysisStatus(status));
}

function getReviewMoveOwner(move, viewerPlayerNumber) {
  if (!viewerPlayerNumber) {
    return `Player ${move.player_number}`;
  }

  return move.player_number === viewerPlayerNumber ? "You" : "Opponent";
}

function getReviewMoveLabel(move, viewerPlayerNumber) {
  const owner = getReviewMoveOwner(move, viewerPlayerNumber);
  if (owner === "You") {
    return "Your Move";
  }
  if (owner === "Opponent") {
    return "Opponent's Move";
  }
  return `${owner}'s Move`;
}

function getReviewMoveFeedback(move) {
  if (move?.reconstructed) {
    return null;
  }

  const nestedAnalysis = Array.isArray(move?.move_analysis)
    ? move.move_analysis[0]
    : move?.move_analysis;
  if (!nestedAnalysis || typeof nestedAnalysis !== "object") {
    return null;
  }

  if (typeof nestedAnalysis.feedback !== "string" || nestedAnalysis.feedback.trim() === "") {
    return null;
  }

  return nestedAnalysis.feedback.trim();
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

function saveAiWaitingSession(queueId, difficulty) {
  window.localStorage.setItem(AI_WAITING_KEY, JSON.stringify({ queueId, difficulty }));
}

function loadAiWaitingSession() {
  try {
    const waitingSession = JSON.parse(window.localStorage.getItem(AI_WAITING_KEY));
    if (waitingSession?.queueId) {
      return waitingSession;
    }
  } catch {
    return null;
  }
  return null;
}

function clearAiWaitingSession() {
  window.localStorage.removeItem(AI_WAITING_KEY);
}

function createMultiplayerRequestId() {
  try {
    if (typeof window.crypto?.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
  } catch {
    // Fall through to a browser-compatible request ID for older environments.
  }

  return `multiplayer-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function savePendingGame(mode, difficulty, ownerName = "", requestId = "") {
  const pendingGame = { mode, difficulty, ownerName, requestId };
  window.sessionStorage.setItem(PENDING_GAME_KEY, JSON.stringify(pendingGame));
  return pendingGame;
}

function clearPendingGame(requestId = "") {
  if (requestId) {
    try {
      const pendingGame = JSON.parse(window.sessionStorage.getItem(PENDING_GAME_KEY));
      if (pendingGame?.requestId && pendingGame.requestId !== requestId) {
        return false;
      }
    } catch {
      // Invalid pending state is safe to discard below.
    }
  }

  window.sessionStorage.removeItem(PENDING_GAME_KEY);
  return true;
}

function loadPendingGame() {
  try {
    const pendingGame = JSON.parse(window.sessionStorage.getItem(PENDING_GAME_KEY));
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

function formatDateTime(value) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatGameStatus(status) {
  const statusLabels = {
    human_win: "Player Wins",
    ai_win: "AI Wins",
    player1_win: "Player 1 Wins",
    player2_win: "Player 2 Wins",
    draw: "Draw",
  };
  return statusLabels[status] || status.replace("_", " ");
}

function formatReviewStatus(result) {
  if (result === "Win") {
    return "You Win";
  }
  if (result === "Loss") {
    return "You Lost";
  }
  if (result === "Draw") {
    return "Tie";
  }
  return "-";
}

function formatWinRate(wins, total) {
  if (total === 0) {
    return "0%";
  }

  return `${Math.round((wins / total) * 100)}%`;
}

function isGamePath(pathname) {
  return pathname === GAME_PATH || pathname.startsWith(`${GAME_PATH}/`);
}

function isGameReviewPath(pathname) {
  return pathname.startsWith(`${GAME_PATH}/`) && pathname.endsWith("/review");
}

function gamePath(gameId) {
  return gameId ? `${GAME_PATH}/${encodeURIComponent(gameId)}` : GAME_PATH;
}

function gameReviewPath(gameId) {
  return gameId ? `${GAME_PATH}/${encodeURIComponent(gameId)}/review` : GAME_REVIEW_PATH;
}

function getRouteGameId() {
  if (!isGamePath(window.location.pathname) || window.location.pathname === GAME_PATH || isGameReviewPath(window.location.pathname)) {
    return null;
  }

  const [gameId] = window.location.pathname.slice(GAME_PATH.length + 1).split("/");
  return gameId ? decodeURIComponent(gameId) : null;
}

function getGameReviewId() {
  if (!isGameReviewPath(window.location.pathname)) {
    return null;
  }

  const reviewPath = window.location.pathname.slice(GAME_PATH.length + 1, -"/review".length);
  const [gameId] = reviewPath.split("/");
  return gameId ? decodeURIComponent(gameId) : null;
}

function getCurrentPath() {
  if (isGameReviewPath(window.location.pathname)) {
    return GAME_REVIEW_PATH;
  }

  if (isGamePath(window.location.pathname)) {
    return GAME_PATH;
  }

  return APP_PATHS.has(window.location.pathname) ? window.location.pathname : SETUP_PATH;
}

function App() {
  const [routePath, setRoutePath] = useState(getCurrentPath);
  const [board, setBoard] = useState(emptyBoard);
  const [status, setStatus] = useState("setup");
  const [message, setMessage] = useState(() => ([GAME_PATH, GAME_REVIEW_PATH].includes(getCurrentPath()) ? "Loading game..." : "Choose difficulty"));
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
  const [multiplayerPlayerNames, setMultiplayerPlayerNames] = useState({});
  const [aiNumber, setAiNumber] = useState(AI);
  const [aiQueuePosition, setAiQueuePosition] = useState(0);
  const [aiWaitingQueueId, setAiWaitingQueueId] = useState(() => loadAiWaitingSession()?.queueId || "");
  const [aiWaitingPosition, setAiWaitingPosition] = useState(0);
  const [playersConnected, setPlayersConnected] = useState(0);
  const [currentPlayer, setCurrentPlayer] = useState(PLAYER);
  const [joinGameId, setJoinGameId] = useState("");
  const [disconnectDeadline, setDisconnectDeadline] = useState(null);
  const [disconnectSecondsLeft, setDisconnectSecondsLeft] = useState(null);
  const [playAgainAccepted, setPlayAgainAccepted] = useState(0);
  const [playAgainRequested, setPlayAgainRequested] = useState(false);
  const [otherPlayerLeftMessage, setOtherPlayerLeftMessage] = useState("");
  const [isRoomPublic, setIsRoomPublic] = useState(false);
  const [roomVisibilityPending, setRoomVisibilityPending] = useState(false);
  const [roomVisibilityCooldown, setRoomVisibilityCooldown] = useState(false);
  const [toast, setToast] = useState(null);
  const [publicGames, setPublicGames] = useState([]);
  const [publicGamesLoading, setPublicGamesLoading] = useState(false);
  const [joiningPublicGameId, setJoiningPublicGameId] = useState("");
  const [authOpen, setAuthOpen] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [authFields, setAuthFields] = useState({
    username: "",
    email: "",
    password: "",
  });
  const [authSession, setAuthSession] = useState(null);
  const [userProfile, setUserProfile] = useState(null);
  const [profileGames, setProfileGames] = useState([]);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [reviewMoves, setReviewMoves] = useState([]);
  const [reviewMoveIndex, setReviewMoveIndex] = useState(0);
  const [reviewAnimatedPieces, setReviewAnimatedPieces] = useState([]);
  const [reviewAnimationRun, setReviewAnimationRun] = useState(0);
  const [reviewWinningPieces, setReviewWinningPieces] = useState([]);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [reviewAnalysisStatus, setReviewAnalysisStatus] = useState("not_requested");
  const [reviewAnalysisError, setReviewAnalysisError] = useState("");
  const [reviewAnalysisAvailable, setReviewAnalysisAvailable] = useState(true);
  const [reviewAnalysisUnavailableReason, setReviewAnalysisUnavailableReason] = useState("");
  const [reviewAnalysisRequestPending, setReviewAnalysisRequestPending] = useState(false);
  const [authReady, setAuthReady] = useState(!supabaseClient);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const [theme, setTheme] = useState(getInitialTheme);

  const boardRef = useRef(board);
  const toastTimerRef = useRef(null);
  const roomVisibilityTimerRef = useRef(null);
  const reviewAnalysisPollTimerRef = useRef(null);
  const reviewAnalysisRequestControllerRef = useRef(null);
  const playerNumberRef = useRef(playerNumber);
  const aiNumberRef = useRef(aiNumber);
  const pendingMoveRef = useRef(null);
  const emitPendingMultiplayerCreateRef = useRef(null);
  const multiplayerCreateInFlightRequestRef = useRef("");
  const completedMultiplayerCreateRequestRef = useRef("");
  const completedMultiplayerCreateGameRef = useRef("");
  const rejectedMultiplayerCreateRequestRef = useRef("");

  const showToast = useCallback((toastMessage, type = "info") => {
    if (toastTimerRef.current) {
      window.clearTimeout(toastTimerRef.current);
    }
    setToast({ message: toastMessage, type });
    toastTimerRef.current = window.setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 3500);
  }, []);

  useEffect(() => () => {
    if (toastTimerRef.current) {
      window.clearTimeout(toastTimerRef.current);
      toastTimerRef.current = null;
    }
  }, []);

  const gameOver = useMemo(() => {
    return ["human_win", "ai_win", "player1_win", "player2_win", "draw"].includes(status);
  }, [status]);
  const profileStats = useMemo(() => {
    const stats = profileGames.reduce(
      (currentStats, game) => {
        if (game.result === "Win") {
          currentStats.wins += 1;
        } else if (game.result === "Loss") {
          currentStats.losses += 1;
        } else if (game.result === "Draw") {
          currentStats.draws += 1;
        }
        currentStats.total += 1;
        return currentStats;
      },
      { total: 0, wins: 0, losses: 0, draws: 0 },
    );

    return {
      ...stats,
      winRate: formatWinRate(stats.wins, stats.total),
    };
  }, [profileGames]);
  const gameReviewId = getGameReviewId();
  const reviewGame = profileGames.find((game) => game.id === gameReviewId) || null;
  const reviewWinnerLabel = reviewGame?.status === "draw"
    ? "Draw"
    : reviewGame?.winnerName || (reviewGame?.winnerPlayerNumber ? `Player ${reviewGame.winnerPlayerNumber} Wins` : "Completed game");
  const reviewBoard = reviewMoves[reviewMoveIndex]?.board_after || emptyBoard();
  const reviewAnalysisActive = isMoveAnalysisActive(reviewAnalysisStatus);
  const reviewAnalysisComplete = isMoveAnalysisComplete(reviewAnalysisStatus);
  const socketRouteActive = SOCKET_PATHS.has(routePath);
  const reviewAnalysisUnavailable = reviewLoading || Boolean(reviewError) || reviewMoves.length === 0;
  const reviewAnalysisButtonDisabled = !reviewAnalysisAvailable || reviewAnalysisUnavailable || reviewAnalysisRequestPending || reviewAnalysisActive || reviewAnalysisComplete;
  const reviewAnalysisStatusMessage = reviewLoading
    ? "Loading move evaluation status."
    : !reviewAnalysisAvailable
      ? reviewAnalysisUnavailableReason || "Move evaluation is temporarily unavailable."
      : reviewAnalysisUnavailable
      ? "Move history is unavailable."
      : reviewAnalysisRequestPending
        ? "Requesting move evaluation."
        : reviewAnalysisActive
          ? "Move evaluation is queued or running."
          : reviewAnalysisComplete
            ? "Move evaluation is complete."
            : reviewAnalysisStatus === "failed"
              ? `Move evaluation failed. ${reviewAnalysisError || "You can retry the request."}`
              : "Move evaluation has not been requested.";

  useEffect(() => {
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    if (reviewMoves.length === 0) {
      setReviewAnimatedPieces([]);
      setReviewWinningPieces([]);
      return;
    }

    const currentBoard = reviewMoves[reviewMoveIndex]?.board_after || emptyBoard();
    const previousBoard = reviewMoves[reviewMoveIndex - 1]?.board_after || emptyBoard();
    setReviewAnimatedPieces(findChangedPieces(previousBoard, currentBoard));
    setReviewWinningPieces(
      reviewMoveIndex === reviewMoves.length - 1 ? findWinningPieces(currentBoard) : [],
    );
    setReviewAnimationRun((currentRun) => currentRun + 1);
  }, [reviewMoves, reviewMoveIndex]);

  useEffect(() => {
    boardRef.current = board;
  }, [board]);

  useEffect(() => {
    playerNumberRef.current = playerNumber;
  }, [playerNumber]);

  useEffect(() => {
    aiNumberRef.current = aiNumber;
  }, [aiNumber]);

  useEffect(() => {
    if (!supabaseClient) {
      setAuthReady(true);
      return undefined;
    }

    let mounted = true;
    supabaseClient.auth.getSession().then(({ data }) => {
      if (!mounted) {
        return;
      }
      setAuthSession(data.session || null);
      setAuthReady(true);
    });

    const { data: listener } = supabaseClient.auth.onAuthStateChange((_event, session) => {
      setAuthSession(session || null);
      setAuthReady(true);
    });

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  const redirectTo = useCallback((path, replace = false) => {
    if (window.location.pathname !== path) {
      window.history[replace ? "replaceState" : "pushState"]({}, "", path);
    }
    setRoutePath(getCurrentPath());
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

  useEffect(() => {
    if (routePath === LOGIN_PATH) {
      setAuthMode("login");
    } else if (routePath === SIGNUP_PATH) {
      setAuthMode("signup");
    }
  }, [routePath]);

  const clearLocalGame = useCallback(() => {
    pendingMoveRef.current = null;
    setAnimatedPieces([]);
    setWinningPieces([]);
    setBoard(emptyBoard());
    setStatus("setup");
    setMessage("Choose difficulty");
    setGameStarted(false);
    setPlayerMoves([]);
    setAiMoves([]);
    setGameId(null);
    setPlayerId(null);
    setGameMode(GAME_MODE_AI);
    setSelectedSetupMode(GAME_MODE_AI);
    setPlayerNumber(null);
    setMultiplayerPlayerNames({});
    setAiNumber(AI);
    setAiQueuePosition(0);
    setPlayersConnected(0);
    setCurrentPlayer(PLAYER);
    setDisconnectDeadline(null);
    setDisconnectSecondsLeft(null);
    setPlayAgainAccepted(0);
    setPlayAgainRequested(false);
    setOtherPlayerLeftMessage("");
    setIsRoomPublic(false);
    setBusy(false);
  }, []);

  const applyServerBoard = useCallback(async (data) => {
    const pendingMove = pendingMoveRef.current;
    // The server acknowledges the optimistic human move before the AI search
    // finishes. Keep that acknowledgement from racing the final AI result and
    // overwriting the newer board after both async handlers resume.
    if (pendingMove && data.mode !== GAME_MODE_MULTIPLAYER && data.aiThinking) {
      setStatus(data.status);
      setMessage(data.message);
      setCurrentPlayer(data.currentPlayer);
      setAiQueuePosition(data.aiQueuePosition || 0);
      setBusy(true);
      return;
    }

    if (pendingMove && data.mode !== GAME_MODE_MULTIPLAYER) {
      const elapsed = Date.now() - pendingMove.startedAt;
      if (elapsed < MIN_AI_RESPONSE_MS) {
        await wait(MIN_AI_RESPONSE_MS - elapsed);
      }
    }

    const previousBoard = pendingMove?.board || boardRef.current;
    const changedPieces = findChangedPieces(previousBoard, data.board);
    const nextPlayerNumber = data.playerNumber || playerNumberRef.current || PLAYER;
    const nextAiNumber = data.aiNumber || aiNumberRef.current || AI;
    const playerColumn = findMoveColumn(previousBoard, data.board, nextPlayerNumber);
    const aiColumn = data.mode !== GAME_MODE_MULTIPLAYER ? findMoveColumn(previousBoard, data.board, nextAiNumber) : null;
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
    if (data.playerNumber) {
      setPlayerNumber(data.playerNumber);
    }
    if (data.aiNumber) {
      setAiNumber(data.aiNumber);
    }
    setAiQueuePosition(data.aiQueuePosition || 0);
    setPlayersConnected(data.playersConnected || 0);
    setMultiplayerPlayerNames(data.playerNames || {});
    setDisconnectDeadline(data.disconnectDeadline || null);
    setPlayAgainAccepted(data.playAgainAccepted || 0);
    setIsRoomPublic(Boolean(data.publicRoom));
    if (data.status === "playing") {
      setPlayAgainRequested(false);
    }
    setGameStarted(true);
    setBusy(data.mode !== GAME_MODE_MULTIPLAYER && Boolean(data.aiThinking) && data.status === "playing");

    if (data.mode === GAME_MODE_MULTIPLAYER && playerColumn !== null) {
      setPlayerMoves((currentMoves) => [playerColumn, ...currentMoves]);
    }

    if (aiColumn !== null) {
      setAiMoves((currentMoves) => [aiColumn, ...currentMoves]);
    }
  }, []);

  const authPayload = useCallback((payload = {}) => {
    return {
      ...payload,
      accessToken: authSession?.access_token || "",
    };
  }, [authSession]);

  const loadUserProfile = useCallback(async (session) => {
    if (!supabaseClient || !session?.user?.id) {
      setUserProfile(null);
      return;
    }

    const { data, error } = await supabaseClient
      .from("profiles")
      .select("username,display_name")
      .eq("id", session.user.id)
      .maybeSingle();

    if (error) {
      setUserProfile(null);
      return;
    }

    setUserProfile(data || null);
  }, []);

  const loadProfileGames = useCallback(async ({ signal } = {}) => {
    if (!authSession?.access_token) {
      setProfileGames([]);
      return;
    }

    setProfileLoading(true);
    setProfileError("");
    try {
      const response = await fetch(`${SOCKET_URL}/api/profile/games`, {
        headers: {
          Authorization: `Bearer ${authSession.access_token}`,
        },
        signal,
      });
      const data = await readJsonResponse(response);
      if (signal?.aborted) {
        return;
      }
      if (!response.ok) {
        throw new Error(data?.message || "Could not load profile games");
      }
      setProfileGames(data.games || []);
    } catch (error) {
      if (error.name === "AbortError" || signal?.aborted) {
        return;
      }
      setProfileError(error.message);
      setProfileGames([]);
    } finally {
      if (!signal?.aborted) {
        setProfileLoading(false);
      }
    }
  }, [authSession]);

  const loadGameReview = useCallback(async (selectedGameId, { background = false, signal } = {}) => {
    if (!authSession?.access_token || !selectedGameId) {
      setReviewMoves([]);
      setReviewAnalysisStatus("not_requested");
      setReviewAnalysisError("");
      setReviewAnalysisAvailable(true);
      setReviewAnalysisUnavailableReason("");
      return null;
    }

    if (!background) {
      setReviewLoading(true);
      setReviewError("");
      setReviewMoveIndex(0);
      setReviewAnalysisStatus("not_requested");
      setReviewAnalysisError("");
      setReviewAnalysisAvailable(true);
      setReviewAnalysisUnavailableReason("");
    }
    try {
      const response = await fetch(`${SOCKET_URL}/api/profile/games/${encodeURIComponent(selectedGameId)}/moves`, {
        headers: {
          Authorization: `Bearer ${authSession.access_token}`,
        },
        signal,
      });
      const data = await readJsonResponse(response);
      if (signal?.aborted) {
        return null;
      }
      if (!response.ok) {
        if (response.status === 404) {
          redirectTo(NOT_FOUND_PATH, true);
          return { status: "not_found", analysisError: data?.message || "Game history not found" };
        }
        throw new Error(data?.message || "Could not load game review");
      }
      if (!Array.isArray(data.moves) || data.moves.length === 0) {
        redirectTo(NOT_FOUND_PATH, true);
        return { status: "not_found", analysisError: "Game history not found" };
      }

      const analysisStatus = normalizeMoveAnalysisStatus(data.analysis_status);
      const analysisError = typeof data.analysis_error === "string" ? data.analysis_error : "";
      const analysisAvailable = data.analysis_available !== false;
      const analysisUnavailableReason = typeof data.analysis_unavailable_reason === "string"
        ? data.analysis_unavailable_reason
        : "";
      if (!background || isMoveAnalysisComplete(analysisStatus) || analysisStatus === "failed") {
        setReviewMoves(data.moves);
      }
      setReviewAnalysisStatus(analysisStatus);
      setReviewAnalysisError(analysisError);
      setReviewAnalysisAvailable(analysisAvailable);
      setReviewAnalysisUnavailableReason(analysisUnavailableReason);
      return { status: analysisStatus, analysisError, analysisAvailable, analysisUnavailableReason };
    } catch (error) {
      if (error.name === "AbortError" || signal?.aborted) {
        return null;
      }
      if (!background) {
        setReviewError(error.message);
        setReviewMoves([]);
      }
      return { status: "error", analysisError: error.message };
    } finally {
      if (!background && !signal?.aborted) {
        setReviewLoading(false);
      }
    }
  }, [authSession, redirectTo]);

  const requestMoveEvaluation = useCallback(async () => {
    if (
      !authSession?.access_token
      || !gameReviewId
      || reviewLoading
      || reviewError
      || reviewMoves.length === 0
      || !reviewAnalysisAvailable
      || reviewAnalysisRequestPending
      || reviewAnalysisRequestControllerRef.current
      || isMoveAnalysisActive(reviewAnalysisStatus)
      || isMoveAnalysisComplete(reviewAnalysisStatus)
    ) {
      return;
    }

    const controller = new AbortController();
    reviewAnalysisRequestControllerRef.current = controller;
    setReviewAnalysisRequestPending(true);
    setReviewAnalysisError("");
    try {
      const response = await fetch(`${SOCKET_URL}/api/profile/games/${encodeURIComponent(gameReviewId)}/analysis`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${authSession.access_token}`,
        },
        signal: controller.signal,
      });
      const data = await readJsonResponse(response);
      if (!response.ok) {
        throw new Error(data?.message || "Could not request move evaluation");
      }

      const nextStatus = normalizeMoveAnalysisStatus(data.status);
      if (isMoveAnalysisComplete(nextStatus)) {
        setReviewAnalysisStatus("complete");
        const refreshedReview = await loadGameReview(gameReviewId, { background: true, signal: controller.signal });
        if (!controller.signal.aborted) {
          if (!isMoveAnalysisComplete(refreshedReview?.status)) {
            throw new Error(refreshedReview?.analysisError || "Could not load completed move evaluation");
          }
          showToast("Move evaluation is already complete.", "success");
        }
        return;
      }
      if (nextStatus === "failed") {
        throw new Error(data?.message || data?.analysis_error || "Move evaluation failed");
      }

      const activeStatus = isMoveAnalysisActive(nextStatus) ? nextStatus : "processing";
      setReviewAnalysisStatus(activeStatus);
      if (activeStatus === "queued") {
        const queuePosition = Number(data.queuePosition || data.queue_position || 0);
        showToast(
          queuePosition > 0
            ? `Move evaluation queued (position ${queuePosition}).`
            : "Move evaluation queued.",
          "info",
        );
      } else {
        showToast("Move evaluation is running.", "info");
      }
    } catch (error) {
      if (error.name === "AbortError" || controller.signal.aborted) {
        return;
      }
      setReviewAnalysisStatus("failed");
      setReviewAnalysisError(error.message);
      showToast(error.message || "Move evaluation failed. You can retry.", "error");
    } finally {
      if (reviewAnalysisRequestControllerRef.current === controller) {
        reviewAnalysisRequestControllerRef.current = null;
        setReviewAnalysisRequestPending(false);
      }
    }
  }, [
    authSession,
    gameReviewId,
    loadGameReview,
    reviewError,
    reviewLoading,
    reviewMoves.length,
    reviewAnalysisAvailable,
    reviewAnalysisRequestPending,
    reviewAnalysisStatus,
    showToast,
  ]);

  useEffect(() => {
    if (!authSession) {
      setUserProfile(null);
      setProfileGames([]);
      return;
    }

    loadUserProfile(authSession);
  }, [authSession, loadUserProfile]);

  useEffect(() => {
    if ((routePath === PROFILE_PATH || routePath === GAME_REVIEW_PATH) && authSession) {
      const controller = new AbortController();
      loadProfileGames({ signal: controller.signal });
      return () => controller.abort();
    }
    return undefined;
  }, [authSession, loadProfileGames, routePath]);

  useEffect(() => {
    const controller = new AbortController();
    if (routePath === GAME_REVIEW_PATH && authSession) {
      if (gameReviewId) {
        loadGameReview(gameReviewId, { signal: controller.signal }).then((result) => {
          if (!controller.signal.aborted && result?.status === "failed") {
            showToast(result.analysisError || "Move evaluation failed. You can retry.", "error");
          }
        });
      } else {
        redirectTo(NOT_FOUND_PATH, true);
      }
      return () => controller.abort();
    }

    setReviewMoves([]);
    setReviewError("");
    setReviewAnalysisStatus("not_requested");
    setReviewAnalysisError("");
    setReviewAnalysisAvailable(true);
    setReviewAnalysisUnavailableReason("");
    return () => controller.abort();
  }, [authSession, gameReviewId, loadGameReview, redirectTo, routePath, showToast]);

  useEffect(() => {
    setReviewAnalysisRequestPending(false);
    return () => {
      if (reviewAnalysisRequestControllerRef.current) {
        reviewAnalysisRequestControllerRef.current.abort();
        reviewAnalysisRequestControllerRef.current = null;
      }
    };
  }, [authSession?.access_token, gameReviewId, routePath]);

  useEffect(() => {
    if (
      routePath !== GAME_REVIEW_PATH
      || !authSession?.access_token
      || !gameReviewId
      || !isMoveAnalysisActive(reviewAnalysisStatus)
    ) {
      return undefined;
    }

    let cancelled = false;
    const controller = new AbortController();
    const pollAnalysis = async () => {
      const result = await loadGameReview(gameReviewId, {
        background: true,
        signal: controller.signal,
      });
      if (cancelled || controller.signal.aborted) {
        return;
      }
      if (result?.analysisAvailable === false) {
        showToast(result.analysisUnavailableReason || "Move evaluation is temporarily unavailable.", "error");
        return;
      }
      if (isMoveAnalysisComplete(result?.status)) {
        showToast("Move evaluation complete.", "success");
        return;
      }
      if (result?.status === "failed") {
        showToast(result.analysisError || "Move evaluation failed. You can retry.", "error");
        return;
      }
      if (result?.status === "error") {
        const errorMessage = result.analysisError || "Could not refresh move evaluation. You can retry.";
        setReviewAnalysisStatus("failed");
        setReviewAnalysisError(errorMessage);
        showToast(errorMessage, "error");
        return;
      }
      reviewAnalysisPollTimerRef.current = window.setTimeout(pollAnalysis, MOVE_ANALYSIS_POLL_MS);
    };

    reviewAnalysisPollTimerRef.current = window.setTimeout(pollAnalysis, MOVE_ANALYSIS_POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      if (reviewAnalysisPollTimerRef.current) {
        window.clearTimeout(reviewAnalysisPollTimerRef.current);
        reviewAnalysisPollTimerRef.current = null;
      }
    };
  }, [authSession, gameReviewId, loadGameReview, reviewAnalysisStatus, routePath, showToast]);

  useEffect(() => {
    if (routePath !== NOT_FOUND_PATH) {
      return undefined;
    }

    const redirectTimer = window.setTimeout(() => redirectTo(SETUP_PATH, true), 3000);
    return () => window.clearTimeout(redirectTimer);
  }, [redirectTo, routePath]);

  useEffect(() => {
    if (!socketRouteActive) {
      setSocketClient(null);
      return undefined;
    }

    const nextSocket = io(SOCKET_URL, { transports: SOCKET_TRANSPORTS });
    setSocketClient(nextSocket);

    function handleConnect() {
      if (!authSession) {
        return;
      }

      const routeGameId = getRouteGameId();
      const waitingSession = loadAiWaitingSession();
      if (waitingSession) {
        setAiWaitingQueueId(waitingSession.queueId);
        nextSocket.emit("check_ai_waiting", authPayload(waitingSession));
        return;
      }
      const pendingGame = loadPendingGame();
      if (pendingGame) {
        clearSession();
        clearMultiplayerSession();
        if (pendingGame.mode === GAME_MODE_MULTIPLAYER) {
          emitPendingMultiplayerCreate(pendingGame);
          return;
        }

        clearPendingGame();
        nextSocket.emit("create_game", authPayload({ difficulty: pendingGame.difficulty || DEFAULT_DIFFICULTY }));
        return;
      }

      const pendingMultiplayerJoin = takePendingMultiplayerJoin();
      if (pendingMultiplayerJoin) {
        clearSession();
        clearMultiplayerSession();
        nextSocket.emit("join_multiplayer_game", authPayload({ gameId: pendingMultiplayerJoin.gameId }));
        return;
      }

      const multiplayerSession = loadMultiplayerSession();
      if (multiplayerSession && (!routeGameId || multiplayerSession.gameId === routeGameId)) {
        nextSocket.emit("join_multiplayer_game", authPayload(multiplayerSession));
        return;
      }

      const storedSession = loadSession();
      if (storedSession && (!routeGameId || storedSession.gameId === routeGameId)) {
        nextSocket.emit("join_game", authPayload(storedSession));
        return;
      }

      if (routeGameId) {
        nextSocket.emit("join_multiplayer_game", authPayload({ gameId: routeGameId }));
      }
    }

    function handleGameCreated(data) {
      clearAiWaitingSession();
      setAiWaitingQueueId("");
      setAiWaitingPosition(0);
      saveSession(data.gameId, data.playerId);
      setGameId(data.gameId);
      setPlayerId(data.playerId);
      setGameMode(data.mode || GAME_MODE_AI);
      setPlayerNumber(data.playerNumber || null);
      setAiNumber(data.aiNumber || AI);
      setAiQueuePosition(data.aiQueuePosition || 0);
      setPlayersConnected(data.playersConnected || 0);
      setMultiplayerPlayerNames(data.playerNames || {});
      setCurrentPlayer(data.currentPlayer || PLAYER);
      setDisconnectDeadline(data.disconnectDeadline || null);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
      setIsRoomPublic(Boolean(data.publicRoom));
      setAnimatedPieces([]);
      setWinningPieces([]);
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setSelectedDifficulty(data.difficulty || DEFAULT_DIFFICULTY);
      setPlayerMoves([]);
      setAiMoves([]);
      setGameStarted(true);
      setBusy(data.mode !== GAME_MODE_MULTIPLAYER && data.currentPlayer === (data.aiNumber || AI) && data.status === "playing");
      redirectTo(gamePath(data.gameId));
    }

    function handleGameJoined(data) {
      const storedSession = loadSession();
      const joinedPlayerId = data.playerId || storedSession?.playerId || null;
      if (data.mode !== GAME_MODE_MULTIPLAYER && joinedPlayerId) {
        clearMultiplayerSession();
        saveSession(data.gameId, joinedPlayerId);
      }
      setGameId(data.gameId);
      setPlayerId(joinedPlayerId);
      setGameMode(data.mode || GAME_MODE_AI);
      setPlayerNumber(data.playerNumber || null);
      setAiNumber(data.aiNumber || AI);
      setAiQueuePosition(data.aiQueuePosition || 0);
      setPlayersConnected(data.playersConnected || 0);
      setMultiplayerPlayerNames(data.playerNames || {});
      setCurrentPlayer(data.currentPlayer || PLAYER);
      setDisconnectDeadline(data.disconnectDeadline || null);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
      setIsRoomPublic(Boolean(data.publicRoom));
      setAnimatedPieces([]);
      setWinningPieces(findWinningPieces(data.board));
      setBoard(data.board);
      setStatus(data.status);
      setMessage(data.message);
      setSelectedDifficulty(data.difficulty || DEFAULT_DIFFICULTY);
      setPlayerMoves([]);
      setAiMoves([]);
      setGameStarted(true);
      setBusy(data.mode !== GAME_MODE_MULTIPLAYER && Boolean(data.aiThinking) && data.status === "playing");
      redirectTo(gamePath(data.gameId), true);
    }

    function applyMultiplayerGameStarted(data) {
      clearSession();
      saveMultiplayerSession(data.gameId, data.playerId);
      setGameId(data.gameId);
      setPlayerId(data.playerId);
      setPlayerNumber(data.playerNumber);
      setAiNumber(AI);
      setAiQueuePosition(0);
      setPlayersConnected(data.playersConnected || 0);
      setMultiplayerPlayerNames(data.playerNames || {});
      setCurrentPlayer(data.currentPlayer || PLAYER);
      setDisconnectDeadline(data.disconnectDeadline || null);
      setPlayAgainAccepted(data.playAgainAccepted || 0);
      setIsRoomPublic(Boolean(data.publicRoom));
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
      setJoiningPublicGameId("");
      redirectTo(gamePath(data.gameId));
    }

    function handleMultiplayerGameCreated(data, fallbackRequestId = "") {
      if (!data?.gameId || !data?.playerId) {
        return false;
      }

      const responseRequestId = data.requestId || fallbackRequestId;
      if (
        (responseRequestId && completedMultiplayerCreateRequestRef.current === responseRequestId)
        || completedMultiplayerCreateGameRef.current === data.gameId
      ) {
        return true;
      }

      const pendingGame = loadPendingGame();
      if (!pendingGame || pendingGame.mode !== GAME_MODE_MULTIPLAYER) {
        return false;
      }
      if (responseRequestId && pendingGame.requestId && responseRequestId !== pendingGame.requestId) {
        return false;
      }

      const completedRequestId = responseRequestId || pendingGame.requestId;
      completedMultiplayerCreateRequestRef.current = completedRequestId;
      completedMultiplayerCreateGameRef.current = data.gameId;
      rejectedMultiplayerCreateRequestRef.current = "";
      multiplayerCreateInFlightRequestRef.current = "";
      clearPendingGame(completedRequestId);
      applyMultiplayerGameStarted(data);
      return true;
    }

    function handleMultiplayerGameJoined(data) {
      clearPendingGame();
      multiplayerCreateInFlightRequestRef.current = "";
      applyMultiplayerGameStarted(data);
    }

    function handleJoinRejected(data) {
      clearSession();
      clearMultiplayerSession();
      clearAiWaitingSession();
      setAiWaitingQueueId("");
      setAiWaitingPosition(0);
      clearLocalGame();
      setMessage(data?.message || "Game not found");
      setJoiningPublicGameId("");
      if (getCurrentPath() === JOIN_PATH) {
        setBusy(false);
        nextSocket.emit("list_public_games", authPayload());
        redirectTo(JOIN_PATH, true);
        return;
      }
      redirectTo(SETUP_PATH, true);
    }

    function handleCreateRejected(data) {
      const pendingGame = loadPendingGame();
      if (data?.requestId && (!pendingGame || pendingGame.mode !== GAME_MODE_MULTIPLAYER)) {
        return;
      }
      if (data?.requestId && pendingGame?.requestId && data.requestId !== pendingGame.requestId) {
        return;
      }

      const rejectedRequestId = data?.requestId || pendingGame?.requestId || "";
      if (rejectedRequestId && rejectedMultiplayerCreateRequestRef.current === rejectedRequestId) {
        return;
      }
      rejectedMultiplayerCreateRequestRef.current = rejectedRequestId;
      multiplayerCreateInFlightRequestRef.current = "";
      clearPendingGame(rejectedRequestId);
      clearAiWaitingSession();
      setAiWaitingQueueId("");
      setAiWaitingPosition(0);
      clearSession();
      clearMultiplayerSession();
      clearLocalGame();
      const rejectionMessage = data?.message || "Could not create game";
      setMessage(rejectionMessage);
      showToast(rejectionMessage, "error");
      redirectTo(SETUP_PATH, true);
    }

    function emitPendingMultiplayerCreate(pendingGame = loadPendingGame()) {
      if (!nextSocket.connected || pendingGame?.mode !== GAME_MODE_MULTIPLAYER) {
        return false;
      }

      const requestId = pendingGame.requestId || createMultiplayerRequestId();
      if (multiplayerCreateInFlightRequestRef.current === requestId) {
        return false;
      }

      const persistedGame = savePendingGame(
        GAME_MODE_MULTIPLAYER,
        null,
        pendingGame.ownerName || "Account",
        requestId,
      );
      multiplayerCreateInFlightRequestRef.current = requestId;
      rejectedMultiplayerCreateRequestRef.current = "";
      setStatus("waiting");
      setMessage("Creating multiplayer room...");
      setBusy(true);

      nextSocket.timeout(MULTIPLAYER_CREATE_TIMEOUT_MS).emit(
        "create_multiplayer_game",
        authPayload({ ownerName: persistedGame.ownerName, requestId }),
        (timeoutError, response) => {
          if (multiplayerCreateInFlightRequestRef.current === requestId) {
            multiplayerCreateInFlightRequestRef.current = "";
          }

          const currentPendingGame = loadPendingGame();
          if (
            !currentPendingGame
            || currentPendingGame.mode !== GAME_MODE_MULTIPLAYER
            || currentPendingGame.requestId !== requestId
          ) {
            return;
          }

          if (timeoutError) {
            const timeoutMessage = "Room creation timed out. Try Create game again.";
            setStatus("error");
            setMessage(timeoutMessage);
            setBusy(false);
            showToast(timeoutMessage, "error");
            return;
          }

          if (!response || response.ok === false) {
            handleCreateRejected({
              ...response,
              requestId: response?.requestId || requestId,
              message: response?.message || "Could not create game",
            });
            return;
          }

          if (!handleMultiplayerGameCreated(response, requestId)) {
            const invalidResponseMessage = "Room creation did not finish. Try Create game again.";
            setStatus("error");
            setMessage(invalidResponseMessage);
            setBusy(false);
            showToast(invalidResponseMessage, "error");
          }
        },
      );
      return true;
    }

    function handleAiWaiting(data) {
      const waitingSession = loadAiWaitingSession();
      const difficulty = data.difficulty || waitingSession?.difficulty || DEFAULT_DIFFICULTY;
      saveAiWaitingSession(data.queueId, difficulty);
      setAiWaitingQueueId(data.queueId);
      setAiWaitingPosition(data.position || 1);
      setBusy(false);
      redirectTo(AI_WAITING_PATH, true);
    }

    function handleAiWaitingCancelled() {
      clearAiWaitingSession();
      setAiWaitingQueueId("");
      setAiWaitingPosition(0);
      setBusy(false);
      if (getCurrentPath() === AI_WAITING_PATH) {
        redirectTo(SETUP_PATH, true);
      }
    }

    async function handleBoardUpdated(data) {
      if (data.gameId) {
        setGameId(data.gameId);
        if (data.playerId) {
          setPlayerId(data.playerId);
          if (data.mode === GAME_MODE_MULTIPLAYER) {
            clearSession();
            saveMultiplayerSession(data.gameId, data.playerId);
          } else {
            clearMultiplayerSession();
            saveSession(data.gameId, data.playerId);
          }
        }
        redirectTo(gamePath(data.gameId), true);
      }
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
      clearPendingGame();
      multiplayerCreateInFlightRequestRef.current = "";
      clearLocalGame();
      redirectTo(SETUP_PATH, true);
    }

    function handleDisconnect() {
      const pendingGame = loadPendingGame();
      if (pendingGame?.mode !== GAME_MODE_MULTIPLAYER) {
        return;
      }

      if (multiplayerCreateInFlightRequestRef.current === pendingGame.requestId) {
        multiplayerCreateInFlightRequestRef.current = "";
      }
      setStatus("error");
      setMessage("Connection interrupted. Room creation will retry after reconnecting.");
      setBusy(false);
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

    function handlePublicGames(data) {
      setPublicGames(data?.games || []);
      setPublicGamesLoading(false);
    }

    function startRoomVisibilityCooldown(duration = 5000) {
      if (roomVisibilityTimerRef.current) {
        window.clearTimeout(roomVisibilityTimerRef.current);
      }
      setRoomVisibilityCooldown(true);
      roomVisibilityTimerRef.current = window.setTimeout(() => {
        setRoomVisibilityCooldown(false);
        roomVisibilityTimerRef.current = null;
      }, duration);
    }

    function handleRoomPublicUpdated(data) {
      setIsRoomPublic(Boolean(data.publicRoom));
      setRoomVisibilityPending(false);
      startRoomVisibilityCooldown(5000);
      showToast(data.message || "Room visibility updated", "success");
    }

    function handleRoomPublicUpdateFailed(data) {
      if (typeof data?.publicRoom === "boolean") {
        setIsRoomPublic(data.publicRoom);
      }
      setRoomVisibilityPending(false);
      if (data?.retryAfterMs > 0) {
        startRoomVisibilityCooldown(data.retryAfterMs);
      }
      showToast(data?.message || "Could not update room visibility", "error");
    }

    emitPendingMultiplayerCreateRef.current = emitPendingMultiplayerCreate;
    nextSocket.on("connect", handleConnect);
    nextSocket.on("disconnect", handleDisconnect);
    nextSocket.on("game_created", handleGameCreated);
    nextSocket.on("game_joined", handleGameJoined);
    nextSocket.on("multiplayer_game_created", handleMultiplayerGameCreated);
    nextSocket.on("multiplayer_game_joined", handleMultiplayerGameJoined);
    nextSocket.on("join_rejected", handleJoinRejected);
    nextSocket.on("create_rejected", handleCreateRejected);
    nextSocket.on("ai_waiting", handleAiWaiting);
    nextSocket.on("ai_waiting_cancelled", handleAiWaitingCancelled);
    nextSocket.on("board_updated", handleBoardUpdated);
    nextSocket.on("play_again_updated", handlePlayAgainUpdated);
    nextSocket.on("player_left", handlePlayerLeft);
    nextSocket.on("game_left", handleGameLeft);
    nextSocket.on("public_games", handlePublicGames);
    nextSocket.on("room_public_updated", handleRoomPublicUpdated);
    nextSocket.on("room_public_update_failed", handleRoomPublicUpdateFailed);
    nextSocket.on("invalid_move", handleInvalidMove);
    nextSocket.on("connect_error", () => {
      multiplayerCreateInFlightRequestRef.current = "";
      setStatus("error");
      setMessage(`Flask SocketIO is not responding at ${SOCKET_URL}`);
      setBusy(false);
    });

    return () => {
      if (roomVisibilityTimerRef.current) {
        window.clearTimeout(roomVisibilityTimerRef.current);
      }
      if (emitPendingMultiplayerCreateRef.current === emitPendingMultiplayerCreate) {
        emitPendingMultiplayerCreateRef.current = null;
      }
      nextSocket.disconnect();
    };
  }, [applyServerBoard, authPayload, clearLocalGame, redirectTo, showToast, socketRouteActive]);

  useEffect(() => {
    if (routePath !== AI_WAITING_PATH || !aiWaitingQueueId || !socketClient?.connected) {
      return undefined;
    }
    const checkWaitingRoom = () => {
      const waitingSession = loadAiWaitingSession();
      socketClient.emit("check_ai_waiting", authPayload({
        queueId: aiWaitingQueueId,
        difficulty: waitingSession?.difficulty || selectedDifficulty,
      }));
    };
    const intervalId = window.setInterval(checkWaitingRoom, 20000);
    return () => window.clearInterval(intervalId);
  }, [aiWaitingQueueId, authPayload, routePath, selectedDifficulty, socketClient]);

  useEffect(() => {
    if (routePath === JOIN_PATH && authSession && socketClient?.connected) {
      setPublicGamesLoading(true);
      socketClient.emit("list_public_games", authPayload());
    }
  }, [authPayload, authSession, routePath, socketClient]);

  function startMultiplayerGame() {
    if (!authSession) {
      redirectTo(LOGIN_PATH);
      return;
    }

    clearAiWaitingSession();
    setAiWaitingQueueId("");
    setAiWaitingPosition(0);
    clearSession();
    clearMultiplayerSession();
    pendingMoveRef.current = null;
    setGameMode(GAME_MODE_MULTIPLAYER);
    setPlayerNumber(null);
    setAiNumber(AI);
    setCurrentPlayer(PLAYER);
    setBoard(emptyBoard());
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);
    setGameId(null);
    setPlayerId(null);
    setStatus("waiting");
    setGameStarted(false);
    setBusy(true);

    const existingPendingGame = loadPendingGame();
    const requestId = existingPendingGame?.mode === GAME_MODE_MULTIPLAYER && existingPendingGame.requestId
      ? existingPendingGame.requestId
      : createMultiplayerRequestId();
    const pendingGame = savePendingGame(GAME_MODE_MULTIPLAYER, null, accountName, requestId);

    if (!socketClient?.connected || !emitPendingMultiplayerCreateRef.current) {
      setMessage("Connecting to create multiplayer room...");
      socketClient?.connect();
      return;
    }

    emitPendingMultiplayerCreateRef.current(pendingGame);
  }

  function joinMultiplayerGame(requestedGameIdOverride = "", publicJoin = false) {
    if (!authSession) {
      redirectTo(LOGIN_PATH);
      return;
    }

    const requestedGameId = (requestedGameIdOverride || joinGameId).trim();
    if (!requestedGameId || !socketClient?.connected) {
      setStatus("error");
      setMessage(!requestedGameId ? "Enter a room ID" : `Flask SocketIO is not responding at ${SOCKET_URL}`);
      setJoiningPublicGameId("");
      return;
    }

    clearSession();
    clearMultiplayerSession();
    clearPendingGame();
    multiplayerCreateInFlightRequestRef.current = "";
    setGameMode(GAME_MODE_MULTIPLAYER);
    setPlayerNumber(null);
    setAiNumber(AI);
    setCurrentPlayer(PLAYER);
    setBoard(emptyBoard());
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);
    setGameId(requestedGameId);
    setPlayerId(null);
    setStatus("waiting");
    setMessage("Joining multiplayer room...");
    setGameStarted(false);
    setBusy(true);
    socketClient.emit("join_multiplayer_game", authPayload({ gameId: requestedGameId, publicJoin, playerName: accountName }));
  }

  function requestPublicGames() {
    if (!authSession) {
      redirectTo(LOGIN_PATH);
      return;
    }

    if (!socketClient?.connected) {
      setMessage(`Flask SocketIO is not responding at ${SOCKET_URL}`);
      return;
    }

    setPublicGamesLoading(true);
    socketClient.emit("list_public_games", authPayload());
  }

  function joinPublicGame(publicGameId) {
    if (busy) {
      return;
    }

    setJoiningPublicGameId(publicGameId);
    joinMultiplayerGame(publicGameId, true);
  }

  function togglePublicRoom() {
    if (!socketClient?.connected || !gameId || !playerId || busy || roomVisibilityPending || roomVisibilityCooldown) {
      return;
    }

    setRoomVisibilityPending(true);
    socketClient.emit("set_room_public", authPayload({ gameId, playerId, public: !isRoomPublic }));
  }

  function requestNewGame() {
    if (!authSession) {
      redirectTo(LOGIN_PATH);
      return;
    }

    if (routePath === SETUP_PATH && selectedSetupMode === GAME_MODE_MULTIPLAYER) {
      startMultiplayerGame();
      return;
    }

    if (gameMode === GAME_MODE_MULTIPLAYER) {
      if (gameId && playerId) {
        setBusy(true);
        setAnimatedPieces([]);
        setWinningPieces([]);
        setPlayerMoves([]);
        setAiMoves([]);
        socketClient.emit("reset_game", authPayload({ gameId, playerId }));
      }
      return;
    }

    if (!gameStarted) {
      if (!socketClient?.connected) {
        setStatus("error");
        setMessage(`Flask SocketIO is not responding at ${SOCKET_URL}`);
        return;
      }

      clearSession();
      clearMultiplayerSession();
      clearAiWaitingSession();
      setAiWaitingQueueId("");
      setAiWaitingPosition(0);
      pendingMoveRef.current = null;
      setGameMode(GAME_MODE_AI);
      setCurrentPlayer(PLAYER);
      setBoard(emptyBoard());
      setAnimatedPieces([]);
      setWinningPieces([]);
      setPlayerMoves([]);
      setAiMoves([]);
      setGameId(null);
      setPlayerId(null);
      setStatus("waiting");
      setMessage("Creating game...");
      setGameStarted(false);
      setBusy(true);
      socketClient.emit("create_game", authPayload({ difficulty: selectedDifficulty }));
      return;
    }

    if (!socketClient?.connected) {
      setStatus("error");
      setMessage(`Flask SocketIO is not responding at ${SOCKET_URL}`);
      return;
    }

    if (gameStarted && gameId && playerId) {
      setBusy(true);
      setAnimatedPieces([]);
      setWinningPieces([]);
      setPlayerMoves([]);
      setAiMoves([]);
      socketClient.emit("reset_game", authPayload({ gameId, playerId, difficulty: selectedDifficulty }));
      return;
    }

    setBusy(true);
    setAnimatedPieces([]);
    setWinningPieces([]);
    setPlayerMoves([]);
    setAiMoves([]);
    socketClient.emit("create_game", authPayload({ difficulty: selectedDifficulty }));
  }

  function chooseDifficulty(difficulty) {
    if (busy) {
      return;
    }

    clearSession();
    clearMultiplayerSession();
    clearPendingGame();
    multiplayerCreateInFlightRequestRef.current = "";
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
    clearPendingGame();
    multiplayerCreateInFlightRequestRef.current = "";
    clearLocalGame();
    setSelectedSetupMode(GAME_MODE_MULTIPLAYER);
  }

  function showJoinGamePage() {
    clearPendingGame();
    multiplayerCreateInFlightRequestRef.current = "";
    redirectTo(JOIN_PATH);
  }

  function leaveAiWaitingRoom() {
    if (socketClient?.connected && aiWaitingQueueId) {
      socketClient.emit("cancel_ai_waiting", authPayload({ queueId: aiWaitingQueueId }));
      return;
    }
    clearAiWaitingSession();
    setAiWaitingQueueId("");
    setAiWaitingPosition(0);
    redirectTo(SETUP_PATH, true);
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
      socketClient.emit("player_move", authPayload({ gameId, playerId, column }));
      return;
    }

    const humanPiece = playerNumber || PLAYER;
    if (!socketClient?.connected || !gameId || !playerId || currentPlayer !== humanPiece) {
      return;
    }

    setBusy(true);
    setMessage("AI is thinking...");
    const playerBoard = applyLocalMove(board, column, humanPiece);
    const playerPieces = findChangedPieces(board, playerBoard);
    pendingMoveRef.current = {
      board: playerBoard,
      startedAt: Date.now(),
    };
    setAnimatedPieces(playerPieces);
    setAnimationRun((currentRun) => currentRun + 1);
    setBoard(playerBoard);
    setPlayerMoves((currentMoves) => [column, ...currentMoves]);
    socketClient.emit("player_move", authPayload({ gameId, playerId, column }));
  }, [authPayload, board, busy, currentPlayer, gameId, gameMode, gameOver, gameStarted, playerId, playerNumber, socketClient, status]);

  useEffect(() => {
    function handleKeyDown(event) {
      if (isTextEntryTarget(event.target)) {
        return;
      }

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
    clearPendingGame();
    multiplayerCreateInFlightRequestRef.current = "";
    window.location.replace(SETUP_PATH);
  }

  function leaveGame() {
    if (gameMode !== GAME_MODE_MULTIPLAYER) {
      if (socketClient?.connected && gameId && playerId) {
        socketClient.emit("leave_game", authPayload({ gameId, playerId }));
      }
      returnToMainMenu();
      return;
    }

    if (!socketClient?.connected || !gameId || !playerId) {
      returnToMainMenu();
      return;
    }

    setBusy(true);
    socketClient.emit("leave_game", authPayload({ gameId, playerId }));
  }

  function requestPlayAgain() {
    if (!socketClient?.connected || !gameId || !playerId || gameMode !== GAME_MODE_MULTIPLAYER || !gameOver) {
      return;
    }

    setPlayAgainRequested(true);
    socketClient.emit("play_again", authPayload({ gameId, playerId }));
  }

  function openAuthModal(mode = "login") {
    setAuthMode(mode);
    setAuthOpen(true);
  }

  function closeAuthModal() {
    setAuthOpen(false);
    setAuthError("");
  }

  async function submitAuthForm(event) {
    event.preventDefault();
    setAuthError("");

    if (!supabaseClient) {
      setAuthError("Supabase auth is not configured");
      return;
    }

    setAuthBusy(true);
    const email = authFields.email.trim();
    const password = authFields.password;
    const username = authFields.username.trim();

    try {
      if (authMode === "signup") {
        if (!username) {
          setAuthError("Username is required");
          return;
        }

        const { data, error } = await supabaseClient.auth.signUp({
          email,
          password,
          options: {
            data: { username },
          },
        });
        if (error) {
          setAuthError(error.message);
          return;
        }

        if (data.session && data.user) {
          await supabaseClient.from("profiles").upsert({
            id: data.user.id,
            username,
            display_name: username,
          });
          setUserProfile({ username, display_name: username });
          setAuthSession(data.session);
          setAuthOpen(false);
          if (routePath === LOGIN_PATH || routePath === SIGNUP_PATH) {
            redirectTo(SETUP_PATH, true);
          }
          return;
        }

        setAuthError("Check your email to finish signup");
        return;
      }

      const { data, error } = await supabaseClient.auth.signInWithPassword({ email, password });
      if (error) {
        setAuthError(error.message);
        return;
      }

      setAuthSession(data.session || null);
      setAuthOpen(false);
      if (routePath === LOGIN_PATH || routePath === SIGNUP_PATH) {
        redirectTo(SETUP_PATH, true);
      }
    } finally {
      setAuthBusy(false);
    }
  }

  async function logout() {
    if (socketClient?.connected && aiWaitingQueueId) {
      socketClient.emit("cancel_ai_waiting", authPayload({ queueId: aiWaitingQueueId }));
    }
    if (supabaseClient) {
      await supabaseClient.auth.signOut();
    }
    clearSession();
    clearMultiplayerSession();
    clearAiWaitingSession();
    clearPendingGame();
    multiplayerCreateInFlightRequestRef.current = "";
    clearLocalGame();
    setAuthSession(null);
    redirectTo(LOGIN_PATH, true);
  }

  function updateAuthField(fieldName, value) {
    const sanitizers = {
      username: sanitizeUsernameInput,
      email: sanitizeEmailInput,
      password: sanitizePasswordInput,
    };
    const sanitizeValue = sanitizers[fieldName] || ((currentValue) => currentValue);
    setAuthFields((currentFields) => ({
      ...currentFields,
      [fieldName]: sanitizeValue(value),
    }));
  }

  const canDropPiece =
    gameStarted &&
    !busy &&
    !gameOver &&
    status === "playing" &&
    ((gameMode === GAME_MODE_AI && currentPlayer === (playerNumber || PLAYER)) ||
      (gameMode === GAME_MODE_MULTIPLAYER && playerNumber === currentPlayer && playersConnected === 2));
  const showingSetup = routePath === SETUP_PATH;
  const showingJoin = routePath === JOIN_PATH;
  const showingAiWaiting = routePath === AI_WAITING_PATH;
  const showingGame = routePath === GAME_PATH;
  const showingGameReview = routePath === GAME_REVIEW_PATH;
  const showingProfile = routePath === PROFILE_PATH;
  const showingLegalPage = routePath === TOS_PATH || routePath === PRIVACY_POLICY_PATH;
  let displayMessage = message;
  if (gameOver && gameMode === GAME_MODE_MULTIPLAYER && status !== "draw") {
    const winnerNumber = status === "player1_win" ? 1 : status === "player2_win" ? 2 : null;
    if (winnerNumber === playerNumber) {
      displayMessage = "You Won!";
    } else if (winnerNumber) {
      displayMessage = `${multiplayerPlayerNames[String(winnerNumber)] || `Player ${winnerNumber}`} won`;
    }
  } else if (!gameOver && canDropPiece) {
    displayMessage = "Your turn";
  } else if (!gameOver && gameStarted && gameMode === GAME_MODE_MULTIPLAYER && playersConnected === 2) {
    displayMessage = "Other player's turn";
  } else if (!gameOver && gameStarted && gameMode === GAME_MODE_AI && busy) {
    displayMessage = aiQueuePosition > 0 ? `AI queued - position ${aiQueuePosition}` : "AI is thinking";
  }

  if (disconnectDeadline && disconnectSecondsLeft !== null && playersConnected < 2 && !gameOver) {
    displayMessage = `${message} Waiting ${disconnectSecondsLeft} seconds.`;
  }

  const currentPlayerWon = gameOver && gameMode === GAME_MODE_MULTIPLAYER
    && ((status === "player1_win" && playerNumber === 1) || (status === "player2_win" && playerNumber === 2));
  const statusClassName = currentPlayerWon
    ? "turn-status game-won"
    : !gameOver && canDropPiece ? "turn-status your-turn" : "turn-status waiting-turn";
  const canLeaveGame =
    gameStarted &&
    showingGame &&
    (gameMode !== GAME_MODE_MULTIPLAYER || (status === "waiting" && playersConnected === 1) || gameOver);
  const canRequestPlayAgain = gameStarted && gameMode === GAME_MODE_MULTIPLAYER && gameOver;
  const canTogglePublicRoom =
    gameStarted && showingGame && gameMode === GAME_MODE_MULTIPLAYER && status === "waiting" && playersConnected === 1 && playerNumber === PLAYER;
  const playerMoveLabel = gameMode === GAME_MODE_MULTIPLAYER ? "Your moves" : "Your moves";
  const opponentMoveLabel = gameMode === GAME_MODE_MULTIPLAYER ? "Other player's moves" : "AI moves";
  const isAuthenticated = Boolean(authSession);
  const accountName = userProfile?.display_name || userProfile?.username || authSession?.user?.user_metadata?.username || "Account";
  const themeToggleLabel = theme === "dark" ? "Light" : "Dark";
  const toggleTheme = () => {
    setTheme((currentTheme) => (currentTheme === "dark" ? "light" : "dark"));
  };
  const authForm = (
    <>
      <div className="auth-mode-tabs" role="tablist" aria-label="Auth mode">
        <button
          type="button"
          role="tab"
          aria-selected={authMode === "login"}
          className={authMode === "login" ? "selected" : ""}
          onClick={() => setAuthMode("login")}
        >
          Login
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={authMode === "signup"}
          className={authMode === "signup" ? "selected" : ""}
          onClick={() => setAuthMode("signup")}
        >
          Sign up
        </button>
      </div>
      <form className="auth-form" onSubmit={submitAuthForm}>
        {authMode === "signup" ? (
          <label>
            Username
            <input
              type="text"
              name="username"
              autoComplete="username"
              placeholder="connect4player"
              value={authFields.username}
              onChange={(event) => updateAuthField("username", event.target.value)}
              maxLength={USERNAME_MAX_LENGTH}
              inputMode="text"
            />
          </label>
        ) : null}
        <label>
          Email
          <input
            type="email"
            name="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={authFields.email}
            onChange={(event) => updateAuthField("email", event.target.value)}
            maxLength={EMAIL_MAX_LENGTH}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            name="password"
            autoComplete={authMode === "signup" ? "new-password" : "current-password"}
            placeholder="Password"
            value={authFields.password}
            onChange={(event) => updateAuthField("password", event.target.value)}
            maxLength={PASSWORD_MAX_LENGTH}
          />
        </label>
        {authError ? <strong className="auth-error">{authError}</strong> : null}
        <button className="auth-submit-button" type="submit" disabled={authBusy}>
          {authMode === "signup" ? "Create account" : "Login"}
        </button>
      </form>
    </>
  );
  const authRequiredView = (
    <section className="difficulty-panel auth-required-panel">
      <strong>Login required</strong>
      <div className="auth-required-actions">
        <button type="button" onClick={() => redirectTo(LOGIN_PATH)}>
          Login
        </button>
        <button type="button" onClick={() => redirectTo(SIGNUP_PATH)}>
          Sign up
        </button>
      </div>
    </section>
  );
  const aiWaitingView = (
    <section className="difficulty-panel ai-waiting-panel" aria-live="polite">
      <span className="ai-waiting-spinner" aria-hidden="true" />
      <div>
        <span className="ai-waiting-eyebrow">AI waiting room</span>
        <h1>AI player is currently busy right now.</h1>
        <p>Your position in line: <strong>{aiWaitingPosition || "-"}</strong></p>
        <small>(checked every 20s)</small>
      </div>
      <button type="button" onClick={leaveAiWaitingRoom}>Leave waiting room</button>
    </section>
  );
  const setupView = !authReady ? (
    <GameLoadingSkeleton message="Loading account..." />
  ) : !isAuthenticated ? (
    authRequiredView
  ) : (
    <section className="difficulty-panel">
      <header className="setup-heading">
        <span>New match</span>
        <h1>Choose how you want to play</h1>
        <p>Challenge a friend or sharpen your strategy against the AI.</p>
      </header>
      <div className="mode-select-layout">
        <div className="mode-side mode-side-player">
          <button
            type="button"
            aria-pressed={selectedSetupMode === GAME_MODE_MULTIPLAYER}
            className={`setup-mode-card vs-player-button${selectedSetupMode === GAME_MODE_MULTIPLAYER ? " selected" : ""}`}
            onClick={chooseMultiplayerMode}
            disabled={busy}
          >
            <span>Vs Player</span>
            <small>Share a room and play live</small>
          </button>
        </div>

        <div className="setup-create-column">
          <div className="setup-selection" aria-live="polite">
            <span>Your selection</span>
            <strong>
              {selectedSetupMode === GAME_MODE_MULTIPLAYER
                ? "Vs Player"
                : `Vs AI · ${DIFFICULTIES.find((difficulty) => difficulty.key === selectedDifficulty)?.label || "Medium"}`}
            </strong>
          </div>
          <button className="play-button" type="button" onClick={requestNewGame} disabled={busy}>
            {busy ? "Creating..." : "Create game"}
          </button>
          <button className="join-game-link" type="button" onClick={showJoinGamePage} disabled={busy}>
            Join a room
          </button>
        </div>

        <div className="mode-side mode-side-ai">
          <span className="mode-side-label">Vs AI</span>
          <div className="difficulty-tabs" role="tablist" aria-label="AI difficulty">
            {DIFFICULTIES.map((difficulty) => (
              <button
                key={difficulty.key}
                type="button"
                role="tab"
                aria-selected={selectedSetupMode === GAME_MODE_AI && selectedDifficulty === difficulty.key}
                className={`difficulty-tab difficulty-${difficulty.key}${selectedSetupMode === GAME_MODE_AI && selectedDifficulty === difficulty.key ? " selected" : ""}`}
                onClick={() => chooseDifficulty(difficulty.key)}
                disabled={busy}
              >
                <span>{difficulty.label}</span>
                <small>{difficulty.hint}</small>
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="setup-tips" aria-label="Game tips">
        <span><strong>01</strong> Pick a mode</span>
        <span><strong>02</strong> Connect four pieces</span>
        <span><strong>03</strong> Review completed games</span>
      </div>
    </section>
  );
  const joinGameView = !authReady ? (
    <GameLoadingSkeleton message="Loading account..." />
  ) : !isAuthenticated ? (
    authRequiredView
  ) : (
    <section className="difficulty-panel join-game-panel">
      <strong>Join Game</strong>
      <div className="multiplayer-join">
        <input
          type="text"
          value={joinGameId}
          onChange={(event) => setJoinGameId(sanitizeRoomIdInput(event.target.value))}
          placeholder="Room ID"
          aria-label="Room ID"
          disabled={busy}
        />
        <button type="button" onClick={() => joinMultiplayerGame()} disabled={busy}>
          Join
        </button>
      </div>
      <section className="public-games-panel" aria-label="Joinable games">
        <div className="public-games-header">
          <strong>Joinable games</strong>
          <button type="button" onClick={requestPublicGames} disabled={busy || publicGamesLoading}>
            Refresh
          </button>
        </div>
        {publicGamesLoading ? (
          <PublicRoomsSkeleton />
        ) : publicGames.length === 0 ? (
          <span className="public-games-empty">No public rooms</span>
        ) : (
          <div className="public-games-list">
            {publicGames.map((publicGame) => (
              <div className="public-game-row" key={publicGame.gameId}>
                <span>{publicGame.ownerName}'s room</span>
                <button
                  type="button"
                  onClick={() => joinPublicGame(publicGame.gameId)}
                  disabled={busy || joiningPublicGameId === publicGame.gameId}
                >
                  {joiningPublicGameId === publicGame.gameId ? "Attempting to join..." : "Join room"}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
      <button className="join-game-link" type="button" onClick={() => redirectTo(SETUP_PATH)} disabled={busy}>
        Back
      </button>
    </section>
  );
  const authPageView = (
    <section className="auth-page">
      <section className="auth-modal auth-route-panel" aria-labelledby="auth-route-title">
        <h2 id="auth-route-title">{authMode === "signup" ? "Create account" : "Login"}</h2>
        {authForm}
      </section>
    </section>
  );
  const loadingView = (
    <GameLoadingSkeleton message={message} />
  );
  const profileReviewView = (
    <section className="profile-page profile-review-page">
      <div className="profile-header">
        <div>
          <span>Game review</span>
          <strong>{reviewGame ? `${reviewWinnerLabel}${reviewGame.winnerName ? " Wins" : ""}` : "Completed game"}</strong>
        </div>
        <div className="profile-review-actions">
          <button
            className="evaluate-moves-button"
            type="button"
            onClick={requestMoveEvaluation}
            disabled={reviewAnalysisButtonDisabled}
            aria-describedby="move-evaluation-status"
            title={reviewAnalysisStatusMessage}
          >
            Evaluate moves
          </button>
          <button type="button" onClick={() => redirectTo(PROFILE_PATH)}>
            Back to games
          </button>
          <span className="sr-only" id="move-evaluation-status" aria-live="polite">
            {reviewAnalysisStatusMessage}
          </span>
        </div>
      </div>
      <section className="status-panel review-status-panel" aria-label="Game review details">
        <div>
          <span>Status</span>
          <strong>{formatReviewStatus(reviewGame?.result)}</strong>
        </div>
        <div>
          <span>Game Mode</span>
          <strong>
            {reviewGame
              ? reviewGame.mode === GAME_MODE_MULTIPLAYER
                ? "Vs Player"
                : `AI - ${formatDifficulty(reviewGame.difficulty || DEFAULT_DIFFICULTY)}`
              : "-"}
          </strong>
        </div>
        <div>
          <span>Room</span>
          <strong>{reviewGame?.id || gameReviewId || "-"}</strong>
        </div>
        <div>
          <span>Move Order</span>
          <strong className={reviewGame?.playerNumber === 1 ? "review-player-first" : reviewGame?.playerNumber === 2 ? "review-player-second" : ""}>
            {reviewGame?.playerNumber === 1 ? "You moved first" : reviewGame?.playerNumber === 2 ? "You moved Second" : "-"}
          </strong>
        </div>
      </section>
      {!reviewLoading && !reviewError && !reviewAnalysisAvailable ? (
        <p className="review-analysis-notice" role="status">
          {reviewAnalysisUnavailableReason || "Move evaluation is temporarily unavailable."}
        </p>
      ) : null}
      {reviewLoading ? (
        <ProfileLoadingSkeleton />
      ) : reviewError ? (
        <strong className="profile-error">{reviewError}</strong>
      ) : reviewMoves.length === 0 ? (
        <section className="profile-empty">
          <strong>No move history available</strong>
        </section>
      ) : (
        <>
          <div className="review-board-layout">
            <button
              className="review-navigation review-navigation-previous"
              type="button"
              onClick={() => setReviewMoveIndex((index) => Math.max(0, index - 1))}
              disabled={reviewMoveIndex === 0}
              aria-label="Previous move"
            >
              &lt;
            </button>
            <div className="board-area review-board-area">
              <div className="board review-board" role="grid" aria-label={`Board after move ${reviewMoves[reviewMoveIndex].move_number}`}>
                {reviewBoard.map((row, rowIndex) => (
                  <div className="review-board-row" role="row" key={`review-board-row-${rowIndex}`}>
                    {row.map((cell, columnIndex) => {
                    const pieceKey = `${rowIndex}-${columnIndex}`;
                    const isDropping = reviewAnimatedPieces.includes(pieceKey);
                    const isWinning = reviewWinningPieces.includes(pieceKey);

                    return (
                      <div
                        key={pieceKey}
                        className={`cell player-${cell}${isDropping ? " dropping" : ""}${isWinning ? " winning" : ""}`}
                        style={isDropping ? { "--drop-start": `-${(rowIndex + 1) * 115}%` } : undefined}
                        role="gridcell"
                        aria-label={`Row ${rowIndex + 1}, column ${columnIndex + 1}`}
                      >
                        <span key={isDropping ? `review-drop-${reviewAnimationRun}` : "review-piece"} />
                      </div>
                    );
                    })}
                  </div>
                ))}
              </div>
            </div>
            <button
              className="review-navigation review-navigation-next"
              type="button"
              onClick={() => setReviewMoveIndex((index) => Math.min(reviewMoves.length - 1, index + 1))}
              disabled={reviewMoveIndex === reviewMoves.length - 1}
              aria-label="Next move"
            >
              &gt;
            </button>
          </div>
          <div className="review-move-list" aria-label="Game moves">
            {splitIntoRows(reviewMoves, 10).map((row, rowIndex) => (
              <div className="review-move-row" key={`review-row-${rowIndex}`}>
                {row.map((move, index) => {
                  const moveIndex = rowIndex * 10 + index;
                  const moveOwner = reviewGame?.playerNumber
                    ? move.player_number === reviewGame.playerNumber ? "You" : "Opponent"
                    : `Player ${move.player_number}`;
                  return (
                    <span className="review-move-entry" key={move.move_number}>
                      <button
                        type="button"
                        className={`review-move-button player-${move.player_number}${moveIndex === reviewMoveIndex ? " current" : ""}`}
                        onClick={() => setReviewMoveIndex(moveIndex)}
                        aria-label={`Show move ${move.move_number} by ${moveOwner}`}
                        aria-current={moveIndex === reviewMoveIndex ? "step" : undefined}
                      >
                        <span>{moveOwner}</span>
                        <span>{move.move_number}</span>
                      </button>
                      {index < row.length - 1 ? <span className="review-move-separator" aria-hidden="true">&gt;</span> : null}
                    </span>
                  );
                })}
              </div>
            ))}
          </div>
          {reviewAnalysisAvailable && reviewAnalysisComplete ? (
            <section className="review-evaluation-log" aria-labelledby="review-evaluation-title">
              <h2 id="review-evaluation-title">Move evaluation</h2>
              <div
                className="review-evaluation-table-wrap"
                role="region"
                aria-label="Move evaluation table"
                tabIndex={0}
              >
                <table className="review-evaluation-table">
                  <caption className="sr-only">
                    Move feedback for each turn
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Turn</th>
                      <th scope="col">Move</th>
                      <th scope="col">Feedback</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reviewMoves.map((move, moveIndex) => {
                      const feedback = getReviewMoveFeedback(move);
                      const isCurrentMove = moveIndex === reviewMoveIndex;
                      return (
                        <tr
                          className={`review-evaluation-row${isCurrentMove ? " current" : ""}`}
                          key={`evaluation-${move.move_number}`}
                          aria-current={isCurrentMove ? "step" : undefined}
                        >
                          <th scope="row" data-label="Turn">Turn {move.move_number}</th>
                          <td data-label="Move">{getReviewMoveLabel(move, reviewGame?.playerNumber)}</td>
                          <td className={!feedback ? "review-evaluation-unavailable" : undefined} data-label="Feedback">
                            {feedback || "Unavailable"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
        </>
      )}
    </section>
  );
  const profileView = (
    <section className="profile-page">
      <div className="profile-header">
        <div>
          <span>Player</span>
          <strong>{accountName}</strong>
        </div>
        <button type="button" onClick={loadProfileGames} disabled={profileLoading}>
          Refresh
        </button>
      </div>
      <section className="profile-stats" aria-label="Profile statistics">
        <div>
          <span>Total played</span>
          <strong>{profileStats.total}</strong>
        </div>
        <div>
          <span>Wins</span>
          <strong>{profileStats.wins}</strong>
        </div>
        <div>
          <span>Losses</span>
          <strong>{profileStats.losses}</strong>
        </div>
        <div>
          <span>Draws</span>
          <strong>{profileStats.draws}</strong>
        </div>
        <div>
          <span>Win rate</span>
          <strong>{profileStats.winRate}</strong>
        </div>
      </section>
      {profileError ? <strong className="profile-error">{profileError}</strong> : null}
      {profileLoading ? (
        <ProfileLoadingSkeleton />
      ) : profileGames.length === 0 ? (
        <section className="profile-empty">
          <strong>No completed games</strong>
        </section>
      ) : (
        <section className="profile-games" aria-label="Completed games">
          {profileGames.map((game) => (
            <article className="profile-game-row" key={game.id}>
              <div>
                <span>{game.mode === GAME_MODE_MULTIPLAYER ? "Vs Player" : `AI - ${formatDifficulty(game.difficulty || DEFAULT_DIFFICULTY)}`}</span>
                <strong>{game.result}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{formatGameStatus(game.status)}</strong>
              </div>
              <div>
                <span>Played</span>
                <strong>{formatDateTime(game.endedAt || game.startedAt)}</strong>
              </div>
              <div className="profile-game-action">
                <button type="button" onClick={() => redirectTo(gameReviewPath(game.id))}>
                  Review Game
                </button>
              </div>
            </article>
          ))}
        </section>
      )}
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
  const actionBar = gameStarted && showingGame ? (
    <section className="game-actionbar" aria-label="Game actions">
      {gameMode !== GAME_MODE_MULTIPLAYER ? (
        <button type="button" onClick={requestNewGame} disabled={busy}>
          Reset
        </button>
      ) : null}
      {canRequestPlayAgain ? (
        <button type="button" onClick={requestPlayAgain} disabled={busy || playAgainRequested}>
          Play again {playAgainAccepted}/2
        </button>
      ) : null}
      {canTogglePublicRoom ? (
        <button
          className="public-room-toggle"
          type="button"
          aria-pressed={isRoomPublic}
          onClick={togglePublicRoom}
          disabled={busy || roomVisibilityPending || roomVisibilityCooldown}
        >
          <span>Make Room Public</span>
          <input type="checkbox" checked={isRoomPublic} readOnly tabIndex={-1} aria-hidden="true" />
        </button>
      ) : null}
      {canLeaveGame ? (
        <button className="leave-game-button" type="button" onClick={leaveGame} disabled={busy}>
          Leave
        </button>
      ) : null}
    </section>
  ) : null;
  const notFoundView = (
    <section className="blank-page not-found-page" aria-label="Page not found">
      <h1>404</h1>
      <p>Page not found. Returning home in 3 seconds.</p>
    </section>
  );
  const legalView = <section className="blank-page" aria-label={routePath === TOS_PATH ? "Terms of Service" : "Privacy Policy"} />;
  let pageView = setupView;
  if (!authReady && (showingSetup || showingGame || showingGameReview || showingProfile || showingAiWaiting)) {
    pageView = loadingView;
  } else if (routePath === LOGIN_PATH || routePath === SIGNUP_PATH) {
    pageView = authPageView;
  } else if (showingGame && !isAuthenticated) {
    pageView = authRequiredView;
  } else if (showingGame) {
    pageView = gameStarted ? gameView : loadingView;
  } else if (showingAiWaiting && !isAuthenticated) {
    pageView = authRequiredView;
  } else if (showingAiWaiting) {
    pageView = aiWaitingView;
  } else if (showingGameReview && !isAuthenticated) {
    pageView = authRequiredView;
  } else if (showingJoin) {
    pageView = joinGameView;
  } else if (showingProfile && !isAuthenticated) {
    pageView = authRequiredView;
  } else if (showingGameReview) {
    pageView = profileReviewView;
  } else if (showingProfile) {
    pageView = profileView;
  } else if (showingLegalPage) {
    pageView = legalView;
  } else if (routePath === NOT_FOUND_PATH) {
    pageView = notFoundView;
  }

  return (
    <div className={`app-shell theme-${theme}`}>
      <header className="site-nav">
        <a className="brand-mark" href={SETUP_PATH}>
          CONNECT 4
        </a>
        {isAuthenticated ? (
          <div className="account-actions">
            <a className="auth-route-link" href={PROFILE_PATH} onClick={(event) => {
              event.preventDefault();
              redirectTo(PROFILE_PATH);
            }}>
              Profile
            </a>
            <button className="theme-toggle-button" type="button" onClick={toggleTheme} aria-label={`Switch to ${themeToggleLabel.toLowerCase()} theme`}>
              {themeToggleLabel}
            </button>
            <span>{accountName}</span>
            <button className="auth-open-button" type="button" onClick={logout}>
              Logout
            </button>
          </div>
        ) : (
          <div className="account-actions">
            <button className="theme-toggle-button" type="button" onClick={toggleTheme} aria-label={`Switch to ${themeToggleLabel.toLowerCase()} theme`}>
              {themeToggleLabel}
            </button>
            <a className="auth-route-link" href={LOGIN_PATH}>
              Login
            </a>
            <button className="auth-open-button" type="button" onClick={() => openAuthModal("login")}>
              Sign up / Login
            </button>
          </div>
        )}
      </header>

      <main className="page-shell">
        {actionBar}
        <section className="game-area">{pageView}</section>
      </main>

      <footer className="site-footer">
        <nav aria-label="Footer">
          <a href={PRIVACY_POLICY_PATH}>Privacy Policy</a>
          <a href={TOS_PATH}>Terms of Service</a>
          <span>Contact</span>
          <span>About</span>
          <a href="https://github.com/Michaeldo2004/Connect4web" target="_blank" rel="noreferrer">
            Repository
          </a>
        </nav>
        <span>Connect4web by Michael D</span>
      </footer>

      {toast ? (
        <div className={`toast toast-${toast.type}`} role="status" aria-live="polite">
          {toast.message}
        </div>
      ) : null}

      {authOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="auth-modal" role="dialog" aria-modal="true" aria-labelledby="auth-modal-title">
            <div className="auth-modal-header">
              <h2 id="auth-modal-title">{authMode === "signup" ? "Create account" : "Login"}</h2>
              <button className="modal-close-button" type="button" onClick={closeAuthModal} aria-label="Close auth popup">
                X
              </button>
            </div>
            {authForm}
          </section>
        </div>
      ) : null}
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
    </div>
  );
}

export default App;
