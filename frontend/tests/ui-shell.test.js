import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";

const appSource = readFileSync(join(import.meta.dirname, "..", "src", "App.jsx"), "utf8");
const cssSource = readFileSync(join(import.meta.dirname, "..", "src", "styles.css"), "utf8");

test("manual routes include game and placeholder legal pages", () => {
  assert.match(appSource, /const SETUP_PATH = "\/";/);
  assert.match(appSource, /const GAME_PATH = "\/game";/);
  assert.match(appSource, /const TOS_PATH = "\/tos";/);
  assert.match(appSource, /const PRIVACY_POLICY_PATH = "\/privacypolicy";/);
  assert.match(appSource, /APP_PATHS\.has\(window\.location\.pathname\)/);
});

test("nav shell includes brand and placeholder auth button", () => {
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
  assert.match(appSource, /role="dialog"/);
  assert.match(appSource, /aria-label="Close auth popup"/);
  assert.match(appSource, /name="username"/);
  assert.match(appSource, /name="email"/);
  assert.match(appSource, /name="password"/);
  assert.match(appSource, /submitAuthPlaceholder/);
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
});

test("connect 4 theme and mobile layout rules exist", () => {
  assert.match(cssSource, /--brand-blue:/);
  assert.match(cssSource, /--brand-red:/);
  assert.match(cssSource, /--brand-yellow:/);
  assert.match(cssSource, /\.brand-mark\s*\{/);
  assert.match(cssSource, /color: var\(--brand-yellow\);/);
  assert.match(cssSource, /@media \(max-width: 760px\)/);
  assert.match(cssSource, /\.play-layout\s*\{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(cssSource, /width: min\(100%, calc\(100vw - 28px\)\);/);
});
