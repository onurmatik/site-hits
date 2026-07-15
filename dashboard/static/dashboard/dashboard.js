(function () {
  "use strict";

  var app = document.getElementById("dashboard-app");
  if (!app) return;
  var site = app.dataset.site;
  var period = app.dataset.period;
  var granularity = app.dataset.granularity;
  var chart = null;
  var numberFormat = new Intl.NumberFormat();

  var siteMenu = document.getElementById("site-menu");
  var siteMenuTrigger = document.getElementById("site-menu-trigger");
  var siteMenuOptions = document.getElementById("site-menu-options");
  var siteMenuChevron = siteMenu.querySelector("[data-site-menu-chevron]");

  function siteMenuLinks() {
    return Array.from(siteMenuOptions.querySelectorAll("a, button"));
  }

  function setSiteMenuOpen(open, returnFocus) {
    siteMenuOptions.hidden = !open;
    siteMenuTrigger.setAttribute("aria-expanded", String(open));
    siteMenuChevron.classList.toggle("rotate-180", open);
    if (!open && returnFocus) siteMenuTrigger.focus();
  }

  siteMenuTrigger.addEventListener("click", function () {
    setSiteMenuOpen(siteMenuOptions.hidden);
  });

  siteMenuTrigger.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    var links = siteMenuLinks();
    setSiteMenuOpen(true);
    (event.key === "ArrowDown" ? links[0] : links[links.length - 1]).focus();
  });

  siteMenuOptions.addEventListener("keydown", function (event) {
    var links = siteMenuLinks();
    var currentIndex = links.indexOf(document.activeElement);
    var nextIndex;
    if (event.key === "Escape") {
      event.preventDefault();
      setSiteMenuOpen(false, true);
      return;
    }
    if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = links.length - 1;
    else if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % links.length;
    else if (event.key === "ArrowUp") nextIndex = (currentIndex - 1 + links.length) % links.length;
    else return;
    event.preventDefault();
    links[nextIndex].focus();
  });

  document.addEventListener("click", function (event) {
    if (!siteMenuOptions.hidden && !siteMenu.contains(event.target)) setSiteMenuOpen(false);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (embedWidgetDialog && embedWidgetDialog.open) {
      event.preventDefault();
      embedWidgetDialog.close();
      return;
    }
    if (newSiteDialog && newSiteDialog.open) {
      event.preventDefault();
      newSiteDialog.close();
      return;
    }
    if (!siteMenuOptions.hidden) setSiteMenuOpen(false, true);
  });

  var newSiteTrigger = document.getElementById("new-site-trigger");
  var newSiteDialog = document.getElementById("new-site-dialog");
  var newSiteClose = document.getElementById("new-site-close");
  var newSiteForm = document.getElementById("new-site-form");
  var newSiteDomain = document.getElementById("new-site-domain");
  var embedWidgetTrigger = document.getElementById("embed-widget-trigger");
  var embedWidgetDialog = document.getElementById("embed-widget-dialog");
  var embedWidgetClose = document.getElementById("embed-widget-close");
  var embedWidgetCode = document.getElementById("embed-widget-code");
  var copyEmbedWidget = document.getElementById("copy-embed-widget");
  var embedWidgetAgentInstruction = document.getElementById("embed-widget-agent-instruction");
  var copyEmbedWidgetAgent = document.getElementById("copy-embed-widget-agent");
  var copyEmbedWidgetStatus = document.getElementById("copy-embed-widget-status");

  if (newSiteTrigger && newSiteDialog) {
    function openNewSiteDialog() {
      setSiteMenuOpen(false);
      newSiteDialog.showModal();
      window.requestAnimationFrame(function () { newSiteDomain.focus(); });
    }

    newSiteTrigger.addEventListener("click", openNewSiteDialog);
    newSiteClose.addEventListener("click", function () { newSiteDialog.close(); });

    newSiteDialog.addEventListener("click", function (event) {
      if (event.target !== newSiteDialog) return;
      var bounds = newSiteDialog.getBoundingClientRect();
      var inside = event.clientX >= bounds.left && event.clientX <= bounds.right
        && event.clientY >= bounds.top && event.clientY <= bounds.bottom;
      if (!inside) newSiteDialog.close();
    });

    newSiteDialog.addEventListener("close", function () {
      if (document.contains(siteMenuTrigger)) siteMenuTrigger.focus();
    });

    newSiteForm.addEventListener("submit", function () {
      var submit = newSiteForm.querySelector('button[type="submit"]');
      submit.disabled = true;
      submit.querySelector("[data-new-site-submit-label]").textContent = "Adding website…";
    });

    if (newSiteDialog.hasAttribute("data-open-on-load")) openNewSiteDialog();
  }

  if (embedWidgetTrigger && embedWidgetDialog) {
    function openEmbedWidgetDialog() {
      embedWidgetDialog.showModal();
      window.requestAnimationFrame(function () { embedWidgetClose.focus(); });
    }

    function setCopyWidgetState(button, label, status) {
      button.textContent = label;
      copyEmbedWidgetStatus.textContent = status;
    }

    function legacyCopyWidgetText(target) {
      target.focus();
      target.select();
      return document.execCommand && document.execCommand("copy");
    }

    function installWidgetCopy(button, target, itemName, defaultLabel) {
      if (!button || !target) return;
      button.addEventListener("click", function () {
        var copy = navigator.clipboard && navigator.clipboard.writeText
          ? navigator.clipboard.writeText(target.value)
          : Promise.resolve(legacyCopyWidgetText(target));
        Promise.resolve(copy).then(function (copied) {
          if (copied === false) {
            setCopyWidgetState(button, "Text selected", "Copy the selected text from the field.");
            return;
          }
          var successMessage = itemName + " copied to the clipboard.";
          setCopyWidgetState(button, "Copied", successMessage);
          window.setTimeout(function () {
            button.textContent = defaultLabel;
            if (copyEmbedWidgetStatus.textContent === successMessage) {
              copyEmbedWidgetStatus.textContent = "";
            }
          }, 2000);
        }).catch(function () {
          legacyCopyWidgetText(target);
          setCopyWidgetState(button, "Text selected", "Copy the selected text from the field.");
        });
      });
    }

    embedWidgetTrigger.addEventListener("click", openEmbedWidgetDialog);
    embedWidgetClose.addEventListener("click", function () { embedWidgetDialog.close(); });

    embedWidgetDialog.addEventListener("click", function (event) {
      if (event.target !== embedWidgetDialog) return;
      var bounds = embedWidgetDialog.getBoundingClientRect();
      var inside = event.clientX >= bounds.left && event.clientX <= bounds.right
        && event.clientY >= bounds.top && event.clientY <= bounds.bottom;
      if (!inside) embedWidgetDialog.close();
    });

    embedWidgetDialog.addEventListener("close", function () {
      if (document.contains(embedWidgetTrigger)) embedWidgetTrigger.focus();
    });

    installWidgetCopy(copyEmbedWidget, embedWidgetCode, "Embed code", "Copy embed code");
    installWidgetCopy(
      copyEmbedWidgetAgent,
      embedWidgetAgentInstruction,
      "Agent instruction",
      "Copy agent instruction"
    );
  }

  function api(path, extra) {
    var params = new URLSearchParams({ site: site, period: period });
    Object.keys(extra || {}).forEach(function (key) { params.set(key, extra[key]); });
    return fetch("/api/analytics/" + path + "?" + params, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (response) {
      if (!response.ok) throw new Error("Analytics request failed with HTTP " + response.status + ".");
      return response.json();
    });
  }

  function duration(seconds) {
    var minutes = Math.floor(seconds / 60);
    var remainder = Math.round(seconds % 60);
    return String(minutes).padStart(2, "0") + ":" + String(remainder).padStart(2, "0");
  }

  function value(metric, raw) {
    if (metric === "bounce_rate") return Number(raw).toFixed(1) + "%";
    if (metric === "avg_session_duration") return duration(raw);
    return numberFormat.format(raw);
  }

  function renderDelta(element, metric, delta) {
    element.textContent = delta == null ? "New" : (delta > 0 ? "+" : "") + delta + "%";
    var improved = metric === "bounce_rate" ? delta <= 0 : delta >= 0;
    element.className = "text-xs " + (
      delta == null ? "text-muted" : improved ? "text-success" : "text-danger"
    );
  }

  function renderKpis(data) {
    Object.keys(data.current).forEach(function (metric) {
      var card = document.querySelector('[data-kpi="' + metric + '"]');
      if (!card) return;
      card.querySelector("[data-value]").textContent = value(metric, data.current[metric]);
      renderDelta(card.querySelector("[data-delta]"), metric, data.deltas[metric]);
    });
  }

  function renderSiteOverviews(data) {
    data.sites.forEach(function (siteData) {
      var row = document.querySelector('[data-site-summary="' + siteData.slug + '"]');
      if (!row) return;
      Object.keys(siteData.current).forEach(function (metric) {
        var cell = row.querySelector('[data-site-metric="' + metric + '"]');
        if (!cell) return;
        cell.querySelector("[data-value]").textContent = value(metric, siteData.current[metric]);
        renderDelta(cell.querySelector("[data-delta]"), metric, siteData.deltas[metric]);
      });
    });
  }

  function renderChart(data) {
    document.getElementById("chart-timezone").textContent = data.timezone + " · " + data.granularity;
    var labels = data.data.map(function (row) {
      var date = new Date(row.bucket);
      return new Intl.DateTimeFormat(undefined, data.granularity === "hourly"
        ? { hour: "2-digit", minute: "2-digit" }
        : { month: "short", day: "numeric" }).format(date);
    });
    var context = document.getElementById("traffic-chart");
    if (chart) chart.destroy();
    chart = new Chart(context, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "Visitors", data: data.data.map(function (row) { return row.visitors; }), borderColor: "#1a3c2b", backgroundColor: "rgba(26,60,43,.08)", fill: true, tension: 0.28, pointRadius: 2, borderWidth: 2 },
          { label: "Pageviews", data: data.data.map(function (row) { return row.pageviews; }), borderColor: "#e78468", backgroundColor: "transparent", fill: false, tension: 0.28, pointRadius: 2, borderWidth: 2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 350 },
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, border: { color: "rgba(23,33,27,.14)" }, ticks: { color: "#667069", maxTicksLimit: 10 } },
          y: { beginAtZero: true, grid: { color: "rgba(23,33,27,.08)" }, border: { display: false }, ticks: { color: "#667069", precision: 0 } },
        },
      },
    });
  }

  function renderRows(dimension, data) {
    var container = document.querySelector('[data-breakdown="' + dimension + '"] [data-rows]');
    container.replaceChildren();
    if (!data.length) {
      var empty = document.createElement("p");
      empty.className = "p-5 text-sm text-muted";
      empty.textContent = "No data in this period.";
      container.appendChild(empty);
      return;
    }
    var max = Math.max.apply(null, data.map(function (row) { return row.count; }));
    data.forEach(function (row) {
      var item = document.createElement("div");
      item.className = "flex min-h-12 items-center px-3 transition-colors hover:bg-paper";
      var body = document.createElement("div");
      body.className = "min-w-0 flex-1 pr-4";
      var label = document.createElement("div");
      label.className = "mb-1 truncate text-[13px] font-medium";
      label.textContent = row.label;
      var bar = document.createElement("div");
      bar.className = "sh-data-bar";
      var fill = document.createElement("span");
      fill.style.width = (max ? row.count / max * 100 : 0) + "%";
      bar.appendChild(fill);
      body.append(label, bar);
      var count = document.createElement("span");
      count.className = "sh-tabular text-[13px] font-medium";
      count.textContent = numberFormat.format(row.count);
      item.append(body, count);
      container.appendChild(item);
    });
  }

  function renderBotProviderRows(data) {
    var container = document.querySelector('[data-bot-breakdown="providers"] [data-rows]');
    container.replaceChildren();
    data.forEach(function (row) {
      var item = document.createElement("div");
      item.className = "flex min-h-12 items-center justify-between gap-4 px-3 transition-colors hover:bg-paper";
      var label = document.createElement("span");
      label.className = "truncate text-[13px] font-medium";
      label.textContent = row.label;
      var count = document.createElement("span");
      count.className = "sh-tabular text-[13px] font-medium";
      count.textContent = numberFormat.format(row.count);
      item.append(label, count);
      container.appendChild(item);
    });
  }

  function renderBotPageRows(data) {
    var container = document.querySelector('[data-bot-breakdown="pages"] [data-rows]');
    container.replaceChildren();
    data.forEach(function (row) {
      var item = document.createElement("div");
      item.className = "flex min-h-12 items-center justify-between gap-4 px-3 transition-colors hover:bg-paper";
      var path = document.createElement("span");
      path.className = "min-w-0 flex-1 truncate text-[13px] font-medium";
      path.textContent = row.path;
      var details = document.createElement("span");
      details.className = "flex shrink-0 items-center gap-3";
      var status = document.createElement("span");
      status.className = "sh-mono text-[10px] " + (
        row.status_code >= 400 ? "text-danger" : row.status_code >= 200 && row.status_code < 400 ? "text-success" : "text-muted"
      );
      status.textContent = row.status_code == null ? "Unknown" : String(row.status_code);
      var count = document.createElement("span");
      count.className = "sh-tabular text-[13px] font-medium";
      count.textContent = numberFormat.format(row.count);
      details.append(status, count);
      item.append(path, details);
      container.appendChild(item);
    });
  }

  function renderBotTraffic(data) {
    var empty = document.querySelector("[data-bot-empty]");
    var content = document.querySelector("[data-bot-content]");
    empty.hidden = data.total !== 0;
    content.hidden = data.total === 0;
    if (!data.total) return;

    var total = document.querySelector('[data-bot-category="total"]');
    total.querySelector("[data-value]").textContent = numberFormat.format(data.total);
    data.categories.forEach(function (category) {
      var card = document.querySelector('[data-bot-category="' + category.key + '"]');
      card.querySelector("[data-value]").textContent = numberFormat.format(category.count);
      card.querySelector("[data-share]").textContent = category.percentage.toFixed(1) + "%";
    });
    var verification = document.querySelector("[data-bot-verification]");
    verification.textContent = data.verification.ip_verified
      ? numberFormat.format(data.verification.ip_verified) + " IP verified · " + numberFormat.format(data.verification.user_agent) + " user-agent matched"
      : numberFormat.format(data.verification.user_agent) + " user-agent matched";
    renderBotProviderRows(data.providers);
    renderBotPageRows(data.pages);
  }

  function showError(error) {
    var target = document.getElementById("dashboard-error");
    target.textContent = error.message || "Analytics could not be loaded.";
    target.classList.remove("hidden");
  }

  var dimensions = ["pages", "referrers", "countries", "regions", "cities", "devices", "campaigns", "events"];
  Promise.all([
    api(site === "all" ? "sites-overview" : "overview"),
    api("timeseries", { granularity: granularity }),
  ].concat(dimensions.map(function (dimension) { return api("breakdowns/" + dimension); })))
    .then(function (responses) {
      if (site === "all") renderSiteOverviews(responses[0]);
      else renderKpis(responses[0]);
      renderChart(responses[1]);
      dimensions.forEach(function (dimension, index) { renderRows(dimension, responses[index + 2].data); });
    })
    .catch(showError);

  api("bots")
    .then(renderBotTraffic)
    .catch(showError);
})();
