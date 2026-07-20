// @vitest-environment jsdom
import { beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(process.cwd(), "analytics/static/analytics/script.js"), "utf8");

function installTracker(actorToken = "") {
  const script = document.createElement("script");
  script.src = "https://sitehits.io/js/script.js";
  script.setAttribute("data-site-key", "sh_test_key_123456");
  script.setAttribute("data-api-url", "https://sitehits.io/api/events");
  if (actorToken) script.setAttribute("data-actor-token", actorToken);
  Object.defineProperty(document, "currentScript", { configurable: true, value: script });
  window.eval(source);
}

describe("cookieless tracker", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    history.replaceState({}, "", "/");
    global.fetch = vi.fn().mockResolvedValue({ ok: true });
    Object.defineProperty(navigator, "webdriver", { configurable: true, value: false });
  });

  test("tracks the initial pageview without cookies or localStorage", () => {
    installTracker();
    expect(fetch).toHaveBeenCalledTimes(1);
    const request = JSON.parse(fetch.mock.calls[0][1].body);
    expect(request.event_type).toBe("pageview");
    expect(request.session_id).toBeTruthy();
    expect(request.automation).toEqual({ webdriver: false });
    expect(document.cookie).toBe("");
    expect(localStorage.length).toBe(0);
  });

  test("tracks custom events and SPA navigation", async () => {
    installTracker();
    window.sitehits("event", "signup", { plan: "pro" });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({
      event_type: "custom",
      event_name: "signup",
      properties: { plan: "pro" },
    });

    history.pushState({}, "", "/pricing");
    await vi.advanceTimersByTimeAsync(101);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(JSON.parse(fetch.mock.calls[2][1].body).url).toContain("/pricing");
  });

  test("attaches signed actor tokens and numeric metric options", () => {
    installTracker("signed-token-1");
    window.sitehits("event", "purchase", { plan: "pro" }, { value: "1499.50", unit: "TRY" });
    var request = JSON.parse(fetch.mock.calls[1][1].body);
    expect(request).toMatchObject({
      actor_token: "signed-token-1",
      event_name: "purchase",
      value: "1499.50",
      unit: "TRY",
    });

    window.sitehits("identify", "signed-token-2");
    window.sitehits("event", "tos_accepted", { policy_version: "2026-07" });
    request = JSON.parse(fetch.mock.calls[2][1].body);
    expect(request.actor_token).toBe("signed-token-2");
    expect(request.value).toBeNull();
    expect(request.unit).toBe("");
  });

  test("rotates a session after thirty minutes of inactivity", () => {
    sessionStorage.setItem(
      "sitehits_session_sh_test_key_123456",
      JSON.stringify({ id: "expired-session", lastActivity: Date.now() - 31 * 60 * 1000 }),
    );
    installTracker();
    const request = JSON.parse(fetch.mock.calls[0][1].body);
    expect(request.session_id).not.toBe("expired-session");
  });

  test("reports webdriver automation instead of silently dropping it", () => {
    Object.defineProperty(navigator, "webdriver", { configurable: true, value: true });

    installTracker();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetch.mock.calls[0][1].body).automation).toEqual({ webdriver: true });
  });
});
