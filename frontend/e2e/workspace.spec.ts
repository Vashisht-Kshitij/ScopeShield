import { test, expect, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";

const sample = fileURLToPath(new URL("../public/samples/day4_freelance_agreement.txt", import.meta.url));
const artifacts = process.env.SCOPESHIELD_E2E_ARTIFACTS || fileURLToPath(new URL("../../.local/day9/", import.meta.url));

async function login(page: Page, username = "qa.freelancer") {
  const password = process.env.SCOPESHIELD_E2E_PASSWORD;
  if (!password) throw new Error("Run python -m scripts.run_browser_tests to provision temporary accounts");
  await page.getByLabel("Username", { exact: true }).fill(username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
}
async function reviewer(scope: Page | Locator, note = "Reviewed synthetic source evidence") {
  await scope.getByLabel("Reviewer name", { exact: true }).fill("Synthetic browser reviewer");
  await scope.getByLabel("Review note", { exact: true }).fill(note);
}
async function confirm(page: Page, label: string) {
  await page.getByRole("button", { name: label, exact: true }).click();
  const dialog = page.getByRole("dialog");
  await reviewer(dialog);
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: `Confirm ${label.toLowerCase()}`, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function audit(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  await fs.mkdir(artifacts, { recursive: true });
  await fs.writeFile(`${artifacts}/axe-${name}.json`, JSON.stringify(results, null, 2));
  expect.soft(results.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((row) => row.target) }))).toEqual([]);
}

test("login labels, keyboard focus, validation, accessibility and logout", async ({ page }) => {
  await page.goto("/contracts");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Username", { exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
  await audit(page, "login");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Username", { exact: true })).toBeFocused();
  await login(page);
  await expect(page.getByRole("link", { name: "Contracts", exact: true })).toHaveAttribute("aria-current", "page");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
});

