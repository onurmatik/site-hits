// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(
  join(process.cwd(), "dashboard/static/dashboard/dashboard.js"),
  "utf8",
);

function installDashboard(botResponse) {
  document.body.innerHTML = `
    <div id="dashboard-app" data-site="example" data-period="last7d" data-granularity="daily">
      <div id="site-menu">
        <button id="site-menu-trigger" aria-expanded="false">
          Example
          <svg data-site-menu-chevron></svg>
        </button>
        <div id="site-menu-options" hidden>
          <a href="/dashboard/all">All sites</a>
          <a href="/dashboard/example">Example</a>
          <button id="new-site-trigger" type="button">New site</button>
        </div>
      </div>
      <dialog id="new-site-dialog">
        <button id="new-site-close" type="button">Close</button>
        <form id="new-site-form">
          <input id="new-site-domain">
          <button type="submit"><span data-new-site-submit-label>Add website</span></button>
        </form>
      </dialog>
      <button id="embed-widget-trigger" type="button">Embed widget</button>
      <dialog id="embed-widget-dialog">
        <button id="embed-widget-close" type="button">Close</button>
        <textarea id="embed-widget-code"><iframe src="/widget/example"></iframe></textarea>
        <button id="copy-embed-widget" type="button">Copy embed code</button>
        <textarea id="embed-widget-agent-instruction">Add this iframe without changing behavior.</textarea>
        <button id="copy-embed-widget-agent" type="button">Copy agent instruction</button>
        <p id="copy-embed-widget-status"></p>
      </dialog>
      <section id="bot-traffic">
        <span data-bot-verification>Known user-agent matches</span>
        <div data-bot-empty hidden>No bot traffic</div>
        <div data-bot-content>
          <article data-bot-category="total"><strong data-value>—</strong><span data-share>Total</span></article>
          <article data-bot-category="answer"><strong data-value>—</strong><span data-share>—</span></article>
          <article data-bot-category="indexing"><strong data-value>—</strong><span data-share>—</span></article>
          <article data-bot-category="training"><strong data-value>—</strong><span data-share>—</span></article>
          <article data-bot-category="other"><strong data-value>—</strong><span data-share>—</span></article>
          <article data-bot-breakdown="providers"><div data-rows></div></article>
          <article data-bot-breakdown="pages"><div data-rows></div></article>
        </div>
      </section>
    </div>`;
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.showModal = function () {
      this.setAttribute("open", "");
    };
    dialog.close = function () {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    };
  });
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(() => Promise.resolve()) },
  });
  global.fetch = vi.fn((url) => {
    if (botResponse && url.startsWith("/api/analytics/bots?")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(botResponse) });
    }
    return new Promise(() => {});
  });
  window.eval(source);
}

describe("dashboard site menu", () => {
  beforeEach(() => {
    installDashboard();
  });

  test("opens from the trigger and closes on an outside click", () => {
    const trigger = document.getElementById("site-menu-trigger");
    const options = document.getElementById("site-menu-options");
    const chevron = document.querySelector("[data-site-menu-chevron]");

    trigger.click();

    expect(options.hidden).toBe(false);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(chevron.classList.contains("rotate-180")).toBe(true);

    document.body.click();

    expect(options.hidden).toBe(true);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  test("loads authenticated region and city breakdowns", () => {
    const urls = global.fetch.mock.calls.map(([url]) => url);

    expect(urls.some((url) => url.startsWith("/api/analytics/breakdowns/regions?"))).toBe(true);
    expect(urls.some((url) => url.startsWith("/api/analytics/breakdowns/cities?"))).toBe(true);
    expect(urls.some((url) => url.startsWith("/api/analytics/bots?"))).toBe(true);
  });

  test("supports arrow-key entry and Escape", () => {
    const trigger = document.getElementById("site-menu-trigger");
    const options = document.getElementById("site-menu-options");
    const firstLink = options.querySelector("a");

    trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));

    expect(options.hidden).toBe(false);
    expect(document.activeElement).toBe(firstLink);

    options.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

    expect(options.hidden).toBe(true);
    expect(document.activeElement).toBe(trigger);
  });

  test("opens and closes the new site dialog from the site menu", () => {
    const trigger = document.getElementById("site-menu-trigger");
    const options = document.getElementById("site-menu-options");
    const newSite = document.getElementById("new-site-trigger");
    const dialog = document.getElementById("new-site-dialog");

    trigger.click();
    newSite.click();

    expect(dialog.open).toBe(true);
    expect(options.hidden).toBe(true);

    document.getElementById("new-site-domain").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );

    expect(dialog.open).toBe(false);
    expect(document.activeElement).toBe(trigger);
  });

  test("opens the embed dialog, copies its code, and restores focus on Escape", async () => {
    const trigger = document.getElementById("embed-widget-trigger");
    const dialog = document.getElementById("embed-widget-dialog");
    const copy = document.getElementById("copy-embed-widget");

    trigger.click();
    expect(dialog.open).toBe(true);

    copy.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.navigator.clipboard.writeText).toHaveBeenCalledWith(
      '<iframe src="/widget/example"></iframe>',
    );
    expect(copy.textContent).toBe("Copied");

    const copyAgent = document.getElementById("copy-embed-widget-agent");
    copyAgent.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(window.navigator.clipboard.writeText).toHaveBeenLastCalledWith(
      "Add this iframe without changing behavior.",
    );
    expect(copyAgent.textContent).toBe("Copied");

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

    expect(dialog.open).toBe(false);
    expect(document.activeElement).toBe(trigger);
  });
});

describe("dashboard bot traffic", () => {
  test("renders bot categories, providers, paths, status, and verification", async () => {
    installDashboard({
      total: 3,
      categories: [
        { key: "answer", count: 1, percentage: 33.3 },
        { key: "indexing", count: 0, percentage: 0 },
        { key: "training", count: 2, percentage: 66.7 },
        { key: "other", count: 0, percentage: 0 },
      ],
      providers: [{ label: "OpenAI", count: 3, percentage: 100 }],
      pages: [{ path: "/missing", status_code: 404, count: 2, percentage: 66.7 }],
      verification: { ip_verified: 0, user_agent: 3 },
    });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    await vi.waitFor(() => {
      expect(document.querySelector('[data-bot-category="total"] [data-value]').textContent).toBe("3");
    });
    expect(document.querySelector('[data-bot-category="training"] [data-share]').textContent).toBe("66.7%");
    expect(document.querySelector('[data-bot-breakdown="providers"] [data-rows]').textContent).toContain("OpenAI");
    expect(document.querySelector('[data-bot-breakdown="pages"] [data-rows]').textContent).toContain("/missing");
    expect(document.querySelector('[data-bot-breakdown="pages"] [data-rows]').textContent).toContain("404");
    expect(document.querySelector("[data-bot-verification]").textContent).toBe("3 user-agent matched");
    expect(document.querySelector("[data-bot-empty]").hidden).toBe(true);
  });
});
