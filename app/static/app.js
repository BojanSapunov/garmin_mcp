// Dashboard: distance charts and the Garmin refresh button.
(function () {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const OTHER_SLOTS = ['--series-2', '--series-3', '--series-4', '--series-5'];
  const UNIT = { week: 'week', month: 'month', year: 'year' };
  const typeLabel = (t) => t === 'other' ? 'Other' : t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  let period = 'week';
  let data = null;
  const charts = {};

  function baseOptions(stacked, showLegend) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: showLegend,
          position: 'bottom',
          labels: { color: css('--ink-secondary'), boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'rectRounded' },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString(undefined, { maximumFractionDigits: 1 })} km`,
          },
          filter: (item) => item.parsed.y > 0,
        },
      },
      scales: {
        x: {
          stacked,
          grid: { display: false },
          border: { color: css('--baseline') },
          ticks: { color: css('--ink-muted'), maxRotation: 0, autoSkip: true, autoSkipPadding: 8 },
        },
        y: {
          stacked,
          beginAtZero: true,
          grid: { color: css('--grid'), lineWidth: 1 },
          border: { display: false },
          ticks: { color: css('--ink-muted'), maxTicksLimit: 5, callback: (v) => (v >= 1000 ? (v / 1000) + 'K' : v) },
        },
      },
    };
  }

  function barStyle(color, stacked) {
    return {
      backgroundColor: color,
      maxBarThickness: 24,
      borderRadius: { topLeft: 4, topRight: 4 },
      borderSkipped: 'start',
      // 2px surface gap between stacked segments and touching bars
      borderColor: css('--chart-surface'),
      borderWidth: stacked ? { top: 2 } : 0,
      categoryPercentage: 0.8,
      barPercentage: 0.9,
    };
  }

  function render() {
    if (!data) return;
    const runCtx = document.getElementById('running-chart');
    const otherCtx = document.getElementById('other-chart');
    Object.values(charts).forEach((c) => c.destroy());

    charts.running = new Chart(runCtx, {
      type: 'bar',
      data: { labels: data.labels, datasets: [{ label: 'Running', data: data.running_km, ...barStyle(css('--series-1'), false) }] },
      options: baseOptions(false, false),
    });

    const otherSets = data.other.map((s, i) => ({
      label: typeLabel(s.type),
      data: s.km,
      ...barStyle(s.type === 'other' ? css('--series-other') : css(OTHER_SLOTS[i] || '--series-other'), true),
    }));
    const hasOther = otherSets.some((d) => d.data.some((v) => v > 0));
    document.getElementById('other-empty').classList.toggle('d-none', hasOther);
    otherCtx.parentElement.classList.toggle('d-none', !hasOther);
    charts.other = new Chart(otherCtx, {
      type: 'bar',
      data: { labels: data.labels, datasets: otherSets },
      options: baseOptions(true, otherSets.length > 1),
    });

    document.getElementById('running-caption').textContent = `km per ${UNIT[period]}`;
    document.getElementById('other-caption').textContent = `km per ${UNIT[period]}, by type`;
    renderTable();
  }

  function renderTable() {
    const table = document.getElementById('distance-table');
    const heads = ['Period', 'Running', ...data.other.map((s) => typeLabel(s.type))];
    const rows = data.labels.map((label, i) =>
      [label, data.running_km[i], ...data.other.map((s) => s.km[i])]).reverse();
    table.innerHTML = '';
    const thead = table.createTHead().insertRow();
    heads.forEach((h, i) => { const th = document.createElement('th'); th.textContent = h; if (i) th.className = 'text-end'; thead.appendChild(th); });
    const body = table.createTBody();
    rows.forEach((r) => {
      const tr = body.insertRow();
      r.forEach((v, i) => { const td = tr.insertCell(); td.textContent = i ? v.toFixed(1) : v; if (i) td.className = 'text-end'; });
    });
  }

  async function load() {
    const res = await fetch(`/api/stats/distance?period=${period}`, { credentials: 'same-origin' });
    if (res.status === 401) { window.location = '/login'; return; }
    data = await res.json();
    render();
  }

  document.querySelectorAll('input[name="period"]').forEach((el) =>
    el.addEventListener('change', (e) => { period = e.target.value; load(); }));
  window.addEventListener('themechange', render);

  // ---- refresh button ----
  const btn = document.getElementById('sync-btn');
  const statusEl = document.getElementById('sync-status');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.querySelector('.spinner-border').classList.remove('d-none');
    btn.querySelector('.label').textContent = 'Refreshing…';
    try {
      const res = await fetch('/api/sync', { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      if (res.status === 401) { window.location = '/login'; return; }
      const body = await res.json();
      if (res.ok) {
        statusEl.textContent = `Synced just now · +${body.added} new, ${body.updated} updated, ${body.deleted} removed`;
        if (body.added || body.updated || body.deleted) setTimeout(() => window.location.reload(), 1200);
      } else {
        statusEl.textContent = body.message || 'Sync failed';
      }
    } catch (err) {
      statusEl.textContent = 'Sync failed: network error';
    } finally {
      btn.disabled = false;
      btn.querySelector('.spinner-border').classList.add('d-none');
      btn.querySelector('.label').textContent = 'Refresh from Garmin';
    }
  });

  load();
})();