test("authenticated upload, review, real revision conflict, scope evidence, order, client response and audit", async ({ page, context }) => {
  test.setTimeout(150_000);
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await page.goto("/contracts"); await login(page);
  await audit(page, "catalog");
  await page.getByRole("button", { name: "Upload agreement", exact: true }).first().click();
  const upload = page.getByRole("dialog");
  await upload.getByLabel("Agreement file").setInputFiles(sample);
  await audit(page, "upload-dialog");
  const stored = page.waitForResponse((response) => response.url().endsWith("/api/contracts") && response.request().method() === "POST");
  await upload.getByRole("button", { name: "Upload agreement", exact: true }).click();
  const uploaded = await (await stored).json();
  await expect(page.getByRole("heading", { name: "day4_freelance_agreement.txt", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Extract agreement", exact: true }).click();
  await page.getByRole("dialog").getByLabel("Analysis provider").selectOption("offline_demo");
  await page.getByRole("dialog").getByRole("button", { name: "Run extraction", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Planning and shot list", exact: true })).toBeVisible();
  await expect(page.getByText("Offline / mocked extraction", { exact: true }).first()).toBeVisible();
  await page.getByLabel("Description", { exact: true }).fill("Stale unsaved browser edit");
  const otherTab = await context.newPage();
  await otherTab.goto(`/contracts/${uploaded.id}`);
  await otherTab.getByLabel("Description", { exact: true }).fill("Concurrent reviewed browser description");
  await reviewer(otherTab, "Save a concurrent synthetic revision");
  const saved = otherTab.waitForResponse((response) => response.request().method() === "PATCH" && response.url().includes("/api/milestones/"));
  await otherTab.getByRole("button", { name: "Save changes for review", exact: true }).click();
  expect((await saved).status()).toBe(200);
  await expect(otherTab.getByRole("button", { name: "Save changes for review", exact: true })).toBeDisabled();
  await otherTab.close();
  await reviewer(page);
  await page.getByRole("button", { name: "Save changes for review", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Review the latest record before continuing" })).toContainText("Review the latest record before continuing");
  await expect(page.getByLabel("Description", { exact: true })).toHaveValue("Stale unsaved browser edit");
  await page.getByRole("button", { name: "Refresh latest record", exact: true }).click();
  await expect(page.getByLabel("Description", { exact: true })).toHaveValue("Concurrent reviewed browser description");
  await confirm(page, "Approve milestone");
  await expect(page.getByRole("button", { name: "Reopen milestone", exact: true })).toBeVisible();
  await audit(page, "milestone");
  await page.getByRole("tab", { name: "Scope changes", exact: true }).click();
  await page.getByRole("button", { name: "New scope request", exact: true }).first().click();
  const requestDialog = page.getByRole("dialog");
  await requestDialog.getByLabel("Request title").fill("Synthetic product video proposal");
  await requestDialog.getByLabel("Requested work", { exact: true }).fill("Add a product launch video.");
  await reviewer(requestDialog);
  await requestDialog.getByRole("button", { name: "Save client request", exact: true }).click();
  await expect(requestDialog).not.toBeVisible();
  await page.getByLabel("Analysis provider").selectOption("offline_demo");
  await reviewer(page);
  await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  await expect(page.getByText("Offline / mocked response", { exact: true })).toBeVisible();
  await expect(page.getByText("offline_synthetic_scope_fixture", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Locate in agreement", exact: true }).click();
  await expect(page.locator("#source-highlight")).toHaveText("Changes beyond these deliverables require separately agreed scope and fees.");
  await audit(page, "scope");
  await page.screenshot({ path: `${artifacts}/authenticated-scope.jpg`, fullPage: false });
  await confirm(page, "Approve assessment");
  await page.getByLabel("Proposed price (optional)").fill("125.10");
  await page.getByLabel("Proposed deadline (optional)").fill("2026-12-01");
  await reviewer(page);
  await page.getByRole("button", { name: "Prepare review draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Draft change order", exact: true })).toBeVisible();
  await audit(page, "order");
  await confirm(page, "Approve change order");
  await page.getByText("Client access to this project", { exact: true }).click();
  await page.getByLabel("Client username", { exact: true }).fill("qa.client");
  await page.getByRole("checkbox", { name: "I reviewed the project contents and intend to share them with this client." }).check();
  await page.getByRole("button", { name: "Share project", exact: true }).click();
  await expect(page.getByText("Project shared with the selected client.", { exact: true })).toBeVisible();
  const activityResponse = page.waitForResponse((response) => response.url().endsWith(`/contracts/${uploaded.id}/activity`) && response.request().method() === "GET");
  await page.getByRole("tab", { name: "Activity", exact: true }).click();
  const activityResult = await activityResponse;
  // The complete suite shares an account across fast workflows and can consume
  // its real per-minute QUERY allowance. Honor admission; never replay a mutation.
  if (activityResult.status() === 429) {
    const seconds = Number(activityResult.headers()["retry-after"]);
    expect(Number.isInteger(seconds) && seconds >= 1 && seconds <= 60).toBe(true);
    await page.waitForTimeout(seconds * 1000 + 100);
    const retry = page.waitForResponse((response) => response.url().endsWith(`/contracts/${uploaded.id}/activity`) && response.request().method() === "GET");
    await page.getByRole("tabpanel", { name: "Activity", exact: true }).getByRole("button", { name: "Try loading again", exact: true }).click();
    expect((await retry).status()).toBe(200);
  } else expect(activityResult.status()).toBe(200);
  await expect(page.getByText("Milestone approved", { exact: true })).toBeVisible();
  await audit(page, "audit");
  await page.screenshot({ path: `${artifacts}/authenticated-audit.jpg`, fullPage: false });
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await login(page, "qa.other");
  await page.goto(`/contracts/${uploaded.id}`);
  await expect(page.getByRole("alert").filter({ hasText: "Resource not found" })).toContainText("Resource not found");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await login(page, "qa.client");
  await expect(page.getByRole("heading", { name: "Shared projects", exact: true })).toBeVisible();
  await expect(page.getByText(/Planning and shot list.*USD 600\.00/)).toBeVisible();
  await page.getByRole("button", { name: "Approve proposed order", exact: true }).click();
  const responseDialog = page.getByRole("dialog");
  await responseDialog.getByLabel("Response note").fill("Reviewed synthetic proposed work and terms");
  await responseDialog.getByRole("checkbox").check();
  await responseDialog.getByRole("button", { name: "Confirm response", exact: true }).click();
  await expect(responseDialog).not.toBeVisible();
  await expect(page.getByText("Reviewed synthetic proposed work and terms", { exact: true })).toBeVisible();
  await audit(page, "client");
  await page.screenshot({ path: `${artifacts}/client-response.jpg`, fullPage: false });
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await login(page);
  await page.goto(`/contracts/${uploaded.id}?tab=activity`);
  await expect(page.getByText("Client order approved", { exact: true })).toBeVisible();
  expect(consoleErrors).toEqual([]);
  await fs.writeFile(`${artifacts}/browser-demo.json`, JSON.stringify({ contract_id: uploaded.id, synthetic_only: true, live_ai: false, workflow: "login/upload/extract/revision-conflict/milestone-approve/scope/evidence/order-approve/share/client-response/audit", console_errors: consoleErrors }, null, 2));
});

test("mobile navigation, keyboard dismissal and no horizontal page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await login(page);
  const toggle = page.getByRole("button", { name: "Toggle navigation", exact: true });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await audit(page, "mobile-overview");
  await page.screenshot({ path: `${artifacts}/authenticated-mobile.jpg`, fullPage: false });
});

test("logging out clears private content from another open tab", async ({ page, context }) => {
  await page.goto("/contracts"); await login(page);
  const other = await context.newPage();
  await other.goto("/contracts");
  await expect(other.getByLabel("Search agreements")).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(other.getByRole("heading", { name: "Welcome back", exact: true })).toBeVisible();
  await expect(other.getByLabel("Search agreements")).not.toBeAttached();
});
