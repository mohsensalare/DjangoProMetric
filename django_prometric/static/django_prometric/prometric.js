/* django-prometric dashboard behaviour: charts, auto-submit filters, confirms. */
(function () {
  "use strict";

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function drawLineChart(canvas) {
    var source = document.getElementById(canvas.dataset.source);
    if (!source || typeof Chart === "undefined") {
      return;
    }
    var data = JSON.parse(source.textContent);
    var reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          data: data.values,
          borderColor: cssVar("--pm-accent"),
          backgroundColor: cssVar("--pm-accent") + "14",
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
            ticks: { color: cssVar("--pm-muted"), maxTicksLimit: 8, maxRotation: 0 }
          },
          y: {
            beginAtZero: true,
            grid: { color: cssVar("--pm-border") },
            ticks: { color: cssVar("--pm-muted"), precision: 0, maxTicksLimit: 6 }
          }
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("canvas[data-source]").forEach(drawLineChart);

    document
      .querySelectorAll("form[data-autosubmit] select, form[data-autosubmit] input[type=checkbox]")
      .forEach(function (control) {
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
