/* Yaan landing page. No framework, no build step.
   Everything shown in the hero console comes from Yaan's real tool output on HERB's synthetic
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
    try { localStorage.setItem('yaan-theme', next); } catch (e) { /* storage blocked: theme lasts this visit */ }
    syncThemeLabel();
    graph && graph.refreshColors();
    shade && shade.refresh();
  });

  /* ---------------- nav ---------------- */
  const nav = $('#nav');
  const onScroll = () => nav.classList.toggle('is-scrolled', scrollY > 12);
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  // the bar turns dark over the dark bands (the metrics side panel and Why FalkorDB)
  const nights = [document.getElementById('falkordb'), document.querySelector('.mx-side')].filter(Boolean);
  if (nights.length) {
    const darkCheck = () => nav.classList.toggle('on-dark', nights.some((el) => { const r = el.getBoundingClientRect(); return r.top <= 40 && r.bottom > 40 && r.right > innerWidth - 40; }));
    addEventListener('scroll', darkCheck, { passive: true }); darkCheck();
  }
  // Hero depth (styles.css): CSS scroll-driven animations where supported; elsewhere the same motion
  // from here, once a frame, written straight onto the moving layers.
  if (!reduced && !(window.CSS && CSS.supports('animation-timeline: scroll()'))) {
    const copy = $('.hero-copy'), fade = $('.hero-fade');
    let queued = false, last = -1;
    const depth = () => {
      queued = false;
      const p = Math.min(Math.max(scrollY, 0), 400);
      if (p === last) return;
      last = p;
      // queried each time: the shade canvas is added further down
      $$('.hero-graph, .hero-shade').forEach((el) => { el.style.translate = `0 ${p * .5}px`; });
      copy.style.translate = fade.style.translate = `0 ${p * .3}px`;
      copy.style.opacity = 1 - p / 460;
    };
    addEventListener('scroll', () => { if (!queued) { queued = true; requestAnimationFrame(depth); } }, { passive: true });
    depth();
  }

  const navLinks = $$('.nav-links a');
  const secObs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      navLinks.forEach((a) => a.classList.toggle('is-current', a.getAttribute('href') === '#' + e.target.id));
    });
  }, { rootMargin: '-45% 0px -50% 0px' });
  // sections without a nav link clear the highlight
  ['problem', 'how', 'selfhost', 'features', 'results', 'falkordb', 'works'].forEach((id) => { const el = document.getElementById(id); el && secObs.observe(el); });

  /* =====================================================================
     Hero shade: slow graphite smoke on the far sides only (a WebGL
     fragment shader at half resolution). Quiet by design: it never
     reaches the headline, and with reduced motion it is one still frame.
     ===================================================================== */
  const shade = (() => {
    const hero = $('.hero');
    const cv = document.createElement('canvas');
    cv.className = 'hero-shade'; cv.setAttribute('aria-hidden', 'true');
    hero.prepend(cv);
    const gl = cv.getContext('webgl', { premultipliedAlpha: true, antialias: false, alpha: true });
    if (!gl) { cv.remove(); return null; }
    const VS = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
    const FS = `precision mediump float;
      uniform vec2 r; uniform float t; uniform vec3 c; uniform float a;
      float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
        return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
      float fbm(vec2 p){float v=0.,s=.5;for(int i=0;i<5;i++){v+=s*n(p);p=p*2.03+vec2(1.7,9.2);s*=.5;}return v;}
      void main(){
        vec2 uv=gl_FragCoord.xy/r; vec2 p=gl_FragCoord.xy*.0045;
        vec2 q=vec2(fbm(p+vec2(0.,t*.035)),fbm(p+vec2(5.2,1.3)-t*.025));
        float f=fbm(p+2.4*q+vec2(t*.012,0.));
        float side=smoothstep(.3,.02,uv.x)+smoothstep(.7,.98,uv.x);
        float vert=smoothstep(0.,.25,uv.y)*smoothstep(1.,.86,uv.y);
        float lift=mix(1.,1.4,smoothstep(.4,.75,uv.y));
        float al=min(.9,a*side*vert*lift*smoothstep(.34,.8,f));
        gl_FragColor=vec4(c*al,al);
      }`;
    const sh = (type, src) => { const o = gl.createShader(type); gl.shaderSource(o, src); gl.compileShader(o); return o; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { cv.remove(); return null; }
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const U = { r: gl.getUniformLocation(prog, 'r'), t: gl.getUniformLocation(prog, 't'), c: gl.getUniformLocation(prog, 'c'), a: gl.getUniformLocation(prog, 'a') };
    const tint = () => {
      const rgb = cssVar('--shade').split(',').map((x) => +x / 255);
      gl.uniform3f(U.c, rgb[0], rgb[1], rgb[2]);
      gl.uniform1f(U.a, parseFloat(cssVar('--shade-a')) || .3);
    };
    const size = () => {
      const w = Math.max(1, Math.round(cv.clientWidth * .5)), h = Math.max(1, Math.round(cv.clientHeight * .5));
      cv.width = w; cv.height = h; gl.viewport(0, 0, w, h); gl.uniform2f(U.r, w, h);
    };
    const frame = (ms) => { gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.uniform1f(U.t, ms / 1000 + 40); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); };
    let raf = 0, run = false, last = 0;
    const loop = (ms) => { if (ms - last > 33) { frame(ms); last = ms; } raf = run ? requestAnimationFrame(loop) : 0; };
    const start = () => { if (run) return; if (reduced) { frame(0); return; } run = true; raf = requestAnimationFrame(loop); };
    const stop = () => { run = false; cancelAnimationFrame(raf); };
    tint(); size(); frame(0);
    requestAnimationFrame(() => cv.classList.add('on'));
    new ResizeObserver(() => { size(); if (!run) frame(0); }).observe(cv);
    new IntersectionObserver(([e]) => (e.isIntersecting ? start() : stop())).observe(hero);
    return { refresh() { tint(); if (!run) frame(0); } };
  })();

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
      const cw = Math.round(W * dpr), ch = Math.round(H * dpr);
      if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const c = consoleEl.getBoundingClientRect();
      const left = c.left - r.left, right = r.right - c.right;
      flankL = Math.max(0, left - 24); flankR = Math.max(0, right - 24);
      cTop = c.top - r.top + 80;
      compact = flankL < 150;
      // beside the console only: from its top bar down to its last row. Layout sets targets (tx, ty);
      // frame() eases each node toward them, so the console growing or shrinking never snaps the graph.
      const top = cTop + 14, bottom = Math.min(H - 30, cTop + c.height + 10);
      NODES.forEach((n) => {
        if (compact) {
          // narrow screens: a quiet band of nodes above the console, no labels
          n.tx = n.s === 'L' ? W * (.04 + n.u * .4) : W * (.56 + n.u * .4);
          n.ty = 40 + n.v * Math.max(120, cTop - 120);
        } else {
          n.ty = top + n.v * (bottom - top);
          const fw = n.s === 'L' ? flankL : flankR;
          n.fw = fw;
          n.tx = n.s === 'L' ? 22 + n.u * (fw - 30) : W - 22 - n.u * (fw - 30);
        }
        if (n.x == null) { n.x = n.tx; n.y = n.ty; n.ph = (n.tx * 13.37 + n.ty * 7.1) % 6.28; n.li = 0; n.ci = 0; }
      });
      const count = compact ? 18 : 46;
      let seed = 7;
      const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
      for (let i = 0; i < count; i++) {
        const side = rnd() < .5 ? 'L' : 'R';
        const fw = compact ? W * .45 : (side === 'L' ? flankL : flankR) + 20;
        const x = side === 'L' ? rnd() * fw : W - rnd() * fw;
        const y = compact ? 20 + rnd() * Math.max(140, cTop - 60) : cTop - 10 + rnd() * (H - cTop - 40);
        const r = 1.2 + rnd() * 1.8, ph = rnd() * 6.28;
        // same seed, same order: dot i keeps its identity across layouts and glides to its new spot
        if (dust[i]) { dust[i].tx = x; dust[i].ty = y; } else dust.push({ x, y, tx: x, ty: y, r, ph });
      }
      dust.length = count;
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

    // per-frame easing toward targets: positions over ~0.35 s, highlight states over ~0.25 s
    let lastT = 0;
    const ease = (v, to, k) => v + (to - v) * k;
    function step(t) {
      const dt = lastT ? Math.min(t - lastT, 200) : 1e3;
      lastT = t;
      const kp = reduced ? 1 : 1 - Math.exp(-dt / 350), ks = reduced ? 1 : 1 - Math.exp(-dt / 250);
      NODES.forEach((n) => {
        n.x = ease(n.x, n.tx, kp); n.y = ease(n.y, n.ty, kp);
        n.li = ease(n.li, lit.has(n.id) ? 1 : 0, ks); n.ci = ease(n.ci, cited.has(n.id) ? 1 : 0, ks);
      });
      dust.forEach((d) => { d.x = ease(d.x, d.tx, kp); d.y = ease(d.y, d.ty, kp); });
      EDGES.forEach((e) => { e.li = ease(e.li || 0, litE.has(e.key) ? 1 : 0, ks); });
    }

    function label(n, x, y, strong, alpha) {
      ctx.font = (strong ? '600 ' : '500 ') + (n.t === 'message' || n.t === 'pr' && n.label.startsWith('pr:') ? '10.5px "JetBrains Mono", monospace' : '11.5px "Schibsted Grotesk", sans-serif');
      if (n.t === 'message') ctx.font = (strong ? '600 ' : '400 ') + '10px "JetBrains Mono", monospace';
      ctx.fillStyle = strong ? colors.ink : colors.muted;
      ctx.globalAlpha = alpha;
      let text = n.label;
      const maxW = Math.max(90, (n.fw || 0) * .95);
      while (ctx.measureText(text).width > maxW && text.length > 6) text = text.slice(0, -2);
      if (text !== n.label) text = text.trimEnd() + '…';
      const tw = ctx.measureText(text).width;
      ctx.fillText(text, Math.max(6, Math.min(x - tw / 2, W - tw - 6)), y);
    }

    function frame(t) {
      step(t);
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
        const on = e.li;
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, dx = x2 - x1, dy = y2 - y1;
        const len = Math.hypot(dx, dy) || 1, bend = Math.min(60, len * .12);
        const cx = mx - dy / len * bend, cy = my + dx / len * bend;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(cx, cy, x2, y2);
        // the quiet edge and the lit (dashed) edge cross-fade
        if (on < .99) {
          ctx.strokeStyle = colors.rule; ctx.globalAlpha = (compact ? .45 : .75) * (1 - on); ctx.lineWidth = 1;
          ctx.setLineDash([]); ctx.stroke();
        }
        if (on > .01) {
          ctx.strokeStyle = colors.petrol; ctx.globalAlpha = .85 * on; ctx.lineWidth = 1.4;
          ctx.setLineDash([5, 5]); ctx.lineDashOffset = reduced ? 0 : -t * .03; ctx.stroke();
        }
      });
      ctx.setLineDash([]); ctx.globalAlpha = 1;

      // nodes
      NODES.forEach((n) => {
        const [x, y] = P.get(n.id);
        const c = cited.get(n.id), li = n.li, ci = n.ci, hot = Math.max(li, ci);
        const r = n.t === 'person' || n.t === 'company' ? 5.5 : 6;
        if (c != null && !reduced) {
          const k = Math.min(1, (t - c) / 900);
          if (k < 1) { ctx.beginPath(); ctx.arc(x, y, 8 + k * 22, 0, 6.28); ctx.strokeStyle = colors.mark; ctx.globalAlpha = 1 - k; ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 1; }
        }
        // three layers cross-fade: quiet, lit (petrol), cited (highlighter)
        const quiet = (compact ? .35 : .55) * (1 - hot), litA = li * (1 - ci);
        if (quiet > .01) { ctx.globalAlpha = quiet; shape(n, x, y, r, colors.faint, colors.faint, 1.4); }
        if (litA > .01) { ctx.globalAlpha = litA; shape(n, x, y, r, colors.petrol, colors.petrol, 1.4); }
        if (ci > .01) { ctx.globalAlpha = ci; shape(n, x, y, r + 1.5 * ci, colors.mark, colors.ink, 1.4); }
        if (!compact && n.label) {
          // the quiet label and the bold one cross-fade (both drawn only mid-transition)
          if (hot < .99) label(n, x, y + r + 14, false, .7 * (1 - hot));
          if (hot > .01) label(n, x, y + r + 14, true, hot);
        }
        ctx.globalAlpha = 1;
      });
    }

    function loop(t) { frame(t); raf = running ? requestAnimationFrame(loop) : 0; }
    function start() { if (running) return; running = true; if (reduced) { frame(performance.now()); running = false; return; } raf = requestAnimationFrame(loop); }
    function stop() { running = false; cancelAnimationFrame(raf); }
    function redrawIfIdle() { if (!running) frame(performance.now()); }

    refreshColors(); layout();
    requestAnimationFrame(() => canvas.classList.add('on'));  // fade in, never pop in
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
    const yaanText = $('#yaanText'), yaanEv = $('#yaanEv'), yaanNote = $('#yaanNote');
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
        yaanText.textContent = 'There is no report of this bug yet on ' + date + '.';
        yaanEv.innerHTML = '';
        yaanNote.textContent = 'No decision in force: the first report comes on 6 Feb.';
      } else if (state === 'rep') {
        yaanText.textContent = 'No. Bob Davis reported it on 6 Feb, and it is still open on ' + date + '.';
        yaanEv.innerHTML = quote('rep');
        yaanNote.textContent = 'In force on ' + date + ': reported, from 6 Feb until 24 Feb.';
      } else if (state === 'res') {
        yaanText.textContent = 'Yes. Hannah Johnson marked it resolved on 24 Feb with PR #53.';
        yaanEv.innerHTML = quote('res');
        yaanNote.textContent = 'In force on ' + date + ': resolved, from 24 Feb until 26 Feb.';
      } else {
        yaanText.innerHTML = 'Resolved on 24 Feb with PR #53, then <strong>reopened on 26 Feb</strong>.';
        yaanEv.innerHTML = quote('res') + quote('reo');
        yaanNote.textContent = 'In force on ' + date + ': reopened, since 26 Feb. The resolved state was closed, not deleted.';
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
     Results: grouped bars, one tab per metric. Yaan is the one coloured
     series; everything it's compared with is a pale bar beside it.
     ===================================================================== */
  (() => {
    const bars = $('#mxBars'); if (!bars) return;
    const ICON = {
      yaan: '<svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M7 6 L16 17 L25 6" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M16 17 V27" stroke="currentColor" stroke-width="3.8" stroke-linecap="round"/><circle cx="16" cy="17" r="3.4" fill="currentColor"/></svg>',
      search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5"/></svg>',
      rag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M9 11h6M9 15h6"/></svg>',
      paper: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 5h7a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H4z"/><path d="M20 5h-7a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h7z"/></svg>',
    };
    const S = {
      yaan: { name: 'Yaan', icon: 'yaan' },
      search: { name: 'Yaan without the FalkorDB graph', icon: 'search' },
      rag: { name: 'Plain RAG', icon: 'rag' },
      paper: { name: 'Salesforce AI Research agent (GPT-4o)', icon: 'paper' },
    };
    const TABS = [
      {
        ylab: 'Judged score (0–100)', max: 80, ticks: [0, 20, 40, 60, 80], unit: '', series: ['yaan', 'search', 'rag'],
        groups: [['Content', [64.0, 17.7, 12.6]], ['People', [65.2, 37.6, 13.3]], ['Customer', [54.2, 0.0, 0.0]], ['Artifact', [71.3, 41.5, 12.6]]],
        cap: 'Held-out products, one run per system, all on Claude Haiku 5.5 with the same judge prompts and search. “Without the FalkorDB graph” keeps vector and full-text search and loses every graph query.',
      },
      {
        ylab: 'Answers right (%)', max: 100, ticks: [0, 25, 50, 75, 100], unit: '%', series: ['yaan', 'search', 'rag'],
        groups: [['All drift questions', [95.8, 70.8, 54.2]], ['Current status', [100, 54.5, 54.5]], ['As of a date', [100, 80, 60]]],
        cap: 'Our drift test: 24 hand-labeled questions on Salesforce AI Research’s enterprise data about status now and on a past date, ground truth read from the raw messages, all systems on Claude Haiku 5.5. Stale answers: Yaan 0%, search only 8.7%, plain RAG 6.7%.',
      },
      {
        ylab: 'Score / share (0–100)', max: 100, ticks: [0, 25, 50, 75, 100], series: ['yaan', 'paper'],
        groups: [['Average score', [63.6, 43.4], '']],
        cap: 'Salesforce AI Research’s best product-specific agent is GPT-4o with ReAct, as published in their HERB paper (EMNLP 2025). Yaan runs Claude Haiku 5.5, scored with the paper’s own judge prompts. Different model, judge and setup: shown for context, not as a head-to-head.',
      },
    ];
    const fmt = (v, unit) => (Number.isInteger(v) && unit === '%' ? v : v.toFixed(1)) + unit;
    const draw = (i) => {
      const T = TABS[i];
      $('#mxYlab').textContent = T.ylab;
      $('#mxLegend').innerHTML = T.series.map((k) => `<span class="lg-${k}">${ICON[S[k].icon]}${S[k].name}</span>`).join('');
      $('#mxCap').textContent = T.cap;
      const grid = T.ticks.map((t) => `<div class="mx-grid" style="bottom:${t / T.max * 100}%"><span>${t}${T.unit === '%' ? '%' : ''}</span></div>`).join('');
      const groups = T.groups.map(([label, vals, unit], g) => {
        const u = unit ?? T.unit;
        const cols = vals.map((v, k) => {
          const key = T.series[k], h = v / T.max * 100;
          return `<div class="mx-bar ${key === 'yaan' ? 'is-yaan' : ''} ${h < 9 ? 'short' : ''}" style="--h:${h};--d:${(g * vals.length + k) * 0.03}s" title="${S[key].name}: ${fmt(v, u)}">${ICON[S[key].icon]}<span class="mx-v">${fmt(v, u)}</span></div>`;
        }).join('');
        return `<div class="mx-group">${cols}<span class="mx-x">${label}</span></div>`;
      }).join('');
      bars.innerHTML = grid + groups;
    };
    const tabs = $$('.mx-tab');
    const pick = (i, focus) => {
      tabs.forEach((t, k) => { t.setAttribute('aria-selected', String(k === i)); t.tabIndex = k === i ? 0 : -1; });
      draw(i);
      if (focus) tabs[i].focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => pick(i));
      t.addEventListener('keydown', (e) => {
        const k = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (k) { e.preventDefault(); pick((i + k + tabs.length) % tabs.length, true); }
      });
    });
    // the bars grow when the chart first scrolls into view
    tabs.forEach((t, k) => { t.tabIndex = k === 0 ? 0 : -1; });
    new IntersectionObserver(([e], obs) => { if (e.isIntersecting) { draw(0); obs.disconnect(); } }, { threshold: .3 }).observe($('#mxPlot'));
  })();

  /* =====================================================================
     Why FalkorDB: the engine stack, the tail-latency plot, the comparison.
     ===================================================================== */
  (() => {
    // stack: point at a layer to read what Yaan uses it for
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
  })();

  /* =====================================================================
     How it works, live parts: the pipeline ticker (Connect) and the
     decision windows (Remember). Each runs only while its scene shows.
     ===================================================================== */
  (() => {
    const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight && getComputedStyle(el.closest('.scene')).opacity > .5; };

    // Connect: one step in focus, the one before fading out above, the next fading in below
    $$('.ticker').forEach((tk) => {
      const list = $('.tk-list', tk), items = $$('.tk-i', tk);
      let i = 0;
      const show = () => {
        items.forEach((it, k) => { it.classList.toggle('on', k === i); it.classList.toggle('near', Math.abs(k - i) === 1); });
        list.style.transform = `translateY(${56 - i * 56}px)`;  // the focused row sits in the middle of three
      };
      show();
      setInterval(() => { if (!visible(tk) || document.hidden) return; i = (i + 1) % items.length; show(); }, reduced ? 2600 : 1500);
    });

    // Remember: three facts arrive in turn; each new one closes the window of the one before
    const STEPS = [
      { x: .152, k: 0, cls: 'st-rep', state: 'reported', who: 'Bob Davis', role: 'Engineering Lead', init: 'BD', when: '6 Feb 2027', quote: 'This is impacting customer CUST-0011.', id: 'msg:20270206-1-47b0c', kept: null },
      { x: .697, k: 1, cls: 'st-res', state: 'resolved', who: 'Hannah Johnson', role: 'assignee', init: 'HJ', when: '24 Feb 2027', quote: 'the bug titled ‘Inefficient Caching Strategy’ has been resolved.', id: 'msg:20270224-0-ca81d', kept: '“reported” closed on 24 Feb, kept in the graph' },
      { x: .758, k: 2, cls: 'st-reo', state: 'reopened', who: 'Bob Davis', role: 'Engineering Lead', init: 'BD', when: '26 Feb 2027', quote: 'the customer has reopened the bug titled ‘Inefficient Caching Strategy’', id: 'msg:20270226-0-1bc7a', kept: '“resolved” closed on 26 Feb, kept in the graph' },
    ];
    const ENDS = [69.7, 75.8, 100];
    $$('.scene-remember').forEach((sc) => {
      const bars = $$('.rm-bar', sc), rows = $$('.rm-row', sc), head = $('.rm-head', sc), card = $('.rm-card', sc);
      let step = 0;
      const render = () => {
        const S = STEPS[Math.min(step, 2)];
        bars.forEach((b, k) => {
          const a = [15.2, 69.7, 75.8][k];
          if (k > S.k) { b.style.width = '0'; b.className = b.className.replace(/ (open|closed)/g, ''); return; }
          const closed = k < S.k;
          b.style.width = ((closed ? ENDS[k] : 100) - a) + '%';
          b.classList.toggle('closed', closed); b.classList.toggle('open', !closed);
        });
        rows.forEach((r, k) => r.classList.toggle('on', k === S.k));
        head.style.setProperty('--x', S.x);
        $('b', head).textContent = S.when.replace(' 2027', '');
        card.innerHTML = `<div class="rm-who"><span class="rm-av">${S.init}</span><span><b>${S.who}</b> <span class="rm-role">${S.role}, ${S.when}</span></span><span class="rm-state ${S.cls}">in force: ${S.state}</span></div>
          <blockquote class="q"><p><mark>${S.quote}</mark></p><footer><code>${S.id}</code></footer></blockquote>
          ${S.kept ? `<p class="rm-kept">${S.kept}</p>` : '<p class="rm-kept">the first fact about this bug</p>'}`;
        card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
      };
      render();
      setInterval(() => { if (!visible(sc) || document.hidden) return; step = (step + 1) % 4; if (step < 3) render(); }, reduced ? 3600 : 2600);
    });
  })();

  /* ---------------- Act: switch between Claude Code, Codex and Cursor ---------------- */
  document.addEventListener('click', (ev) => {
    const tab = ev.target.closest('.cw-tab'); if (!tab) return;
    const cw = tab.closest('.cw'), c = tab.dataset.c;
    cw.dataset.client = c;
    $$('.cw-tab', cw).forEach((t) => t.setAttribute('aria-selected', String(t === tab)));
    $$('.cw-body', cw).forEach((b) => { b.hidden = b.dataset.c !== c; });
  });

  /* ---------------- Self-hosted: one point in focus, the one before and after it faded ----------------
     The list wraps, so there is always a point above and below. The rail's fill sets the pace (its
     animationend advances); hovering, focusing or leaving the screen pauses it. The point in focus
     lights the part of the drawing it is about. */
  (() => {
    const box = $('#pts'); if (!box) return;
    const list = $('.pt-list', box), items = $$('.pt', box), dots = $$('.pt-dot', box);
    const parts = $$('.perim [data-part]');
    const n = items.length, GAP = 26, NEAR = .72;  // NEAR: a neighbour's title scale (styles.css .pt.is-near h3)
    let cur = 0, hover = false, seen = false;
    const layout = () => {
      const H = list.clientHeight, hc = items[cur].offsetHeight, top = (H - hc) / 2;
      items.forEach((it, i) => {
        const d = ((i - cur) % n + n) % n;  // 0 in focus, 1 next, n - 1 previous
        const off = d === 0 ? 0 : d === 1 ? 1 : d === n - 1 ? -1 : d <= n / 2 ? 2 : -2;
        const th = it.firstElementChild.offsetHeight * NEAR;  // a neighbour shows only its scaled title
        const y = off === 0 ? top : off === 1 ? top + hc + GAP : off === -1 ? top - GAP - th : off === 2 ? H + 40 : -th - 160;
        it.style.setProperty('--y', `${y}px`);
        it.classList.toggle('is-on', off === 0);
        it.classList.toggle('is-near', Math.abs(off) === 1);
      });
    };
    const go = (i) => {
      cur = (i + n) % n;
      layout();
      dots.forEach((d, k) => { d.classList.remove('on'); d.setAttribute('aria-pressed', String(k === cur)); });
      void dots[cur].offsetWidth;  // restart the fill
      dots[cur].classList.add('on');
      parts.forEach((p) => p.classList.toggle('is-hot', p.dataset.part === items[cur].dataset.part));
    };
    const pace = () => box.classList.toggle('paused', hover || !seen);
    dots.forEach((d, i) => {
      d.addEventListener('click', () => go(i));
      d.firstElementChild.addEventListener('animationend', () => go(cur + 1));
    });
    items.forEach((it, i) => it.addEventListener('click', () => { if (i !== cur) go(i); }));
    const zone = box.closest('.sh-body');
    zone.addEventListener('pointerenter', () => { hover = true; pace(); });
    zone.addEventListener('pointerleave', () => { hover = false; pace(); });
    zone.addEventListener('focusin', () => { hover = true; pace(); });
    zone.addEventListener('focusout', () => { hover = false; pace(); });
    new IntersectionObserver(([e]) => { seen = e.isIntersecting; pace(); }, { threshold: .4 }).observe(box);
    new ResizeObserver(layout).observe(list);
    document.fonts && document.fonts.ready.then(layout);
    pace(); go(0);
    requestAnimationFrame(() => list.classList.add('ready'));  // no slide-in on the first layout
  })();

  /* ---------------- links filled at launch ----------------
     index.html carries {APP_URL}, {VIDEO_URL}, {REPO_URL} and {BLOG_URL} until launch (site/README.md).
     Locally the app is the API's built UI on :8000; elsewhere an unfilled link says so instead of 404ing. */
  (() => {
    const local = ['localhost', '127.0.0.1'].includes(location.hostname);
    let note;
    $$('a[href*="{"]').forEach((a) => {
      const href = a.getAttribute('href');
      if (local && href.startsWith('{APP_URL}')) { a.href = 'http://localhost:8000/'; return; }
      a.addEventListener('click', (e) => {
        e.preventDefault();
        if (!note) { note = el('div', 'link-note'); note.setAttribute('role', 'status'); document.body.appendChild(note); }
        note.textContent = 'This link goes live at launch.';
        note.classList.add('on');
        clearTimeout(note.t); note.t = setTimeout(() => note.classList.remove('on'), 2200);
      });
    });
  })();

  /* ---------------- dialogs ---------------- */
  $$('[data-open]').forEach((b) => b.addEventListener('click', () => { const d = document.getElementById(b.dataset.open); d && d.showModal(); }));
  $$('dialog').forEach((d) => {
    $$('[data-close]', d).forEach((b) => b.addEventListener('click', () => d.close()));
    d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  });
})();
