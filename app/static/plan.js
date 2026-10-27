// Plan pages: weekly planned-vs-run chart (training), weight chart and weight entry (nutrition).
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const readJson = (id) => { const el = document.getElementById(id); return el ? JSON.parse(el.textContent) : null; };
  const fmtDay = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  let charts = [];

  const axes = (yTitle) => ({
    x: { grid: { display: false }, border: { color: css('--baseline') }, ticks: { color: css('--ink-muted'), maxRotation: 0, autoSkipPadding: 8 } },
    y: {
      grid: { color: css('--grid'), lineWidth: 1 }, border: { display: false },
      ticks: { color: css('--ink-muted'), maxTicksLimit: 5 },
      title: { display: !!yTitle, text: yTitle, color: css('--ink-muted') },
    },
  });
  const legend = { position: 'bottom', labels: { color: css('--ink-secondary'), boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'rectRounded' } };

  function weeksChart(weeks) {
    const bar = { maxBarThickness: 24, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'start', categoryPercentage: 0.7, barPercentage: 0.9 };
    return new Chart(document.getElementById('weeks-chart'), {
      type: 'bar',
      data: {
        labels: weeks.map((w) => fmtDay(w.week)),
        datasets: [
          { label: 'Planned', data: weeks.map((w) => w.planned_km), backgroundColor: css('--series-planned'), ...bar },
          { label: 'Run', data: weeks.map((w) => w.actual_km), backgroundColor: css('--series-1'), ...bar },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend,
          tooltip: { callbacks: { title: (items) => `Week of ${items[0].label}`, label: (i) => ` ${i.dataset.label}: ${i.parsed.y ?? '–'} km` } },
        },
        scales: axes(),
      },
    });
  }

  function weightChart(data) {
    const entries = new Map(data.weights);
    const labels = data.days.slice();
    data.weights.forEach(([d]) => { if (!labels.includes(d)) labels.push(d); }); // entries before the plan started
    labels.sort();
    const values = labels.map((d) => entries.get(d) ?? null);
    const expected = labels.map((d) => { const i = data.days.indexOf(d); return i >= 0 ? data.expected[i] : null; });
    return new Chart(document.getElementById('weight-chart'), {
      type: 'line',
      data: {
        labels: labels.map(fmtDay),
        datasets: [
          { label: 'Plan', data: expected, borderColor: css('--ink-muted'), borderWidth: 2, pointRadius: 0, spanGaps: true },
          {
            label: 'You', data: values, borderColor: css('--series-1'), backgroundColor: css('--series-1'), borderWidth: 2,
            pointRadius: 4, pointBorderWidth: 2, pointBorderColor: css('--chart-surface'), pointHoverRadius: 5, spanGaps: true,
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend, tooltip: { filter: (i) => i.parsed.y != null, callbacks: { label: (i) => ` ${i.dataset.label}: ${i.parsed.y.toFixed(1)} kg` } } },
        scales: axes(),
      },
    });
  }

  function render() {
    charts.forEach((c) => c.destroy());
    charts = [];
    const weeks = readJson('weeks-data');
    if (weeks) charts.push(weeksChart(weeks));
    const weight = readJson('weight-data');
    if (weight) charts.push(weightChart(weight));
  }
  window.addEventListener('themechange', render);
  render();

  // Bring today's day into view
  const todayRow = document.querySelector('.plan-today');
  if (todayRow && !window.location.hash) todayRow.scrollIntoView({ block: 'center' });

  // ---- weight entry ----
  const form = document.getElementById('weight-form');
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const statusEl = document.getElementById('w-status');
      const res = await fetch('/api/weight', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: JSON.stringify({ date: document.getElementById('w-date').value, weight_kg: Number(document.getElementById('w-kg').value) }),
      });
      if (res.ok) { statusEl.textContent = 'Saved.'; setTimeout(() => window.location.reload(), 500); }
      else { const out = await res.json().catch(() => ({})); statusEl.textContent = (out.detail && (out.detail[0]?.msg || out.detail)) || 'Could not save.'; }
    });
  }
})();
