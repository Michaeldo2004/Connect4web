import { expect, test } from "@playwright/test";

const primaryEmail = process.env.E2E_EMAIL;
const primaryPassword = process.env.E2E_PASSWORD;
const secondEmail = process.env.E2E_SECOND_EMAIL;
const secondPassword = process.env.E2E_SECOND_PASSWORD;

async function login(page, email, password) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Login", exact: true }).click();
  await expect(page.getByRole("button", { name: "Create game" })).toBeVisible();
}

test("authenticated player starts an AI match and makes a move", async ({ page }) => {
  test.skip(!primaryEmail || !primaryPassword, "Set E2E_EMAIL and E2E_PASSWORD to run authenticated E2E tests.");
  await login(page, primaryEmail, primaryPassword);
  await page.getByRole("tab", { name: /Medium/ }).click();
  await page.getByRole("button", { name: "Create game" }).click();
  await expect(page.getByRole("grid", { name: "Connect 4 board" })).toBeVisible();
  const playableColumn = page
    .getByRole("button", { name: /Drop in column/ })
    .filter({ visible: true })
    .first();
  await expect(playableColumn).toBeEnabled({ timeout: 15000 });
  await playableColumn.click();
  await expect(page.locator(".move-history-list li")).toHaveCount(1);
});

test("multiplayer room survives a browser reconnect", async ({ browser }) => {
  test.skip(
    !primaryEmail || !primaryPassword || !secondEmail || !secondPassword,
    "Set both E2E player credential pairs to run multiplayer E2E tests.",
  );
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  await login(host, primaryEmail, primaryPassword);
  await login(opponent, secondEmail, secondPassword);
  await host.getByRole("button", { name: /Vs Player/ }).click();
  await host.getByRole("button", { name: "Create game" }).click();
  await expect(host.getByRole("grid", { name: "Connect 4 board" })).toBeVisible();
  const roomId = await host.locator(".match-details code").textContent();
  await opponent.goto(`/join?room=${encodeURIComponent(roomId)}`);
  await opponent.getByRole("button", { name: "Join", exact: true }).click();
  await expect(opponent.getByRole("grid", { name: "Connect 4 board" })).toBeVisible();
  await opponent.reload();
  await expect(opponent.getByRole("grid", { name: "Connect 4 board" })).toBeVisible({ timeout: 15000 });
  await hostContext.close();
  await opponentContext.close();
});

test("authenticated player opens a completed-game review", async ({ page }) => {
  test.skip(!primaryEmail || !primaryPassword, "Set E2E_EMAIL and E2E_PASSWORD to run authenticated E2E tests.");
  await login(page, primaryEmail, primaryPassword);
  await page.getByRole("link", { name: "Profile" }).click();
  const reviewButton = page.getByRole("button", { name: "Review Game" }).first();
  test.skip(!(await reviewButton.isVisible()), "The E2E account has no completed game to review.");
  await reviewButton.click();
  await expect(page.getByRole("grid", { name: /Board after move/ })).toBeVisible();
  await expect(page.getByLabel("Game moves")).toBeVisible();
});
