(function () {
  const intent = document.getElementById("id_intent");
  document.querySelectorAll("[data-goal-example]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!intent) return;
      intent.value = button.dataset.goalExample;
      intent.focus();
    });
  });

  document.querySelectorAll("[data-busy-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      if (!form.checkValidity()) return;
      form.setAttribute("aria-busy", "true");
      const status = form.querySelector("[data-busy-status]");
      const submit = form.querySelector("button[type='submit']");
      if (!submit) return;
      const pendingLabel = submit.dataset.pendingLabel || "Working…";
      submit.disabled = true;
      submit.setAttribute("aria-disabled", "true");
      submit.textContent = pendingLabel;
      if (status) status.textContent = pendingLabel;
    });
  });

  const copyStatus = document.getElementById("copy-status");
  let copyTimer;

  function reportCopy(button, message, copied) {
    if (copyStatus) copyStatus.textContent = message;
    if (!copied) return;
    const original = button.dataset.copyLabel || button.textContent;
    button.dataset.copyLabel = original;
    button.textContent = "Copied";
    window.clearTimeout(copyTimer);
    copyTimer = window.setTimeout(() => {
      button.textContent = original;
      if (copyStatus) copyStatus.textContent = "";
    }, 2000);
  }

  async function copyText(button, text, fallbackTarget) {
    try {
      await navigator.clipboard.writeText(text);
      reportCopy(button, "Copied to the clipboard.", true);
    } catch (error) {
      if (fallbackTarget && typeof fallbackTarget.select === "function") {
        fallbackTarget.focus();
        fallbackTarget.select();
        reportCopy(button, "Text selected; copy it from the field.", false);
      } else {
        reportCopy(button, "Automatic copy failed. Copy the values from the fields above.", false);
      }
    }
  }

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (target) copyText(button, target.value, target);
    });
  });

  document.querySelectorAll("[data-copy-environment]").forEach((button) => {
    button.addEventListener("click", () => {
      const endpoint = document.getElementById("server-event-endpoint");
      const siteKey = document.getElementById("sitehits-site-key");
      const privateKey = document.getElementById("sitehits-server-event-key");
      if (!endpoint || !siteKey || !privateKey) return;
      const value = [
        `SITEHITS_EVENT_ENDPOINT=${endpoint.value}`,
        `SITEHITS_SITE_KEY=${siteKey.value}`,
        `SITEHITS_SERVER_EVENT_KEY=${privateKey.value}`,
      ].join("\n");
      copyText(button, value, null);
    });
  });

  document.querySelectorAll("[data-toggle-secret]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.getAttribute("aria-controls"));
      if (!target) return;
      const revealing = target.type === "password";
      target.type = revealing ? "text" : "password";
      button.textContent = revealing ? "Hide" : "Show";
      button.setAttribute("aria-pressed", String(revealing));
    });
  });
})();
