import { test, expect, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";

const artifacts = process.env.SCOPESHIELD_E2E_ARTIFACTS || "../.local/day17";
async function login(page: Page) {
  const password = process.env.SCOPESHIELD_E2E_PASSWORD;
  if (!password) throw new Error("Use the isolated browser test runner");
  await page.goto("/demos");
  await page.getByLabel("Username", { exact: true }).fill("qa.conversation");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Offline demo scenarios" })).toBeVisible();
}
async function reviewer(scope: Page | Locator) {
  await scope.getByLabel("Reviewer name", { exact: true }).fill("Synthetic conversation reviewer");
  await scope.getByLabel("Review note", { exact: true }).fill("Reviewed the contract and exact message sources");
}
async function decide(page: Page, label: string, acknowledge = true) {
  await page.getByRole("button", { name: label, exact: true }).click();
  const dialog = page.getByRole("dialog");
  await reviewer(dialog);
  if (acknowledge) await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: `Confirm ${label.toLowerCase()}`, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function load(page: Page, title: string) {
  await page.getByRole("button", { name: `Load ${title}`, exact: true }).click();
  await expect(page.getByRole("region", { name: "Saved conversation" })).toBeVisible();
  await reviewer(page);
  await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  await expect(page.getByText("Offline / mocked response", { exact: true })).toBeVisible();
  await expect(page.getByText("What changed across the conversation", { exact: true })).toBeVisible();
}

test("conversation expansion: exact evidence, human correction, explicit terms, approval and accessibility", async ({ page }) => {
  test.setTimeout(150_000);
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  await login(page); await load(page, "B · Gradual scope expansion");
  const sources = page.getByRole("region", { name: "Saved conversation" });
  await expect(sources.getByText(/Manually entered, unauthenticated/)).toBeVisible();
  await expect(sources.getByText("Could you also add a small FAQ section?", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified source text", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Prepare review draft", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Locate in agreement", exact: true }).first().click();
  await expect(page.locator("#source-highlight")).toHaveText("Additional sections and a content management system are excluded from the agreed deliverables.");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  await fs.mkdir(artifacts, { recursive: true });
  await fs.writeFile(`${artifacts}/day17-conversation-axe.json`, JSON.stringify(results, null, 2));
  expect(results.violations.map((item) => item.id)).toEqual([]);
  await sources.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${artifacts}/day17-conversation-sources.png`, fullPage: false });
  await page.getByText("What changed across the conversation", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${artifacts}/day17-cumulative-review.png`, fullPage: false });
  await page.getByRole("button", { name: "Edit assessment", exact: true }).click();
  const editor = page.getByRole("dialog");
  await editor.getByLabel("Classification", { exact: true }).first().selectOption("AMBIGUOUS");
  await editor.getByLabel("Explanation", { exact: true }).first().fill("Clarify whether the FAQ replaces an existing agreed section.");
  await editor.getByLabel("Uncertainties (one per line)", { exact: true }).first().fill("Confirm the intended section boundary.");
  await reviewer(editor);
  await editor.getByRole("button", { name: "Save reviewed assessment", exact: true }).click();
  await expect(editor).not.toBeVisible();
  await expect(page.getByText("Human edited", { exact: true })).toBeVisible();
  await decide(page, "Approve assessment");
  await expect(page.getByLabel("Proposed additional deliverables")).toHaveValue("");
  await expect(page.getByLabel("Proposed price (optional)")).toHaveValue("");
  await page.getByLabel("Proposed additional deliverables").fill("Additional FAQ section and editing system, subject to agreed acceptance criteria");
  await reviewer(page);
  await page.getByRole("button", { name: "Prepare review draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Draft change order", exact: true })).toBeVisible();
  await decide(page, "Approve change order");
  expect(errors).toEqual([]);
});

test("included revisions remain in scope and duplicates are explicitly flagged", async ({ page }) => {
  await login(page); await load(page, "A · Mostly included revisions");
  await page.getByText(/Review flags \(/).click();
  await expect(page.getByText(/EXACT DUPLICATE DO NOT DOUBLE COUNT/i)).toBeVisible();
  await decide(page, "Approve assessment");
  await expect(page.getByRole("heading", { name: "Prepare a change order", exact: true })).toHaveCount(0);
});

test("ambiguous conflicting directions can be rejected without creating a change order", async ({ page }) => {
  await login(page); await load(page, "C · Clarify before agreeing");
  await expect(page.getByText("Conflicting · Revisions", { exact: true })).toBeVisible();
  await decide(page, "Reject assessment", false);
  await expect(page.getByRole("button", { name: "Approve assessment", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Prepare review draft", exact: true })).toHaveCount(0);
});

test("manual message entry preserves exact text, optional dates and stable IDs across reordering", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "Load B · Gradual scope expansion", exact: true }).click();
  await expect(page.getByRole("region", { name: "Saved conversation" })).toBeVisible();
  await page.getByRole("button", { name: "Add conversation", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Conversation title").fill("Manual synthetic launch discussion");
  await dialog.getByLabel("Sender 1", { exact: true }).fill("Synthetic client");
  const exact = "  Please revise\n  the café headline 👩🏽‍💻.  ";
  await dialog.getByLabel("Message text 1", { exact: true }).fill(exact);
  await dialog.getByRole("button", { name: "Add another message", exact: true }).click();
  await dialog.getByLabel("Sender 2", { exact: true }).fill("Synthetic client");
  await dialog.getByLabel("Message text 2", { exact: true }).fill("Could we also review the launch support boundary?");
  await reviewer(dialog);
  await dialog.getByLabel("Conversation title").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${artifacts}/day17-manual-entry.png`, fullPage: false });
  const creation = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/change-requests"));
  await dialog.getByRole("button", { name: "Save conversation", exact: true }).click();
  const saved = await (await creation).json();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole("heading", { name: "Manual synthetic launch discussion", exact: true })).toBeVisible();
  expect(saved.conversation_messages[0].content).toBe(exact);
  expect(saved.conversation_messages[0].sent_at).toBeNull();
  await page.getByRole("button", { name: "Edit conversation", exact: true }).click();
  await dialog.getByRole("button", { name: "Move message 2 earlier", exact: true }).click();
  await reviewer(dialog);
  const edit = page.waitForResponse((response) => response.request().method() === "PATCH" && response.url().includes(`/change-requests/${saved.id}`));
  await dialog.getByRole("button", { name: "Save conversation and reset analysis", exact: true }).click();
  const reordered = await (await edit).json();
  expect(reordered.conversation_messages.map((item: { id: string }) => item.id)).toEqual(saved.conversation_messages.map((item: { id: string }) => item.id).reverse());
  await expect(dialog).not.toBeVisible();
  await page.reload();
  await expect(page.getByRole("region", { name: "Saved conversation" }).locator("pre").nth(1)).toHaveText(exact);
  await reviewer(page);
  await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Choose a compatible offline example" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Saved conversation" }).locator("pre").nth(1)).toHaveText(exact);
});
