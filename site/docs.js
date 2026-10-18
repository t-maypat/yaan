/* Yaan docs: theme, copy buttons, and the table of contents that follows the reader. */
(() => {
  'use strict';
  const root = document.documentElement;

  const btn = document.getElementById('themeToggle');
  const sync = () => btn.setAttribute('aria-label', root.dataset.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  sync();
  btn.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('lore-theme', next); } catch (e) { /* storage blocked: lasts this visit */ }
    sync();
  });

  // a Copy button on every code block
  document.querySelectorAll('pre.code').forEach((pre) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'copy'; b.textContent = 'Copy';
    b.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pre.querySelector('code').innerText);
        b.textContent = 'Copied'; b.classList.add('done');
      } catch (e) {
        b.textContent = 'Select and copy';
      }
      setTimeout(() => { b.textContent = 'Copy'; b.classList.remove('done'); }, 1600);
    });
    pre.appendChild(b);
  });

  // the table of contents marks the section in view
  const links = [...document.querySelectorAll('.toc a')];
  const byId = new Map(links.map((a) => [a.getAttribute('href').slice(1), a]));
  const obs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.classList.remove('is-on'));
      const a = byId.get(e.target.id);
      if (a) { a.classList.add('is-on'); a.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    });
  }, { rootMargin: '-30% 0px -60% 0px' });
  document.querySelectorAll('.doc section[id]').forEach((s) => obs.observe(s));

  // links still carrying a {PLACEHOLDER} say so instead of breaking
  document.querySelectorAll('a[href*="{"]').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    a.title = 'This link goes live at launch.';
  }));
})();
