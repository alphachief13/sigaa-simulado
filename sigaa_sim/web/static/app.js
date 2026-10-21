/* SIGAA Study Agent — painel web (SPA por hash, sem framework).
 *
 * Telas: início, plano, prazos, disciplinas, avisos, notas, materiais, novidades,
 * progresso, buscar, operação, config. Tudo vem de /api/*; nada é renderizado sem escapar.
 * O Nimbo (mascote) usa as tiras de static/nimbo/: um companheiro flutuante que abre o chat + poses nas telas.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------------ utils
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const icons = () => { if (window.lucide && lucide.createIcons) lucide.createIcons(); };
  const MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

  // ------------------------------------------------------------------ Nimbo
  // tira → [quadros, lado nativo do quadro em px, centro x do corpo (fração do quadro)]
  const NIMBO = {
    hero: [1, 185, 0.497], idle: [5, 92, 0.5], blink: [3, 90, 0.5], walk: [5, 104, 0.49], run: [4, 106, 0.5],
    jump: [3, 149, 0.497], look: [5, 95, 0.495], express: [5, 92, 0.5], emotion: [4, 107, 0.495],
    interact: [5, 122, 0.459], hold: [4, 123, 0.496], telegram: [4, 142, 0.479], sleep: [3, 101, 0.495],
    notify: [1, 112, 0.455], effects: [5, 132, 0.515],
  };
  function nimboStyle(sheet, frame, scale) {
    const [n, size] = NIMBO[sheet];
    return `--nimbo-n:${n};--nimbo-s:${size};--nimbo-f:${frame || 0};${scale != null ? `--nimbo-scale:${scale};` : ''}background-image:url(/static/nimbo/${sheet}.webp)`;
  }
  function nimbo(sheet, frame, scale, cls, dur) {
    return `<span class="nimbo ${cls || ''}" style="${nimboStyle(sheet, frame, scale)}${dur ? `;--nimbo-dur:${dur}` : ''}" aria-hidden="true"></span>`;
  }
  document.querySelectorAll('[data-nimbo]').forEach((el) => {
    el.setAttribute('style', nimboStyle(el.dataset.nimbo, Number(el.dataset.frame) || 0, el.dataset.scale));
    el.setAttribute('aria-hidden', 'true');
  });

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
    if (variant === 'err') buddy.oops(); else buddy.cheer();
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
  // vazio = Nimbo cochilando; erro = Nimbo confuso
  function empty(msg, icon) {
    const art = /^alert/.test(icon || '') ? nimbo('emotion', 2, 0.6) : nimbo('sleep', 0, 0.55, 'nimbo-loop', '2.7s');
    return `<div class="dash-empty">${art}${esc(msg)}</div>`;
  }
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
    if (res.ok) { hideGate(); $('gate-token').value = ''; buddy.start(); route(); refreshIndicator(); } else { $('gate-error').hidden = false; }
  });

  // ---------------------------------------------------------------- sidebar
  (function initSidebar() {
    const sb = $('sidebar'), bd = $('sidebar-backdrop'), bg = $('sidebar-burger');
    const close = () => { sb.classList.remove('is-open'); bd.classList.remove('is-open'); };
    bg.addEventListener('click', () => { sb.classList.toggle('is-open'); bd.classList.toggle('is-open'); });
    bd.addEventListener('click', close);
    sb.querySelectorAll('a').forEach((a) => a.addEventListener('click', close));
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!$('drawer-root').hidden) closeDrawer(); else nimboChat.close();
      close();
    });
  })();

  // ---------------------------------------------------------- companheiro
  // Máquina de estados simples: uma "timeline" de poses por vez (a nova cancela a anterior);
  // entre ações, comportamentos ambientes (piscar, olhar em volta); sem interação, cochila.
  // Clicar nele abre o chat: ele pula para cima da janela e reage enquanto você conversa.
  let lastOverview = null;
  const buddy = (() => {
    const root = $('buddy'), body = $('buddy-body'), sprite = $('buddy-sprite'), bubble = $('buddy-bubble');
    const still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const SLEEP_AFTER = 90000;
    let timeline = 0, acting = false, asleep = false, napping = false, working = false, started = false, chatting = false;
    let lastInput = Date.now(), ambientTimer = null, bubbleTimer = null, listenTimer = null;
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));

    function pose(sheet, frame) {
      sprite.style.cssText = `${nimboStyle(sheet, frame, 0.8)};transform:translateX(${-NIMBO[sheet][2] * 100}%)`;
    }
    function base() {
      if (asleep) return pose('sleep', 0);
      if (working) return pose('run', 0);
      pose('idle', 1);
    }
    function fx(cls, ms) {
      if (still) return;
      root.classList.remove(cls); void root.offsetWidth; root.classList.add(cls);
      setTimeout(() => root.classList.remove(cls), ms);
    }
    // toca uma sequência [[tira, quadro, ms], ...]; hold mantém a última pose
    async function play(steps, hold) {
      const my = ++timeline; acting = true;
      for (const [sheet, frame, ms] of steps) {
        if (my !== timeline) return false;
        pose(sheet, frame);
        await wait(still ? Math.min(ms, 400) : ms);
      }
      if (my !== timeline) return false;
      acting = !!hold;
      if (!hold) base();
      return true;
    }
    async function loop(steps) {
      const my = ++timeline; acting = true;
      while (my === timeline) {
        for (const [sheet, frame, ms] of steps) { if (my !== timeline) return; pose(sheet, frame); await wait(ms); }
      }
    }
    function release() { timeline++; acting = false; base(); }

    // ------- balão
    function say(html, opts) {
      opts = opts || {};
      if (chatting) return;
      clearTimeout(bubbleTimer);
      const acts = (opts.actions || []).map((a) => a.href
        ? `<a href="${esc(a.href)}" data-bubble-close>${esc(a.label)}</a>`
        : `<button type="button" data-bubble-act="${esc(a.act)}">${esc(a.label)}</button>`).join('');
      bubble.innerHTML = `<span class="nimbo-buddy-bubble-name">Nimbo</span>${html}${acts ? `<div class="nimbo-buddy-bubble-actions">${acts}</div>` : ''}`;
      bubble.classList.remove('is-leaving'); bubble.hidden = false;
      bubbleTimer = setTimeout(hush, opts.ms || (acts ? 9000 : 5500));
    }
    function hush() {
      clearTimeout(bubbleTimer);
      if (bubble.hidden) return;
      bubble.classList.add('is-leaving');
      bubbleTimer = setTimeout(() => { bubble.hidden = true; bubble.classList.remove('is-leaving'); if (acting && !working) release(); }, 200);
    }
    bubble.addEventListener('click', (e) => {
      if (e.target.closest('[data-bubble-close]')) hush();
      const act = e.target.closest('[data-bubble-act]');
      if (act && act.dataset.bubbleAct === 'chat') { hush(); nimboChat.open(); }
    });

    // ------- sono
    function sleep(manual) {
      if (asleep) return;
      asleep = true; napping = !!manual; hush();
      root.classList.add('is-asleep');
      play([['blink', 0, 600], ['emotion', 1, 500], ['sleep', 0, 300]]).then((ok) => {
        if (ok && asleep && !still) loop([['sleep', 0, 1100], ['sleep', 1, 900], ['sleep', 2, 1100]]);
      });
    }
    function wake() {
      if (!asleep) return;
      asleep = false; napping = false; root.classList.remove('is-asleep');
      fx('is-hop', 560);
      play([['notify', 0, 650], ['blink', 0, 140], ['idle', 1, 300], ['blink', 0, 120]]);
    }

    // ------- ambiente
    const AMBIENT = [
      [5, [['blink', 0, 130]]],
      [2, [['blink', 0, 110], ['idle', 1, 140], ['blink', 0, 110]]],
      [2, [['look', 0, 420], ['look', 1, 380], ['look', 2, 380], ['look', 3, 380], ['look', 4, 420], ['blink', 0, 120]]],
      [2, [['idle', 2, 600], ['idle', 3, 600], ['idle', 4, 700], ['blink', 0, 120]]],
      [1, [['express', 0, 900], ['idle', 0, 500]]],
      [1, [['express', 3, 700], ['express', 4, 700]]],
      [1, [['walk', 0, 120], ['walk', 1, 120], ['walk', 2, 120], ['walk', 3, 120], ['walk', 4, 120], ['walk', 0, 120]]],
    ];
    function ambient() {
      clearTimeout(ambientTimer);
      ambientTimer = setTimeout(ambient, 2600 + Math.random() * 4200);
      if (!started || document.hidden) return;
      if (!asleep && Date.now() - lastInput > SLEEP_AFTER) { sleep(false); return; }
      if (asleep || acting || working || still) return;
      let r = Math.random() * AMBIENT.reduce((a, x) => a + x[0], 0);
      const pick = AMBIENT.find((x) => (r -= x[0]) < 0) || AMBIENT[0];
      play(pick[1]);
    }
    const touch = () => { lastInput = Date.now(); if (asleep && !napping) wake(); };
    ['pointermove', 'keydown', 'scroll', 'touchstart'].forEach((ev) => window.addEventListener(ev, touch, { passive: true }));

    body.addEventListener('mouseenter', () => { if (!acting && !asleep && !working) pose('interact', 1); });
    body.addEventListener('mouseleave', () => { if (!acting && !asleep) base(); });
    body.addEventListener('click', () => {
      lastInput = Date.now();
      if (asleep) wake();
      hush();
      nimboChat.toggle();
    });

    return {
      start() {
        if (started) return;
        started = true; root.hidden = false; base();
        let met = false;
        try { met = localStorage.getItem('nimbo-met') === '1'; localStorage.setItem('nimbo-met', '1'); } catch (e) { /* ignore */ }
        if (!still) root.classList.add('is-entering');
        setTimeout(() => root.classList.remove('is-entering'), 950);
        play([['effects', 0, 380], ['effects', 1, 320], ['jump', 2, 160], ['express', 0, 700], ['blink', 0, 130]]).then(() => {
          if (!met) {
            const h = new Date().getHours();
            say(`${h < 5 || h >= 18 ? 'Boa noite' : h < 12 ? 'Bom dia' : 'Boa tarde'}! Eu sou o <b>Nimbo</b>, seu jerimum de confiança no SIGAA. Clica em mim pra conversar.`, { ms: 8000, actions: [{ act: 'chat', label: 'Conversar' }] });
            play([['interact', 2, 1600]]);
          }
        });
        ambient();
      },
      walk() { if (started && !asleep && !working) play([['walk', 0, 110], ['walk', 1, 110], ['walk', 2, 110], ['walk', 3, 110], ['walk', 4, 110]]); },
      cheer() {
        if (!started) return;
        if (asleep) wake();
        fx('is-hop', 560);
        play([['express', 1, 260], ['effects', 3, 900], ['express', 0, 500]]);
      },
      oops() {
        if (!started) return;
        if (asleep) wake();
        fx('is-wiggle', 520);
        play([['emotion', 2, 1300], ['emotion', 1, 500]]);
      },
      idea() { if (started) { fx('is-hop', 560); play([['effects', 4, 1400], ['express', 0, 500]]); } },
      news(html) {
        if (!started) return;
        if (asleep) wake();
        play([['telegram', 0, 700], ['telegram', 2, 700], ['telegram', 3, 1000]]);
        say(html, { actions: [{ href: '#/novidades', label: 'Ver novidades' }] });
      },
      working(on) {
        if (!started || working === !!on) return;
        working = !!on;
        if (working) {
          if (asleep) wake();
          say('Correndo no SIGAA, já volto!', { ms: 3000 });
          if (!still) loop([['run', 0, 95], ['run', 1, 95], ['run', 2, 95], ['run', 3, 95]]); else pose('run', 0);
        } else release();
      },
      // chat aberto: sobe na janela acenando; fechado: desce de volta pro canto
      chat(on) {
        if (chatting === !!on) return;
        chatting = !!on;
        root.classList.toggle('is-chatting', chatting);
        body.setAttribute('aria-expanded', String(chatting));
        if (!started) return;
        hush(); fx('is-hop', 560);
        if (chatting) play([['jump', 0, 110], ['jump', 1, 260], ['jump', 2, 140], ['interact', 2, 1100], ['blink', 0, 130]]);
        else play([['jump', 1, 220], ['jump', 2, 160], ['blink', 0, 130]]);
      },
      // digitando: ele presta atenção
      listen() {
        lastInput = Date.now();
        if (!started || acting || asleep || working) return;
        pose('interact', 1);
        clearTimeout(listenTimer);
        listenTimer = setTimeout(() => { if (!acting && !asleep && !working) base(); }, 1400);
      },
      // esperando a resposta: olha em volta, pensando
      thinking() {
        if (!started) return;
        if (asleep) wake();
        clearTimeout(listenTimer);
        if (still) { ++timeline; acting = true; pose('look', 2); }
        else loop([['look', 0, 320], ['look', 1, 280], ['look', 2, 360], ['look', 3, 280], ['look', 4, 320], ['blink', 0, 120]]);
      },
      answered(ok) {
        if (!started) return;
        if (!ok) { this.oops(); return; }
        fx('is-hop', 560);
        play([['express', 1, 240], ['express', 0, 800], ['blink', 0, 130]]);
      },
      say,
    };
  })();

  // ------------------------------------------------------------------ chat
  // Janela flutuante presa ao Nimbo: sobrevive à navegação e usa o mesmo roteador do Telegram.
  // No lugar de "/comandos", atalhos clicáveis; menções a /comando nas respostas viram botões.
  const nimboChat = (() => {
    const panel = $('nimbo-chat'), form = $('nimbo-chat-form'), input = $('nimbo-chat-input'), send = $('nimbo-chat-send');
    const thread = $('nimbo-chat-thread'), emptyEl = $('nimbo-chat-empty'), scroll = $('nimbo-chat-scroll'), status = $('nimbo-chat-status');
    const tray = $('nimbo-chat-tray'), trayBtn = $('nimbo-chat-tray-btn'), mode = $('nimbo-chat-mode');
    const PLACEHOLDER = input.placeholder;
    // comando → [rótulo, ícone, descrição]
    const SHORTCUTS = {
      hoje: ['Estudar hoje', 'target', 'próximo passo do plano'],
      prazos: ['Prazos', 'calendar-clock', 'entregas e provas'],
      notas: ['Notas', 'clipboard-check', 'notas lançadas'],
      resumo: ['Novidades', 'sparkles', 'o que mudou'],
      avisos: ['Avisos', 'megaphone', 'dos professores'],
      materiais: ['Materiais', 'paperclip', 'arquivos das turmas'],
      disciplinas: ['Disciplinas', 'book-open', 'suas turmas'],
      buscar: ['Buscar', 'search', 'nos materiais'],
      status: ['Status', 'activity', 'saúde do agente'],
    };
    let open = false, busy = false, searching = false;

    const tiles = (compact) => Object.entries(SHORTCUTS).map(([cmd, [label, icon, hint]], i) =>
      `<button type="button" class="nimbo-chat-tile" data-cmd="${cmd}" style="--i:${i}"><i data-lucide="${icon}"></i><b>${esc(label)}</b>${compact ? '' : `<small>${esc(hint)}</small>`}</button>`).join('');
    tray.innerHTML = tiles(true);

    function hello() {
      const h = new Date().getHours(), ov = lastOverview;
      const d = ov && ov.next_deadlines && ov.next_deadlines[0];
      $('nimbo-chat-hello').textContent = `${h < 5 || h >= 18 ? 'Boa noite' : h < 12 ? 'Bom dia' : 'Boa tarde'}! Como posso ajudar?`;
      $('nimbo-chat-heads').innerHTML = d ? `<button type="button" class="nimbo-chat-heads" data-cmd="prazos"><i data-lucide="alarm-clock"></i><span>Próximo prazo: <b>${esc(d.title)}</b> (${esc(d.course)}), ${esc(d.relative)}.</span></button>` : '';
      $('nimbo-chat-menu').innerHTML = tiles(false);
      icons();
    }
    function setOpen(v) {
      if (open === v) return;
      open = v;
      panel.classList.toggle('is-open', v);
      panel.setAttribute('aria-hidden', String(!v));
      panel.inert = !v;
      buddy.chat(v);
      if (v) { if (!emptyEl.hidden) hello(); setTimeout(() => input.focus(), 280); } else { showTray(false); if (panel.contains(document.activeElement)) $('buddy-body').focus(); }
    }
    function showTray(v) {
      tray.hidden = !v; trayBtn.classList.toggle('is-on', v); trayBtn.setAttribute('aria-expanded', String(v));
      if (v) icons();
    }
    function searchMode(v) {
      searching = v; mode.hidden = !v;
      input.placeholder = v ? 'O que procurar nos materiais?' : PLACEHOLDER;
      if (v) input.focus();
    }
    const setStatus = (text, cls) => { status.className = cls || ''; status.innerHTML = `<i></i>${esc(text)}`; };
    const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 128)}px`; };
    const row = (side, html) => {
      emptyEl.hidden = true; thread.hidden = false;
      const r = document.createElement('div'); r.className = `nimbo-chat-row is-${side}`;
      r.innerHTML = `<div class="nimbo-chat-bubble">${html}</div>`;
      thread.appendChild(r); icons(); scroll.scrollTop = scroll.scrollHeight; return r;
    };
    // "/prazos" no texto (já escapado) vira um botão do atalho
    const cmdPills = (h) => h.replace(/\/([a-z]+)\b/g, (m, cmd) => SHORTCUTS[cmd]
      ? `<button type="button" class="nimbo-chat-cmd" data-cmd="${cmd}"><i data-lucide="${SHORTCUTS[cmd][1]}"></i>${esc(SHORTCUTS[cmd][0])}</button>` : m);
    const viewHtml = (v) => {
      if (v.help) return `<div class="nimbo-chat-title">Posso te ajudar com isso aqui:</div><div class="nimbo-chat-menu is-inline">${tiles(true)}</div><div class="nimbo-chat-footer">Ou pergunte do seu jeito, tipo “quando é a prova de Física?”.</div>`;
      const bold = (line, bolds) => { let h = esc(line); (bolds || []).forEach((b) => { if (b) h = h.split(esc(b)).join(`<b>${esc(b)}</b>`); }); return cmdPills(h); };
      let h = `<div class="nimbo-chat-title">${v.emoji ? esc(v.emoji) + ' ' : ''}${cmdPills(esc(v.title))}</div>`;
      if (v.subtitle) h += `<div class="nimbo-chat-subtitle">${cmdPills(esc(v.subtitle))}</div>`;
      (v.blocks || []).forEach((b) => { h += `<div class="nimbo-chat-block ${b.italic ? 'is-italic' : ''}">${b.lines.map((l) => `<p>${bold(l, b.bold)}</p>`).join('')}</div>`; });
      if (v.footer) h += `<div class="nimbo-chat-footer">${cmdPills(esc(v.footer))}</div>`;
      if (v.buttons && v.buttons.length) h += `<div class="nimbo-chat-actions">${v.buttons.map((b) => `<button type="button" class="nimbo-chat-chip" data-callback="${esc(b.callback)}">${esc(b.label)}</button>`).join('')}</div>`;
      return h;
    };
    const setBusy = (v) => { busy = v; input.disabled = v; send.disabled = v || !input.value.trim(); };
    async function ask(path, body, userText) {
      if (busy) return;
      showTray(false);
      thread.querySelectorAll('[data-callback]').forEach((b) => { b.disabled = true; });
      if (userText) row('user', `<p>${esc(userText)}</p>`);
      input.value = ''; grow(); setBusy(true);
      setStatus('pensando…', 'is-busy'); buddy.thinking();
      const typing = row('bot', '<span class="nimbo-chat-typing"><i></i><i></i><i></i></span>');
      try {
        const r = await post(path, body);
        typing.remove(); row('bot', viewHtml(r.view)); buddy.answered(true);
      } catch (e) {
        typing.remove(); row('bot', `<p>Não consegui responder agora <span class="nimbo-chat-muted">(${esc(e.message)})</span>.</p>`); buddy.answered(false);
      } finally { setBusy(false); setStatus('pronto pra ajudar'); if (open) input.focus(); }
    }
    function runCmd(cmd) {
      if (busy) return;
      if (cmd === 'buscar') { showTray(false); searchMode(true); return; }
      ask('chat', { text: `/${cmd}` }, SHORTCUTS[cmd] ? SHORTCUTS[cmd][0] : cmd);
    }
    const submit = () => {
      const t = input.value.trim();
      if (!t) return;
      if (searching) { searchMode(false); ask('chat', { text: `/buscar ${t}` }, `Buscar: ${t}`); } else ask('chat', { text: t }, t);
    };
    input.addEventListener('input', () => { grow(); send.disabled = busy || !input.value.trim(); buddy.listen(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
      if (e.key === 'Backspace' && searching && !input.value) searchMode(false);
    });
    form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
    panel.addEventListener('click', (e) => {
      const c = e.target.closest('[data-cmd]');
      if (c) { runCmd(c.dataset.cmd); return; }
      const b = e.target.closest('[data-callback]');
      if (b && !b.disabled) ask('chat/action', { callback: b.dataset.callback }, b.textContent.trim());
    });
    trayBtn.addEventListener('click', () => showTray(tray.hidden));
    $('nimbo-chat-mode-x').addEventListener('click', () => searchMode(false));
    $('nimbo-chat-close').addEventListener('click', () => setOpen(false));
    $('nimbo-chat-clear').addEventListener('click', () => { if (busy) return; thread.innerHTML = ''; thread.hidden = true; emptyEl.hidden = false; searchMode(false); hello(); input.focus(); });

    return { open: () => setOpen(true), close: () => setOpen(false), toggle: () => setOpen(!open) };
  })();

  // ------------------------------------------------------------ indicador
  async function refreshIndicator() {
    try {
      const ov = await api('overview');
      const prev = lastOverview;
      lastOverview = ov;
      if (prev && ov.last_success_at && ov.last_success_at !== prev.last_success_at && !location.hash.startsWith('#/operacao')) {
        buddy.news(`Coleta nova chegou! ${ov.kpis.events_7d ? `<b>${ov.kpis.events_7d}</b> novidade(s) na semana.` : 'Nada de novo, pode respirar.'}`);
      }
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

  function setPage(title) {
    $('page-title').textContent = title;
    document.title = `${title} · SIGAA Simulado`;
  }
  function render(html) { main.innerHTML = `<div class="dash-animate">${html}</div>`; icons(); }

  // --- início
  views.home = async () => {
    setPage('Início');
    render(skeleton(1, 8) + `<div class="dash-kpi-grid mb-4">${skeleton(4, 6).replace(/mb-3/g, '')}</div>`);
    const ov = await api('overview');
    lastOverview = ov;
    const k = ov.kpis;
    const alerts = [];
    if (ov.last_run && ov.last_run.status === 'failed') alerts.push(`<div class="dash-alert dash-alert-danger"><i data-lucide="alert-circle"></i><span>Última coleta falhou${ov.last_run.error ? ': ' + esc(ov.last_run.error) : ''}. Dados mostrados são de ${esc(fmtDateTime(ov.last_success_at))}.</span></div>`);
    if (ov.sections_bad.length) alerts.push(`<div class="dash-alert dash-alert-warn"><i data-lucide="alert-triangle"></i><span>${ov.sections_bad.length} seção(ões) com falha na última coleta: ${esc(ov.sections_bad.slice(0, 3).map((s) => `${s.course}/${s.section}`).join(', '))}${ov.sections_bad.length > 3 ? '…' : ''}</span></div>`);
    if (k.jobs_pending > 0) alerts.push(`<div class="dash-alert dash-alert-info"><i data-lucide="clock"></i><span>${k.jobs_pending} pendência(s) de processamento (downloads/IA). <a href="#/operacao" class="underline">Processar agora</a>.</span></div>`);

    const kpis = [
      { label: 'Prazos em 7 dias', value: k.deadlines_7d, sub: `${k.deadlines_30d} em 30 dias`, icon: 'calendar-clock', href: '#/prazos', accent: k.deadlines_7d > 0 ? '#f58a1f' : null },
      { label: 'Novidades (7 dias)', value: k.events_7d, sub: `${k.grades_7d} nota(s)`, icon: 'history', href: '#/novidades', accent: '#2fb79a' },
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

    render(`${nimboHero(ov)}
      ${alerts.length ? `<div class="flex flex-col gap-2 mb-4">${alerts.join('')}</div>` : ''}
      <div class="dash-kpi-grid mb-5">${kpiHtml}</div>
      <div class="dash-grid-3">
        ${card('Próximos prazos', 'calendar-clock', deadlines, `<a href="#/prazos" class="dash-btn dash-btn-ghost dash-btn-sm">ver todos</a>`)}
        ${card('Estudo de hoje', 'target', planHtml)}
        ${card('Novidades recentes', 'history', events, `<a href="#/novidades" class="dash-btn dash-btn-ghost dash-btn-sm">ver todas</a>`)}
      </div>`);
  };

  // o humor do Nimbo no banner segue o dia: prazo colado → alerta; semana cheia → prancheta;
  // plano concluído → brilhos; semana livre → acenando
  function nimboHero(ov) {
    const k = ov.kpis, d = ov.next_deadlines[0], h = new Date().getHours();
    const greet = h < 5 || h >= 18 ? 'Boa noite' : h < 12 ? 'Bom dia' : 'Boa tarde';
    const planDone = ov.plan && ov.plan.tasks && ov.plan.done >= ov.plan.tasks;
    let art, say;
    if (!ov.last_run) {
      return `<section class="nimbo-hero"><div class="nimbo-hero-art" id="hero-nimbo" title="Oi!"><span class="nimbo-bob" style="display:inline-flex">${nimbo('interact', 2, 1)}</span></div>
        <div class="nimbo-hero-text"><h2 class="nimbo-hero-hi">${greet}! <span>Eu sou o Nimbo.</span></h2>
        <p class="nimbo-hero-say">Ainda não li o seu SIGAA. Um clique e eu <b>gero o SIGAA Simulado</b>, faço a <b>primeira coleta</b> e monto o <b>plano de hoje</b>.</p>
        <div class="dash-quick-actions mt-3"><button type="button" class="dash-quick-action dash-quick-action-primary" data-setup><i data-lucide="rocket"></i>Preparar tudo</button><a href="#/operacao" class="dash-quick-action"><i data-lucide="sliders-horizontal"></i>Painel de controle</a></div></div></section>`;
    }
    if (d && d.days_left <= 1) { art = nimbo('notify', 0, 1.05); say = `Atenção: <b>${esc(d.title)}</b> (${esc(d.course)}) vence <b>${esc(d.relative)}</b>.`; }
    else if (k.deadlines_7d > 0) { art = nimbo('hold', 2, 0.98); say = `Você tem <b>${k.deadlines_7d} prazo(s)</b> nesta semana. O mais próximo é <b>${esc(d.title)}</b> (${esc(d.course)}), ${esc(d.relative)}.`; }
    else if (planDone) { art = nimbo('effects', 3, 0.95); say = 'Plano de hoje concluído. Mandou bem!'; }
    else { art = nimbo('interact', 2, 1); say = 'Semana tranquila: nenhum prazo nos próximos 7 dias.'; }
    const plan = ov.plan ? (planDone ? '' : ` Plano de hoje: <b>${ov.plan.done}/${ov.plan.tasks}</b> sessões feitas.`) : ' Ainda sem plano pra hoje; que tal montar um?';
    return `<section class="nimbo-hero"><div class="nimbo-hero-art" id="hero-nimbo" title="Oi!"><span class="nimbo-bob" style="display:inline-flex">${art}</span></div>
      <div class="nimbo-hero-text"><h2 class="nimbo-hero-hi">${greet}! <span>Bora estudar?</span></h2><p class="nimbo-hero-say">${say}${plan}</p>
      <p class="nimbo-hero-meta">${esc(ov.institution.toUpperCase())} · perfil ${esc(ov.profile)} · ${ov.last_success_at ? 'última coleta ' + esc(fmtDateTime(ov.last_success_at)) : 'sem coleta concluída'}</p></div></section>`;
  }
  document.addEventListener('click', (e) => {
    const art = e.target.closest('#hero-nimbo .nimbo');
    if (!art) return;
    art.style.animation = 'none'; void art.offsetWidth; art.style.animation = 'nimbo-hop 0.5s ease';
  });

  // "Preparar tudo" (home vazia): roda a sequência e leva ao painel de controle para acompanhar
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-setup]');
    if (!b || b.disabled) return;
    b.disabled = true;
    try { await post('ops/run', { preset: 'setup-all' }); } catch (err) {
      try { await post('ops/run', { preset: 'sync' }); } catch (e2) { toast(e2.message, 'err'); b.disabled = false; return; }
    }
    location.hash = '#/operacao';
  });

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
      body = `<div class="dash-card"><div class="dash-empty">${nimbo('hold', 2, 0.6)}Ainda não há plano para este dia (${minutesLabel(avail)} disponíveis). Clique em <b>Gerar plano</b>.</div></div>`;
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
        toast('Plano atualizado.'); renderPlan(day, r.plan, avail); setTimeout(() => buddy.idea(), 1800);
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

  // --- controle: todo comando da CLI vira botão (ou formulário curto) aqui
  let opsTimer = null;
  const opsMore = {};  // seções recolhíveis abertas (sobrevivem ao re-render)
  views.operacao = async () => {
    setPage('Controle');
    render(skeleton(3, 6));
    await renderOps();
  };
  const ICONS = {
    'setup-all': 'rocket', init: 'file-cog', sync: 'refresh-cw', 'sync-collect': 'download', 'process-pending': 'play', 'telegram-poll': 'send',
    plan: 'target', mochila: 'backpack', 'mochila-custom': 'backpack', 'sim-gerar': 'database', 'sim-info': 'info',
    'sim-avancar-hora': 'clock-1', 'sim-avancar-dia': 'sun', 'sim-avancar-semana': 'calendar-range', 'sim-avancar': 'fast-forward',
    'sim-eventos': 'list', 'sim-eventos-todos': 'list-checks', 'sim-sessao-expirar': 'log-out', 'sim-sessao-renovar': 'log-in',
    doctor: 'stethoscope', status: 'info', demo: 'flask-conical',
    'sync-fixture-changed': 'git-compare', 'sync-fixture-baseline': 'database', 'sync-fixture-expired': 'timer-off',
  };
  async function renderOps() {
    const [st, runs] = await Promise.all([api('ops/status'), api('ops/runs')]);
    if (!location.hash.startsWith('#/operacao')) return;
    const has = new Set(st.presets.map((p) => p.id));
    const label = Object.fromEntries(st.presets.map((p) => [p.id, p.label.replace(/^Simulador: /, '')]));
    const off = st.running ? 'disabled' : '';
    const btn = (id, text, primary) => has.has(id)
      ? `<button type="button" class="dash-quick-action ${primary ? 'dash-quick-action-primary' : ''}" data-preset="${id}" ${off}><i data-lucide="${ICONS[id] || 'terminal'}"></i>${esc(text || label[id])}</button>` : '';
    const form = (id, fields, text, primary) => has.has(id)
      ? `<form class="dash-ctl-form" data-preset="${id}">${fields}<button type="submit" class="dash-btn ${primary ? 'dash-btn-primary' : 'dash-btn-outline'}" ${off}><i data-lucide="${ICONS[id] || 'play'}"></i>${esc(text)}</button></form>` : '';
    const num = (name, lbl, ph, min, max) => `<label class="dash-ctl-field"><span>${esc(lbl)}</span><input class="dash-input" type="number" name="${name}" placeholder="${esc(ph)}" min="${min}" max="${max}"></label>`;
    const day = (name, lbl) => `<label class="dash-ctl-field"><span>${esc(lbl)}</span><input class="dash-input" type="date" name="${name}"></label>`;
    const when = (name, lbl) => `<label class="dash-ctl-field"><span>${esc(lbl)}</span><input class="dash-input" type="datetime-local" name="${name}"></label>`;
    const chk = (name, lbl, on) => `<label class="dash-ctl-check"><input type="checkbox" name="${name}" ${on ? 'checked' : ''}>${esc(lbl)}</label>`;
    const group = (title, icon, sub, body) => body.replace(/\s/g, '') ? card(title, icon, `${sub ? `<p class="dash-ctl-sub">${sub}</p>` : ''}${body}`) : '';

    const su = st.setup, simulado = su.profile === 'simulado';
    const steps = [
      ...(simulado ? [[su.sim_db, 'SIGAA Simulado gerado', 'banco fictício com turmas, prazos e notas']] : []),
      [su.synced, 'Primeira coleta feita', 'o agente leu o SIGAA'],
      [su.plan_today, 'Plano de hoje montado', 'Mochila 0/1 escolheu as sessões'],
    ];
    const ready = (!simulado || su.sim_db) && su.synced;
    const allDone = ready && su.plan_today;
    const lockBadge = st.lock ? ' ' + badge(`trava ativa (pid ${st.lock.pid})`, 'warning') : '';
    const onboarding = allDone
      ? `<div class="dash-ctl-ready"><i data-lucide="check-circle-2"></i><span><b>Tudo pronto.</b> SIGAA lido e plano de hoje montado.${lockBadge}</span>${btn('setup-all', 'Refazer coleta e plano')}</div>`
      : `<section class="nimbo-hero dash-ctl-onboard">
      <div class="nimbo-hero-art"><span class="nimbo-bob" style="display:inline-flex">${nimbo(ready ? 'express' : 'hold', ready ? 0 : 2, 0.9)}</span></div>
      <div class="nimbo-hero-text"><h2 class="nimbo-hero-hi">${ready ? 'Quase lá!' : 'Vamos começar?'} <span>${ready ? 'Falta pouco.' : 'Um clique e eu preparo tudo.'}</span></h2>
        <ul class="dash-ctl-steps">${steps.map(([ok, t, d]) => `<li class="${ok ? 'is-ok' : ''}"><i data-lucide="${ok ? 'check-circle-2' : 'circle'}"></i><b>${esc(t)}</b><small>${esc(d)}</small></li>`).join('')}</ul>
        <div class="dash-quick-actions mt-3">${btn('setup-all', ready ? 'Refazer coleta e plano' : 'Preparar tudo', true)}${!su.config ? btn('init') : ''}</div>${lockBadge}</div></section>`;

    const clock = simulado && su.sim_db ? `<div class="dash-ctl-clock"><i data-lucide="clock"></i><div><small>agora no SIGAA Simulado</small><b>${esc(new Date(su.now).toLocaleString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long', hour: '2-digit', minute: '2-digit' }))}</b></div></div>` : '';
    // o essencial fica à vista; o resto (formulários, cenários, diagnóstico) vai para "Mais comandos"
    const essentials = [
      group('Coleta', 'refresh-cw', 'Lê o SIGAA, detecta mudanças e gera avisos.',
        `<div class="dash-quick-actions">${btn('sync', 'Coletar agora', true)}${btn('process-pending', 'Processar pendências')}</div>`),
      simulado && su.sim_db ? group('Tempo do SIGAA Simulado', 'clock', 'Avance o relógio e colete para o agente perceber as mudanças.',
        `${clock}<div class="dash-quick-actions">${btn('sim-avancar-hora', '+1 hora')}${btn('sim-avancar-dia', '+1 dia', true)}${btn('sim-avancar-semana', '+1 semana')}</div>`) : '',
      group('Plano do dia', 'target', 'A Mochila 0/1 escolhe as sessões que cabem no seu tempo livre.',
        form('plan', '<input type="hidden" name="new" value="1">', 'Gerar plano de hoje', true)),
    ].filter(Boolean).join('');
    const advanced = [
      group('Coleta', 'refresh-cw', '',
        `<div class="dash-quick-actions">${btn('sync-collect')}${btn('telegram-poll')}</div>
         ${has.has('sync-fixture-changed') ? `<p class="dash-ctl-sub mt-3">Cenários do perfil demo:</p><div class="dash-quick-actions">${btn('sync-fixture-baseline')}${btn('sync-fixture-changed')}${btn('sync-fixture-expired')}</div>` : ''}`),
      simulado && su.sim_db ? group('Relógio', 'clock', '',
        `${form('sim-avancar', num('dias', 'Dias', '0', 0, 365) + num('horas', 'Horas', '0', 0, 23), 'Avançar')}
         ${form('sim-avancar', when('ate', 'Ir até'), 'Avançar até')}
         <div class="dash-quick-actions mt-3">${btn('sim-eventos', 'Próximos eventos')}${btn('sim-eventos-todos', 'Todos os eventos')}${btn('sim-info', 'Info')}</div>`) : '',
      group('Plano e Mochila 0/1', 'backpack', '',
        `${form('plan', day('date', 'Dia (vazio = hoje)') + chk('new', 'nova versão', true) + chk('no_ai', 'sem IA'), 'Gerar plano')}
         ${form('mochila-custom', day('date', 'Dia') + num('minutos', 'Minutos livres', 'do dia', 1, 1440) + chk('tabela', 'tabela T[i][w]') + chk('comparar', 'comparar guloso/força bruta', true), 'Rodar mochila')}
         ${form('mochila-custom', '<input type="hidden" name="exemplo" value="1">' + num('capacidade', 'Capacidade', '7', 1, 50) + chk('tabela', 'tabela', true) + chk('comparar', 'comparar', true), 'Exemplo didático')}`),
      simulado ? group('SIGAA Simulado', 'database', 'Mesma semente → mesmos dados.',
        `${has.has('sim-sessao-expirar') && su.sim_db ? `<div class="dash-quick-actions mb-3">${btn('sim-sessao-expirar', 'Expirar sessão')}${btn('sim-sessao-renovar', 'Renovar sessão')}</div>` : ''}
         ${form('sim-gerar', num('semente', 'Semente', '2026', 0, 2147483647) + num('discentes', 'Discentes', '40', 1, 500) + when('relogio', 'Relógio inicial') + (su.sim_db ? chk('sobrescrever', 'apagar e recriar') : ''), su.sim_db ? 'Recriar SIGAA' : 'Gerar SIGAA', !su.sim_db)}`) : '',
      group('Diagnóstico', 'stethoscope', '',
        `<div class="dash-quick-actions mb-3">${btn('status')}${btn('doctor')}${btn('init')}${btn('demo')}</div>
         ${st.checks.map((c) => `<div class="dash-check ${c.ok ? 'is-ok' : c.soft ? 'is-soft' : 'is-bad'}"><i data-lucide="${c.ok ? 'check-circle-2' : c.soft ? 'alert-triangle' : 'x-circle'}"></i><span>${esc(c.label)}</span>${!c.ok && c.hint ? `<small>— ${esc(c.hint)}</small>` : ''}</div>`).join('')}`),
    ].filter(Boolean).join('');
    const more = (key, title, sub, body) => `<details class="dash-more" data-more="${key}" ${opsMore[key] ? 'open' : ''}><summary><i data-lucide="chevron-right"></i><b>${esc(title)}</b><small>${esc(sub)}</small></summary><div class="dash-more-body">${body}</div></details>`;

    const lr = st.last_run;
    const runsRows = st.runs.map((r) => `<tr><td>#${r.id}</td><td>${statusBadge(r.status)}${r.baseline ? ' ' + badge('baseline', 'light') : ''}</td><td class="whitespace-nowrap">${esc(fmtDateTime(r.started_at))}</td><td>${r.stats.duration_s != null ? r.stats.duration_s + 's' : '—'}</td><td>${r.stats.items_seen ?? '—'}</td><td>${r.stats.created ?? '—'}/${r.stats.updated ?? '—'}/${r.stats.removed ?? '—'}</td><td>${r.stats.sections_ok ?? '—'}/${r.stats.sections_partial ?? '—'}/${r.stats.sections_empty ?? '—'}/${r.stats.sections_failed ?? '—'}</td><td class="text-xs text-red-600">${esc(r.error || '')}</td></tr>`).join('');
    const jobsKinds = Object.entries(st.jobs_pending_by_kind).map(([k, n]) => badge(`${n} ${k.replace(/_/g, ' ')}`, 'warning')).join(' ') || badge('nenhuma pendência', 'success');
    const failed = st.jobs_failed.length ? `<div class="dash-table-wrap mt-3"><table class="dash-table"><thead><tr><th>#</th><th>Tipo</th><th>Status</th><th>Tent.</th><th>Erro</th></tr></thead><tbody>${st.jobs_failed.slice(0, 5).map((j) => `<tr><td>${j.id}</td><td>${esc(j.kind)}</td><td>${statusBadge(j.status)}</td><td>${j.attempts}</td><td class="text-xs">${esc(j.last_error || '')}</td></tr>`).join('')}</tbody></table></div>${st.jobs_failed.length > 5 ? `<p class="text-xs text-slate-500 mt-2">+ ${st.jobs_failed.length - 5} outras adiadas/falhas</p>` : ''}` : '';
    const aiPct = st.ai_limits.tokens_per_day ? Math.min(100, Math.round((st.ai_today.tokens / st.ai_limits.tokens_per_day) * 100)) : 0;
    const execRows = runs.length ? runs.map((r) => `<tr class="is-clickable" data-run="${r.id}"><td>#${r.id}</td><td>${esc(r.label)}</td><td>${r.status === 'running' ? badge('executando…', 'info') : statusBadge(r.status)}</td><td class="whitespace-nowrap">${esc(fmtDateTime(new Date(r.started_at * 1000).toISOString()))}</td><td>${r.finished_at ? Math.round(r.finished_at - r.started_at) + 's' : '—'}</td><td>${r.line_count} linhas</td></tr>`).join('') : '';
    render(`${onboarding}${st.running ? `<p class="text-sm text-slate-500 mb-3"><span class="refresh-dot is-loading"></span> ${esc(st.running.label)} em execução…</p>` : ''}
      <div id="run-output" class="mb-4"></div>
      <div class="dash-grid-3 mb-5">${essentials}</div>
      ${more('tools', 'Mais comandos', 'avanço personalizado, mochila, gerar SIGAA, diagnóstico', `<div class="dash-grid-2">${advanced}</div>`)}
      ${more('monitor', 'Monitoramento', 'coletas, pendências, mensagens e log', `
      <div class="dash-grid-2 mb-4">
        ${card('Última coleta', 'refresh-cw', lr ? `<div class="dash-kv"><dt>Status</dt><dd>${statusBadge(lr.status)}</dd><dt>Início</dt><dd>${esc(fmtDateTime(lr.started_at))}</dd><dt>Duração</dt><dd>${lr.stats.duration_s ?? '—'}s</dd><dt>Itens</dt><dd>${lr.stats.items_seen ?? '—'} (novos ${lr.stats.created ?? 0}, alterados ${lr.stats.updated ?? 0})</dd><dt>Seções</dt><dd>ok ${lr.stats.sections_ok ?? 0} · parcial ${lr.stats.sections_partial ?? 0} · vazia ${lr.stats.sections_empty ?? 0} · falha ${lr.stats.sections_failed ?? 0}</dd><dt>Sucesso</dt><dd>${esc(fmtDateTime(st.last_success_at))}</dd></div>${lr.error ? `<div class="dash-alert dash-alert-danger mt-3"><i data-lucide="alert-circle"></i>${esc(lr.error)}</div>` : ''}` : empty('Nenhuma coleta ainda.', 'refresh-cw'))}
        ${card('Pendências e IA', 'cpu', `<div class="mb-3">${jobsKinds}</div><div class="dash-kv"><dt>Jobs</dt><dd>${esc(JSON.stringify(st.jobs))}</dd><dt>Outbox</dt><dd>${esc(JSON.stringify(st.outbox))}</dd><dt>IA hoje</dt><dd>${st.ai_today.calls} chamadas · ${st.ai_today.tokens} tokens</dd></div><div class="dash-minutes-bar mt-2"><div class="dash-progress"><div class="dash-progress-bar ${aiPct > 80 ? 'dash-progress-crit' : 'dash-progress-ok'}" style="width:${aiPct}%"></div></div><span>${aiPct}% do orçamento diário</span></div>${failed}`)}
      </div>
      ${runs.length ? card('Comandos executados nesta sessão', 'terminal', `<div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>#</th><th>Comando</th><th>Status</th><th>Início</th><th>Duração</th><th></th></tr></thead><tbody>${execRows}</tbody></table></div>`) + '<div class="mb-4"></div>' : ''}
      ${card('Histórico de coletas', 'history', runsRows ? `<div class="dash-table-wrap"><table class="dash-table"><thead><tr><th>#</th><th>Status</th><th>Início</th><th>Dur.</th><th>Itens</th><th>Novos/alt./rem.</th><th>Seções ok/parc./vazia/falha</th><th>Erro</th></tr></thead><tbody>${runsRows}</tbody></table></div>` : empty('Nenhuma coleta ainda.', 'history'))}
      <div class="mb-4"></div>
      <div class="dash-grid-2">${card('Mensagens (outbox)', 'send', '<div id="outbox-box">' + skeleton(2, 3) + '</div>')}${card('Log', 'scroll-text', '<div id="log-box">' + skeleton(1, 8) + '</div>', '<button type="button" class="dash-btn dash-btn-ghost dash-btn-sm" id="log-refresh"><i data-lucide="refresh-cw"></i></button>')}</div>`)}`);
    main.querySelectorAll('details[data-more]').forEach((d) => d.addEventListener('toggle', () => { opsMore[d.dataset.more] = d.open; }));

    const run = async (preset, params) => {
      if (preset === 'sim-gerar' && params.sobrescrever && !window.confirm('Apagar o SIGAA Simulado atual e recriar?')) return;
      try {
        const r = await post('ops/run', { preset, params: params || {} });
        toast(`${r.label} iniciado.`);
        main.querySelectorAll('[data-preset] button, button[data-preset]').forEach((b) => { b.disabled = true; });
        followRun(r.id); $('run-output').scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (e) { toast(e.message, 'err'); }
    };
    main.querySelectorAll('button[data-preset]').forEach((b) => b.addEventListener('click', () => run(b.dataset.preset)));
    main.querySelectorAll('form[data-preset]').forEach((f) => f.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const params = {};
      new FormData(f).forEach((v, k) => { if (v !== '') params[k] = v; });
      run(f.dataset.preset, params);
    }));
    main.querySelectorAll('[data-run]').forEach((tr) => tr.addEventListener('click', () => followRun(Number(tr.dataset.run))));
    if (st.running) followRun(st.running.id);
    loadOutbox(); loadLog();
    $('log-refresh').addEventListener('click', loadLog);
  }
  let buddyRun = null;
  async function followRun(id) {
    clearTimeout(opsTimer);
    const box = $('run-output'); if (!box) return;
    const r = await api(`ops/runs/${id}`);
    if (r.status === 'running' && buddyRun !== id) { buddyRun = id; buddy.working(true); }
    else if (r.status !== 'running' && buddyRun === id) {
      buddyRun = null; buddy.working(false);
      if (r.exit_code === 0) { buddy.cheer(); buddy.say(`Pronto! <b>${esc(r.label)}</b> terminou.`); }
      else { buddy.oops(); buddy.say(`Ih… <b>${esc(r.label)}</b> saiu com código ${esc(r.exit_code)}. Dá uma olhada no log.`); }
    }
    box.innerHTML = card(`${r.label} · #${r.id}`, 'terminal', `<div class="text-xs text-slate-500 mb-2">${r.commands.map((c) => `<code>sigaa-sim ${esc(c)}</code>`).join(' → ')} · ${r.status === 'running' ? '<span class="refresh-dot is-loading"></span> executando' : statusBadge(r.status) + ` · exit ${r.exit_code}`}</div><pre class="dash-log">${r.output.map(esc).join('\n') || '(sem saída ainda)'}</pre>`);
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
    let { name, rest, params } = parseHash();
    // links antigos para a tela de chat: abre a janela flutuante sobre o início
    if (name === 'chat') { nimboChat.open(); history.replaceState(null, '', '#/'); name = 'home'; }
    const fn = views[name] || views.home;
    document.querySelectorAll('#sidebar-nav [data-route]').forEach((a) => a.classList.toggle('is-active', a.dataset.route === name || (name === 'home' && a.dataset.route === 'home')));
    const my = ++routing;
    try { await fn(params, rest); } catch (e) {
      if (my !== routing || e.message === 'unauthorized') return;
      render(`<div class="dash-card">${empty(`Erro ao carregar: ${e.message}`, 'alert-circle')}</div>`);
      buddy.oops();
    }
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', () => { buddy.walk(); route(); });

  (async () => {
    icons();
    if (await initGate()) { buddy.start(); route(); refreshIndicator(); }
    setInterval(() => { if ($('gate').hidden) refreshIndicator(); }, 60000);
  })();
})();
