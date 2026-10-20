/* SIGAA Study Agent — painel web (SPA por hash, sem framework).
 *
 * Telas: chat, início, plano, prazos, disciplinas, avisos, notas, materiais, novidades,
 * progresso, buscar, operação, config. Tudo vem de /api/*; nada é renderizado sem escapar.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------------ utils
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icons = () => { if (window.lucide && lucide.createIcons) lucide.createIcons(); };
  const MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

  let toastHost = null;
  function toast(msg, variant) {
    if (!toastHost) {
      toastHost = document.createElement('div');
      toastHost.className = 'dash-toast-host';
      toastHost.setAttribute('aria-live', 'polite');
      document.body.appendChild(toastHost);
    }
    const el = document.createElement('div');
    el.className = `dash-toast ${variant === 'err' ? 'dash-toast-err' : 'dash-toast-ok'}`;
    el.setAttribute('role', 'status');
    el.textContent = msg;
    toastHost.appendChild(el);
    setTimeout(() => { el.classList.add('is-leaving'); setTimeout(() => el.remove(), 220); }, 4000);
  }

  function fmtDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  // prazo só com data ("AAAA-MM-DD") não tem hora: não converter de fuso nem inventar horário
  function fmtDue(v) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '');
    return m ? `${m[3]}/${m[2]}/${m[1]}` : fmtDateTime(v);
  }
  function fmtRelative(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (isNaN(diff)) return '';
    if (diff < 60) return 'agora';
    if (diff < 3600) return `há ${Math.floor(diff / 60)} min`;
    if (diff < 86400) return `há ${Math.floor(diff / 3600)} h`;
    return `há ${Math.floor(diff / 86400)} d`;
  }
  function dayBox(iso, daysLeft) {
    if (!iso) return '';
    const d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
    const cls = daysLeft <= 1 ? 'is-urgent' : daysLeft <= 7 ? 'is-soon' : '';
    return `<div class="dash-daybox ${cls}"><b>${String(d.getDate()).padStart(2, '0')}</b><small>${MONTHS[d.getMonth()]}</small></div>`;
  }
  function badge(text, variant) { return `<span class="dash-badge dash-badge-${variant || 'light'}">${esc(text)}</span>`; }
  function kindBadge(kind, label) {
    const v = { assessment: 'danger', activity: 'warning', grade: 'success', material: 'info', announcement: 'primary', content: 'light' }[kind] || 'light';
    return badge(label || kind, v);
  }
  function statusBadge(st) {
    const map = {
      success: ['completa', 'success'], partial: ['parcial', 'warning'], failed: ['falhou', 'danger'], running: ['em andamento', 'info'],
      pending: ['pendente', 'light'], sent: ['enviada', 'success'], done: ['concluída', 'success'], deferred: ['adiado', 'warning'],
      skipped: ['pulada', 'light'], in_progress: ['em andamento', 'info'], downloaded: ['baixado', 'success'], invalid: ['inválido', 'danger'],
      blocked: ['bloqueado', 'warning'], too_large: ['grande demais', 'warning'], no_text: ['sem texto', 'warning'], extracted: ['extraído', 'success'],
      ok: ['ok', 'success'], unsupported: ['não suportado', 'light'],
    };
    const [l, v] = map[st] || [st, 'light'];
    return badge(l, v);
  }
  function empty(msg, icon) { return `<div class="dash-empty"><i data-lucide="${icon || 'inbox'}"></i>${esc(msg)}</div>`; }
  function skeleton(n, h) { return Array.from({ length: n || 3 }, () => `<div class="dash-skeleton mb-3" style="min-height:${h || 4}rem"></div>`).join(''); }
  function intro(title, sub, actions) {
    return `<div class="dash-page-intro"><div><h2>${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ''}</div>${actions ? `<div class="dash-page-actions">${actions}</div>` : ''}</div>`;
  }
  function card(title, icon, body, extraHead) {
    return `<section class="dash-card"><div class="dash-card-head"><h3><i data-lucide="${icon}"></i>${esc(title)}</h3>${extraHead || ''}</div><div class="dash-card-body">${body}</div></section>`;
  }
  function minutesLabel(m) { return m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? String(m % 60).padStart(2, '0') : ''}` : `${m} min`; }

  // -------------------------------------------------------------------- API
  async function api(path, options) {
    const res = await fetch(`/api/${path}`, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {}));
    if (res.status === 401) { showGate(); throw new Error('unauthorized'); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* vazio */ }
    if (!res.ok) throw new Error((data && data.error) || `http_${res.status}`);
    return data;
  }
  const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

  // ------------------------------------------------------------------- gate
  function showGate() { $('gate').hidden = false; setTimeout(() => $('gate-token').focus(), 50); }
  function hideGate() { $('gate').hidden = true; }
  async function initGate() {
    try {
      const st = await fetch('/api/auth').then((r) => r.json());
      if (st.required && !st.ok) { showGate(); return false; }
    } catch (e) { /* servidor sem auth */ }
    return true;
  }
  $('gate-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    $('gate-error').hidden = true;
    const token = $('gate-token').value.trim();
    const res = await fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
    if (res.ok) { hideGate(); $('gate-token').value = ''; route(); refreshIndicator(); } else { $('gate-error').hidden = false; }
  });

  // ------------------------------------------------------------------ theme
  (function initTheme() {
    const key = 'sigaa-theme';
    const apply = (t) => {
      document.documentElement.setAttribute('data-theme', t);
      const btn = $('theme-toggle');
      btn.querySelector('.dash-icon-sun').classList.toggle('dash-theme-icon-hidden', t !== 'dark');
      btn.querySelector('.dash-icon-moon').classList.toggle('dash-theme-icon-hidden', t !== 'light');
    };
    let cur = document.documentElement.getAttribute('data-theme') || 'light';
    apply(cur);
    $('theme-toggle').addEventListener('click', () => {
      cur = cur === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(key, cur); } catch (e) { /* ignore */ }
      apply(cur); icons();
    });
  })();

  // ---------------------------------------------------------------- sidebar
  (function initSidebar() {
    const sb = $('sidebar'), bd = $('sidebar-backdrop'), bg = $('sidebar-burger');
    const close = () => { sb.classList.remove('is-open'); bd.classList.remove('is-open'); };
    bg.addEventListener('click', () => { sb.classList.toggle('is-open'); bd.classList.toggle('is-open'); });
    bd.addEventListener('click', close);
    sb.querySelectorAll('a').forEach((a) => a.addEventListener('click', close));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { close(); closeDrawer(); } });
  })();

  // ------------------------------------------------------------ indicador
  async function refreshIndicator() {
    try {
      const ov = await api('overview');
      const dot = $('sync-dot'), lbl = $('sync-label');
      const st = ov.last_run && ov.last_run.status;
      dot.className = 'refresh-dot ' + (st === 'failed' ? 'is-bad' : st === 'partial' ? 'is-warn' : st === 'running' ? 'is-loading' : '');
      lbl.textContent = ov.last_success_at ? `coleta ${fmtRelative(ov.last_success_at)}` : 'sem coleta';
      $('sync-indicator').title = ov.last_success_at ? `Última coleta bem-sucedida: ${fmtDateTime(ov.last_success_at)}` : 'Nenhuma coleta concluída';
    } catch (e) { /* silencioso */ }
  }

  // ----------------------------------------------------------------- drawer
  function openDrawer(html) {
    $('drawer-body').innerHTML = html;
    $('drawer-root').hidden = false;
    document.body.classList.add('dash-drawer-open');
    icons();
  }
  function closeDrawer() { $('drawer-root').hidden = true; document.body.classList.remove('dash-drawer-open'); }
  document.querySelectorAll('[data-drawer-close]').forEach((b) => b.addEventListener('click', closeDrawer));

  async function openItem(itemId) {
    openDrawer(skeleton(3));
    try {
      const it = await api(`items/${encodeURIComponent(itemId)}`);
      const fields = [];
      const f = (k, v) => { if (v) fields.push(`<div class="dash-drawer-field"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`); };
      f('Disciplina', it.course);
      f('Publicado', it.published_label);
      f('Prazo', it.due_label ? `${it.due_label} · ${it.due_relative}` : null);
      f('Peso', it.weight);
      f('Nota', it.grade_value ? `${it.grade_value}${it.grade_max ? ' / ' + it.grade_max : ''}` : null);
      f('Arquivo', it.file_name);
      f('Tópico', it.topic);
      f('Revisão', `${it.revision} · visto ${fmtRelative(it.last_seen_at)}`);
      let html = `<h3 class="dash-drawer-title">${esc(it.title)}</h3>
        <div class="dash-drawer-sub">${kindBadge(it.kind, it.kind_label)}${it.removed_at ? badge('removido do SIGAA', 'danger') : ''}</div>
        <div class="dash-drawer-fields">${fields.join('')}</div>`;
      if (it.scope) html += `<div class="dash-drawer-section">Conteúdo / escopo</div><div class="dash-drawer-text">${esc(it.scope)}</div>`;
      if (it.body) html += `<div class="dash-drawer-section">Texto</div><div class="dash-drawer-text">${esc(it.body)}</div>`;
      if (it.material) {
        const m = it.material;
        html += `<div class="dash-drawer-section">Material</div><div class="dash-drawer-fields">
          <div class="dash-drawer-field"><span>Download</span><strong>${statusBadge(m.status)}</strong></div>
          <div class="dash-drawer-field"><span>Extração</span><strong>${m.doc_status ? statusBadge(m.doc_status) : '—'}</strong></div>
          ${m.page_count ? `<div class="dash-drawer-field"><span>Páginas</span><strong>${m.page_count}</strong></div>` : ''}
          ${m.size_bytes ? `<div class="dash-drawer-field"><span>Tamanho</span><strong>${(m.size_bytes / 1024).toFixed(0)} KB</strong></div>` : ''}
        </div>`;
        if (m.error) html += `<div class="dash-alert dash-alert-warn"><i data-lucide="alert-triangle"></i>${esc(m.error)}</div>`;
        if (m.summary) html += summaryHtml(m.summary);
      }
      const actions = [];
      if (it.material && it.material.file_available) actions.push(`<a class="dash-btn dash-btn-primary" href="/api/materials/${encodeURIComponent(it.id)}/file" target="_blank" rel="noopener"><i data-lucide="file-down"></i>Abrir arquivo</a>`);
      if (it.url) actions.push(`<a class="dash-btn dash-btn-outline" href="${esc(it.url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Abrir no SIGAA</a>`);
      actions.push(`<a class="dash-btn dash-btn-ghost" href="#/disciplinas/${encodeURIComponent(it.course_id)}" data-drawer-nav><i data-lucide="book-open"></i>Ver disciplina</a>`);
      html += `<div class="dash-drawer-actions">${actions.join('')}</div>`;
      if (it.events && it.events.length) {
        html += `<div class="dash-drawer-section">Histórico</div><div class="dash-timeline">${it.events.map(eventHtml).join('')}</div>`;
      }
      openDrawer(html);
      $('drawer-body').querySelectorAll('[data-drawer-nav]').forEach((a) => a.addEventListener('click', closeDrawer));
    } catch (e) { openDrawer(empty('Não consegui carregar este item.', 'alert-circle')); }
  }

  function summaryHtml(s) {
    let h = `<div class="dash-drawer-section">Resumo (IA)</div><div class="dash-drawer-text">${esc(s.summary)}</div>`;
    if (s.topics && s.topics.length) {
      h += `<div class="dash-drawer-section">Tópicos</div><ul class="dash-task-steps">${s.topics.map((t) => `<li><b>${esc((t.refs || []).slice(0, 2).join(', '))}</b><span>${esc(t.topic)}</span></li>`).join('')}</ul>`;
    }
    if (s.exercises_refs && s.exercises_refs.length) h += `<div class="dash-task-kv mt-2"><b>Exercícios em:</b> ${esc(s.exercises_refs.join(', '))}</div>`;
    if (s.missing_or_unclear && s.missing_or_unclear.length) h += `<div class="dash-task-kv mt-1"><b>Lacunas:</b> ${esc(s.missing_or_unclear.join('; '))}</div>`;
    return h;
  }

  function eventHtml(e) {
    const cls = e.event_type === 'deadline_changed' ? 'is-deadline' : e.kind === 'grade' ? 'is-grade' : e.kind === 'material' ? 'is-material' : '';
    let diff = '';
    if (e.event_type === 'deadline_changed' && e.old && e.new) {
      const o = e.old.due_at || e.old.due_date, n = e.new.due_at || e.new.due_date;
      diff = `<div class="dash-list-meta">${esc(fmtDue(o))} → <b>${esc(fmtDue(n))}</b></div>`;
    }
    return `<div class="dash-tl-item ${cls}">
      <div class="dash-list-title" style="cursor:pointer" data-item="${esc(e.item_id)}">${esc(e.course)}: ${esc(e.line)}</div>
      ${diff}<div class="dash-tl-date">${esc(fmtDateTime(e.detected_at))} · ${esc(e.event_label)}</div></div>`;
  }

  // delegação: qualquer [data-item] abre o drawer
  document.addEventListener('click', (ev) => {
    const t = ev.target.closest('[data-item]');
    if (t && !ev.target.closest('a')) { ev.preventDefault(); openItem(t.dataset.item); }
  });

  // ------------------------------------------------------------------ views
  const main = $('main');
  const views = {};

  function setPage(title, isChat) {
    $('page-title').textContent = title;
    document.title = `${title} · SIGAA Simulado`;
    document.body.classList.toggle('is-chat', !!isChat);
  }
  function render(html) { main.innerHTML = `<div class="dash-animate">${html}</div>`; icons(); }

  // --- início
  views.home = async () => {
    setPage('Início');
    render(skeleton(1, 8) + `<div class="dash-kpi-grid mb-4">${skeleton(4, 6).replace(/mb-3/g, '')}</div>`);
    const ov = await api('overview');
    const k = ov.kpis;
    const alerts = [];
    if (!ov.last_run) alerts.push(`<div class="dash-alert dash-alert-info"><i data-lucide="info"></i><span>Nenhuma coleta ainda. Vá em <a href="#/operacao" class="underline">Agente</a> e execute <b>Coletar e processar</b>.</span></div>`);
    if (ov.last_run && ov.last_run.status === 'failed') alerts.push(`<div class="dash-alert dash-alert-danger"><i data-lucide="alert-circle"></i><span>Última coleta falhou${ov.last_run.error ? ': ' + esc(ov.last_run.error) : ''}. Dados mostrados são de ${esc(fmtDateTime(ov.last_success_at))}.</span></div>`);
    if (ov.sections_bad.length) alerts.push(`<div class="dash-alert dash-alert-warn"><i data-lucide="alert-triangle"></i><span>${ov.sections_bad.length} seção(ões) com falha na última coleta: ${esc(ov.sections_bad.slice(0, 3).map((s) => `${s.course}/${s.section}`).join(', '))}${ov.sections_bad.length > 3 ? '…' : ''}</span></div>`);
    if (k.jobs_pending > 0) alerts.push(`<div class="dash-alert dash-alert-info"><i data-lucide="clock"></i><span>${k.jobs_pending} pendência(s) de processamento (downloads/IA). <a href="#/operacao" class="underline">Processar agora</a>.</span></div>`);

    const kpis = [
      { label: 'Prazos em 7 dias', value: k.deadlines_7d, sub: `${k.deadlines_30d} em 30 dias`, icon: 'calendar-clock', href: '#/prazos', accent: k.deadlines_7d > 0 ? '#ef4444' : null },
      { label: 'Novidades (7 dias)', value: k.events_7d, sub: `${k.grades_7d} nota(s)`, icon: 'history', href: '#/novidades', accent: '#3b82f6' },
      { label: 'Disciplinas', value: k.courses, sub: `${k.items} itens acompanhados`, icon: 'book-open', href: '#/disciplinas' },
      { label: 'Materiais', value: `${k.materials_downloaded}/${k.materials_total}`, sub: 'baixados', icon: 'paperclip', href: '#/materiais' },
    ];
    const kpiHtml = kpis.map((c) => `
      <a href="${c.href}" class="dash-kpi-tile ${c.accent ? 'dash-kpi-accent' : ''}" ${c.accent ? `style="--kpi-accent:${c.accent}"` : ''}>
        <div class="dash-kpi-tile-head"><span class="dash-kpi-label">${esc(c.label)}</span><span class="dash-kpi-tile-icon"><i data-lucide="${c.icon}"></i></span></div>
        <div><div class="dash-kpi-value">${esc(c.value)}</div><div class="dash-kpi-sub">${esc(c.sub)}</div></div></a>`).join('');

    const deadlines = ov.next_deadlines.length ? `<div class="dash-list">${ov.next_deadlines.map((d) => `
      <div class="dash-list-item is-clickable" data-item="${esc(d.item_id)}">${dayBox(d.due, d.days_left)}
        <div class="dash-list-main"><div class="dash-list-title">${esc(d.title)}</div><div class="dash-list-meta">${esc(d.course)} · ${esc(d.kind)} · ${esc(d.due_label)}</div></div>
        <div class="dash-list-side">${badge(d.relative, d.days_left <= 1 ? 'danger' : d.days_left <= 7 ? 'warning' : 'light')}</div></div>`).join('')}</div>` : empty('Nenhum prazo nos próximos 30 dias.', 'calendar-check');

    const events = ov.recent_events.length ? `<div class="dash-timeline">${ov.recent_events.map(eventHtml).join('')}</div>` : empty('Sem novidades nos últimos 7 dias.', 'inbox');

    let planHtml;
    if (ov.plan) {
      const pct = ov.plan.tasks ? Math.round((ov.plan.done / ov.plan.tasks) * 100) : 0;
      planHtml = `<div class="dash-minutes-bar mb-2"><span>${ov.plan.done}/${ov.plan.tasks} sessões</span><div class="dash-progress"><div class="dash-progress-bar dash-progress-ok" style="width:${pct}%"></div></div><span>${minutesLabel(ov.plan.minutes_done)} de ${minutesLabel(ov.plan.available_minutes)}</span></div>
        <p class="text-sm text-slate-500 mb-3">Plano v${ov.plan.version} · ${ov.plan.source === 'llm' ? 'sequência da IA' : 'plano básico'}</p>
        <a href="#/plano" class="dash-btn dash-btn-primary"><i data-lucide="target"></i>Abrir plano de hoje</a>`;
    } else {
      planHtml = `<p class="text-sm text-slate-500 mb-3">Ainda não há plano para hoje.</p><a href="#/plano" class="dash-btn dash-btn-primary"><i data-lucide="sparkles"></i>Gerar plano de hoje</a>`;
    }

    render(`${intro('Seu dia acadêmico', `${ov.institution.toUpperCase()} · perfil ${ov.profile} · ${ov.last_success_at ? 'última coleta ' + fmtDateTime(ov.last_success_at) : 'sem coleta concluída'}`)}
      ${alerts.length ? `<div class="flex flex-col gap-2 mb-4">${alerts.join('')}</div>` : ''}
      <div class="dash-kpi-grid mb-5">${kpiHtml}</div>
      <section class="mb-5"><h2 class="dash-section-title">O que fazer agora?</h2><nav class="dash-quick-actions">
        <a href="#/chat" class="dash-quick-action dash-quick-action-primary"><i data-lucide="sparkles"></i>Perguntar ao agente</a>
        <a href="#/plano" class="dash-quick-action"><i data-lucide="target"></i>Plano de estudo</a>
        <a href="#/prazos" class="dash-quick-action"><i data-lucide="calendar-clock"></i>Ver prazos</a>
        <a href="#/buscar" class="dash-quick-action"><i data-lucide="search"></i>Buscar nos materiais</a>
        <a href="#/operacao" class="dash-quick-action"><i data-lucide="refresh-cw"></i>Coletar agora</a></nav></section>
      <div class="dash-grid-3">
        ${card('Próximos prazos', 'calendar-clock', deadlines, `<a href="#/prazos" class="dash-btn dash-btn-ghost dash-btn-sm">ver todos</a>`)}
        ${card('Estudo de hoje', 'target', planHtml)}
        ${card('Novidades recentes', 'history', events, `<a href="#/novidades" class="dash-btn dash-btn-ghost dash-btn-sm">ver todas</a>`)}
      </div>`);
  };

  // --- plano
  views.plano = async (params) => {
    setPage('Plano de estudo');
    const day = params.date || new Date().toISOString().slice(0, 10);
    render(skeleton(3, 6));
    const data = await api(`plan?date=${day}`);
    renderPlan(day, data.plan, data.available_minutes);
  };

  function renderPlan(day, plan, avail) {
    const dateInput = `<input type="date" id="plan-date" class="dash-input" style="width:auto" value="${esc(day)}">`;
    const actions = `${dateInput}
      <button type="button" class="dash-btn dash-btn-outline" id="plan-noai" title="Plano básico, sem IA"><i data-lucide="list"></i>Básico</button>
      <button type="button" class="dash-btn dash-btn-primary" id="plan-new"><i data-lucide="sparkles"></i>${plan ? 'Replanejar' : 'Gerar plano'}</button>`;
    const d = new Date(day + 'T12:00:00');
    const title = `Plano de ${d.toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long' })}`;
    let body;
    if (!plan) {
      body = `<div class="dash-card"><div class="dash-empty"><i data-lucide="target"></i>Ainda não há plano para este dia (${minutesLabel(avail)} disponíveis). Clique em <b>Gerar plano</b>.</div></div>`;
    } else {
      const pct = plan.available_minutes ? Math.min(100, Math.round((plan.used_minutes / plan.available_minutes) * 100)) : 0;
      const done = plan.tasks.filter((t) => t.status === 'done').length;
      const head = `<div class="dash-card dash-card-pad mb-4">
        <div class="flex flex-wrap items-center justify-between gap-3 mb-2">
          <div class="flex flex-wrap gap-2 items-center">${badge(`v${plan.version}`, 'light')}${badge(plan.source === 'llm' ? 'sequência da IA' : 'plano básico', plan.source === 'llm' ? 'brand' : 'light')}${plan.stale ? badge('compromissos mudaram — replaneje', 'warning') : ''}</div>
          <span class="text-sm text-slate-500">${done}/${plan.tasks.length} sessões concluídas</span></div>
        <div class="dash-minutes-bar"><span>${minutesLabel(plan.used_minutes)} planejados</span><div class="dash-progress"><div class="dash-progress-bar" style="width:${pct}%"></div></div><span>${minutesLabel(plan.available_minutes)} disponíveis</span></div>
        ${plan.notes.length ? `<p class="text-xs text-slate-500 mt-3">${esc(plan.notes.join(' · '))}</p>` : ''}</div>`;
      const tasks = plan.tasks.length ? plan.tasks.map(taskHtml).join('') : `<div class="dash-card">${empty(plan.notes[0] || 'Sem sessões para este dia.', 'coffee')}</div>`;
      const commits = plan.commitments.length ? `<div class="dash-list">${plan.commitments.map((c) => `
        <div class="dash-list-item is-clickable" data-item="${esc(c.item_id)}">${dayBox(c.due, c.days_left)}
          <div class="dash-list-main"><div class="dash-list-title">${esc(c.title)}</div><div class="dash-list-meta">${esc(c.course)} · ${esc(c.reasons.join(', '))}</div></div>
          <div class="dash-list-side"><span title="prioridade explicável">prio ${c.priority}</span></div></div>`).join('')}</div>` : empty('Nenhum compromisso no horizonte.', 'calendar-check');
      body = `${head}<div class="dash-grid-3"><div class="dash-span-2 flex flex-col gap-3">${tasks}</div>${card('Compromissos considerados', 'calendar-clock', commits)}</div>`;
    }
    render(intro(title, 'Prioridade calculada em código (prazo, peso, frequência do tópico, pendência); a IA só sugere a sequência.', actions) + body);

    $('plan-date').addEventListener('change', (e) => { location.hash = `#/plano?date=${e.target.value}`; });
    const build = async (opts) => {
      const btn = $('plan-new'); btn.classList.add('is-loading'); btn.disabled = true; $('plan-noai').disabled = true;
      try {
        const r = await post('plan', Object.assign({ date: day }, opts));
        toast('Plano atualizado.'); renderPlan(day, r.plan, avail);
      } catch (e) { toast(`Falha ao planejar: ${e.message}`, 'err'); btn.classList.remove('is-loading'); btn.disabled = false; $('plan-noai').disabled = false; }
    };
    $('plan-new').addEventListener('click', () => build({ new: !!plan }));
    $('plan-noai').addEventListener('click', () => build({ new: !!plan, no_ai: true }));
    main.querySelectorAll('[data-progress]').forEach((b) => b.addEventListener('click', () => progressDialog(b.dataset.progress, day, avail)));
  }

  function taskHtml(t) {
    const stBadge = t.status === 'pending' ? '' : statusBadge(t.status);
    const diff = t.difficulty ? badge({ easy: 'fácil', ok: 'ok', hard: 'difícil' }[t.difficulty] || t.difficulty, 'light') : '';
    return `<article class="dash-task ${t.status === 'done' ? 'is-done' : ''}">
      <div class="dash-task-head"><span class="dash-task-seq">${t.status === 'done' ? '✓' : t.seq}</span>
        <div class="flex-1 min-w-0"><div class="flex flex-wrap items-center gap-2"><strong class="text-sm">${esc(t.course)}</strong>${badge(minutesLabel(t.minutes), 'brand')}${stBadge}${diff}</div>
        <div class="text-sm mt-1 font-medium">${esc(t.objective)}</div></div></div>
      <div class="dash-task-body">
        <div class="dash-task-kv"><b>Por que agora:</b> ${esc(t.why_now)}</div>
        <ol class="dash-task-steps">${t.steps.map((s) => `<li><b>${s.minutes} min</b><span>${esc(s.action)}</span></li>`).join('')}</ol>
        <div class="dash-task-kv"><b>Concluído quando:</b> ${esc(t.done_when)}</div>
        ${t.materials.length ? `<div class="dash-task-kv"><b>Fontes:</b> ${t.materials.map((m) => `<a href="#" data-item="${esc(m.id)}" class="underline">${esc(m.title)}</a>`).join(', ')}${t.is_suggested_exercise ? ' · exercícios sugeridos (autorais)' : ''}</div>` : ''}
        ${t.note ? `<div class="dash-task-kv"><b>Sua nota:</b> ${esc(t.note)}</div>` : ''}
        ${t.task_id ? `<div class="dash-task-actions"><button type="button" class="dash-btn dash-btn-primary dash-btn-sm" data-progress="${t.task_id}"><i data-lucide="check"></i>Registrar progresso</button></div>` : ''}
      </div></article>`;
  }

  function progressDialog(taskId, day, avail) {
    openDrawer(`<h3 class="dash-drawer-title">Registrar progresso</h3><p class="text-sm text-slate-500 mb-4">Isso alimenta a prioridade das próximas sessões. Não altera nada no SIGAA.</p>
      <form id="progress-form" class="flex flex-col gap-3">
        <div><label class="dash-form-label">Status</label><select name="status" class="dash-select"><option value="done">Concluí</option><option value="in_progress">Comecei</option><option value="skipped">Pulei</option><option value="pending">Voltar a pendente</option></select></div>
        <div><label class="dash-form-label">Dificuldade</label><select name="difficulty" class="dash-select"><option value="">—</option><option value="easy">Fácil</option><option value="ok">Ok</option><option value="hard">Difícil</option></select></div>
        <div><label class="dash-form-label">Minutos gastos (opcional)</label><input name="minutes" type="number" min="0" class="dash-input"></div>
        <div><label class="dash-form-label">Nota (dúvida, o que travou)</label><textarea name="note" rows="3" class="dash-textarea" maxlength="500"></textarea></div>
        <button type="submit" class="dash-btn dash-btn-primary dash-btn-lg">Salvar</button></form>`);
    $('progress-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        await post('progress', { task_id: Number(taskId), status: fd.get('status'), difficulty: fd.get('difficulty') || null, minutes: Number(fd.get('minutes')) || null, note: fd.get('note') });
        toast('Progresso registrado.'); closeDrawer();
        const data = await api(`plan?date=${day}`); renderPlan(day, data.plan, avail);
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  // --- prazos
  views.prazos = async (params) => {
    setPage('Prazos');
    const horizon = Number(params.h) || 60;
    render(skeleton(4, 4));
    const list = await api(`deadlines?horizon=${horizon}`);
    const periods = [14, 30, 60, 120].map((h) => `<a href="#/prazos?h=${h}" class="dash-period-btn ${h === horizon ? 'is-active' : ''}">${h} dias</a>`).join('');
    const groups = {};
    list.forEach((d) => {
      const g = d.days_left <= 0 ? 'Hoje' : d.days_left <= 7 ? 'Esta semana' : d.days_left <= 14 ? 'Próxima semana' : d.days_left <= 30 ? 'Este mês' : 'Depois';
      (groups[g] = groups[g] || []).push(d);
    });
    const body = list.length ? Object.entries(groups).map(([g, ds]) => `<h2 class="dash-section-title mt-4">${esc(g)} · ${ds.length}</h2><div class="dash-card"><div class="dash-card-body"><div class="dash-list">${ds.map((d) => `
      <div class="dash-list-item is-clickable" data-item="${esc(d.item_id)}">${dayBox(d.due, d.days_left)}
        <div class="dash-list-main"><div class="dash-list-title">${esc(d.title)}</div><div class="dash-list-meta">${kindBadge(d.kind, d.kind_label)}<span>${esc(d.course)}</span>${d.weight ? `<span>· peso ${d.weight}</span>` : ''}${d.scope ? `<span class="truncate" style="max-width:26rem">· ${esc(d.scope)}</span>` : ''}</div></div>
        <div class="dash-list-side">${badge(d.relative, d.days_left <= 1 ? 'danger' : d.days_left <= 7 ? 'warning' : 'light')}<span>${esc(d.due_label.split(',')[0])}${d.has_time ? '' : ' · sem horário'}</span></div></div>`).join('')}</div></div></div>`).join('')
      : `<div class="dash-card">${empty(`Nenhum prazo nos próximos ${horizon} dias.`, 'calendar-check')}</div>`;
    render(intro('Entregas e provas', 'Datas vêm direto dos registros do SIGAA. Sem horário informado, nada é inventado.', `<div class="dash-tabs">${periods}</div>`) + body);
  };

  // --- disciplinas
  views.disciplinas = async (params, rest) => {
    if (rest) return viewCourse(decodeURIComponent(rest));
    setPage('Disciplinas');
    render(skeleton(3, 8));
    const list = await api('courses');
    const cards = list.map((c) => {
      const counts = Object.entries(c.counts).map(([k, n]) => badge(`${n} ${({ announcement: 'avisos', activity: 'tarefas', assessment: 'avaliações', grade: 'notas', content: 'conteúdos', material: 'materiais' })[k] || k}`, 'light')).join('');
      const nd = c.next_deadline ? `<div class="dash-list-meta"><i data-lucide="calendar-clock" style="width:.8rem;height:.8rem"></i> ${esc(c.next_deadline.title)} · <b>${esc(c.next_deadline.relative)}</b></div>` : '<div class="dash-list-meta">sem prazos próximos</div>';
      return `<a href="#/disciplinas/${encodeURIComponent(c.id)}" class="dash-course-card"><strong>${esc(c.short_name)}</strong><small>${esc(c.code)} · ${esc(c.term)}${c.professor ? ' · ' + esc(c.professor) : ''}</small>${nd}<div class="dash-course-counts">${counts}</div></a>`;
    }).join('');
    render(intro('Disciplinas', `${list.length} turma(s) coletada(s)`) + (list.length ? `<div class="dash-course-grid">${cards}</div>` : `<div class="dash-card">${empty('Nenhuma disciplina coletada ainda.', 'book-open')}</div>`));
  };

  async function viewCourse(courseId) {
    render(skeleton(3, 6));
    const d = await api(`courses/${encodeURIComponent(courseId)}`);
    setPage(d.course.short_name);
    const order = ['assessment', 'activity', 'announcement', 'grade', 'content', 'material'];
    const labels = { assessment: 'Avaliações', activity: 'Tarefas', announcement: 'Avisos', grade: 'Notas', content: 'Conteúdo', material: 'Materiais' };
    const sections = order.filter((k) => d.items[k] && d.items[k].length).map((k) => {
      const rows = d.items[k].map((it) => itemRow(it)).join('');
      return card(`${labels[k]} · ${d.items[k].length}`, { assessment: 'clipboard-list', activity: 'file-check', announcement: 'megaphone', grade: 'clipboard-check', content: 'list', material: 'paperclip' }[k], `<div class="dash-list">${rows}</div>`);
    }).join('');
    render(intro(d.course.short_name, `${d.course.name} · ${d.course.code} · ${d.course.term}${d.course.professor ? ' · ' + d.course.professor : ''}`,
      `<a href="#/disciplinas" class="dash-btn dash-btn-ghost"><i data-lucide="arrow-left"></i>Disciplinas</a>${d.course.url ? `<a href="${esc(d.course.url)}" target="_blank" rel="noopener" class="dash-btn dash-btn-outline"><i data-lucide="external-link"></i>SIGAA</a>` : ''}<a href="#/buscar?course=${encodeURIComponent(d.course.id)}" class="dash-btn dash-btn-outline"><i data-lucide="search"></i>Buscar nos materiais</a>`)
      + (sections ? `<div class="dash-grid-2">${sections}</div>` : `<div class="dash-card">${empty('Nenhum item coletado para esta turma.', 'inbox')}</div>`));
  }

  function itemRow(it) {
    let meta = [];
    if (it.published_label) meta.push(`publicado ${it.published_label}`);
    if (it.due_label) meta.push(`<b>${it.due_relative}</b> · ${it.due_label}`);
    if (it.grade_value) meta.push(`<b>${esc(it.grade_value)}${it.grade_max ? ' / ' + esc(it.grade_max) : ''}</b>`);
    if (it.topic) meta.push(esc(it.topic));
    if (it.weight) meta.push(`peso ${it.weight}`);
    return `<div class="dash-list-item is-clickable" data-item="${esc(it.id)}"><div class="dash-list-main"><div class="dash-list-title">${esc(it.title)}</div><div class="dash-list-meta">${meta.join(' · ')}</div>${it.body && it.kind === 'announcement' ? `<div class="text-xs text-slate-500 mt-1 line-clamp-2">${esc(it.body)}</div>` : ''}</div></div>`;
  }

  // --- avisos
  views.avisos = async (params) => {
    setPage('Avisos');
    render(skeleton(4, 4));
    const [list, courses] = await Promise.all([api(`items?kind=announcement${params.course ? '&course=' + encodeURIComponent(params.course) : ''}`), api('courses')]);
    const sel = `<select id="filter-course" class="dash-select" style="width:auto"><option value="">Todas as disciplinas</option>${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === params.course ? 'selected' : ''}>${esc(c.short_name)}</option>`).join('')}</select>`;
    const body = list.length ? `<div class="dash-card"><div class="dash-card-body"><div class="dash-list">${list.map((it) => `
      <div class="dash-list-item is-clickable" data-item="${esc(it.id)}"><div class="dash-list-main"><div class="dash-list-meta mb-1">${badge(it.course, 'brand')}<span>${esc(it.published_label || '')}</span></div><div class="dash-list-title">${esc(it.title)}</div>${it.body ? `<div class="text-sm text-slate-500 mt-1 line-clamp-3">${esc(it.body)}</div>` : ''}</div></div>`).join('')}</div></div></div>` : `<div class="dash-card">${empty('Nenhum aviso encontrado.', 'megaphone')}</div>`;
    render(intro('Avisos dos professores', `${list.length} aviso(s) · fonte: SIGAA · Notícias`, sel) + body);
    $('filter-course').addEventListener('change', (e) => { location.hash = `#/avisos${e.target.value ? '?course=' + encodeURIComponent(e.target.value) : ''}`; });
  };

  // --- notas
  views.notas = async () => {
    setPage('Notas');
    render(skeleton(3, 6));
    const groups = await api('grades');
    const body = groups.length ? `<div class="dash-grid-2">${groups.map((g) => card(g.course, 'clipboard-check', `<div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>Avaliação</th><th>Nota</th></tr></thead><tbody>${g.grades.map((it) => `<tr class="is-clickable" data-item="${esc(it.id)}"><td>${esc(it.title)}${it.grade_label ? `<div class="text-xs text-slate-400">${esc(it.grade_label)}</div>` : ''}</td><td><b>${esc(it.grade_value || '—')}</b>${it.grade_max ? `<span class="text-slate-400"> / ${esc(it.grade_max)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>`)).join('')}</div>` : `<div class="dash-card">${empty('Nenhuma nota lançada ainda.', 'clipboard-check')}</div>`;
    render(intro('Notas lançadas', 'Só o que está publicado no SIGAA. Mudanças aparecem em Novidades.') + body);
  };

  // --- materiais
  views.materiais = async (params) => {
    setPage('Materiais');
    render(skeleton(4, 4));
    const [list, courses] = await Promise.all([api(`materials${params.course ? '?course=' + encodeURIComponent(params.course) : ''}`), api('courses')]);
    const sel = `<select id="filter-course" class="dash-select" style="width:auto"><option value="">Todas as disciplinas</option>${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === params.course ? 'selected' : ''}>${esc(c.short_name)}</option>`).join('')}</select>`;
    const ok = list.filter((m) => m.status === 'downloaded').length;
    const rows = list.map((m) => `<tr class="is-clickable" data-item="${esc(m.item_id)}">
      <td><div class="font-medium">${esc(m.title)}</div><div class="text-xs text-slate-400">${esc(m.file_name || '')}${m.topic ? ' · ' + esc(m.topic) : ''}</div></td>
      <td>${esc(m.course)}</td><td>${statusBadge(m.status)}</td><td>${m.doc_status ? statusBadge(m.doc_status) : '—'}${m.page_count ? `<div class="text-xs text-slate-400">${m.page_count} pág.</div>` : ''}</td>
      <td>${m.summary ? badge('resumo IA', 'brand') : ''}</td>
      <td>${m.file_available ? `<a class="dash-btn dash-btn-ghost dash-btn-sm" href="/api/materials/${encodeURIComponent(m.item_id)}/file" target="_blank" rel="noopener" title="Abrir arquivo"><i data-lucide="file-down"></i></a>` : ''}</td></tr>`).join('');
    const body = list.length ? `<div class="dash-card"><div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>Arquivo</th><th>Disciplina</th><th>Download</th><th>Texto</th><th>IA</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></div>` : `<div class="dash-card">${empty('Nenhum material coletado.', 'paperclip')}</div>`;
    render(intro('Materiais', `${ok} de ${list.length} baixados · arquivos por hash, nunca extraídos duas vezes`, sel) + body);
    $('filter-course').addEventListener('change', (e) => { location.hash = `#/materiais${e.target.value ? '?course=' + encodeURIComponent(e.target.value) : ''}`; });
  };

  // --- novidades
  views.novidades = async (params) => {
    setPage('Novidades');
    const days = Number(params.d) || 14;
    render(skeleton(5, 3));
    const list = await api(`events?days=${days}`);
    const periods = [1, 3, 7, 14, 30, 90].map((d) => `<a href="#/novidades?d=${d}" class="dash-period-btn ${d === days ? 'is-active' : ''}">${d === 1 ? '24h' : d + ' dias'}</a>`).join('');
    const byDay = {};
    list.forEach((e) => { const k = new Date(e.detected_at).toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'short' }); (byDay[k] = byDay[k] || []).push(e); });
    const body = list.length ? Object.entries(byDay).map(([d, evs]) => `<h2 class="dash-section-title mt-4">${esc(d)} · ${evs.length}</h2><div class="dash-card dash-card-pad"><div class="dash-timeline">${evs.map(eventHtml).join('')}</div></div>`).join('') : `<div class="dash-card">${empty(`Nenhuma mudança detectada nos últimos ${days} dias.`, 'inbox')}</div>`;
    render(intro('O que mudou', 'Cada alteração detectada entre coletas: prazos, notas, arquivos, avisos.', `<div class="dash-tabs">${periods}</div>`) + body);
  };

  // --- progresso
  views.progresso = async () => {
    setPage('Progresso');
    render(skeleton(3, 6));
    const d = await api('progress?days=30');
    const maxMin = Math.max(1, ...d.daily.map((x) => x.minutes));
    const bars = d.daily.length ? `<div class="dash-bars">${d.daily.map((x) => `<div style="height:${Math.max(4, (x.minutes / maxMin) * 100)}%" title="${esc(x.date)}: ${x.minutes} min"></div>`).join('')}</div><div class="dash-bars-labels"><span>${esc(d.daily[0].date)}</span><span>${esc(d.daily[d.daily.length - 1].date)}</span></div>` : empty('Nenhuma sessão concluída nos últimos 30 dias.', 'bar-chart-3');
    const perCourse = d.per_course.length ? d.per_course.map((c) => { const pct = c.total ? Math.round((c.done / c.total) * 100) : 0; return `<div class="mb-3"><div class="flex justify-between text-sm mb-1"><span class="font-medium">${esc(c.course)}</span><span class="text-slate-500">${c.done}/${c.total} · ${minutesLabel(c.minutes)}</span></div><div class="dash-progress"><div class="dash-progress-bar ${pct >= 70 ? 'dash-progress-ok' : pct >= 30 ? 'dash-progress-warn' : 'dash-progress-crit'}" style="width:${pct}%"></div></div></div>`; }).join('') : empty('Sem sessões planejadas ainda.', 'target');
    const log = d.entries.length ? `<div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>Quando</th><th>Disciplina</th><th>Sessão</th><th>Status</th><th>Dif.</th><th>Nota</th></tr></thead><tbody>${d.entries.slice(0, 60).map((e) => `<tr><td class="whitespace-nowrap">${esc(fmtDateTime(e.recorded_at))}</td><td>${esc(e.course)}</td><td>${esc(e.objective || '')}</td><td>${statusBadge(e.status)}</td><td>${e.difficulty ? ({ easy: 'fácil', ok: 'ok', hard: 'difícil' })[e.difficulty] : ''}</td><td class="text-slate-500">${esc(e.note || '')}</td></tr>`).join('')}</tbody></table></div>` : empty('Nenhum registro ainda. Marque sessões no plano.', 'clipboard-list');
    render(intro('Progresso de estudo', 'Registros feitos por você; alimentam a pendência na prioridade.') + `<div class="dash-grid-2 mb-4">${card('Minutos por dia (30 dias)', 'bar-chart-3', bars)}${card('Por disciplina', 'book-open', perCourse)}</div>${card('Histórico', 'history', log)}`);
  };

  // --- buscar
  views.buscar = async (params) => {
    setPage('Buscar');
    const q = params.q || '';
    const courses = await api('courses');
    const sel = `<select id="search-course" class="dash-select"><option value="">Todas</option>${courses.map((c) => `<option value="${esc(c.id)}" ${c.id === params.course ? 'selected' : ''}>${esc(c.short_name)}</option>`).join('')}</select>`;
    render(intro('Buscar nos materiais', 'Busca textual (FTS5) nos arquivos extraídos. Resultados citam arquivo e página/slide.') +
      `<form id="search-form" class="dash-card dash-card-pad mb-4"><div class="flex flex-col sm:flex-row gap-2"><input id="search-q" class="dash-input" placeholder="ex.: jacobiano coordenadas polares" value="${esc(q)}" autofocus><div class="flex gap-2">${sel}<button type="submit" class="dash-btn dash-btn-primary"><i data-lucide="search"></i>Buscar</button></div></div></form><div id="search-results"></div>`);
    $('search-form').addEventListener('submit', (e) => { e.preventDefault(); const c = $('search-course').value; location.hash = `#/buscar?q=${encodeURIComponent($('search-q').value)}${c ? '&course=' + encodeURIComponent(c) : ''}`; });
    if (!q.trim()) return;
    const box = $('search-results'); box.innerHTML = skeleton(3, 4);
    const r = await api(`search?q=${encodeURIComponent(q)}${params.course ? '&course=' + encodeURIComponent(params.course) : ''}&limit=20`);
    const terms = q.toLowerCase().split(/\s+/).filter((t) => t.length >= 3);
    const hl = (t) => { let h = esc(t); terms.forEach((w) => { h = h.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'), '<mark>$1</mark>'); }); return h; };
    box.innerHTML = r.hits.length ? `<div class="dash-card dash-card-pad">${!r.fts ? `<div class="dash-alert dash-alert-warn mb-3"><i data-lucide="alert-triangle"></i>FTS5 indisponível: busca simples (LIKE).</div>` : ''}${r.hits.map((h) => `<div class="dash-hit"><div class="dash-hit-title"><a href="#" data-item="${esc(h.item_id)}">${esc(h.title)}</a>${badge(h.ref, 'brand')}${badge(h.course, 'light')}</div><div class="dash-hit-text">${hl(h.text.slice(0, 600))}${h.text.length > 600 ? '…' : ''}</div></div>`).join('')}</div>` : `<div class="dash-card">${empty(`Nada encontrado para “${q}”. Materiais sem texto (no_text) não são pesquisáveis.`, 'search-x')}</div>`;
    icons();
  };

  // --- chat
  const chat = { busy: false, history: [] };
  views.chat = async () => {
    setPage('Chat', true);
    const chips = [['O que eu estudo hoje?', 'hoje'], ['Tem novidade?', 'resumo'], ['Próximos prazos', 'pz:1'], ['Minhas notas', 'notas'], ['Status do agente', 'status:full']];
    main.innerHTML = `<div class="dash-chat">
      <div class="dash-chat-topbar"><span class="text-xs text-slate-500 px-1">Mesmo roteador do Telegram: datas por template, conteúdo via IA com fonte.</span>
        <button type="button" class="dash-chat-topbtn dash-chat-topbtn--primary" id="chat-new"><i data-lucide="plus"></i><span>Limpar</span></button></div>
      <div class="dash-chat-scroll" id="chat-scroll">
        <div class="dash-chat-empty" id="chat-empty"><h1>Como posso ajudar?</h1><p>Pergunte sobre prazos, notas, avisos ou conteúdo dos seus materiais. Comandos como <code>/prazos</code> também funcionam.</p>
          <div class="dash-chat-empty-grid">${chips.map((c) => `<button type="button" class="dash-chat-action" data-callback="${esc(c[1])}">${esc(c[0])}</button>`).join('')}</div></div>
        <div class="dash-chat-thread" id="chat-thread" hidden></div></div>
      <div class="dash-chat-composer-wrap"><div class="dash-chat-composer-inner">
        <div class="dash-chat-chips">${chips.map((c) => `<button type="button" class="dash-chat-chip" data-callback="${esc(c[1])}">${esc(c[0])}</button>`).join('')}</div>
        <div class="dash-chat-composer"><textarea id="chat-input" class="dash-chat-input" rows="1" placeholder="Quando é minha prova de Física II?"></textarea>
          <button type="button" class="dash-chat-send" id="chat-send" aria-label="Enviar" disabled><i data-lucide="arrow-up"></i></button></div>
        <p class="dash-chat-hint">Enter envia · Shift+Enter quebra linha · nada é executado, só consultado</p></div></div></div>`;
    icons();
    const input = $('chat-input'), send = $('chat-send'), thread = $('chat-thread'), emptyEl = $('chat-empty'), scroll = $('chat-scroll');
    const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 160)}px`; };
    const show = () => { emptyEl.hidden = true; thread.hidden = false; };
    const toEnd = () => { scroll.scrollTop = scroll.scrollHeight; };
    const row = (side, html) => { show(); const r = document.createElement('div'); r.className = `dash-chat-row dash-chat-row--${side}`; r.innerHTML = `<div class="dash-chat-bubble">${html}</div>`; thread.appendChild(r); toEnd(); return r; };
    const lock = () => thread.querySelectorAll('.dash-chat-action').forEach((b) => { b.disabled = true; });
    let typing = null;
    const setBusy = (v) => { chat.busy = v; input.disabled = v; send.disabled = v || !input.value.trim(); };
    const viewHtml = (v) => {
      const bold = (line, bolds) => { let h = esc(line); (bolds || []).forEach((b) => { if (b) h = h.split(esc(b)).join(`<b>${esc(b)}</b>`); }); return h; };
      let h = `<div class="dash-chat-title">${v.emoji ? esc(v.emoji) + ' ' : ''}${esc(v.title)}</div>`;
      if (v.subtitle) h += `<div class="dash-chat-subtitle">${esc(v.subtitle)}</div>`;
      (v.blocks || []).forEach((b) => { h += `<div class="dash-chat-block ${b.italic ? 'is-italic' : ''}">${b.lines.map((l) => `<p>${bold(l, b.bold)}</p>`).join('')}</div>`; });
      if (v.footer) h += `<div class="dash-chat-footer">${esc(v.footer)}</div>`;
      if (v.buttons && v.buttons.length) h += `<div class="dash-chat-actions">${v.buttons.map((b) => `<button type="button" class="dash-chat-action" data-callback="${esc(b.callback)}">${esc(b.label)}</button>`).join('')}</div>`;
      return h;
    };
    const ask = async (path, body, userText) => {
      if (chat.busy) return;
      lock();
      if (userText) row('user', `<p>${esc(userText)}</p>`);
      input.value = ''; grow(); setBusy(true);
      typing = row('bot', '<span class="dash-chat-typing"><i></i><i></i><i></i></span>');
      try {
        const r = await post(path, body);
        typing.remove(); typing = null;
        row('bot', viewHtml(r.view));
      } catch (e) {
        if (typing) { typing.remove(); typing = null; }
        const n = document.createElement('div'); n.className = 'dash-chat-note'; n.textContent = `Não consegui responder agora (${e.message}).`; thread.appendChild(n);
      } finally { setBusy(false); input.focus(); }
    };
    input.addEventListener('input', () => { grow(); send.disabled = chat.busy || !input.value.trim(); });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const t = input.value.trim(); if (t) ask('chat', { text: t }, t); } });
    send.addEventListener('click', () => { const t = input.value.trim(); if (t) ask('chat', { text: t }, t); });
    main.addEventListener('click', (e) => { const b = e.target.closest('[data-callback]'); if (b && !b.disabled) ask('chat/action', { callback: b.dataset.callback }, b.textContent.trim()); });
    $('chat-new').addEventListener('click', () => { thread.innerHTML = ''; thread.hidden = true; emptyEl.hidden = false; input.focus(); });
    input.focus();
  };

  // --- operação
  let opsTimer = null;
  views.operacao = async () => {
    setPage('Agente');
    render(skeleton(3, 6));
    await renderOps();
  };
  async function renderOps() {
    const [st, runs] = await Promise.all([api('ops/status'), api('ops/runs')]);
    if (!location.hash.startsWith('#/operacao')) return;
    const presets = st.presets.map((p) => `<button type="button" class="dash-quick-action ${p.id === 'sync' ? 'dash-quick-action-primary' : ''}" data-preset="${esc(p.id)}" ${st.running ? 'disabled' : ''}><i data-lucide="${({ sync: 'refresh-cw', 'sync-collect': 'download', 'process-pending': 'play', doctor: 'stethoscope', status: 'info', 'sim-avancar-dia': 'clock', 'sim-avancar-semana': 'calendar-clock', 'sim-eventos': 'list', mochila: 'backpack' })[p.id] || 'terminal'}"></i>${esc(p.label)}</button>`).join('');
    const checks = st.checks.map((c) => `<div class="dash-check ${c.ok ? 'is-ok' : c.soft ? 'is-soft' : 'is-bad'}"><i data-lucide="${c.ok ? 'check-circle-2' : c.soft ? 'alert-triangle' : 'x-circle'}"></i><span>${esc(c.label)}</span>${!c.ok && c.hint ? `<small>— ${esc(c.hint)}</small>` : ''}</div>`).join('');
    const lr = st.last_run;
    const runsRows = st.runs.map((r) => `<tr><td>#${r.id}</td><td>${statusBadge(r.status)}${r.baseline ? ' ' + badge('baseline', 'light') : ''}</td><td class="whitespace-nowrap">${esc(fmtDateTime(r.started_at))}</td><td>${r.stats.duration_s != null ? r.stats.duration_s + 's' : '—'}</td><td>${r.stats.items_seen ?? '—'}</td><td>${r.stats.created ?? '—'}/${r.stats.updated ?? '—'}/${r.stats.removed ?? '—'}</td><td>${r.stats.sections_ok ?? '—'}/${r.stats.sections_partial ?? '—'}/${r.stats.sections_empty ?? '—'}/${r.stats.sections_failed ?? '—'}</td><td class="text-xs text-red-600">${esc(r.error || '')}</td></tr>`).join('');
    const jobsKinds = Object.entries(st.jobs_pending_by_kind).map(([k, n]) => badge(`${n} ${k.replace(/_/g, ' ')}`, 'warning')).join(' ') || badge('nenhuma pendência', 'success');
    const failed = st.jobs_failed.length ? `<div class="dash-table-wrap mt-3"><table class="dash-table"><thead><tr><th>#</th><th>Tipo</th><th>Status</th><th>Tent.</th><th>Erro</th></tr></thead><tbody>${st.jobs_failed.map((j) => `<tr><td>${j.id}</td><td>${esc(j.kind)}</td><td>${statusBadge(j.status)}</td><td>${j.attempts}</td><td class="text-xs">${esc(j.last_error || '')}</td></tr>`).join('')}</tbody></table></div>` : '';
    const aiPct = st.ai_limits.tokens_per_day ? Math.min(100, Math.round((st.ai_today.tokens / st.ai_limits.tokens_per_day) * 100)) : 0;
    const execRows = runs.length ? runs.map((r) => `<tr class="is-clickable" data-run="${r.id}"><td>#${r.id}</td><td>${esc(r.label)}</td><td>${r.status === 'running' ? badge('executando…', 'info') : statusBadge(r.status)}</td><td class="whitespace-nowrap">${esc(fmtDateTime(new Date(r.started_at * 1000).toISOString()))}</td><td>${r.finished_at ? Math.round(r.finished_at - r.started_at) + 's' : '—'}</td><td>${r.line_count} linhas</td></tr>`).join('') : '';
    render(intro('Operação do agente', `dados em ${st.data_dir}`, `<span class="text-sm text-slate-500">${st.lock ? badge(`trava ativa (pid ${st.lock.pid})`, 'warning') : ''}</span>`) +
      `<section class="mb-4"><h2 class="dash-section-title">Executar</h2><div class="dash-quick-actions">${presets}</div>${st.running ? `<p class="text-sm text-slate-500 mt-2"><span class="refresh-dot is-loading"></span> ${esc(st.running.label)} em execução…</p>` : ''}</section>
      <div id="run-output" class="mb-4"></div>
      <div class="dash-grid-3 mb-4">
        ${card('Última coleta', 'refresh-cw', lr ? `<div class="dash-kv"><dt>Status</dt><dd>${statusBadge(lr.status)}</dd><dt>Início</dt><dd>${esc(fmtDateTime(lr.started_at))}</dd><dt>Duração</dt><dd>${lr.stats.duration_s ?? '—'}s</dd><dt>Itens</dt><dd>${lr.stats.items_seen ?? '—'} (novos ${lr.stats.created ?? 0}, alterados ${lr.stats.updated ?? 0})</dd><dt>Seções</dt><dd>ok ${lr.stats.sections_ok ?? 0} · parcial ${lr.stats.sections_partial ?? 0} · vazia ${lr.stats.sections_empty ?? 0} · falha ${lr.stats.sections_failed ?? 0}</dd><dt>Sucesso</dt><dd>${esc(fmtDateTime(st.last_success_at))}</dd></div>${lr.error ? `<div class="dash-alert dash-alert-danger mt-3"><i data-lucide="alert-circle"></i>${esc(lr.error)}</div>` : ''}` : empty('Nenhuma coleta ainda.', 'refresh-cw'))}
        ${card('Diagnóstico', 'stethoscope', checks)}
        ${card('Pendências e IA', 'cpu', `<div class="mb-3">${jobsKinds}</div><div class="dash-kv"><dt>Jobs</dt><dd>${esc(JSON.stringify(st.jobs))}</dd><dt>Outbox</dt><dd>${esc(JSON.stringify(st.outbox))}</dd><dt>IA hoje</dt><dd>${st.ai_today.calls} chamadas · ${st.ai_today.tokens} tokens</dd></div><div class="dash-minutes-bar mt-2"><div class="dash-progress"><div class="dash-progress-bar ${aiPct > 80 ? 'dash-progress-crit' : 'dash-progress-ok'}" style="width:${aiPct}%"></div></div><span>${aiPct}% do orçamento diário</span></div>${failed}`)}
      </div>
      ${runs.length ? card('Comandos executados nesta sessão', 'terminal', `<div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>#</th><th>Comando</th><th>Status</th><th>Início</th><th>Duração</th><th></th></tr></thead><tbody>${execRows}</tbody></table></div>`) + '<div class="mb-4"></div>' : ''}
      ${card('Histórico de coletas', 'history', `<div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>#</th><th>Status</th><th>Início</th><th>Dur.</th><th>Itens</th><th>Novos/alt./rem.</th><th>Seções ok/parc./vazia/falha</th><th>Erro</th></tr></thead><tbody>${runsRows}</tbody></table></div>`)}
      <div class="mb-4"></div>
      <div class="dash-grid-2">${card('Mensagens (outbox)', 'send', '<div id="outbox-box">' + skeleton(2, 3) + '</div>')}${card('Log', 'scroll-text', '<div id="log-box">' + skeleton(1, 8) + '</div>', '<button type="button" class="dash-btn dash-btn-ghost dash-btn-sm" id="log-refresh"><i data-lucide="refresh-cw"></i></button>')}</div>`);

    main.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', async () => {
      try { const r = await post('ops/run', { preset: b.dataset.preset }); toast(`${r.label} iniciado.`); followRun(r.id); } catch (e) { toast(e.message, 'err'); }
    }));
    main.querySelectorAll('[data-run]').forEach((tr) => tr.addEventListener('click', () => followRun(Number(tr.dataset.run))));
    if (st.running) followRun(st.running.id);
    loadOutbox(); loadLog();
    $('log-refresh').addEventListener('click', loadLog);
  }
  async function followRun(id) {
    clearTimeout(opsTimer);
    const box = $('run-output'); if (!box) return;
    const r = await api(`ops/runs/${id}`);
    box.innerHTML = card(`${r.label} · #${r.id}`, 'terminal', `<div class="text-xs text-slate-500 mb-2"><code>sigaa-sim ${esc(r.argv.join(' '))}</code> · ${r.status === 'running' ? '<span class="refresh-dot is-loading"></span> executando' : statusBadge(r.status) + ` · exit ${r.exit_code}`}</div><pre class="dash-log">${r.output.map(esc).join('\n') || '(sem saída ainda)'}</pre>`);
    icons();
    const pre = box.querySelector('pre'); pre.scrollTop = pre.scrollHeight;
    if (r.status === 'running') opsTimer = setTimeout(() => followRun(id), 1200);
    else { refreshIndicator(); opsTimer = setTimeout(renderOps, 600); }
  }
  async function loadOutbox() {
    const box = $('outbox-box'); if (!box) return;
    const list = await api('ops/outbox?limit=20');
    box.innerHTML = list.length ? `<div class="dash-list">${list.map((m) => `<div class="dash-list-item"><div class="dash-list-main"><div class="dash-list-meta mb-1">${statusBadge(m.status)}${badge(m.kind, 'light')}${m.urgent ? badge('urgente', 'danger') : ''}<span>${esc(fmtDateTime(m.created_at))}</span></div><div class="dash-drawer-text" style="font-size:.8rem">${esc(m.text)}</div>${m.last_error ? `<div class="text-xs text-red-600 mt-1">${esc(m.last_error)}</div>` : ''}</div></div>`).join('')}</div>` : empty('Nenhuma mensagem gerada.', 'send');
    icons();
  }
  async function loadLog() {
    const box = $('log-box'); if (!box) return;
    const r = await api('ops/logs?lines=300');
    box.innerHTML = `<pre class="dash-log">${r.lines.map((l) => { const m = l.match(/\b(DEBUG|INFO|WARNING|ERROR)\b/); return `<span class="${m ? 'lv-' + m[1] : ''}">${esc(l)}</span>`; }).join('\n') || '(log vazio)'}</pre>`;
    const pre = box.querySelector('pre'); pre.scrollTop = pre.scrollHeight;
  }

  // --- config
  views.config = async () => {
    setPage('Configuração');
    render(skeleton(3, 6));
    const c = await api('config');
    const kv = (obj) => `<dl class="dash-kv">${Object.entries(obj).map(([k, v]) => `<dt>${esc(k)}</dt><dd><span class="dash-code">${esc(typeof v === 'object' ? JSON.stringify(v) : String(v))}</span></dd>`).join('')}</dl>`;
    const secrets = Object.entries(c.secrets_present).map(([k, v]) => `<div class="dash-check ${v ? 'is-ok' : 'is-soft'}"><i data-lucide="${v ? 'check-circle-2' : 'circle'}"></i><span class="dash-code">${esc(k)}</span><small>${v ? 'definido' : 'ausente'}</small></div>`).join('');
    render(intro('Configuração', `Somente leitura · ${c.config_path || 'defaults (sem config.toml)'} · edite os arquivos e reinicie o servidor`) +
      `<div class="dash-alert dash-alert-info mb-4"><i data-lucide="info"></i><span>Segredos ficam no <code>.env</code> e nunca são exibidos aqui — só a presença.</span></div>
      <div class="dash-grid-2">${card('Segredos (.env)', 'key-round', secrets)}${card('[general]', 'settings', kv(c.general))}${card('[sigaa]', 'globe', kv(c.sigaa))}${card('[planner]', 'target', kv(c.planner))}${card('[telegram]', 'send', kv(c.telegram))}${card('[groq]', 'cpu', kv(c.groq))}${card('[knowledge]', 'paperclip', kv(c.knowledge))}${card('[display_names]', 'tag', Object.keys(c.display_names).length ? kv(c.display_names) : '<p class="text-sm text-slate-500">Nenhum nome curto extra (os da UFRN já são embutidos).</p>')}</div>`);
  };

  // ------------------------------------------------------------------ router
  function parseHash() {
    const h = location.hash.replace(/^#\/?/, '');
    const [pathPart, qs] = h.split('?');
    const [name, ...rest] = pathPart.split('/');
    const params = {};
    new URLSearchParams(qs || '').forEach((v, k) => { params[k] = v; });
    return { name: name || 'home', rest: rest.join('/'), params };
  }
  let routing = 0;
  async function route() {
    if (!$('gate').hidden) return;
    clearTimeout(opsTimer);
    closeDrawer();
    const { name, rest, params } = parseHash();
    const fn = views[name] || views.home;
    document.querySelectorAll('#sidebar-nav [data-route]').forEach((a) => a.classList.toggle('is-active', a.dataset.route === name || (name === 'home' && a.dataset.route === 'home')));
    const my = ++routing;
    try { await fn(params, rest); } catch (e) {
      if (my !== routing || e.message === 'unauthorized') return;
      render(`<div class="dash-card"><div class="dash-empty"><i data-lucide="alert-circle"></i>Erro ao carregar: ${esc(e.message)}</div></div>`);
    }
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', route);
  $('quick-chat-btn').addEventListener('click', () => { location.hash = '#/chat'; });

  (async () => {
    icons();
    if (await initGate()) { route(); refreshIndicator(); }
    setInterval(() => { if ($('gate').hidden) refreshIndicator(); }, 60000);
  })();
})();
