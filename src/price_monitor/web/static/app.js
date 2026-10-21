// Price history chart. The data comes from a JSON block on the page; CSP forbids inline scripts.
(function () {
  function drawChart() {
    var canvas = document.getElementById("price-chart");
    var source = document.getElementById("chart-data");
    if (!canvas || !source || typeof Chart === "undefined") return;
    var points = JSON.parse(source.textContent);
    var css = getComputedStyle(document.documentElement);
    var line = css.getPropertyValue("--chart-line").trim();
    var grid = css.getPropertyValue("--chart-grid").trim();
    var text = css.getPropertyValue("--muted").trim();
    var labels = { IN_STOCK: "в наявності", OUT_OF_STOCK: "немає в наявності", UNKNOWN: "невідомо" };
    // Prices are stored as exact decimal strings; numbers are used only to place the points.
    var data = points.map(function (p) {
      return { x: Date.parse(p.t), y: p.price === null ? null : Number(p.price), price: p.price, availability: p.availability };
    });
    function stamp(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
    new Chart(canvas, {
      type: "line",
      data: { datasets: [{ data: data, borderColor: line, backgroundColor: line, stepped: "before", spanGaps: false, pointRadius: 3 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: {
            title: function (items) { return stamp(items[0].raw.x) + " UTC"; },
            label: function (item) {
              var raw = item.raw;
              return (raw.price === null ? "немає ціни" : raw.price) + " · " + (labels[raw.availability] || raw.availability);
            }
          } }
        },
        scales: {
          x: { type: "linear", grid: { color: grid }, ticks: { color: text, maxTicksLimit: 6, callback: function (v) { return stamp(v); } } },
          y: { grid: { color: grid }, ticks: { color: text } }
        }
      }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", drawChart);
  else drawChart();
})();
