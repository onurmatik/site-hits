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
    </div>`;
  const dialog = document.getElementById("new-site-dialog");
  dialog.showModal = function () {
    this.setAttribute("open", "");
  };
  dialog.close = function () {
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
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
});
