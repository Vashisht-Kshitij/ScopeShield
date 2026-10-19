import { test, expect, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";

const messages = [
  "Please update the headline wording during the first revision round.",
  "Could you also add a small FAQ section?",
  "Please give our team a content management system to edit the page.",
  "Can you deliver on 2026-11-12 instead?",
  "Please keep the page updated after launch and cover weekend support.",
];
const artifacts = process.env.SCOPESHIELD_E2E_ARTIFACTS!;
async function review(scope: Page | Locator) {
  await scope.getByLabel("Reviewer name", { exact: true }).fill("Synthetic release reviewer");
  await scope.getByLabel("Review note", { exact: true }).fill("Synthetic rehearsal decision; not real client acceptance or payment.");
}
async function approve(page: Page, action: string) {
  await page.getByRole("button", { name: action, exact: true }).click();
  const dialog = page.getByRole("dialog"); await review(dialog);
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: `Confirm ${action.toLowerCase()}`, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function picture(page: Page, name: string) {
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  await fs.writeFile(`${artifacts}/axe-day24-${name}.json`, JSON.stringify(axe, null, 2));
  expect(axe.violations.map(v => v.id)).toEqual([]);
  await page.screenshot({ path: `${artifacts}/day24-${name}.png` });
}

test("Cedar release rehearsal: one coherent project, human correction, mock draft and evidence", async ({ page }) => {
  test.skip(process.env.SCOPESHIELD_E2E_DAY24 !== "1", "Use the isolated offline Day 24 runner");
  test.setTimeout(180_000);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  let creates = 0, sends = 0;
  page.on("request", r => {
    if (r.method() === "POST" && /\/milestones\/[^/]+\/invoice$/.test(new URL(r.url()).pathname)) creates++;
    if (r.method() === "POST" && r.url().endsWith("/invoice/send")) sends++;
  });
  await page.goto("/");
  await page.getByLabel("Username", { exact: true }).fill("qa.release");
  await page.getByLabel("Password", { exact: true }).fill(process.env.SCOPESHIELD_E2E_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Upload agreement", exact: true }).first().click();
  const upload = page.getByRole("dialog");
  await upload.getByLabel("Agreement file").setInputFiles(fileURLToPath(new URL("../../examples/day24_cedar_agreement.txt", import.meta.url)));
  await upload.getByRole("button", { name: "Upload agreement", exact: true }).click();
  await page.getByRole("button", { name: "Extract agreement", exact: true }).click();
  const extraction = page.getByRole("dialog");
  const extracted = page.waitForResponse(r => r.url().endsWith("/extract") && r.request().method() === "POST");
  await extraction.getByRole("button", { name: "Run extraction", exact: true }).click();
  const contract = await (await extracted).json();
  expect(contract.extraction.live_ai).toBe(false);
  expect(contract.extraction.milestone_total).toBe("1500.00");
  await expect(page.getByRole("heading", { name: "Landing page wireframe", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Locate in agreement", exact: true }).first().click();
  await expect(page.locator("#source-highlight")).not.toBeEmpty();
  await picture(page, "contract-review");
  await approve(page, "Approve milestone");
  await page.getByRole("tab", { name: "Scope changes", exact: true }).click();
  await page.getByRole("button", { name: "Add conversation", exact: true }).click();
  const conversation = page.getByRole("dialog");
  await conversation.getByLabel("Conversation title").fill("Synthetic Cedar launch conversation");
  for (let i = 0; i < messages.length; i++) {
    if (i) await conversation.getByRole("button", { name: "Add another message", exact: true }).click();
    await conversation.getByLabel(`Sender ${i+1}`, { exact: true }).fill("Alex (synthetic client)");
    await conversation.getByLabel(`Message text ${i+1}`, { exact: true }).fill(messages[i]);
  }
  await review(conversation);
  const creation = page.waitForResponse(r => r.url().endsWith("/change-requests") && r.request().method() === "POST");
  await conversation.getByRole("button", { name: "Save conversation", exact: true }).click();
  const request = await (await creation).json();
  expect(request.conversation_messages.map((m: { content: string }) => m.content)).toEqual(messages);
  expect(request.conversation_messages.every((m: { sent_at: unknown }) => m.sent_at === null)).toBe(true);
  await expect(conversation).not.toBeVisible();
  await review(page);
  const analysis = page.waitForResponse(r => r.url().endsWith("/analyze") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Analyze scope", exact: true }).click();
  const analyzed = await (await analysis).json();
  expect(analyzed.analysis.live_ai).toBe(false);
  expect(analyzed.analysis.findings.at(-1).classification).toBe("OUT_OF_SCOPE");
  expect(creates).toBe(0);
  await page.getByRole("button", { name: "Edit assessment", exact: true }).click();
  const editor = page.getByRole("dialog");
  await editor.getByLabel("Classification", { exact: true }).nth(1).selectOption("AMBIGUOUS");
  await editor.getByLabel("Explanation", { exact: true }).nth(1).fill("Clarify whether FAQ means an additional section or replacement content.");
  await editor.getByLabel("Uncertainties (one per line)", { exact: true }).nth(1).fill("Confirm whether an existing section would be replaced.");
  await review(editor);
  const edited = page.waitForResponse(r => r.url().endsWith("/analysis") && r.request().method() === "PATCH");
  await editor.getByRole("button", { name: "Save reviewed assessment", exact: true }).click();
  expect((await (await edited).json()).analysis.findings.at(-1).classification).toBe("AMBIGUOUS");
  await expect(editor).not.toBeVisible();
  await expect(page.getByText("Human edited", { exact: true })).toBeVisible();
  await page.getByText("What changed across the conversation", { exact: true }).scrollIntoViewIfNeeded();
  await picture(page, "conversation-review");
  await approve(page, "Approve assessment");
  await expect(page.getByLabel("Proposed price (optional)")).toHaveValue("");
  await page.getByLabel("Proposed additional deliverables").fill("SYNTHETIC PROPOSAL: CMS editor; FAQ, dates and support remain separate negotiation items.");
  await page.getByLabel("Proposed price (optional)").fill("300.00");
  await review(page); await page.getByRole("button", { name: "Prepare review draft", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Draft change order", exact: true })).toBeVisible();
  expect(creates).toBe(0); expect(sends).toBe(0);
  await page.goto(`/contracts/${contract.id}?tab=invoices`);
  await page.getByRole("button", { name: "Preview invoice draft", exact: true }).click();
  const preview = page.getByRole("dialog");
  await expect(preview.getByText("Mocked PayPal fixture — no real provider action.", { exact: true })).toBeVisible();
  await review(preview);
  await preview.getByRole("checkbox", { name: "I reviewed the discrepancies and intend this exact partial draft." }).check();
  await preview.getByLabel("Discrepancy explanation").fill("Only the synthetic first milestone is approved; the other USD 1000 remains unapproved.");
  await preview.getByRole("checkbox", { name: "I confirm creating one sandbox draft for this milestone and recipient." }).check();
  await preview.getByRole("button", { name: "Create sandbox draft", exact: true }).click();
  await expect(preview).not.toBeVisible();
  await expect(page.locator(".record-identifier")).toHaveText(/^INV2-MOCK-/);
  await expect(page.getByText("PayPal status: DRAFT", { exact: true })).toBeVisible();
  await picture(page, "mock-invoice");
  expect(creates).toBe(1); expect(sends).toBe(0);
  await page.getByRole("tab", { name: "Project overview", exact: true }).click();
  const dashboard = page.getByRole("region", { name: "Project intelligence", exact: true });
  await expect(dashboard).toContainText("Cedar community launch page");
  await expect(dashboard).toContainText("SIMULATED");
  await dashboard.evaluate(element => element.scrollIntoView({ block: "start" }));
  await picture(page, "project-overview");
  await page.getByRole("tab", { name: "Evidence Pack", exact: true }).click();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download executive summary", exact: true }).click();
  const download = await downloadEvent;
  await download.saveAs(`${artifacts}/day24-executive-summary.pdf`);
  expect(errors).toEqual([]);
  const projects = await page.request.get("http://127.0.0.1:8001/api/projects");
  expect((await projects.json()).length).toBe(1);
});
