/* Lore landing page. No framework, no build step.
   Everything shown in the hero console comes from Lore's real tool output on HERB's synthetic
   ExplainabilityForce data (decision_lineage, rank, who_knows), with verbatim quotes. */
(() => {
  'use strict';

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  // Reduced motion ("calm"): no travelling motion, but typing, highlighting and fades stay.
  // ?motion=full shows everything, for a screen recording on a machine with animations off.
  const forceMotion = new URLSearchParams(location.search).get('motion') === 'full';
  const reduced = !forceMotion && matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) document.documentElement.classList.add('calm');
  const root = document.documentElement;
  const cssVar = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  /* ---------------- theme ---------------- */
  const themeBtn = $('#themeToggle');
  const isDark = () => root.dataset.theme === 'dark';  // light unless chosen (index.html sets it before paint)
  const syncThemeLabel = () => themeBtn.setAttribute('aria-label', isDark() ? 'Switch to light theme' : 'Switch to dark theme');
  syncThemeLabel();
  themeBtn.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('lore-theme', next); } catch (e) { /* storage blocked: theme lasts this visit */ }
    syncThemeLabel();
    graph && graph.refreshColors();
  });

  /* ---------------- nav ---------------- */
  const nav = $('#nav');
  const onScroll = () => nav.classList.toggle('is-scrolled', scrollY > 12);
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  const night = document.getElementById('falkordb');
  if (night) {
    const darkCheck = () => { const r = night.getBoundingClientRect(); nav.classList.toggle('on-dark', r.top <= 40 && r.bottom > 40); };
    addEventListener('scroll', darkCheck, { passive: true }); darkCheck();
  }
  const navLinks = $$('.nav-links a');
  const secObs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      navLinks.forEach((a) => a.classList.toggle('is-current', a.getAttribute('href') === '#' + e.target.id));
    });
  }, { rootMargin: '-45% 0px -50% 0px' });
  // sections without a nav link clear the highlight
  ['problem', 'how', 'features', 'results', 'falkordb', 'works', 'next'].forEach((id) => { const el = document.getElementById(id); el && secObs.observe(el); });

  /* =====================================================================
     Hero graph: the real subgraph around ExplainabilityForce, drawn in the
     margins. The console lights the nodes its tools touch; cited sources
     turn highlighter yellow, as they do in the product.
     ===================================================================== */
  const NODES = [
    // left flank: the decision chain of one issue
    { id: 'product', t: 'product', label: 'ExplainabilityForce', s: 'L', u: .42, v: .2 },
    { id: 'pr46', t: 'pr', label: 'PR #46', s: 'L', u: .82, v: .1 },
    { id: 'hannah', t: 'person', label: 'Hannah Johnson', s: 'L', u: .74, v: .33 },
    { id: 'pr53', t: 'pr', label: 'PR #53', s: 'L', u: .3, v: .38 },
    { id: 'bob', t: 'person', label: 'Bob Davis', s: 'L', u: .16, v: .44 },
    { id: 'iss_cache', t: 'issue', label: 'Inefficient Caching Strategy', s: 'L', u: .6, v: .55 },
    { id: 'dec_rep', t: 'decision', label: 'reported', s: 'L', u: .2, v: .66 },
    { id: 'dec_res', t: 'decision', label: 'resolved', s: 'L', u: .52, v: .7 },
    { id: 'dec_reo', t: 'decision', label: 'reopened', s: 'L', u: .82, v: .64 },
    { id: 'msg_47', t: 'message', label: 'msg:20270206-1-47b0c', s: 'L', u: .16, v: .84 },
    { id: 'msg_ca', t: 'message', label: 'msg:20270224-0-ca81d', s: 'L', u: .5, v: .9 },
    { id: 'msg_1b', t: 'message', label: 'msg:20270226-0-1bc7a', s: 'L', u: .84, v: .82 },
    // right flank: customers and companies, then expertise
    { id: 'julia', t: 'person', label: 'Julia Miller', s: 'R', u: .3, v: .08 },
    { id: 'pr55', t: 'pr', label: 'PR #55', s: 'R', u: .86, v: .1 },
    { id: 'area', t: 'area', label: 'Backend Processing Engine', s: 'R', u: .55, v: .22 },
    { id: 'charlie', t: 'person', label: 'Charlie Williams', s: 'R', u: .84, v: .3 },
    { id: 'fiona', t: 'person', label: 'Fiona Smith', s: 'R', u: .22, v: .35 },
    { id: 'cust11', t: 'customer', label: 'CUST-0011', s: 'R', u: .7, v: .46 },
    { id: 'datasol', t: 'company', label: 'DataSolutions', s: 'R', u: .34, v: .54 },
    { id: 'iss_auth', t: 'issue', label: 'Improper Authentication Token Validation', s: 'R', u: .8, v: .6 },
    { id: 'msg_b5', t: 'message', label: 'msg:20270217-3-b54eb', s: 'R', u: .9, v: .74 },
    { id: 'cust_x', t: 'customer', label: '', s: 'R', u: .16, v: .7 },
    { id: 'iss_edge', t: 'issue', label: 'Incorrect Output in Edge Cases', s: 'R', u: .4, v: .78 },
    { id: 'iss_query', t: 'issue', label: 'Suboptimal Query Optimization', s: 'R', u: .66, v: .86 },
    { id: 'iss_pdp', t: 'issue', label: 'Faulty Partial Dependence Plots', s: 'R', u: .14, v: .9 },
    { id: 'smart', t: 'company', label: 'SmartData', s: 'R', u: .9, v: .96 },
  ];
  const EDGES = [
    ['iss_cache', 'product'], ['pr53', 'iss_cache'], ['hannah', 'pr53'], ['hannah', 'pr46'],
    ['dec_rep', 'iss_cache'], ['dec_res', 'iss_cache'], ['dec_reo', 'iss_cache'],
    ['dec_res', 'dec_rep'], ['dec_reo', 'dec_res'],
    ['dec_rep', 'bob'], ['dec_res', 'hannah'], ['dec_reo', 'bob'],
    ['dec_rep', 'msg_47'], ['dec_res', 'msg_ca'], ['dec_reo', 'msg_1b'],
    ['iss_cache', 'cust11'], ['cust11', 'datasol'], ['iss_auth', 'cust11'], ['iss_auth', 'msg_b5'],
    ['iss_edge', 'cust_x'], ['iss_query', 'cust_x'], ['iss_pdp', 'cust_x'], ['cust_x', 'datasol'],
    ['iss_query', 'smart'],
    ['hannah', 'area'], ['charlie', 'area'], ['julia', 'area'], ['iss_cache', 'area'], ['fiona', 'hannah'],
    ['charlie', 'pr55'], ['pr46', 'area'], ['pr55', 'area'], ['bob', 'product'], ['iss_query', 'area'],
  ].map(([a, b]) => ({ a, b, key: a + '|' + b }))
   .filter((e) => NODES.find((n) => n.id === e.a).s === NODES.find((n) => n.id === e.b).s);

  const graph = (() => {
    const canvas = $('#heroGraph');
    if (!canvas || !canvas.getContext) return null;
    const ctx = canvas.getContext('2d');
    const hero = canvas.parentElement;
    const consoleEl = $('#console');
    const byId = new Map(NODES.map((n) => [n.id, n]));
    const lit = new Set();       // node ids a running tool touched
    const litE = new Set();      // edge keys between lit nodes
    const cited = new Map();     // node id -> time it turned yellow
    let W = 0, H = 0, dpr = 1, flankL = 0, flankR = 0, cTop = 0, compact = false;
    let colors = {};
    const dust = [];             // unlabeled background nodes for texture
    let running = false, raf = 0, t0 = performance.now();

    function refreshColors() {
      colors = {
        ink: cssVar('--ink'), muted: cssVar('--muted'), faint: cssVar('--faint'), rule: cssVar('--rule-2'),
        petrol: cssVar('--petrol'), mark: cssVar('--mark'), paper: cssVar('--paper'), surface: cssVar('--surface'),
      };
    }

    function layout() {
      const r = hero.getBoundingClientRect();
      W = r.width; H = r.height + 80;
      dpr = Math.min(devicePixelRatio || 1, 2);
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const c = consoleEl.getBoundingClientRect();
      const left = c.left - r.left, right = r.right - c.right;
      flankL = Math.max(0, left - 24); flankR = Math.max(0, right - 24);
      cTop = c.top - r.top + 80;
      compact = flankL < 150;
      const top = Math.max(70, cTop - 360), bottom = Math.min(H - 40, cTop + c.height + 40);
      NODES.forEach((n) => {
        if (compact) {
          // narrow screens: a quiet band of nodes above the console, no labels
          n.x = n.s === 'L' ? W * (.04 + n.u * .4) : W * (.56 + n.u * .4);
          n.y = 40 + n.v * Math.max(120, cTop - 120);
        } else {
          n.y = top + n.v * (bottom - top);
          // above the console the margin widens to the edge of the subhead
          let fw = n.s === 'L' ? flankL : flankR;
          if (n.y < cTop - 40) fw = Math.max(fw, (W - 760) / 2 - 10);
          n.fw = fw;
          n.x = n.s === 'L' ? 22 + n.u * (fw - 30) : W - 22 - n.u * (fw - 30);
        }
        n.ph = (n.x * 13.37 + n.y * 7.1) % 6.28;
      });
      dust.length = 0;
      const count = compact ? 18 : 46;
      let seed = 7;
      const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
      for (let i = 0; i < count; i++) {
        const side = rnd() < .5 ? 'L' : 'R';
        const fw = compact ? W * .45 : (side === 'L' ? flankL : flankR) + 160;
        const x = side === 'L' ? rnd() * fw : W - rnd() * fw;
        const y = compact ? 20 + rnd() * Math.max(140, cTop - 60) : 80 + rnd() * (H - 120);
        dust.push({ x, y, r: 1.2 + rnd() * 1.8, ph: rnd() * 6.28 });
      }
    }

    const pos = (n, t) => reduced ? [n.x, n.y] : [n.x + Math.sin(t * .00042 + n.ph) * 4, n.y + Math.cos(t * .00036 + n.ph * 1.3) * 4];

    function shape(n, x, y, r, fill, stroke, lw, dash) {
      ctx.beginPath();
      ctx.setLineDash(dash || []);
      switch (n.t) {
        case 'person': ctx.arc(x, y, r, 0, Math.PI * 2); break;
        case 'area': ctx.arc(x, y, r + 1.5, 0, Math.PI * 2); fill = null; break;
        case 'issue': ctx.moveTo(x, y - r - 1.5); ctx.lineTo(x + r + 1, y + r * .8); ctx.lineTo(x - r - 1, y + r * .8); ctx.closePath(); fill = fill === colors.mark ? fill : null; dash = [3, 2.4]; ctx.setLineDash(dash); break;
        case 'customer': for (let i = 0; i < 5; i++) { const a = -Math.PI / 2 + i * 2 * Math.PI / 5; ctx[i ? 'lineTo' : 'moveTo'](x + Math.cos(a) * r, y + Math.sin(a) * r); } ctx.closePath(); break;
        case 'product': for (let i = 0; i < 6; i++) { const a = Math.PI / 6 + i * Math.PI / 3; ctx[i ? 'lineTo' : 'moveTo'](x + Math.cos(a) * (r + 1), y + Math.sin(a) * (r + 1)); } ctx.closePath(); break;
        case 'company': ctx.roundRect(x - r, y - r, r * 2, r * 2, 2.5); break;
        case 'decision': for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * .45 : r + 1; ctx[i ? 'lineTo' : 'moveTo'](x + Math.cos(a) * rr, y + Math.sin(a) * rr); } ctx.closePath(); if (fill !== colors.mark) fill = null; break;
        case 'message': case 'pr': ctx.roundRect(x - r, y - r, r * 2, r * 2, n.t === 'pr' ? 2 : 4); if (fill !== colors.mark) fill = null; break;
      }
      if (fill) { ctx.fillStyle = fill; ctx.fill(); }
      if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
      ctx.setLineDash([]);
    }

    function frame(t) {
      ctx.clearRect(0, 0, W, H);
      const P = new Map();
      NODES.forEach((n) => P.set(n.id, pos(n, t)));

      // dust and its faint mesh
      ctx.fillStyle = colors.rule;
      dust.forEach((d) => {
        ctx.globalAlpha = .55;
        ctx.beginPath(); ctx.arc(d.x + Math.sin(t * .0003 + d.ph) * 3, d.y + Math.cos(t * .00025 + d.ph) * 3, d.r, 0, 6.28); ctx.fill();
      });
      ctx.globalAlpha = 1;

      // edges
      EDGES.forEach((e) => {
        const [x1, y1] = P.get(e.a), [x2, y2] = P.get(e.b);
        const on = litE.has(e.key);
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, dx = x2 - x1, dy = y2 - y1;
        const len = Math.hypot(dx, dy) || 1, bend = Math.min(60, len * .12);
        const cx = mx - dy / len * bend, cy = my + dx / len * bend;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(cx, cy, x2, y2);
        if (on) {
          ctx.strokeStyle = colors.petrol; ctx.globalAlpha = .85; ctx.lineWidth = 1.4;
          ctx.setLineDash([5, 5]); ctx.lineDashOffset = reduced ? 0 : -t * .03;
        } else {
          ctx.strokeStyle = colors.rule; ctx.globalAlpha = compact ? .45 : .75; ctx.lineWidth = 1;
          ctx.setLineDash([]);
        }
        ctx.stroke();
      });
      ctx.setLineDash([]); ctx.globalAlpha = 1;

      // nodes
      NODES.forEach((n) => {
        const [x, y] = P.get(n.id);
        const isLit = lit.has(n.id), c = cited.get(n.id);
        const r = n.t === 'person' || n.t === 'company' ? 5.5 : 6;
        if (c != null && !reduced) {
          const k = Math.min(1, (t - c) / 900);
          if (k < 1) { ctx.beginPath(); ctx.arc(x, y, 8 + k * 22, 0, 6.28); ctx.strokeStyle = colors.mark; ctx.globalAlpha = 1 - k; ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 1; }
        }
        ctx.globalAlpha = c != null ? 1 : isLit ? 1 : (compact ? .35 : .55);
        const base = isLit || c != null ? colors.petrol : colors.faint;
        if (c != null) shape(n, x, y, r + 1.5, colors.mark, colors.ink, 1.4);
        else shape(n, x, y, r, base, base, 1.4);
        if (!compact && n.label) {
          ctx.font = (c != null || isLit ? '600 ' : '500 ') + (n.t === 'message' || n.t === 'pr' && n.label.startsWith('pr:') ? '10.5px "JetBrains Mono", monospace' : '11.5px "Schibsted Grotesk", sans-serif');
          if (n.t === 'message') ctx.font = (c != null || isLit ? '600 ' : '400 ') + '10px "JetBrains Mono", monospace';
          ctx.fillStyle = c != null || isLit ? colors.ink : colors.muted;
          ctx.globalAlpha = c != null || isLit ? 1 : .7;
          let label = n.label;
          const maxW = Math.max(90, (n.fw || 0) * .95);
          while (ctx.measureText(label).width > maxW && label.length > 6) label = label.slice(0, -2);
          if (label !== n.label) label = label.trimEnd() + '…';
          const tw = ctx.measureText(label).width;
          let lx = x - tw / 2;
          lx = Math.max(6, Math.min(lx, W - tw - 6));
          ctx.fillText(label, lx, y + r + 14);
        }
        ctx.globalAlpha = 1;
      });
    }

    function loop(t) { frame(t); raf = running ? requestAnimationFrame(loop) : 0; }
    function start() { if (running) return; running = true; if (reduced) { frame(performance.now()); running = false; return; } raf = requestAnimationFrame(loop); }
    function stop() { running = false; cancelAnimationFrame(raf); }
    function redrawIfIdle() { if (!running) frame(performance.now()); }

    refreshColors(); layout();
    new ResizeObserver(() => { layout(); redrawIfIdle(); }).observe(hero);
    new IntersectionObserver(([e]) => (e.isIntersecting ? start() : stop())).observe(hero);
    document.fonts && document.fonts.ready.then(redrawIfIdle);

    return {
      light(ids) {
        ids.forEach((id) => lit.add(id));
        EDGES.forEach((e) => { if (lit.has(e.a) && lit.has(e.b)) litE.add(e.key); });
        redrawIfIdle();
      },
      cite(id) { cited.set(id, performance.now()); redrawIfIdle(); },
      clear() { lit.clear(); litE.clear(); cited.clear(); redrawIfIdle(); },
      refreshColors() { refreshColors(); redrawIfIdle(); },
    };
  })();

  /* =====================================================================
     The console: types a question, runs the tools, streams the answer,
     verifies each quote, then moves to the next question.
     ===================================================================== */
  const PR = 'pr:github_com_salesforce_ExplainabilityForce_pull_';
  const QUESTIONS = [
    {
      q: 'Is the ‘Inefficient Caching Strategy’ bug fixed?',
      entities: [{ after: 'Strategy’', kind: 'Issue', text: 'Inefficient Caching Strategy', id: 'iss:ExplainabilityForce:8dd15023ef', node: 'iss_cache' }],
      trace: [
        { tool: 'decision_lineage', arg: 'subject: Inefficient Caching Strategy', ms: 12, cy: ['lineage_subjects.cypher', 'decision_chain.cypher'],
          light: ['iss_cache', 'product', 'dec_rep', 'dec_res', 'dec_reo', 'bob', 'hannah', 'pr53', 'msg_47', 'msg_ca', 'msg_1b'] },
      ],
      answer: [
        { text: 'No. Hannah Johnson marked it resolved on 24 Feb 2027 with PR #53,', cite: 1 },
        { text: ' then on 26 Feb Bob Davis reported that the customer had reopened it. That is the state in force now.', cite: 2 },
      ],
      sources: [
        { quote: 'the bug titled ‘Inefficient Caching Strategy’ has been resolved.', id: 'msg:20270224-0-ca81d', meta: 'Slack, #bug-bobdavis-ExplainabilityForce. Hannah Johnson, 24 Feb 2027', node: 'msg_ca' },
        { quote: 'the customer has reopened the bug titled ‘Inefficient Caching Strategy’', id: 'msg:20270226-0-1bc7a', meta: 'Slack, #bug-bobdavis-ExplainabilityForce. Bob Davis, 26 Feb 2027', node: 'msg_1b' },
      ],
      state: { good: false, html: 'In force: <b>reopened</b>, since 26 Feb 2027 20:17 UTC. A regression alert is open.' },
    },
    {
      q: 'Which company has the most unresolved bugs?',
      entities: [
        { after: 'company', kind: 'Rank by', text: 'company', id: 'rank(by: company)' },
        { after: 'unresolved', kind: 'Metric', text: 'issues unresolved', id: 'issues_unresolved' },
      ],
      trace: [
        { tool: 'rank', arg: 'metric: issues_unresolved, by: company', ms: 5, cy: ['rank_companies.cypher'],
          light: ['datasol', 'smart', 'cust11', 'cust_x', 'iss_cache', 'iss_auth', 'iss_edge', 'iss_query', 'iss_pdp', 'msg_47', 'msg_b5'] },
      ],
      answer: [
        { text: 'DataSolutions, with 5 unresolved issues; SmartData is next with 3. Their customer CUST-0011 reported the caching bug,', cite: 1 },
        { text: ' and four of the five were reopened after being marked fixed, among them ‘Improper Authentication Token Validation’.', cite: 2 },
      ],
      sources: [
        { quote: 'This is impacting customer CUST-0011.', id: 'msg:20270206-1-47b0c', meta: 'Slack, #bug-bobdavis-ExplainabilityForce. Bob Davis, 6 Feb 2027', node: 'msg_47' },
        { quote: 'This is a big security risk for customer CUST-0011.', id: 'msg:20270217-3-b54eb', meta: 'Slack, #bug-bobdavis-ExplainabilityForce. Bob Davis, 17 Feb 2027', node: 'msg_b5' },
      ],
      state: { good: true, html: 'Counted in Cypher with <code>count()</code>: 9 companies ranked, not guessed from text.' },
    },
    {
      q: 'Who knows the Backend Processing Engine best?',
      entities: [{ after: 'Engine', kind: 'Area', text: 'Backend Processing Engine', id: 'area:ExplainabilityForce:c:backend-processing-engine', node: 'area' }],
      trace: [
        { tool: 'who_knows', arg: 'topic: Backend Processing Engine', ms: 17,
          cy: ['product_areas.cypher', 'area_experts.cypher', 'area_evidence.cypher', 'area_owners.cypher', 'product_truck.cypher', 'collab_paths.cypher'],
          light: ['area', 'hannah', 'charlie', 'julia', 'fiona', 'pr53', 'pr46', 'pr55', 'iss_cache', 'iss_query'] },
      ],
      answer: [
        { text: 'Hannah Johnson. She wrote the caching fix,', cite: 1 },
        { text: ' and an earlier PR in the same component.', cite: 2 },
        { text: ' Charlie Williams is next, with a backend performance PR.', cite: 3 },
      ],
      sources: [
        { quote: 'Optimize Caching Strategy to Reduce Cache Misses and Redundant Computations', id: PR + '53', meta: 'Pull request #53. Hannah Johnson, 24 Feb 2027', node: 'pr53' },
        { quote: 'Optimize Model Interpretation Algorithm for Complex Models', id: PR + '46', meta: 'Pull request #46. Hannah Johnson, 23 Dec 2026', node: 'pr46' },
        { quote: 'Optimize Backend Processing for Large Datasets to Improve Response Time', id: PR + '55', meta: 'Pull request #55. Charlie Williams, 3 Mar 2027', node: 'pr55' },
      ],
      state: { good: true, html: 'The listed owner, Fiona Smith, is one hop from her. Hannah is a <b>key person</b>: truck factor 3.' },
    },
  ];

  const ui = {
    query: $('.c-query'), typed: $('#qTyped'), ask: $('#qAsk'), ents: $('#qEntities'), prog: $('#qProgress'),
    trace: $('#cTrace'), answer: $('#cAnswer'), sources: $('#cSources'),
    ver: $('#mVerified'), verBar: $('#mVerBar'), ms: $('#mMs'), calls: $('#mCalls'), state: $('#mState'),
    pause: $('#cPause'), tabs: $$('.sugg'), asOf: $('#cAsOf'),
  };

  const CANCEL = Symbol('cancel');
  let token = { cancelled: false };
  let paused = false, offscreen = false, userPaused = false;

  function wait(ms, tk) {
    return new Promise((resolve, reject) => {
      let left = ms, last = performance.now();
      const step = (now) => {
        if (tk.cancelled) return reject(CANCEL);
        if (!(paused || offscreen || document.hidden)) left -= now - last;
        last = now;
        left <= 0 ? resolve() : requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }

  function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function resetConsole() {
    ui.typed.textContent = ''; ui.ents.innerHTML = ''; ui.trace.innerHTML = ''; ui.answer.innerHTML = ''; ui.sources.innerHTML = '';
    ui.prog.className = 'q-progress'; ui.query.classList.remove('is-typing', 'is-running'); ui.ask.classList.remove('is-pressed');
    ui.ver.textContent = '0 / 0'; ui.verBar.style.width = '0'; ui.ms.textContent = '0 ms'; ui.calls.textContent = '0';
    ui.state.textContent = 'idle'; ui.state.className = 'm-v';
    graph && graph.clear();
  }

  function selectTab(i) { ui.tabs.forEach((b, k) => b.setAttribute('aria-selected', String(k === i))); }

  function addEntity(e) {
    const chip = el('span', 'ent', `<span class="ek">${esc(e.kind)}</span>${esc(e.text)} <code>${esc(e.id)}</code>`);
    ui.ents.appendChild(chip);
    e.node && graph && graph.light([e.node]);
  }

  async function run(i, tk) {
    const Q = QUESTIONS[i];
    selectTab(i);
    resetConsole();
    let verified = 0, total = Q.sources.length, msTotal = 0;

    // 1. type the question
    ui.query.classList.add('is-typing');
    ui.state.textContent = 'typing';
    const shown = new Set();
    for (let k = 1; k <= Q.q.length; k++) {
      ui.typed.textContent = Q.q.slice(0, k);
      Q.entities.forEach((e, j) => { if (!shown.has(j) && Q.q.slice(0, k).endsWith(e.after)) { shown.add(j); addEntity(e); } });
      const ch = Q.q[k - 1];
      await wait(ch === ' ' ? 55 : /[,?’‘]/.test(ch) ? 110 : 30 + Math.random() * 42, tk);
    }
    ui.query.classList.remove('is-typing');
    await wait(380, tk);
    ui.ask.classList.add('is-pressed');
    await wait(160, tk);
    ui.ask.classList.remove('is-pressed');

    // 2. run the graph tools
    ui.query.classList.add('is-running');
    ui.prog.className = 'q-progress run';
    ui.state.textContent = 'querying FalkorDB';
    for (let s = 0; s < Q.trace.length; s++) {
      const T = Q.trace[s];
      const li = el('li', 'tr is-run', `<span class="tr-n">${s + 1}</span><div class="tr-tool">${T.tool}</div><div class="tr-arg">${esc(T.arg)}</div><div class="tr-cys"></div><div class="tr-meta"><span class="tr-ms">…</span></div>`);
      ui.trace.appendChild(li);
      const cys = $('.tr-cys', li);
      for (const c of T.cy) {
        await wait(170, tk);
        cys.appendChild(el('div', 'tr-cy', esc(c)));
      }
      graph && graph.light(T.light);
      await wait(260, tk);
      li.classList.replace('is-run', 'is-done');
      $('.tr-ms', li).textContent = T.ms + ' ms';
      msTotal += T.ms;
      ui.ms.textContent = msTotal + ' ms';
      ui.calls.textContent = String(s + 1);
    }
    ui.query.classList.remove('is-running');

    // 3. stream the answer, verifying each quote as its sentence lands
    ui.state.textContent = 'answering';
    ui.ver.textContent = `0 / ${total}`;
    const pending = [];
    for (const S of Q.answer) {
      const span = el('span', 'sent');
      ui.answer.appendChild(span);
      const words = S.text.split(/(\s+)/);
      for (const w of words) {
        if (!w) continue;
        span.appendChild(el('span', 'w', esc(w)));
        if (w.trim()) await wait(26, tk);
      }
      const chip = el('span', 'cite', String(S.cite));
      span.appendChild(chip);
      const src = Q.sources[S.cite - 1];
      const card = el('li', 'src', `<span class="src-n">${S.cite}</span><p class="src-q"><span class="hl">${esc(src.quote)}</span></p><p class="src-m">${esc(src.meta)}<code title="${esc(src.id)}">${esc(src.id)}</code></p><span class="src-chk">checking the quote word for word</span>`);
      ui.sources.appendChild(card);
      pending.push((async () => {
        await wait(620, tk);
        card.classList.add('ok');
        $('.src-chk', card).textContent = 'verified in source';
        chip.classList.add('ok');
        graph && graph.cite(src.node);
        verified++;
        ui.ver.textContent = `${verified} / ${total}`;
        ui.verBar.style.width = (verified / total * 100) + '%';
      })());
      await wait(140, tk);
    }
    await Promise.all(pending);
    ui.prog.className = 'q-progress done';
    const st = el('div', 'ans-state' + (Q.state.good ? ' good' : ''), `<span class="sd"></span><span>${Q.state.html}</span>`);
    ui.answer.appendChild(st);
    ui.state.textContent = 'every quote verified';
    ui.state.className = 'm-v ok';
  }

  // Final state, no motion: for reduced motion and for a direct pick while paused.
  function renderStatic(i) {
    const Q = QUESTIONS[i];
    selectTab(i); resetConsole();
    ui.typed.textContent = Q.q;
    Q.entities.forEach(addEntity);
    let ms = 0;
    Q.trace.forEach((T, s) => {
      ui.trace.appendChild(el('li', 'tr is-done', `<span class="tr-n">${s + 1}</span><div class="tr-tool">${T.tool}</div><div class="tr-arg">${esc(T.arg)}</div><div class="tr-cys">${T.cy.map((c) => `<div class="tr-cy">${esc(c)}</div>`).join('')}</div><div class="tr-meta"><span class="tr-ms">${T.ms} ms</span></div>`));
      ms += T.ms; graph && graph.light(T.light);
    });
    Q.answer.forEach((S) => ui.answer.appendChild(el('span', 'sent', `${esc(S.text)}<span class="cite ok">${S.cite}</span>`)));
    ui.answer.appendChild(el('div', 'ans-state' + (Q.state.good ? ' good' : ''), `<span class="sd"></span><span>${Q.state.html}</span>`));
    Q.sources.forEach((src, k) => {
      ui.sources.appendChild(el('li', 'src ok', `<span class="src-n">${k + 1}</span><p class="src-q"><span class="hl">${esc(src.quote)}</span></p><p class="src-m">${esc(src.meta)}<code title="${esc(src.id)}">${esc(src.id)}</code></p><span class="src-chk">verified in source</span>`));
      graph && graph.cite(src.node);
    });
    ui.prog.className = 'q-progress done';
    ui.ver.textContent = `${Q.sources.length} / ${Q.sources.length}`; ui.verBar.style.width = '100%';
    ui.ms.textContent = ms + ' ms'; ui.calls.textContent = String(Q.trace.length);
    ui.state.textContent = 'every quote verified'; ui.state.className = 'm-v ok';
  }

  let current = 0;
  async function cycle(start) {
    token.cancelled = true;
    const tk = token = { cancelled: false };
    let i = start;
    try {
      for (;;) {
        current = i;
        await run(i, tk);
        await wait(5200, tk);
        i = (i + 1) % QUESTIONS.length;
      }
    } catch (e) { if (e !== CANCEL) throw e; }
  }

  ui.tabs.forEach((b, i) => b.addEventListener('click', () => {
    if (userPaused) { token.cancelled = true; current = i; renderStatic(i); return; }
    cycle(i);
  }));
  ui.pause.addEventListener('click', () => {
    userPaused = !userPaused; paused = userPaused;
    ui.pause.setAttribute('aria-pressed', String(userPaused));
    $('span', ui.pause).textContent = userPaused ? 'Play' : 'Pause';
    if (!userPaused && token.cancelled) cycle(current);
  });
  new IntersectionObserver(([e]) => { offscreen = !e.isIntersecting; }, { threshold: .15 }).observe($('#console'));

  setTimeout(() => cycle(0), reduced ? 300 : 900);

  /* =====================================================================
     Problem: ask the same question on any day in February 2027.
     ===================================================================== */
  (() => {
    const range = $('#driftRange'); if (!range) return;
    const dateEl = $('#driftDate'), now = $('#dtlNow'), rag = $('#ansRag'), ragStamp = $('#ragStamp'), ragNote = $('#ragNote');
    const loreText = $('#loreText'), loreEv = $('#loreEv'), loreNote = $('#loreNote');
    const wins = { rep: $('.w-rep'), res: $('.w-res'), reo: $('.w-reo') };
    const MAX = 33;
    const fmt = (d) => new Date(Date.UTC(2027, 1, 1 + d)).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
    const Q = {
      rep: { q: 'This is impacting customer CUST-0011.', id: 'msg:20270206-1-47b0c', by: 'Bob Davis, 6 Feb 2027' },
      res: { q: 'the bug titled ‘Inefficient Caching Strategy’ has been resolved.', id: 'msg:20270224-0-ca81d', by: 'Hannah Johnson, 24 Feb 2027' },
      reo: { q: 'the customer has reopened the bug titled ‘Inefficient Caching Strategy’', id: 'msg:20270226-0-1bc7a', by: 'Bob Davis, 26 Feb 2027' },
    };
    const quote = (k) => `<blockquote class="q"><p><mark>${Q[k].q}</mark></p><footer><code>${Q[k].id}</code>${Q[k].by}</footer></blockquote>`;

    function update() {
      const d = +range.value, date = fmt(d);
      dateEl.textContent = date;
      range.setAttribute('aria-valuetext', date);
      now.style.left = (d / MAX * 100) + '%';
      const state = d < 5 ? 'none' : d < 23 ? 'rep' : d < 25 ? 'res' : 'reo';
      Object.entries(wins).forEach(([k, w]) => w.classList.toggle('in', k === state));

      const right = state === 'res';
      rag.classList.toggle('is-right', right);
      ragStamp.textContent = right ? 'Right on this day' : state === 'reo' ? 'Stale' : 'Wrong for this date';
      ragNote.textContent = right
        ? 'Right by luck: on this one day, the message it matched is still current.'
        : state === 'reo'
          ? 'The message it trusted was overturned on 26 Feb. Search can’t tell.'
          : 'It quotes a message written after this date. Search has no clock.';

      if (state === 'none') {
        loreText.textContent = 'There is no report of this bug yet on ' + date + '.';
        loreEv.innerHTML = '';
        loreNote.textContent = 'No decision in force: the first report comes on 6 Feb.';
      } else if (state === 'rep') {
        loreText.textContent = 'No. Bob Davis reported it on 6 Feb, and it is still open on ' + date + '.';
        loreEv.innerHTML = quote('rep');
        loreNote.textContent = 'In force on ' + date + ': reported, from 6 Feb until 24 Feb.';
      } else if (state === 'res') {
        loreText.textContent = 'Yes. Hannah Johnson marked it resolved on 24 Feb with PR #53.';
        loreEv.innerHTML = quote('res');
        loreNote.textContent = 'In force on ' + date + ': resolved, from 24 Feb until 26 Feb.';
      } else {
        loreText.innerHTML = 'Resolved on 24 Feb with PR #53, then <strong>reopened on 26 Feb</strong>.';
        loreEv.innerHTML = quote('res') + quote('reo');
        loreNote.textContent = 'In force on ' + date + ': reopened, since 26 Feb. The resolved state was closed, not deleted.';
      }
    }
    range.addEventListener('input', update);
    update();
  })();

  /* =====================================================================
     How it works: the sticky stage follows the step in view.
     ===================================================================== */
  (() => {
    const steps = $$('.step'), scenes = $$('#stage .scene');
    // narrow screens: each step carries its own copy of its scene
    $$('.stage-inline').forEach((slot) => {
      const sc = scenes[+slot.dataset.inline];
      if (sc) { const c = sc.cloneNode(true); c.classList.add('is-active'); slot.appendChild(c); }
    });
    const setStep = (i) => {
      steps.forEach((s, k) => s.classList.toggle('is-active', k === i));
      scenes.forEach((s, k) => s.classList.toggle('is-active', k === i));
    };
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) setStep(+e.target.dataset.step); });
    }, { rootMargin: '-48% 0px -48% 0px' });
    steps.forEach((s) => io.observe(s));

    document.addEventListener('click', (ev) => {
      const ap = ev.target.closest('[data-approve]');
      const di = ev.target.closest('[data-dismiss]');
      if (!ap && !di) return;
      const alert = ev.target.closest('.alert');
      const apBtn = $('[data-approve]', alert), diBtn = $('[data-dismiss]', alert), list = $('.delivery', alert);
      if (diBtn.dataset.mode === 'reset' && di) {
        apBtn.disabled = false; apBtn.textContent = 'Approve'; apBtn.classList.remove('is-done');
        diBtn.textContent = 'Dismiss'; diBtn.dataset.mode = ''; list.classList.remove('go');
        return;
      }
      if (ap) {
        apBtn.disabled = true; apBtn.textContent = 'Approved'; apBtn.classList.add('is-done');
        list.classList.remove('go'); void list.offsetWidth; list.classList.add('go');
      } else {
        apBtn.disabled = true; apBtn.textContent = 'Dismissed';
      }
      diBtn.textContent = 'Reset'; diBtn.dataset.mode = 'reset';
    });
  })();

  /* =====================================================================
     Feature tour: tabs that advance on their own until someone picks one.
     ===================================================================== */
  (() => {
    const tour = $('#tour'); if (!tour) return;
    const tabs = $$('.tour-tab', tour), imgs = $$('.tour-img', tour), path = $('#tourPath'), panel = $('#tp');
    let idx = 0, auto = !reduced;
    const show = (i, focus) => {
      idx = (i + tabs.length) % tabs.length;
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === idx)); t.tabIndex = k === idx ? 0 : -1; });
      imgs.forEach((im, k) => im.classList.toggle('is-on', k === idx));
      path.textContent = imgs[idx].dataset.path;
      panel.setAttribute('aria-labelledby', tabs[idx].id);
      if (focus) tabs[idx].focus();
      // restart the progress animation on the newly selected tab
      const bar = $('.tt-progress i', tabs[idx]);
      bar.style.animation = 'none'; void bar.offsetWidth; bar.style.animation = '';
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => { auto = false; tour.classList.remove('autoplay'); show(i); });
      t.addEventListener('keydown', (e) => {
        const k = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (k) { e.preventDefault(); auto = false; tour.classList.remove('autoplay'); show(idx + k, true); }
      });
      $('.tt-progress i', t).addEventListener('animationend', () => { if (auto) show(idx + 1); });
    });
    tour.addEventListener('mouseenter', () => tour.classList.add('paused'));
    tour.addEventListener('mouseleave', () => tour.classList.remove('paused'));
    tour.addEventListener('focusin', () => tour.classList.add('paused'));
    tour.addEventListener('focusout', () => tour.classList.remove('paused'));
    new IntersectionObserver(([e]) => {
      if (!auto) return;
      tour.classList.toggle('autoplay', e.isIntersecting);
    }, { threshold: .35 }).observe(tour);
    // warm the cache so switching is instant
    imgs.forEach((im) => { im.loading = 'eager'; });
    show(0);
  })();

  /* =====================================================================
     Results: the FalkorDB-graph switch and the bar tooltips.
     ===================================================================== */
  (() => {
    const sw = $('#graphSwitch'); if (!sw) return;
    const box = $('#ablate'), label = $('#swLabel');
    const bar = $('#barLore'), barV = $('#barLoreV');
    const figs = [
      { n: $('#abAvg'), on: 63.6, off: 24.2, pct: false, bar: true },
      { n: $('#abCust'), on: 54.2, off: 0.0, pct: false },
      { n: $('#abStale'), on: 0, off: 8.7, pct: true },
    ];
    const fmt = (f, v) => f.pct ? (v === 0 ? '0%' : v.toFixed(1) + '%') : v.toFixed(1);
    sw.addEventListener('click', () => {
      const on = sw.getAttribute('aria-checked') !== 'true';
      sw.setAttribute('aria-checked', String(on));
      label.textContent = on ? 'FalkorDB graph on' : 'FalkorDB graph off';
      box.classList.toggle('is-off', !on);
      figs.forEach((f) => {
        const from = parseFloat(f.n.textContent), to = on ? f.on : f.off;
        if (f.bar) { bar.style.setProperty('--v', to); barV.parentElement.style.setProperty('--v', to); barV.style.setProperty('--v', to); }
        const set = (v) => { f.n.textContent = fmt(f, v); if (f.bar) barV.textContent = fmt(f, v); };
        if (reduced) return set(to);
        const t0 = performance.now(), D = 900;
        const tick = (t) => {
          const k = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - k, 3);
          set(k < 1 ? from + (to - from) * e : to);
          if (k < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    });
    // one tooltip per bar, near the end of the hovered bar
    const tip = $('#barsTip'), fig = $('.bars');
    $$('.bar-row').forEach((row) => {
      const show = () => {
        tip.textContent = row.dataset.tip;
        const r = row.getBoundingClientRect(), f = fig.getBoundingClientRect(), b = $('.bar', row).getBoundingClientRect();
        tip.style.left = Math.min(b.right - f.left + 12, f.width - 300) + 'px';
        tip.style.top = (r.top - f.top - 30) + 'px';
        tip.classList.add('on');
      };
      row.addEventListener('mouseenter', show);
      row.addEventListener('mouseleave', () => tip.classList.remove('on'));
    });
  })();

  /* =====================================================================
     Why FalkorDB: the engine stack, the tail-latency plot, the comparison.
     ===================================================================== */
  (() => {
    // stack: point at a layer to read what Lore uses it for
    const out = $('#stackUse');
    if (out) {
      const idle = out.textContent;
      $$('#stack [data-use]').forEach((el) => {
        el.tabIndex = 0;
        const on = () => { $$('#stack [data-use]').forEach((x) => x.classList.toggle('is-on', x === el)); out.textContent = el.dataset.use; };
        el.addEventListener('mouseenter', on);
        el.addEventListener('focus', on);
      });
      $('#stack').addEventListener('mouseleave', () => { $$('#stack [data-use]').forEach((x) => x.classList.remove('is-on')); out.textContent = idle; });
    }
    // tail plot: log scale from 10 ms to 100 s, drawn when it scrolls into view
    const plot = $('#tailPlot');
    if (plot) {
      const x = (ms) => Math.max(0, Math.min(100, (Math.log10(ms) - 1) / 4 * 100));
      const label = (ms) => ms >= 1000 ? (ms / 1000).toLocaleString('en', { maximumFractionDigits: 1 }) + ' s' : ms + ' ms';
      const draw = () => $$('.tail-row', plot).forEach((row) => {
        const f = +row.dataset.f, o = +row.dataset.o;
        $('.tb-f', row).style.width = x(f) + '%';
        $('.tb-o', row).style.width = x(o) + '%';
        const vf = $('.tv-f', row), vo = $('.tv-o', row);
        vf.textContent = label(f); vo.textContent = 'Neo4j ' + label(o);
        vf.style.left = `calc(${x(f)}% + 8px)`;
        vo.style.left = x(o) > 70 ? `calc(${x(o)}% - ${vo.offsetWidth + 8}px)` : `calc(${x(o)}% + 8px)`;
        if (x(o) > 70) vo.style.color = '#0a1117';
      });
      new IntersectionObserver(([e], obs) => { if (e.isIntersecting) { draw(); obs.disconnect(); } }, { threshold: .4 }).observe(plot);
    }
    // comparison tabs
    const tabs = $$('.cmp-tab');
    const pick = (i, focus) => {
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.tabIndex = k === i ? 0 : -1; document.getElementById(t.getAttribute('aria-controls')).hidden = k !== i; });
      if (focus) tabs[i].focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => pick(i));
      t.addEventListener('keydown', (e) => {
        const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (k) { e.preventDefault(); pick((i + k + tabs.length) % tabs.length, true); }
      });
    });
  })();

  /* =====================================================================
     Why a graph: three reasons, each with its scene and its Cypher.
     ===================================================================== */
  (() => {
    const tabs = $$('.gw-tab'); if (!tabs.length) return;
    const svgs = $$('.gw-svg'), codes = $$('.cy-code');
    const show = (i, focus) => {
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.tabIndex = k === i ? 0 : -1; });
      svgs.forEach((s, k) => s.classList.toggle('is-on', k === i));
      codes.forEach((c, k) => c.classList.toggle('is-on', k === i));
      if (focus) tabs[i].focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => show(i));
      t.addEventListener('keydown', (e) => {
        const k = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (k) { e.preventDefault(); show((i + k + tabs.length) % tabs.length, true); }
      });
    });
    show(0);
  })();

  /* ---------------- dialogs ---------------- */
  $$('[data-open]').forEach((b) => b.addEventListener('click', () => { const d = document.getElementById(b.dataset.open); d && d.showModal(); }));
  $$('dialog').forEach((d) => {
    $$('[data-close]', d).forEach((b) => b.addEventListener('click', () => d.close()));
    d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  });
})();
