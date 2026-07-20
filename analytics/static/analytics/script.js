(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var siteKey = script.getAttribute("data-site-key") || "";
  var apiUrl = script.getAttribute("data-api-url") || new URL("/api/events", script.src).href;
  var actorToken = script.getAttribute("data-actor-token") || "";
  var sessionKey = "sitehits_session_" + siteKey;
  var pageviewKey = "sitehits_pageview_" + siteKey;
  var sessionTimeout = 30 * 60 * 1000;
  var pageviewThrottle = 60 * 1000;
  var queued = window.sitehits && Array.isArray(window.sitehits.q)
    ? window.sitehits.q.slice()
    : [];

  if (!siteKey) return;

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (char) {
      var random = Math.random() * 16 | 0;
      var value = char === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    });
  }

  function sessionId() {
    var now = Date.now();
    var state = null;
    try {
      state = JSON.parse(sessionStorage.getItem(sessionKey));
    } catch (_error) {
      state = null;
    }
    if (!state || !state.id || !state.lastActivity || now - state.lastActivity > sessionTimeout) {
      state = { id: uuid(), lastActivity: now };
    } else {
      state.lastActivity = now;
    }
    try {
      sessionStorage.setItem(sessionKey, JSON.stringify(state));
    } catch (_error) {
      // A blocked sessionStorage should not break the host page.
    }
    return state.id;
  }

  function dimensions(source) {
    return {
      width: Math.max(0, Number(source && source.width) || 0),
      height: Math.max(0, Number(source && source.height) || 0),
    };
  }

  function payload(eventType, eventName, properties, metric) {
    return {
      site_key: siteKey,
      event_type: eventType,
      event_name: eventName || "",
      session_id: sessionId(),
      url: window.location.href,
      referrer: document.referrer || "",
      timestamp: new Date().toISOString(),
      language: navigator.language || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
      viewport: dimensions({ width: window.innerWidth, height: window.innerHeight }),
      screen: dimensions(window.screen),
      automation: { webdriver: navigator.webdriver === true },
      properties: properties || {},
      actor_token: actorToken,
      value: metric && metric.value != null ? metric.value : null,
      unit: metric && metric.unit ? metric.unit : "",
    };
  }

  function send(eventType, eventName, properties, metric) {
    var body = payload(eventType, eventName, properties, metric);
    return fetch(apiUrl, {
      method: "POST",
      mode: "cors",
      credentials: "omit",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(function () {
      return null;
    });
  }

  function pageview() {
    var now = Date.now();
    var url = window.location.href;
    var previous = null;
    try {
      previous = JSON.parse(sessionStorage.getItem(pageviewKey));
    } catch (_error) {
      previous = null;
    }
    if (previous && previous.url === url && now - previous.time < pageviewThrottle) return;
    try {
      sessionStorage.setItem(pageviewKey, JSON.stringify({ url: url, time: now }));
    } catch (_error) {
      // Continue tracking if sessionStorage is unavailable.
    }
    return send("pageview", "", {}, null);
  }

  function cleanProperties(properties) {
    var result = {};
    if (!properties || typeof properties !== "object" || Array.isArray(properties)) return result;
    Object.keys(properties).slice(0, 10).forEach(function (key) {
      var normalized = String(key).toLowerCase();
      if (/^[a-z0-9][a-z0-9_-]{0,31}$/.test(normalized)) {
        var value = properties[key] == null ? "" : String(properties[key]);
        result[normalized] = value.slice(0, 255);
      }
    });
    return result;
  }

  function cleanMetric(options) {
    if (!options || typeof options !== "object" || options.value == null) {
      return { value: null, unit: "" };
    }
    var value = String(options.value);
    var unit = typeof options.unit === "string" ? options.unit.trim() : "";
    if (!value || !Number.isFinite(Number(value))) return { value: null, unit: "" };
    if (!/^[a-z][a-z0-9_-]{0,31}$/i.test(unit)) return { value: null, unit: "" };
    return { value: value, unit: unit };
  }

  function track(command, name, properties, options) {
    if (command === "identify") {
      actorToken = typeof name === "string" ? name : "";
      return;
    }
    if (command !== "event") return;
    if (typeof name !== "string" || !/^[a-z0-9][a-z0-9_:-]{0,63}$/.test(name)) return;
    return send("custom", name, cleanProperties(properties), cleanMetric(options));
  }

  function declarativeEvent(element) {
    var name = element.getAttribute("data-sitehits-event");
    if (!name) return;
    var properties = {};
    var options = {
      value: element.getAttribute("data-sitehits-value"),
      unit: element.getAttribute("data-sitehits-unit") || "",
    };
    Array.prototype.forEach.call(element.attributes, function (attribute) {
      if (
        attribute.name.indexOf("data-sitehits-") !== 0
        || ["data-sitehits-event", "data-sitehits-value", "data-sitehits-unit"].indexOf(attribute.name) !== -1
      ) {
        return;
      }
      var key = attribute.name.slice("data-sitehits-".length).replace(/-/g, "_");
      properties[key] = attribute.value;
    });
    track("event", name, properties, options);
  }

  function closestEventTarget(target) {
    return target && typeof target.closest === "function"
      ? target.closest("[data-sitehits-event]")
      : null;
  }

  window.sitehits = track;
  queued.forEach(function (args) {
    if (Array.isArray(args)) track.apply(null, args);
  });

  document.addEventListener("click", function (event) {
    var target = closestEventTarget(event.target);
    if (target) declarativeEvent(target);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    var target = closestEventTarget(event.target);
    if (target) declarativeEvent(target);
  });

  var routeTimer = null;
  var lastPath = window.location.pathname + window.location.search;
  function routeChanged() {
    var nextPath = window.location.pathname + window.location.search;
    if (nextPath === lastPath) return;
    lastPath = nextPath;
    window.clearTimeout(routeTimer);
    routeTimer = window.setTimeout(pageview, 100);
  }

  ["pushState", "replaceState"].forEach(function (method) {
    var original = window.history[method];
    window.history[method] = function () {
      var result = original.apply(this, arguments);
      routeChanged();
      return result;
    };
  });
  window.addEventListener("popstate", routeChanged);

  pageview();
})();
