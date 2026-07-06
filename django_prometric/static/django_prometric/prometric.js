/* django-prometric dashboard behaviour: theme toggle, charts, period picker,
   card customisation, snapshot limits and comparison, confirms. */
(function () {
  "use strict";

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  /* -- theme -------------------------------------------------------------- */
  var charts = [];

  function chartColors() {
    return {
      accent: cssVar("--pm-accent"),
      muted: cssVar("--pm-muted"),
      border: cssVar("--pm-border")
    };
  }

  function chartPalette() {
    var swatches = [];
    for (var i = 1; i <= 8; i++) {
      swatches.push(cssVar("--pm-chart-" + i));
    }
    return swatches;
  }

  function applyChartTheme(chart) {
    var colors = chartColors();
    if (chart.config.type === "doughnut") {
      chart.data.datasets[0].backgroundColor = chartPalette();
      chart.data.datasets[0].borderColor = cssVar("--pm-surface");
      chart.update("none");
      return;
    }
    chart.data.datasets.forEach(function (dataset) {
      dataset.borderColor = colors.accent;
      dataset.backgroundColor = colors.accent + "22";
    });
    chart.options.scales.x.ticks.color = colors.muted;
    chart.options.scales.y.ticks.color = colors.muted;
    chart.options.scales.y.grid.color = colors.border;
    chart.update("none");
  }

  function setupThemeToggle() {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem("pm-theme", next); } catch (e) {}
        charts.forEach(applyChartTheme);
      });
    });
  }

  /* -- charts ------------------------------------------------------------- */
  function drawLineChart(canvas) {
    var source = document.getElementById(canvas.dataset.source);
    if (!source || typeof Chart === "undefined") {
      return;
    }
    var data = JSON.parse(source.textContent);
    var colors = chartColors();
    var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    charts.push(new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          data: data.values,
          borderColor: colors.accent,
          backgroundColor: colors.accent + "22",
          borderWidth: 2,
          pointRadius: 0,
          pointHitRadius: 12,
          tension: 0.3,
          fill: true
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: reduceMotion ? false : { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: { intersect: false, mode: "index", displayColors: false }
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: colors.muted, maxTicksLimit: 8, maxRotation: 0 }
          },
          y: {
            beginAtZero: true,
            grid: { color: colors.border },
            ticks: { color: colors.muted, precision: 0, maxTicksLimit: 6 }
          }
        }
      }
    }));
  }

  function drawDonutChart(canvas) {
    var source = document.getElementById(canvas.dataset.donut);
    if (!source || typeof Chart === "undefined") {
      return;
    }
    var data = JSON.parse(source.textContent);
    var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    charts.push(new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: data.labels,
        datasets: [{
          data: data.values,
          backgroundColor: chartPalette(),
          borderColor: cssVar("--pm-surface"),
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        animation: reduceMotion ? false : { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: { displayColors: false }
        }
      }
    }));
  }

  /* -- async fragments -------------------------------------------------------
     Pages render instantly with skeletons; every [data-card-url] and
     [data-section-url] element then fetches its own HTML. A failure stays
     inside its slot, with a retry button. */
  var I18N = {};
  try {
    I18N = JSON.parse(document.getElementById("pm-i18n").textContent);
  } catch (e) {}

  function failureHtml(wrapInCard) {
    var notice =
      '<div class="pm-notice pm-notice--error" role="alert"><p></p>' +
      '<button type="button" class="pm-btn" data-card-retry></button></div>';
    return wrapInCard ? '<section class="pm-card">' + notice + "</section>" : notice;
  }

  function initFragment(root) {
    root.querySelectorAll("canvas[data-source]").forEach(drawLineChart);
    root.querySelectorAll("canvas[data-donut]").forEach(drawDonutChart);
  }

  function loadFragment(el) {
    if (el.pmSkeleton === undefined) {
      el.pmSkeleton = el.innerHTML;
    }
    el.setAttribute("aria-busy", "true");
    fetch(el.dataset.cardUrl || el.dataset.sectionUrl, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.text();
      })
      .then(function (html) {
        el.innerHTML = html;
        initFragment(el);
      })
      .catch(function () {
        el.innerHTML = failureHtml(Boolean(el.dataset.cardUrl));
        el.querySelector("[role=alert] p").textContent = I18N.loadFailed;
        el.querySelector("[data-card-retry]").textContent = I18N.retry;
      })
      .then(function () {
        el.removeAttribute("aria-busy");
      });
  }

  function setupFragments() {
    document.querySelectorAll("[data-card-url], [data-section-url]").forEach(function (el) {
      if (!el.hidden) {
        loadFragment(el); // hidden cards wait until they're switched back on
      }
    });
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-card-retry]");
      var host = button && button.closest("[data-card-url], [data-section-url]");
      if (!host) {
        return;
      }
      if (host.pmSkeleton !== undefined) {
        host.innerHTML = host.pmSkeleton;
      }
      loadFragment(host);
    });
  }

  /* -- routes table: the traffic columns arrive as JSON after the table ------- */
  function setupRouteTraffic() {
    var host = document.querySelector("[data-traffic-url]");
    if (!host) {
      return;
    }
    var errorBox = document.querySelector("[data-traffic-error]");

    function fillCells(totals) {
      document.querySelectorAll("[data-traffic-req]").forEach(function (cell) {
        var stat = totals && totals[cell.dataset.trafficReq];
        cell.textContent = stat ? stat.requests : "·";
      });
      document.querySelectorAll("[data-traffic-bytes]").forEach(function (cell) {
        var stat = totals && totals[cell.dataset.trafficBytes];
        cell.textContent = stat ? stat.bandwidth : "·";
      });
    }

    function showError(message) {
      fillCells(null);
      if (errorBox) {
        errorBox.querySelector("p").textContent = message;
        errorBox.hidden = false;
      }
    }

    function load() {
      if (errorBox) {
        errorBox.hidden = true;
      }
      fetch(host.dataset.trafficUrl, { credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) {
            throw new Error(String(response.status));
          }
          return response.json();
        })
        .then(function (data) {
          if (data.error) {
            showError(data.error);
            return;
          }
          fillCells(data.totals);
          var note = document.querySelector("[data-traffic-note]");
          if (note && data.note) {
            note.textContent = data.note;
            note.hidden = false;
          }
          var notices = document.querySelector("[data-traffic-notices]");
          if (notices) {
            notices.textContent = "";
            (data.notices || []).forEach(function (notice) {
              var div = document.createElement("div");
              div.className = "pm-notice pm-notice--" + (notice.level || "info");
              div.textContent = notice.message;
              notices.appendChild(div);
            });
          }
        })
        .catch(function () {
          showError(I18N.loadFailed);
        });
    }

    if (errorBox) {
      errorBox.querySelector("[data-traffic-retry]").addEventListener("click", load);
    }
    load();
  }

  /* -- period picker -------------------------------------------------------
     Preset changes submit right away (when the form asks for it); choosing
     "custom" reveals the date inputs instead, and Apply submits. */
  function setupPeriodControl(control) {
    var select = control.querySelector("select[name=period]");
    var dates = control.querySelector(".pm-period__dates");
    if (!select || !dates) {
      return;
    }
    select.addEventListener("change", function () {
      var custom = select.value === "custom";
      dates.hidden = !custom;
      if (custom) {
        dates.querySelector("input[name=from]").focus();
      } else if (select.form.hasAttribute("data-autosubmit")) {
        select.form.submit();
      }
      select.form.dispatchEvent(new Event("pm:period", { bubbles: true }));
    });
    dates.querySelectorAll("input[type=date]").forEach(function (input) {
      input.addEventListener("change", function () {
        select.form.dispatchEvent(new Event("pm:period", { bubbles: true }));
      });
    });
  }

  /* -- dashboard card customisation -------------------------------------------
     The server renders the stored state (checkboxes + hidden slots); every
     change is written back to the user's preferences. */
  function setupCustomize() {
    var toggle = document.querySelector("[data-customize-toggle]");
    var panel = document.getElementById("pm-customize");
    if (!toggle || !panel) {
      return;
    }
    toggle.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      toggle.setAttribute("aria-expanded", String(!panel.hidden));
    });

    function save() {
      var token = document.querySelector("[name=csrfmiddlewaretoken]");
      if (!panel.dataset.prefsUrl || !token) {
        return;
      }
      var body = new URLSearchParams();
      body.append("csrfmiddlewaretoken", token.value);
      panel.querySelectorAll("[data-card-toggle]").forEach(function (box) {
        body.append("order", box.dataset.cardToggle);
        if (!box.checked) {
          body.append("hidden", box.dataset.cardToggle);
        }
      });
      fetch(panel.dataset.prefsUrl, {
        method: "POST",
        credentials: "same-origin",
        body: body
      });
    }

    panel.querySelectorAll("[data-card-toggle]").forEach(function (box) {
      var card = document.querySelector('[data-slot="' + box.dataset.cardToggle + '"]');
      box.addEventListener("change", function () {
        if (card) {
          card.hidden = !box.checked;
          if (box.checked && card.getAttribute("aria-busy") === "true") {
            loadFragment(card); // it was hidden at page load and never fetched
          }
        }
        save();
      });
    });

    // ↑/↓ swap the row with its neighbour — in the panel and on the page.
    panel.addEventListener("click", function (event) {
      var button = event.target.closest("[data-move]");
      if (!button) {
        return;
      }
      var row = button.closest("[data-order-row]");
      var up = Number(button.dataset.move) < 0;
      var other = up ? row.previousElementSibling : row.nextElementSibling;
      if (!other) {
        return;
      }
      row.parentNode.insertBefore(up ? row : other, up ? other : row);
      var card = document.querySelector('[data-slot="' + row.dataset.orderRow + '"]');
      var otherCard = document.querySelector('[data-slot="' + other.dataset.orderRow + '"]');
      if (card && otherCard) {
        card.parentNode.insertBefore(up ? card : otherCard, up ? otherCard : card);
      }
      button.focus();
      save();
    });
  }

  /* -- snapshot form: window-limit warnings --------------------------------
     A source that can't look back far enough shows a red warning; saving
     needs an explicit "shorten it" tick for every affected source. */
  var PRESET_DAYS = { "24h": 1, "7d": 7, "30d": 30, "90d": 90 };

  function selectedDays(form) {
    var period = form.querySelector("select[name=period]").value;
    if (period !== "custom") {
      return PRESET_DAYS[period] || 1;
    }
    var from = new Date(form.querySelector("input[name=from]").value);
    var to = new Date(form.querySelector("input[name=to]").value);
    var days = (to - from) / 86400000 + 1;
    return isFinite(days) && days > 0 ? days : 1;
  }

  function setupSnapshotForm(form) {
    var submit = form.querySelector("[data-take-submit]");

    function refresh() {
      var days = selectedDays(form);
      var blocked = false;
      form.querySelectorAll("[data-source]").forEach(function (source) {
        var maxDays = parseInt(source.dataset.maxDays, 10);
        var pick = source.querySelector("[data-source-pick]");
        var limit = source.querySelector("[data-limit]");
        if (!limit || !maxDays) {
          return;
        }
        var limited = pick.checked && days > maxDays;
        limit.hidden = !limited;
        var accept = limit.querySelector("[data-accept]");
        if (limited && accept && !accept.checked) {
          blocked = true;
        }
      });
      if (submit) {
        submit.disabled = blocked;
      }
    }

    form.addEventListener("pm:period", refresh);
    form.querySelectorAll("[data-source-pick], [data-accept]").forEach(function (box) {
      box.addEventListener("change", refresh);
    });
    refresh();
  }

  /* -- snapshot comparison picker: 2 to 4 snapshots at once ------------------ */
  function setupComparePicker() {
    var bar = document.querySelector("[data-compare-bar]");
    if (!bar) {
      return;
    }
    var link = bar.querySelector("[data-compare-link]");
    var count = bar.querySelector("[data-compare-count]");
    var picks = document.querySelectorAll("[data-compare-pick]");

    function refresh() {
      var chosen = Array.prototype.filter.call(picks, function (box) { return box.checked; });
      bar.hidden = chosen.length === 0;
      var ready = chosen.length >= 2;
      link.classList.toggle("pm-btn--primary", ready);
      if (ready) {
        link.href = link.dataset.url + "?" + chosen.map(function (box) {
          return "s=" + box.value;
        }).join("&");
        link.removeAttribute("aria-disabled");
      } else {
        link.removeAttribute("href");
        link.setAttribute("aria-disabled", "true");
      }
      count.textContent = chosen.length + " / 4";
    }

    picks.forEach(function (box) {
      box.addEventListener("change", function () {
        var chosen = Array.prototype.filter.call(picks, function (b) { return b.checked; });
        if (chosen.length > 4) {
          box.checked = false;
        }
        refresh();
      });
    });
    refresh();
  }

  /* -- routes table: tick routes to hide them from the list ------------------
     Hidden route keys live in localStorage; a toolbar toggle reveals them
     again (dimmed) so they can be brought back. */
  var HIDDEN_ROUTES_KEY = "pm-hidden-routes";

  function hiddenRoutes() {
    try {
      return JSON.parse(localStorage.getItem(HIDDEN_ROUTES_KEY)) || [];
    } catch (e) {
      return [];
    }
  }

  function setupRouteHiding() {
    var bar = document.querySelector("[data-routes-bar]");
    var rows = document.querySelectorAll("[data-route-row]");
    if (!bar || !rows.length) {
      return;
    }
    var picks = document.querySelectorAll("[data-route-pick]");
    var toggle = document.querySelector("[data-hidden-toggle]");
    var showingHidden = false;

    function saveHidden(list) {
      try { localStorage.setItem(HIDDEN_ROUTES_KEY, JSON.stringify(list)); } catch (e) {}
    }

    function apply() {
      var hidden = hiddenRoutes();
      rows.forEach(function (row) {
        var isHidden = hidden.indexOf(row.dataset.routeRow) !== -1;
        row.classList.toggle("pm-row--hidden", isHidden);
        row.hidden = isHidden && !showingHidden;
      });
      if (toggle) {
        toggle.hidden = hidden.length === 0;
        toggle.setAttribute("aria-pressed", String(showingHidden));
        toggle.querySelector("[data-hidden-count]").textContent = hidden.length;
      }
    }

    function chosen() {
      return Array.prototype.filter.call(picks, function (box) { return box.checked; })
        .map(function (box) { return box.value; });
    }

    function clearPicks() {
      picks.forEach(function (box) { box.checked = false; });
    }

    function refreshBar() {
      var keys = chosen();
      bar.hidden = keys.length === 0;
      bar.querySelector("[data-routes-count]").textContent = keys.length;
    }

    bar.querySelector("[data-routes-hide]").addEventListener("click", function () {
      var merged = hiddenRoutes();
      chosen().forEach(function (key) {
        if (merged.indexOf(key) === -1) {
          merged.push(key);
        }
      });
      saveHidden(merged);
      clearPicks();
      refreshBar();
      apply();
    });

    bar.querySelector("[data-routes-unhide]").addEventListener("click", function () {
      var keys = chosen();
      saveHidden(hiddenRoutes().filter(function (key) { return keys.indexOf(key) === -1; }));
      clearPicks();
      refreshBar();
      apply();
    });

    if (toggle) {
      toggle.addEventListener("click", function () {
        showingHidden = !showingHidden;
        apply();
      });
    }

    picks.forEach(function (box) {
      box.addEventListener("change", refreshBar);
    });
    refreshBar();
    apply();
  }

  /* -- boot ----------------------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    setupThemeToggle();
    document.querySelectorAll("canvas[data-source]").forEach(drawLineChart);
    document.querySelectorAll("canvas[data-donut]").forEach(drawDonutChart);
    setupFragments();
    setupRouteTraffic();
    document.querySelectorAll("[data-period-control]").forEach(setupPeriodControl);
    setupCustomize();
    document.querySelectorAll("[data-snapshot-form]").forEach(setupSnapshotForm);
    setupComparePicker();
    setupRouteHiding();

    document
      .querySelectorAll("form[data-autosubmit] select, form[data-autosubmit] input[type=checkbox]")
      .forEach(function (control) {
        if (control.name === "period") {
          return; // handled by the period control above
        }
        control.addEventListener("change", function () {
          control.form.submit();
        });
      });

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.dataset.confirm)) {
          event.preventDefault();
        }
      });
    });
  });
})();
