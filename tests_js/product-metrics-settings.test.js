// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(
  join(process.cwd(), "dashboard/static/dashboard/product_metrics_settings.js"),
  "utf8",
);

function installFlow({ clipboard = vi.fn(() => Promise.resolve()) } = {}) {
  document.body.innerHTML = `
    <textarea id="id_intent"></textarea>
    <button type="button" data-goal-example="Track completed signup.">Example</button>
    <form data-busy-form>
      <input required value="ready">
      <button type="submit" data-pending-label="Drafting plan…">Draft tracking plan</button>
      <p data-busy-status></p>
    </form>
    <input id="server-event-endpoint" value="https://stats.example/api/server-events">
    <input id="sitehits-site-key" value="sh_public">
    <input id="sitehits-server-event-key" type="password" value="shs_private">
    <button type="button" data-toggle-secret aria-controls="sitehits-server-event-key" aria-pressed="false">Show</button>
    <button type="button" data-copy-environment>Copy environment</button>
    <textarea id="product-agent-instruction">Use $SITEHITS_SERVER_EVENT_KEY.</textarea>
    <button type="button" data-copy-target="product-agent-instruction">Copy agent instruction</button>
    <p id="copy-status"></p>`;
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: clipboard },
  });
  window.eval(source);
  return clipboard;
}

describe("product metrics setup enhancements", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  test("fills an example and exposes a stable pending state on submit", () => {
    installFlow();
    document.querySelector("[data-goal-example]").click();
    expect(document.getElementById("id_intent").value).toBe("Track completed signup.");

    const form = document.querySelector("[data-busy-form]");
    form.dispatchEvent(new Event("submit"));

    const submit = form.querySelector("button[type='submit']");
    expect(form.getAttribute("aria-busy")).toBe("true");
    expect(submit.disabled).toBe(true);
    expect(submit.textContent).toBe("Drafting plan…");
    expect(form.querySelector("[data-busy-status]").textContent).toBe("Drafting plan…");
  });

  test("copies the deterministic agent instruction", async () => {
    const clipboard = installFlow();

    document.querySelector("[data-copy-target]").click();
    await Promise.resolve();
    await Promise.resolve();

    expect(clipboard).toHaveBeenCalledWith("Use $SITEHITS_SERVER_EVENT_KEY.");
    expect(document.querySelector("[data-copy-target]").textContent).toBe("Copied");
    expect(document.getElementById("copy-status").textContent).toBe(
      "Copied to the clipboard.",
    );
  });

  test("keeps the private key masked until requested and copies all environment values", async () => {
    const clipboard = installFlow();
    const secret = document.getElementById("sitehits-server-event-key");
    const toggle = document.querySelector("[data-toggle-secret]");

    expect(secret.type).toBe("password");
    toggle.click();
    expect(secret.type).toBe("text");
    expect(toggle.textContent).toBe("Hide");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    document.querySelector("[data-copy-environment]").click();
    await Promise.resolve();
    await Promise.resolve();

    expect(clipboard).toHaveBeenCalledWith(
      [
        "SITEHITS_EVENT_ENDPOINT=https://stats.example/api/server-events",
        "SITEHITS_SITE_KEY=sh_public",
        "SITEHITS_SERVER_EVENT_KEY=shs_private",
      ].join("\n"),
    );
  });

  test("selects a text artifact when clipboard access fails", async () => {
    const clipboard = vi.fn(() => Promise.reject(new Error("denied")));
    installFlow({ clipboard });
    const target = document.getElementById("product-agent-instruction");
    target.select = vi.fn();
    target.focus = vi.fn();

    document.querySelector("[data-copy-target]").click();
    await Promise.resolve();
    await Promise.resolve();

    expect(target.focus).toHaveBeenCalled();
    expect(target.select).toHaveBeenCalled();
    expect(document.getElementById("copy-status").textContent).toBe(
      "Text selected; copy it from the field.",
    );
  });
});
