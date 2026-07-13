import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";

const appSource = readFileSync(join(import.meta.dirname, "..", "src", "App.jsx"), "utf8");
const cssSource = readFileSync(join(import.meta.dirname, "..", "src", "styles.css"), "utf8");

test("manual routes are env-backed with local defaults", () => {
  assert.match(appSource, /function getEnvString\(name, fallback\)/);
  assert.match(appSource, /function getEnvRoute\(name, fallback\)/);
  assert.match(appSource, /const SOCKET_URL = getEnvString\("VITE_BACKEND_URL", "http:\/\/localhost:5000"\)/);
  assert.match(appSource, /const SETUP_PATH = getEnvRoute\("VITE_SETUP_PATH", "\/"\);/);
  assert.match(appSource, /const GAME_PATH = getEnvRoute\("VITE_GAME_PATH", "\/game"\);/);
  assert.match(appSource, /const JOIN_PATH = getEnvRoute\("VITE_JOIN_PATH", "\/join"\);/);
  assert.match(appSource, /const LOGIN_PATH = getEnvRoute\("VITE_LOGIN_PATH", "\/login"\);/);
  assert.match(appSource, /const SIGNUP_PATH = getEnvRoute\("VITE_SIGNUP_PATH", "\/signup"\);/);
  assert.match(appSource, /const PROFILE_PATH = getEnvRoute\("VITE_PROFILE_PATH", "\/profiles"\);/);
  assert.match(appSource, /const AI_WAITING_PATH = getEnvRoute\("VITE_AI_WAITING_PATH", "\/ai\/waiting"\);/);
  assert.match(appSource, /const TOS_PATH = getEnvRoute\("VITE_TOS_PATH", "\/tos"\);/);
  assert.match(appSource, /const PRIVACY_POLICY_PATH = getEnvRoute\("VITE_PRIVACY_POLICY_PATH", "\/privacypolicy"\);/);
  assert.match(appSource, /function isGamePath\(pathname\)/);
  assert.match(appSource, /function gamePath\(gameId\)/);
  assert.match(appSource, /function getRouteGameId\(\)/);
  assert.match(appSource, /pathname\.startsWith\(`\$\{GAME_PATH\}\/`\)/);
  assert.match(appSource, /redirectTo\(gamePath\(data\.gameId\)\)/);
  assert.match(appSource, /APP_PATHS\.has\(window\.location\.pathname\)/);
});

test("supabase auth client and jwt payload are wired", () => {
  assert.match(appSource, /import \{ createClient \} from "@supabase\/supabase-js";/);
  assert.match(appSource, /const supabaseClient = createAuthClient\(\);/);
  assert.match(appSource, /supabaseClient\.auth\.signUp/);
  assert.match(appSource, /supabaseClient\.auth\.signInWithPassword/);
  assert.match(appSource, /supabaseClient\.auth\.signOut/);
  assert.match(appSource, /accessToken: authSession\?\.access_token \|\| ""/);
});

test("profile route loads username and completed games", () => {
  assert.match(appSource, /const \[userProfile, setUserProfile\] = useState\(null\);/);
  assert.match(appSource, /const \[profileGames, setProfileGames\] = useState\(\[\]\);/);
  assert.match(appSource, /const profileStats = useMemo/);
  assert.match(appSource, /function formatWinRate\(wins, total\)/);
  assert.match(appSource, /from\("profiles"\)/);
  assert.match(appSource, /select\("username,display_name"\)/);
  assert.match(appSource, /api\/profile\/games/);
  assert.match(appSource, /Authorization: `Bearer \$\{authSession\.access_token\}`/);
  assert.match(appSource, /setProfileError\(error\.message\)/);
  assert.match(appSource, /formatGameStatus\(game\.status\)/);
  assert.match(appSource, /human_win: "Player Wins"/);
  assert.match(appSource, /formatDateTime\(game\.endedAt \|\| game\.startedAt\)/);
  assert.match(appSource, /className="profile-page"/);
  assert.match(appSource, /className="profile-stats"/);
  assert.match(appSource, />\s*Total played\s*</);
  assert.match(appSource, />\s*Win rate\s*</);
  assert.match(appSource, /No completed games/);
  assert.match(appSource, /accountName/);
});

