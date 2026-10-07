// Monthly cost chart (Chart.js). The data is embedded as JSON by the template.
(function () {
  "use strict";
  var canvas = document.getElementById("costs-chart");
  var source = document.getElementById("costs-chart-data");
  if (!canvas || !source || typeof Chart === "undefined") return;

  var data = JSON.parse(source.textContent);
  var colors = { fuel: "#2f7ed8", maintenance: "#e8833a", insurance: "#6f42c1" };
  var money = function (value) {
    return value.toLocaleString("bg-BG", { maximumFractionDigits: 0 }) + " €";
  };

  new Chart(canvas, {
    type: "bar",
    data: {
      labels: data.labels,
      datasets: data.datasets.map(function (set) {
        return {
          label: set.label,
          data: set.data,
          backgroundColor: colors[set.key],
          borderRadius: 3,
          maxBarThickness: 46
        };
      })
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, ticks: { callback: money } }
      },
      plugins: {
        legend: { position: "bottom" },
        tooltip: {
          callbacks: {
            label: function (ctx) { return ctx.dataset.label + ": " + money(ctx.parsed.y); },
            footer: function (items) {
              var sum = items.reduce(function (acc, item) { return acc + item.parsed.y; }, 0);
              return "Общо: " + money(sum);
            }
          }
        }
      }
    }
  });
})();
