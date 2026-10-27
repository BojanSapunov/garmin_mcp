// Activity detail page: pace/speed, heart rate and elevation charts with a shared crosshair, plus splits.
(function () {
  const root = document.getElementById('activity');
  const activityId = root.dataset.activityId;
  const isRunning = root.dataset.running === 'true';
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const charts = [];
  let details = null;
  let splitMode = 'km';

  const fmtPace = (s) => (s == null ? '–' : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`);
  const fmtDuration = (s) => {
    if (s == null) return '–';
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
  };
  const speedKmh = (pace) => (pace ? 3600 / pace : null);

  // Draws a vertical line at the hovered point; the same index is highlighted in every chart.
  const crosshair = {
    id: 'crosshair',
    afterDatasetsDraw(chart) {
      const active = chart.getActiveElements();
      if (!active.length) return;
      const { ctx, chartArea } = chart;
      const x = active[0].element.x;
      ctx.save();
      ctx.strokeStyle = css('--baseline');
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };

  function syncHover(source, index) {
    charts.forEach((c) => {
      if (c === source) return;
      const points = index == null || c.data.datasets[0].data[index] == null ? [] : [{ datasetIndex: 0, index }];
      c.setActiveElements(points);
      const el = points.length && c.getDatasetMeta(0).data[index];
      c.tooltip.setActiveElements(points, el ? { x: el.x, y: el.y } : { x: 0, y: 0 });
      c.update('none');
    });
  }

  // Axis range from the 2nd-98th percentile, so warm-up seconds, stops and sprints don't flatten the line.
  // Points outside it (start-up readings, GPS glitches) are left as gaps rather than drawn off the chart.
  function robustRange(values, pad) {
    const sorted = values.filter((v) => v != null).sort((a, b) => a - b);
    if (sorted.length < 10) return {};
    return { min: sorted[Math.floor(sorted.length * 0.02)] - pad, max: sorted[Math.floor(sorted.length * 0.98)] + pad };
  }

  function lineChart(canvasId, values, { color, label, fill = false, reverse = false, range = {}, tick, tooltip }) {
    const s = details.series;
    const unitX = details.x_unit;
    const inRange = (v) => v != null && (range.min == null || v >= range.min) && (range.max == null || v <= range.max);
    const points = s.x.map((x, i) => ({ x, y: inRange(values[i]) ? values[i] : null }));
    const chart = new Chart(document.getElementById(canvasId), {
      type: 'line',
      data: {
        datasets: [{
          label,
          data: points,
          borderColor: color,
          backgroundColor: color + '1a', // ~10% wash for the area fill
          borderWidth: 2,
          borderJoinStyle: 'round',
          borderCapStyle: 'round',
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBorderWidth: 2,
          pointHoverBorderColor: css('--chart-surface'),
          pointHoverBackgroundColor: color,
          fill: fill ? 'start' : false,
          spanGaps: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        interaction: { mode: 'nearest', axis: 'x', intersect: false },
        onHover: (evt, active, c) => syncHover(c, active.length ? active[0].index : null),
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            callbacks: {
              title: (items) => (items.length ? `${items[0].parsed.x.toFixed(unitX === 'km' ? 2 : 0)} ${unitX}` : ''),
              label: (item) => tooltip(item.parsed.y),
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: 0,
            max: s.x[s.x.length - 1],
            grid: { display: false },
            border: { color: css('--baseline') },
            ticks: { color: css('--ink-muted'), maxTicksLimit: 7, callback: (v) => `${Math.round(v)}` },
            title: { display: true, text: unitX, color: css('--ink-muted'), padding: 0 },
          },
          y: {
            reverse,
            ...range,
            grace: '5%',
            grid: { color: css('--grid'), lineWidth: 1 },
            border: { display: false },
            ticks: { color: css('--ink-muted'), maxTicksLimit: 5, callback: tick },
          },
        },
      },
      plugins: [crosshair],
    });
    chart.canvas.addEventListener('mouseleave', () => syncHover(null, null));
    charts.push(chart);
    return chart;
  }

  function renderCharts() {
    charts.splice(0).forEach((c) => c.destroy());
    const s = details.series;
    const has = (arr) => arr.some((v) => v != null);

    if (isRunning) {
      document.getElementById('pace-title').textContent = 'Pace';
      document.getElementById('pace-caption').textContent = 'min/km, faster is higher';
      lineChart('pace-chart', s.pace, {
        color: css('--series-1'), label: 'Pace', reverse: true, range: robustRange(s.pace, 10),
        tick: (v) => fmtPace(v), tooltip: (v) => `${fmtPace(v)} /km`,
      });
    } else {
      document.getElementById('pace-title').textContent = 'Speed';
      document.getElementById('pace-caption').textContent = 'km/h';
      lineChart('pace-chart', s.pace.map(speedKmh), {
        color: css('--series-1'), label: 'Speed', tick: (v) => v.toFixed(0), tooltip: (v) => `${v.toFixed(1)} km/h`,
      });
    }

    document.getElementById('hr-card').classList.toggle('d-none', !has(s.hr));
    if (has(s.hr)) {
      lineChart('hr-chart', s.hr, {
        color: css('--series-hr'), label: 'Heart rate', range: robustRange(s.hr, 5),
        tick: (v) => Math.round(v), tooltip: (v) => `${Math.round(v)} bpm`,
      });
    }
    document.getElementById('elevation-card').classList.toggle('d-none', !has(s.elevation));
    if (has(s.elevation)) {
      lineChart('elevation-chart', s.elevation, {
        color: css('--series-elev'), label: 'Elevation', fill: true,
        tick: (v) => Math.round(v), tooltip: (v) => `${Math.round(v)} m`,
      });
    }
  }

  function renderSplits() {
    const perKm = splitMode === 'km';
    const rows = perKm ? details.km_splits : details.laps;
    document.getElementById('splits-section').classList.toggle('d-none', !details.km_splits.length && !details.laps.length);
    const paces = rows.map((r) => r.pace_s_km).filter((p) => p);
    const fastest = Math.min(...paces), slowest = Math.max(...paces);
    const showCadence = perKm && rows.some((r) => r.cadence_spm);
    const speedOrPace = (p) => (isRunning ? fmtPace(p) : (p ? (3600 / p).toFixed(1) : '–'));
    const elev = (r) => (r.elev_gain_m == null ? '–'
      : `+${Math.round(r.elev_gain_m)} −${Math.round(r.elev_loss_m || 0)}`);

    // Per km, every row is 1 km, so time equals pace: show distance and time only for laps.
    const columns = [
      { head: perKm ? 'Km' : 'Lap', cell: (r, i) => (perKm ? (r.distance_m < 990 ? (r.distance_m / 1000).toFixed(2) : r.km) : i + 1) },
      ...(perKm ? [] : [
        { head: 'Dist', end: true, cell: (r) => (r.distance_m / 1000).toFixed(2) },
        { head: 'Time', end: true, cell: (r) => fmtDuration(r.duration_s) },
      ]),
      { head: isRunning ? 'Pace' : 'km/h', pace: true },
      { head: 'HR', end: true, cell: (r) => (r.avg_hr ? Math.round(r.avg_hr) : '–') },
      { head: 'Elev', end: true, cell: elev },
      ...(showCadence ? [{ head: 'Cad', end: true, cell: (r) => (r.cadence_spm ? Math.round(r.cadence_spm) : '–') }] : []),
    ];

    const table = document.getElementById('splits-table');
    table.innerHTML = '';
    const headRow = table.createTHead().insertRow();
    columns.forEach((c) => {
      const th = document.createElement('th');
      th.textContent = c.head;
      th.scope = 'col';
      if (c.end) th.className = 'text-end';
      if (c.pace) th.className = 'w-100';
      headRow.appendChild(th);
    });
    const body = table.createTBody();
    rows.forEach((r, i) => {
      const row = body.insertRow();
      columns.forEach((c) => {
        const td = row.insertCell();
        if (c.end) td.className = 'text-end';
        if (!c.pace) { td.textContent = c.cell(r, i); return; }
        // Pace value, then a bar in its own track: longer = faster, relative to this activity's range.
        const share = r.pace_s_km && slowest > fastest ? 0.15 + 0.85 * (slowest - r.pace_s_km) / (slowest - fastest) : 1;
        td.innerHTML = '<div class="pace-cell"><span class="pace-value"></span><span class="pace-track"><span class="pace-bar"></span></span></div>';
        td.querySelector('.pace-value').textContent = speedOrPace(r.pace_s_km);
        td.querySelector('.pace-bar').style.width = `${Math.round(share * 100)}%`;
        if (r.pace_s_km === fastest && paces.length > 1) td.querySelector('.pace-value').classList.add('fw-semibold');
      });
    });
  }

  document.querySelectorAll('input[name="split"]').forEach((el) =>
    el.addEventListener('change', (e) => { splitMode = e.target.value; renderSplits(); }));
  window.addEventListener('themechange', () => { if (details) renderCharts(); });

  fetch(`/api/activity/${activityId}/details`, { credentials: 'same-origin' })
    .then(async (res) => {
      if (res.status === 401) { window.location = '/login'; return; }
      const body = await res.json();
      document.getElementById('details-loading').classList.add('d-none');
      if (!body.available) {
        const box = document.getElementById('details-unavailable');
        box.textContent = `No detailed data for this activity: ${body.reason}.`;
        box.classList.remove('d-none');
        return;
      }
      details = body;
      if (!details.km_splits.length) splitMode = 'laps';
      document.getElementById('split-toggle').classList.toggle('d-none', !(details.km_splits.length && details.laps.length > 1));
      document.getElementById('details').classList.remove('d-none');
      renderCharts();
      renderSplits();
    })
    .catch(() => {
      document.getElementById('details-loading').classList.add('d-none');
      const box = document.getElementById('details-unavailable');
      box.textContent = 'Could not load details. Check your connection and reload.';
      box.classList.remove('d-none');
    });
})();
