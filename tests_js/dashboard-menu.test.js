// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(
  join(process.cwd(), "dashboard/static/dashboard/dashboard.js"),
  "utf8",
);

function installDashboard() {
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
          <a href="/?start=over">New site</a>
        </div>
      </div>
    </div>`;
  global.fetch = vi.fn(() => new Promise(() => {}));
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
});
