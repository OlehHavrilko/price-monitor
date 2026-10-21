"use strict";
(async function () {
  const canvas = document.getElementById("price-history");
  if (!canvas) return;
  const message = document.getElementById("chart-message");
  try {
    const response = await fetch(canvas.dataset.url, {credentials: "same-origin"});
    if (!response.ok) throw new Error("history unavailable");
    const {observations} = await response.json();
    const tbody = document.getElementById("history-values");
    for (const point of observations) {
      const row = document.createElement("tr");
      for (const value of [point.observed_at, point.price === null ? "Ціну не вказано" : `${point.price} ${point.currency}`]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      tbody.appendChild(row);
    }
    if (!observations.length || observations.every(p => p.price === null)) {
      message.textContent = "Для графіка ще немає цін. Повні спостереження — у таблиці нижче.";
      canvas.hidden = true;
      return;
    }
    if (!window.Chart) throw new Error("chart library unavailable");
    const theme = window.matchMedia("(prefers-color-scheme: dark)");
    function colors() {
      const style = getComputedStyle(document.documentElement);
      return {text: style.getPropertyValue("--muted").trim(), line: style.getPropertyValue("--line").trim(), accent: style.getPropertyValue("--accent").trim()};
    }
    const c = colors();
    // Only the drawing coordinates use JS numbers. Exact prices remain strings in
    // the API, table and tooltip; no monetary calculations are performed here.
    const chart = new Chart(canvas, {
      type: "line",
      data: {labels: observations.map(p => p.observed_at), datasets: [{label: "Ціна", data: observations.map(p => p.price === null ? null : Number(p.price)), borderColor: c.accent, backgroundColor: c.accent, spanGaps: false, tension: 0, pointRadius: 4}]},
      options: {responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {legend: {display: false}, tooltip: {callbacks: {label: ctx => {const p = observations[ctx.dataIndex]; return `${p.price} ${p.currency}`;}}}},
        scales: {x: {ticks: {color: c.text, maxTicksLimit: 6}, grid: {color: c.line}}, y: {ticks: {color: c.text}, grid: {color: c.line}}}}
    });
    theme.addEventListener("change", () => {
      const c = colors();
      chart.data.datasets[0].borderColor = c.accent;
      chart.data.datasets[0].backgroundColor = c.accent;
      for (const axis of ["x", "y"]) {chart.options.scales[axis].ticks.color = c.text; chart.options.scales[axis].grid.color = c.line;}
      chart.update();
    });
    message.textContent = `${observations.length} спостережень. Пропуски означають, що ціну не вказано.`;
  } catch (_) {
    message.textContent = "Не вдалося завантажити графік. Оновіть сторінку або увійдіть знову. Точні значення, якщо завантажені, доступні нижче.";
  }
})();
