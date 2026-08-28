/**
 * Phase 12 E2E — real-browser smoke of landing + console, desktop & mobile.
 * Runs against vite dev server (5173) proxying the API (8000).
 * Fails (exit 1) on: navigation errors, missing critical UI text, severe
 * console errors, horizontal overflow at mobile width.
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.E2E_BASE ?? "http://localhost:5173";
const SHOTS = "../docs/screenshots";
mkdirSync(SHOTS, { recursive: true });

const results = [];
const consoleErrors = [];

function record(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
}

async function newPage(browser, { mobile }) {
  const context = await browser.newContext(
    mobile
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
          userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148" }
      : { viewport: { width: 1440, height: 900 } }
  );
  const page = await context.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(`[${mobile ? "mobile" : "desktop"}] ${msg.text()}`);
  });
  page.on("pageerror", (err) => consoleErrors.push(`[pageerror] ${err.message}`));
  return { context, page };
}

async function run() {
  const browser = await chromium.launch();

  // ---------------- desktop ----------------
  {
    const { context, page } = await newPage(browser, { mobile: false });

    await page.goto(BASE + "/#/", { waitUntil: "networkidle" });
    const heroVisible = await page.getByText("Revenue recovery is", { exact: false }).first().isVisible();
    record("landing hero headline visible (desktop)", heroVisible);
    const ctaVisible = await page.getByText("Open the console").first().isVisible();
    record("landing CTA visible (desktop)", ctaVisible);
    const corpusLabel = await page.getByText("labeled synthetic evaluation corpus").first().isVisible();
    record("landing corpus label visible (no fake stats)", corpusLabel);
    await page.screenshot({ path: `${SHOTS}/01-landing-desktop.png` });

    // scroll section reveals
    await page.locator("#loop").scrollIntoViewIfNeeded();
    await page.waitForTimeout(900);
    record("loop section present", await page.getByText("A decision engine with constraints.").isVisible());
    await page.screenshot({ path: `${SHOTS}/02-landing-loop-desktop.png` });

    // console sign-in flow
    await page.goto(BASE + "/#/console", { waitUntil: "networkidle" });
    await page.waitForURL("**/login**", { timeout: 8000 }).catch(() => {});
    await page.getByText("Owner (full access)").click();
    await page.waitForURL("**/console**", { timeout: 8000 });
    record("dev login redirects to console", page.url().includes("/console"));
    await page.getByText("Control Center", { exact: true }).first().waitFor({ timeout: 8000 });
    record("control center loads", true);

    // corpus visibility: synthetic bucket must show data when corpus exists
    const corpusVisible = await page.evaluate(async () => {
      const token = localStorage.getItem("revora_token") ?? "";
      const res = await fetch("/api/control-center", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) return { ok: false };
      const body = await res.json();
      return { ok: true, syn: body.summary.synthetic.cases_total };
    });
    record(
      "corpus visible to dev merchant (synthetic bucket)",
      corpusVisible.ok && corpusVisible.syn > 0,
      `synthetic cases=${corpusVisible.syn ?? "?"}`
    );
    await page.screenshot({ path: `${SHOTS}/03-console-control-center.png` });

    // Demo Lab: run a scenario end-to-end (candidate table must have ROWS, not
    // just the heading — regression: empty table when explanation lost candidates)
    await page.goto(BASE + "/#/console/simulator", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: /Run scenario/i }).click();
    await page.getByText("Candidate actions evaluated").waitFor({ timeout: 8000 });
    await page.waitForTimeout(400); // rows render right after the heading
    const candidateRows = await page.locator("table tbody tr").count();
    record("simulator runs scenario with candidate table",
      candidateRows >= 3, `candidate rows=${candidateRows}`);
    await page.screenshot({ path: `${SHOTS}/04-console-demo-lab.png` });

    // cases list + detail
    await page.goto(BASE + "/#/console/cases", { waitUntil: "networkidle" });
    await page.locator("table tbody tr").first().waitFor({ timeout: 8000 });
    await page.locator("table tbody tr a").first().click();
    await page.getByText("Audit trail").waitFor({ timeout: 8000 });
    record("case detail shows audit trail", true);
    // decision evidence: candidate table with expected values + policy verdicts
    const evColumn = await page.getByRole("columnheader", { name: "expected value" })
      .isVisible().catch(() => false);
    const policyColumn = await page.getByRole("columnheader", { name: "policy" })
      .isVisible().catch(() => false);
    const verdictVisible = await page.locator("td", { hasText: /allowed|blocked/ })
      .first().isVisible().catch(() => false);
    record("case detail: candidate EV table + policy verdicts",
      evColumn && policyColumn && verdictVisible);
    await page.screenshot({ path: `${SHOTS}/05-console-case-detail.png` });

    // policies page: versioned policy visible (wait — fetch resolves asynchronously)
    await page.goto(BASE + "/#/console/policies", { waitUntil: "networkidle" });
    await page.getByText("Recovery Policies").waitFor({ timeout: 8000 });
    const policyRow = await page.getByText(/default-v1|inactive/).first()
      .waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    record("policies page lists versioned policy", policyRow);
    await page.screenshot({ path: `${SHOTS}/06a-console-policies.png` });

    // experiments page (empty state is fine on a fresh DB)
    await page.goto(BASE + "/#/console/experiments", { waitUntil: "networkidle" });
    await page.getByText("No experiments yet.").waitFor({ timeout: 8000 });
    record("experiments empty state well-formed", true);

    // create + start an experiment via API (owner), then verify the UI shows it running
    const expName = `e2e sanity ${Date.now()}`;
    const expOk = await page.evaluate(async (name) => {
      const token = localStorage.getItem("revora_token") ?? "";
      const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
      const created = await fetch("/api/experiments", {
        method: "POST", headers, body: JSON.stringify({ name }),
      });
      if (!created.ok) return false;
      const { id } = await created.json();
      const started = await fetch(`/api/experiments/${id}/start`, { method: "POST", headers });
      return started.ok;
    }, expName);
    await page.reload({ waitUntil: "networkidle" });
    const expRow = page.locator("tr", { hasText: expName }).first();
    const runningVisible = await expRow.isVisible().catch(() => false);
    const runningBadge = await expRow.getByText("running").isVisible().catch(() => false);
    record("experiment lifecycle (create → start → visible running)", expOk && runningVisible && runningBadge);

    // run scenarios while the experiment is live, then open RESULTS (real computed arms)
    const scenariosOk = await page.evaluate(async () => {
      const token = localStorage.getItem("revora_token") ?? "";
      const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
      const results = [];
      for (const scenario of ["insufficient_funds", "expired_card", "hard_decline"]) {
        const r = await fetch(`/api/simulator/scenarios/${scenario}/run`, {
          method: "POST", headers, body: JSON.stringify({ amount_paise: 499900 }),
        });
        results.push(r.ok);
      }
      return results.every(Boolean);
    });
    await page.reload({ waitUntil: "networkidle" });
    await page.locator("tr", { hasText: expName }).first().click();
    const incrementalVisible = await page.getByText("Incremental recovery")
      .isVisible().catch(() => false);
    const armData = await page.evaluate(async (name) => {
      const token = localStorage.getItem("revora_token") ?? "";
      const list = await (await fetch("/api/experiments", {
        headers: { Authorization: `Bearer ${token}` },
      })).json();
      const id = list.experiments.find((e) => e.name === name)?.id;
      if (!id) return null;
      return await (await fetch(`/api/experiments/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })).json();
    }, expName);
    const armsAssigned = armData && armData.total_assigned >= 3;
    record(
      "experiment results computed from real engine (treatment/control/incremental)",
      scenariosOk && incrementalVisible && Boolean(armsAssigned),
      `assigned=${armData?.total_assigned ?? "?"} incremental=${armData?.incremental_recovered_paise ?? "?"}paise`
    );
    await page.screenshot({ path: `${SHOTS}/06-console-experiments.png` });

    await context.close();
  }

  // ---------------- mobile ----------------
  {
    const { context, page } = await newPage(browser, { mobile: true });
    await page.goto(BASE + "/#/", { waitUntil: "networkidle" });
    await page.waitForTimeout(1600); // entrance choreography
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    record("landing: no horizontal overflow at 390px", overflow <= 1, `overflow=${overflow}px`);
    const heroVisible = await page.getByText("Revenue recovery is", { exact: false }).first().isVisible();
    record("landing hero visible (mobile)", heroVisible);
    await page.screenshot({ path: `${SHOTS}/07-landing-mobile.png`, fullPage: false });

    await page.goto(BASE + "/#/console", { waitUntil: "networkidle" });
    await page.waitForURL("**/login**", { timeout: 8000 }).catch(() => {});
    await page.getByText("Owner (full access)").click();
    await page.waitForURL("**/console**", { timeout: 8000 });
    await page.getByText("Control Center", { exact: true }).first().waitFor({ timeout: 8000 });
    const overflow2 = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    record("console: no horizontal overflow at 390px", overflow2 <= 1, `overflow=${overflow2}px`);
    await page.screenshot({ path: `${SHOTS}/08-console-mobile.png`, fullPage: false });

    await context.close();
  }

  await browser.close();

  const blockingErrors = consoleErrors.filter(
    (e) => !e.includes("favicon") && !e.includes("ERR_CONNECTION_REFUSED") // video/font fetches offline are non-blocking
        && !e.includes("Failed to load resource")
  );
  record("no severe browser console errors", blockingErrors.length === 0,
         blockingErrors.slice(0, 3).join(" | "));

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed`);
  if (consoleErrors.length) {
    console.log("console messages captured (informational):");
    consoleErrors.slice(0, 10).forEach((e) => console.log("  ·", e));
  }
  process.exit(failed.length ? 1 : 0);
}

run().catch((err) => {
  console.error("E2E crashed:", err);
  process.exit(1);
});