test("profile games open a dedicated move review", () => {
  assert.match(appSource, /const GAME_REVIEW_PATH = `\$\{GAME_PATH\}\/review`;/);
  assert.match(appSource, /function isGameReviewPath\(pathname\)/);
  assert.match(appSource, /function gameReviewPath\(gameId\)/);
  assert.match(appSource, /function getGameReviewId\(\)/);
  assert.match(appSource, /const NOT_FOUND_PATH = "\/404";/);
  assert.match(appSource, /redirectTo\(NOT_FOUND_PATH, true\)/);
  assert.match(appSource, /Returning home in 3 seconds/);
  assert.match(appSource, /setTimeout\(\(\) => redirectTo\(SETUP_PATH, true\), 3000\)/);
  assert.match(appSource, /api\/profile\/games\/\$\{encodeURIComponent\(selectedGameId\)\}\/moves/);
  assert.match(appSource, /GAME_REVIEW_PATH/);
  assert.match(appSource, />\s*Review Game\s*</);
  assert.match(appSource, />\s*Back to games\s*</);
  assert.match(appSource, /aria-label="Previous move"/);
  assert.match(appSource, /aria-label="Next move"/);
  assert.match(appSource, /splitIntoRows\(reviewMoves, 10\)/);
  assert.match(appSource, /className=\{`review-move-button player-\$\{move\.player_number\}/);
  assert.match(appSource, /board review-board/);
  assert.match(appSource, /reviewAnimatedPieces/);
  assert.match(appSource, /reviewWinningPieces/);
  assert.match(appSource, /findChangedPieces\(previousBoard, currentBoard\)/);
  assert.match(appSource, /reviewMoveIndex === reviewMoves\.length - 1 \? findWinningPieces\(currentBoard\)/);
  assert.match(appSource, /drop-start/);
  assert.match(appSource, /review-drop-\$\{reviewAnimationRun\}/);
  assert.match(appSource, /function formatReviewStatus\(result\)/);
  assert.match(appSource, /You Win/);
  assert.match(appSource, /You Lost/);
  assert.match(appSource, /return "Tie"/);
  assert.match(appSource, /className="status-panel review-status-panel"/);
  assert.match(appSource, /You moved first/);
  assert.match(appSource, /You moved Second/);
  assert.match(appSource, /review-player-first/);
  assert.match(appSource, /review-player-second/);
  assert.match(appSource, /move\.player_number === reviewGame\.playerNumber \? "You" : "Opponent"/);
  assert.match(appSource, /<span>\{move\.move_number\}<\/span>/);
});

test("join route supports public room discovery", () => {
  assert.match(appSource, /const \[publicGames, setPublicGames\] = useState\(\[\]\);/);
  assert.match(appSource, /const \[joiningPublicGameId, setJoiningPublicGameId\] = useState\(""\);/);
  assert.match(appSource, /socketClient\.emit\("list_public_games", authPayload\(\)\)/);
  assert.match(appSource, /nextSocket\.on\("public_games", handlePublicGames\)/);
  assert.match(appSource, /className="public-games-panel"/);
  assert.match(appSource, />\s*Joinable games\s*</);
  assert.match(appSource, /Attempting to join\.\.\./);
  assert.match(appSource, /joinPublicGame\(publicGame\.gameId\)/);
  assert.match(appSource, /joinMultiplayerGame\(publicGameId, true\)/);
});

test("waiting multiplayer room can be made public", () => {
  assert.match(appSource, /const \[isRoomPublic, setIsRoomPublic\] = useState\(false\);/);
  assert.match(appSource, /socketClient\.emit\("set_room_public", authPayload\(\{ gameId, playerId, public: !isRoomPublic \}\)\)/);
  assert.match(appSource, /className="public-room-toggle"/);
  assert.match(appSource, />\s*Make Room Public\s*</);
  assert.match(appSource, /aria-pressed=\{isRoomPublic\}/);
  assert.match(appSource, /checked=\{isRoomPublic\} readOnly/);
});

test("live multiplayer results use relative winner messaging", () => {
  assert.match(appSource, /displayMessage = "You Won!"/);
  assert.match(appSource, /multiplayerPlayerNames\[String\(winnerNumber\)\]/);
  assert.match(appSource, /\} won`/);
});

test("board updates can replace stored game id", () => {
  assert.match(appSource, /async function handleBoardUpdated\(data\)/);
  assert.match(appSource, /setGameId\(data\.gameId\)/);
  assert.match(appSource, /setPlayerId\(data\.playerId\)/);
  assert.match(appSource, /saveMultiplayerSession\(data\.gameId, data\.playerId\)/);
  assert.match(appSource, /saveSession\(data\.gameId, data\.playerId\)/);
  assert.match(appSource, /redirectTo\(gamePath\(data\.gameId\), true\)/);
});

test("AI rooms persist across refresh and ignore intermediate optimistic acknowledgements", () => {
  assert.match(appSource, /pendingMove && data\.mode !== GAME_MODE_MULTIPLAYER && data\.aiThinking/);
  assert.match(appSource, /saveSession\(data\.gameId, joinedPlayerId\)/);
  assert.match(appSource, /Boolean\(data\.aiThinking\)/);
  assert.match(appSource, /nextSocket\.emit\("join_game", authPayload\(storedSession\)\)/);
  assert.match(appSource, /const \[aiQueuePosition, setAiQueuePosition\] = useState\(0\)/);
  assert.match(appSource, /AI queued - position \$\{aiQueuePosition\}/);
});

test("AI admission waiting room persists and checks position every 20 seconds", () => {
  assert.match(appSource, /const AI_WAITING_KEY = "connect4_ai_waiting"/);
  assert.match(appSource, /function saveAiWaitingSession/);
  assert.match(appSource, /nextSocket\.emit\("check_ai_waiting", authPayload\(waitingSession\)\)/);
  assert.match(appSource, /window\.setInterval\(checkWaitingRoom, 20000\)/);
  assert.match(appSource, /AI player is currently busy right now\./);
  assert.match(appSource, /Your position in line:/);
  assert.match(appSource, /\(checked every 20s\)/);
  assert.match(appSource, /cancel_ai_waiting/);
  assert.match(cssSource, /\.ai-waiting-panel/);
});

test("nav shell includes brand and auth entry points", () => {
  assert.match(appSource, /className="site-nav"/);
  assert.match(appSource, /className="brand-mark"/);
  assert.match(appSource, />\s*CONNECT 4\s*</);
  assert.match(appSource, /const THEME_KEY = "connect4_theme";/);
  assert.match(appSource, /const \[theme, setTheme\] = useState\(getInitialTheme\);/);
  assert.match(appSource, /className=\{`app-shell theme-\$\{theme\}`\}/);
  assert.match(appSource, /className="theme-toggle-button"/);
  assert.match(appSource, /Switch to \$\{themeToggleLabel\.toLowerCase\(\)\} theme/);
  assert.match(appSource, /Sign up \/ Login/);
  assert.match(appSource, /openAuthModal\("login"\)/);
});

test("loading states use skeleton views", () => {
  assert.match(appSource, /function SkeletonBlock/);
  assert.match(appSource, /function GameLoadingSkeleton/);
  assert.match(appSource, /function ProfileLoadingSkeleton/);
  assert.match(appSource, /function PublicRoomsSkeleton/);
  assert.match(appSource, /<GameLoadingSkeleton message=\{message\} \/>/);
  assert.match(appSource, /<ProfileLoadingSkeleton \/>/);
  assert.match(appSource, /<PublicRoomsSkeleton \/>/);
  assert.match(cssSource, /\.skeleton-block/);
  assert.match(cssSource, /@keyframes skeleton-pulse/);
});

test("setup view explains modes and confirms the current selection", () => {
  assert.match(appSource, />Choose how you want to play</);
  assert.match(appSource, /className="setup-selection" aria-live="polite"/);
  assert.match(appSource, />Share a room and play live</);
  assert.match(appSource, /difficulty\.hint/);
  assert.match(appSource, /className="setup-tips"/);
  assert.match(cssSource, /\.setup-heading/);
  assert.match(cssSource, /\.setup-selection/);
  assert.match(cssSource, /\.setup-tips/);
});

test("auth modal supports login, signup, and close states", () => {
  assert.match(appSource, /const \[authOpen, setAuthOpen\] = useState\(false\);/);
  assert.match(appSource, /const \[authMode, setAuthMode\] = useState\("login"\);/);
  assert.match(appSource, /const \[authFields, setAuthFields\] = useState/);
  assert.match(appSource, /const \[authSession, setAuthSession\] = useState\(null\);/);
  assert.match(appSource, /role="dialog"/);
  assert.match(appSource, /aria-label="Close auth popup"/);
  assert.match(appSource, /name="username"/);
  assert.match(appSource, /name="email"/);
  assert.match(appSource, /name="password"/);
  assert.match(appSource, /submitAuthForm/);
});

test("input sanitizers preserve valid username numbers and strip unsafe characters", () => {
  assert.match(appSource, /function isTextEntryTarget\(target\)/);
  assert.match(appSource, /if \(isTextEntryTarget\(event\.target\)\) \{\s*return;\s*\}/);
  assert.match(appSource, /function sanitizeRoomIdInput\(value\)/);
  assert.match(appSource, /function sanitizeUsernameInput\(value\)/);
  assert.match(appSource, /function sanitizeEmailInput\(value\)/);
  assert.match(appSource, /function sanitizePasswordInput\(value\)/);
  assert.match(appSource, /replace\(\/\[\^A-Za-z0-9_\]\//);
  assert.match(appSource, /replace\(\/\[\^A-Za-z0-9_-\]\//);
  assert.match(appSource, /replace\(\/\[\\s<>"\]\//);
  assert.match(appSource, /maxLength=\{USERNAME_MAX_LENGTH\}/);
  assert.match(appSource, /onChange=\{\(event\) => updateAuthField\("username", event\.target\.value\)\}/);
});

test("footer links point to placeholder legal routes", () => {
  assert.match(appSource, /className="site-footer"/);
  assert.match(appSource, /href=\{PRIVACY_POLICY_PATH\}/);
  assert.match(appSource, /href=\{TOS_PATH\}/);
  assert.match(appSource, /Privacy Policy/);
  assert.match(appSource, /Terms of Service/);
  assert.match(appSource, /https:\/\/github\.com\/Michaeldo2004\/Connect4web/);
  assert.match(appSource, /target="_blank"/);
  assert.match(appSource, /rel="noreferrer"/);
  assert.match(appSource, /Repository/);
  assert.match(appSource, /Connect4web by Michael D/);
});

test("connect 4 theme and mobile layout rules exist", () => {
  assert.match(cssSource, /--brand-blue:/);
  assert.match(cssSource, /--brand-red:/);
  assert.match(cssSource, /--brand-yellow:/);
  assert.match(cssSource, /\.theme-dark\s*\{/);
  assert.match(cssSource, /\.theme-toggle-button\s*\{/);
  assert.match(cssSource, /\.brand-mark\s*\{/);
  assert.match(cssSource, /\.profile-page\s*\{/);
  assert.match(cssSource, /\.profile-game-row\s*\{/);
  assert.match(cssSource, /\.review-board-layout\s*\{/);
  assert.match(cssSource, /\.review-move-row\s*\{/);
  assert.match(cssSource, /\.review-move-button\.current\s*\{/);
  assert.match(cssSource, /color: var\(--brand-yellow\);/);
  assert.match(cssSource, /@media \(max-width: 760px\)/);
  assert.match(cssSource, /\.play-layout\s*\{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(cssSource, /width: min\(100%, calc\(100vw - 28px\)\);/);
});
