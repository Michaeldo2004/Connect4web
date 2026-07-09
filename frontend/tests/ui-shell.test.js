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
  assert.match(appSource, /const LOGIN_PATH = getEnvRoute\("VITE_LOGIN_PATH", "\/login"\);/);
  assert.match(appSource, /const SIGNUP_PATH = getEnvRoute\("VITE_SIGNUP_PATH", "\/signup"\);/);
  assert.match(appSource, /const PROFILE_PATH = getEnvRoute\("VITE_PROFILE_PATH", "\/profiles"\);/);
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
  assert.match(appSource, /from\("profiles"\)/);
  assert.match(appSource, /select\("username,display_name"\)/);
  assert.match(appSource, /api\/profile\/games/);
  assert.match(appSource, /Authorization: `Bearer \$\{authSession\.access_token\}`/);
  assert.match(appSource, /setProfileError\(error\.message\)/);
  assert.match(appSource, /formatGameStatus\(game\.status\)/);
  assert.match(appSource, /formatDateTime\(game\.endedAt \|\| game\.startedAt\)/);
  assert.match(appSource, /className="profile-page"/);
  assert.match(appSource, /No completed games/);
  assert.match(appSource, /accountName/);
});

test("board updates can replace stored game id", () => {
  assert.match(appSource, /async function handleBoardUpdated\(data\)/);
  assert.match(appSource, /setGameId\(data\.gameId\)/);
  assert.match(appSource, /setPlayerId\(data\.playerId\)/);
  assert.match(appSource, /saveMultiplayerSession\(data\.gameId, data\.playerId\)/);
  assert.match(appSource, /saveSession\(data\.gameId, data\.playerId\)/);
  assert.match(appSource, /redirectTo\(gamePath\(data\.gameId\), true\)/);
});

test("nav shell includes brand and auth entry points", () => {
  assert.match(appSource, /className="site-nav"/);
  assert.match(appSource, /className="brand-mark"/);
  assert.match(appSource, />\s*CONNECT 4\s*</);
  assert.match(appSource, /Sign up \/ Login/);
  assert.match(appSource, /openAuthModal\("login"\)/);
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
  assert.match(cssSource, /\.brand-mark\s*\{/);
  assert.match(cssSource, /\.profile-page\s*\{/);
  assert.match(cssSource, /\.profile-game-row\s*\{/);
  assert.match(cssSource, /color: var\(--brand-yellow\);/);
  assert.match(cssSource, /@media \(max-width: 760px\)/);
  assert.match(cssSource, /\.play-layout\s*\{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(cssSource, /width: min\(100%, calc\(100vw - 28px\)\);/);
});
