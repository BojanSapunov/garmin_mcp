// Plans page: import a plan from JSON, archive/delete plans, build a brief for a new plan.
(function () {
  const post = (url, body, method = 'POST') => fetch(url, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  // ---- import ----
  const status = document.getElementById('import-status');
  const say = (text, ok) => { status.textContent = text; status.className = `small ${ok ? 'text-success-emphasis' : 'text-danger-emphasis'}`; };
  document.getElementById('import-btn').addEventListener('click', async () => {
    const file = document.getElementById('plan-file').files[0];
    const pasted = document.getElementById('plan-json').value.trim();
    let text = pasted;
    if (file) text = await file.text();
    if (!text) { say('Choose a file or paste JSON first.', false); return; }
    let body;
    try { body = JSON.parse(text); } catch (e) { say(`Not valid JSON: ${e.message}`, false); return; }
    const res = await post('/api/plans', body);
    const out = await res.json().catch(() => ({}));
    if (res.ok && out.ok) {
      say(`Imported ${out.days} days.`, true);
      setTimeout(() => { window.location = `/plans/${out.id}`; }, 700);
    } else {
      say((out.errors || [out.detail || 'Import failed']).join(' · '), false);
    }
  });

  // ---- archive / restore / delete ----
  document.querySelectorAll('[data-plan-status]').forEach((btn) => btn.addEventListener('click', async () => {
    const res = await post(`/api/plans/${btn.dataset.plan}/status`, { status: btn.dataset.planStatus });
    if (res.ok) window.location.reload();
  }));
  document.querySelectorAll('[data-plan-delete]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!window.confirm(`Delete “${btn.dataset.planName}”? This can't be undone (re-import the file to restore it).`)) return;
    const res = await post(`/api/plans/${btn.dataset.planDelete}`, undefined, 'DELETE');
    if (res.ok) window.location.reload();
  }));

  // ---- new plan brief ----
  const form = document.getElementById('brief-form');
  const kind = document.getElementById('b-kind');
  const showKind = () => {
    document.querySelectorAll('.training-only').forEach((el) => el.classList.toggle('d-none', kind.value !== 'training'));
    document.querySelectorAll('.nutrition-only').forEach((el) => el.classList.toggle('d-none', kind.value !== 'nutrition'));
  };
  kind.addEventListener('change', showKind);
  showKind();

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = {};
    new FormData(form).forEach((value, key) => {
      const field = form.elements[key];
      if (value === '' || field.closest('.d-none')) return; // skip empty and hidden (other plan type) fields
      data[key] = field.type === 'number' ? Number(value) : value;
    });
    const res = await post('/api/plans/brief', data);
    const out = await res.json().catch(() => ({}));
    const box = document.getElementById('brief-out');
    const text = document.getElementById('brief-text');
    box.classList.remove('d-none');
    if (res.ok) {
      text.value = out.brief;
      document.getElementById('brief-status').textContent = 'Paste this into Claude, then import the JSON it returns.';
    } else {
      const detail = Array.isArray(out.detail) ? out.detail.map((d) => `${d.loc.slice(-1)[0]}: ${d.msg}`).join(' · ') : out.detail;
      text.value = '';
      document.getElementById('brief-status').textContent = detail || 'Could not build the brief.';
    }
  });
  document.getElementById('brief-copy').addEventListener('click', async () => {
    const text = document.getElementById('brief-text');
    try { await navigator.clipboard.writeText(text.value); } catch (e) { text.select(); document.execCommand('copy'); }
    document.getElementById('brief-status').textContent = 'Copied.';
  });
})();
