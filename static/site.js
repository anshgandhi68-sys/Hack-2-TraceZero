// TraceZero shared chrome: splash iris, fixed nav + mobile menu, footer,
// reveal-on-scroll, live status, toasts.
(function () {
  const REPO = 'https://github.com/anshgandhi68-sys/Hack-2-TraceZero';
  const LINKS = [
    ['/', 'Overview'],
    ['/how-it-works', 'How it works'],
    ['/store', 'Live store'],
    ['/warroom', 'War Room'],
    ['/about', 'FAQ'],
  ];
  const path = location.pathname.replace(/\/$/, '') || '/';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const body = document.body;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  window.tzEsc = esc;

  const CHEV = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
  window.tzChev = CHEV;
  // Original TraceZero mark: four swept blades around a core (a "trace" pinwheel)
  const MARK = `<svg class="tz-mark" width="32" height="32" viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">
      ${[0, 90, 180, 270].map(r => `<path transform="rotate(${r} 16 16)" d="M16 2.5c3.6 3.4 4.6 7.1 2.4 11.1-.6.9-1.5 1.4-2.4 1.4s-1.8-.5-2.2-1.2c.9-3.6.7-7.3-.6-10.6.9-.3 1.9-.5 2.8-.7z"/>`).join('')}
      <circle cx="16" cy="16" r="2.1"/></svg>`;

  // ------------------------------------------------------------------ nav
  function renderNav() {
    const el = document.getElementById('site-nav');
    if (!el) return;
    const isWar = path === '/warroom';
    const cta = isWar ? ['/store', 'Break the store'] : ['/warroom', 'Open War Room'];
    el.innerHTML = `
    <nav class="tz-nav fixed top-0 left-0 right-0 z-50 px-4 sm:px-6 lg:px-10 py-4 flex items-center gap-4 text-white" aria-label="Main">
      <a href="/" class="flex items-center gap-3 shrink-0" aria-label="TraceZero home">
        ${MARK}<span class="tz-word hidden sm:block text-[13px] font-semibold tracking-[.2em] uppercase transition-colors duration-500">TraceZero</span>
      </a>
      <span id="navStatus" class="hidden xl:flex items-center gap-2 ml-2 px-2.5 py-1 rounded-md bg-black/60 backdrop-blur border border-white/10 text-[10.5px] text-white/70"><span class="w-1.5 h-1.5 rounded-full bg-white/40"></span>connecting</span>
      <div class="tz-pill hidden lg:flex absolute left-1/2 -translate-x-1/2 items-center gap-1 bg-white rounded-lg p-1 shadow-[0_8px_30px_-10px_rgba(0,0,0,.45)]">
        ${LINKS.map(([href, label]) => `<a href="${href}" class="px-3.5 py-2 rounded-md text-[13px] font-medium ${path === href ? 'bg-black text-white' : 'text-black/70 hover:text-black'}" ${path === href ? 'aria-current="page"' : ''}>${label}</a>`).join('')}
      </div>
      <div class="ml-auto flex items-center gap-2">
        <a href="${REPO}" target="_blank" rel="noopener" class="hidden lg:grid w-11 h-11 rounded-lg bg-black text-white place-items-center border border-white/10 btn" aria-label="Source code on GitHub"><i data-lucide="github" class="w-4 h-4"></i></a>
        <a href="${cta[0]}" class="hidden lg:inline-flex btn items-center gap-3 bg-black text-white rounded-lg pl-1.5 pr-4 py-1.5 text-[13px] font-medium group">
          <span class="w-9 h-9 rounded-md bg-white/10 grid place-items-center arrow-ico">${CHEV}</span>${cta[1]}</a>
        <button class="tz-burger lg:hidden relative w-10 h-10 rounded-lg bg-white/10 backdrop-blur border border-white/10 grid place-items-center text-white btn" aria-label="Open menu" aria-expanded="false" aria-controls="tzMenu">
          <i data-lucide="menu" class="i-open w-5 h-5"></i><i data-lucide="x" class="i-close w-5 h-5 opacity-0 rotate-90 scale-50"></i>
        </button>
      </div>
    </nav>
    <div id="tzMenu" class="tz-menu fixed inset-0 z-40 bg-black/95 opacity-0 invisible lg:hidden flex flex-col px-5 pt-24 pb-8" aria-hidden="true">
      <div class="flex-1 flex flex-col gap-1">
        ${LINKS.map(([href, label], i) => `<a href="${href}" class="tz-mlink opacity-0 translate-y-4 flex items-center justify-between rounded-xl px-4 py-4 text-2xl ${path === href ? 'bg-white/10 text-white' : 'text-white/60'}" data-i="${i}">${label}${path === href ? CHEV : ''}</a>`).join('')}
      </div>
      <a href="${cta[0]}" class="tz-mlink opacity-0 translate-y-4 btn w-full bg-white text-black rounded-xl py-4 text-center font-semibold" data-i="${LINKS.length}">${cta[1]}</a>
      <p class="tz-mlink opacity-0 text-center text-xs text-white/30 mt-4" data-i="${LINKS.length + 1}">Autonomous incident response for production code.</p>
    </div>`;
    if (body.dataset.navOverlay !== 'true') {
      const sp = document.createElement('div'); sp.style.height = '76px'; el.after(sp);
    }
    setupMenu();
    setupNavTheme();
  }

  function setupMenu() {
    const btn = document.querySelector('.tz-burger'), menu = document.getElementById('tzMenu');
    if (!btn) return;
    const links = [...menu.querySelectorAll('.tz-mlink')];
    let open = false;
    const set = (v) => {
      open = v;
      btn.setAttribute('aria-expanded', String(v));
      btn.setAttribute('aria-label', v ? 'Close menu' : 'Open menu');
      menu.setAttribute('aria-hidden', String(!v));
      menu.classList.toggle('opacity-0', !v); menu.classList.toggle('invisible', !v);
      document.documentElement.style.overflow = v ? 'hidden' : '';
      const [o, c] = [btn.querySelector('.i-open'), btn.querySelector('.i-close')];
      o.style.opacity = v ? 0 : 1; o.style.transform = v ? 'rotate(-90deg) scale(.5)' : '';
      c.style.opacity = v ? 1 : 0; c.style.transform = v ? 'none' : 'rotate(90deg) scale(.5)';
      links.forEach((a, i) => {
        a.style.transitionDelay = v ? `${150 + i * 70}ms` : `${(links.length - i) * 30}ms`;
        a.classList.toggle('opacity-0', !v); a.classList.toggle('translate-y-4', !v);
      });
    };
    btn.onclick = () => set(!open);
    links.forEach(a => a.addEventListener('click', () => set(false)));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && open) set(false); });
  }

  // Flip the mark to black while the nav sits over a pale section
  function setupNavTheme() {
    const nav = document.querySelector('.tz-nav');
    const update = () => {
      const y = 36;
      const light = [...document.querySelectorAll('[data-theme="light"]')].some(s => {
        const r = s.getBoundingClientRect(); return r.top <= y && r.bottom >= y;
      });
      nav.classList.toggle('is-dark', light);
    };
    addEventListener('scroll', update, { passive: true });
    addEventListener('resize', update);
    update();
  }

  // ------------------------------------------------------------------ footer
  function renderFooter() {
    const el = document.getElementById('site-footer');
    if (!el) return;
    el.innerHTML = `
    <footer data-theme="light" class="relative z-10 bg-pale text-black">
      <div class="px-4 sm:px-6 lg:px-10 pt-16 pb-8">
        <div class="grid gap-10 md:grid-cols-[1.4fr_1fr_1fr]">
          <div class="max-w-sm">
            <div class="flex items-center gap-3 text-black">${MARK}<span class="text-[13px] font-semibold tracking-[.2em] uppercase">TraceZero</span></div>
            <p class="text-sm text-black/60 mt-5 leading-relaxed">Shield users in milliseconds. Prove the fix on real traffic. Vaccinate the twins. Ship with a human in the loop.</p>
            <a href="/warroom" class="cta mt-6"><span class="chip">${CHEV}</span>Open the War Room</a>
          </div>
          <div>
            <div class="text-[11px] tracking-[.25em] uppercase text-black/40 mb-4">Product</div>
            <ul class="space-y-2.5 text-sm text-black/70">
              <li><a class="hover:text-black" href="/warroom">War Room</a></li>
              <li><a class="hover:text-black" href="/store">Live store demo</a></li>
              <li><a class="hover:text-black" href="/how-it-works">How it works</a></li>
              <li><a class="hover:text-black" href="/how-it-works#safety">Safety model</a></li>
            </ul>
          </div>
          <div>
            <div class="text-[11px] tracking-[.25em] uppercase text-black/40 mb-4">Project</div>
            <ul class="space-y-2.5 text-sm text-black/70">
              <li><a class="hover:text-black" href="/about">About & FAQ</a></li>
              <li><a class="hover:text-black" href="${REPO}" target="_blank" rel="noopener">GitHub</a></li>
              <li><a class="hover:text-black" href="/docs" target="_blank">API docs</a></li>
            </ul>
          </div>
        </div>
        <div class="mt-14 font-bubbledot text-[16vw] leading-[.8] tracking-tight text-black select-none" aria-hidden="true">TRACEZERO</div>
        <div class="mt-6 flex flex-wrap gap-3 justify-between text-[11px] text-black/45">
          <span>© ${new Date().getFullYear()} TraceZero · built at a hackathon in 4 hours</span>
          <span>NovaMart is a fictional demo store · live numbers come from this server</span>
        </div>
      </div>
    </footer>`;
  }

  // ------------------------------------------------------------------ live status
  async function pollStatus() {
    const pill = document.getElementById('navStatus');
    try {
      const s = await fetch('/api/state').then(r => r.json());
      window.dispatchEvent(new CustomEvent('tz:state', { detail: s }));
      if (!pill) return;
      const open = s.incidents.filter(i => i.status !== 'deployed');
      const raw = s.shields.some(sh => !sh.active && !sh.retired) || open.some(i => !i.shield_id);
      const ready = open.filter(i => i.status === 'verified').length;
      const [dot, text] = raw ? ['bg-red-500', 'incident · 500s live'] : open.length ? ['bg-amber-400', ready === open.length ? `shielded · ${ready} fix${ready > 1 ? 'es' : ''} awaiting approval` : `shielded · ${open.length - ready} healing`] : ['bg-emerald-400', s.incidents.length ? 'immune · all fixed' : 'all systems healthy'];
      pill.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${dot} pulse-dot"></span>${text}`;
    } catch {
      if (pill) pill.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-white/30"></span>offline';
    }
  }

  // ------------------------------------------------------------------ reveal
  function initReveal() {
    const io = new IntersectionObserver((entries) => entries.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add(e.target.classList.contains('reveal') ? 'in' : 'is-visible'); io.unobserve(e.target); }
    }), { threshold: 0.1 });
    document.querySelectorAll('[data-reveal]:not([data-reveal-on]), .reveal').forEach(el => io.observe(el));
  }
  function revealReady() {
    document.querySelectorAll('[data-reveal-on="ready"]').forEach(el => el.classList.add('is-visible'));
    window.dispatchEvent(new Event('tz:ready'));
  }

  // ------------------------------------------------------------------ splash iris
  function splash(onComplete) {
    const wrap = document.createElement('div');
    wrap.id = 'tz-splash'; wrap.setAttribute('aria-hidden', 'true');
    const canvas = document.createElement('canvas');
    wrap.appendChild(canvas); body.appendChild(wrap);
    const ctx = canvas.getContext('2d');
    const size = () => { canvas.width = innerWidth; canvas.height = innerHeight; };
    size(); addEventListener('resize', size);
    const finish = () => {
      wrap.style.pointerEvents = 'none'; wrap.style.opacity = '0';
      setTimeout(() => { removeEventListener('resize', size); wrap.remove(); if (!fired) onComplete(); }, 400);
    };
    if (reduced) { ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height); wrap.style.transition = 'opacity .2s'; requestAnimationFrame(() => { wrap.style.opacity = '0'; setTimeout(() => { wrap.remove(); onComplete(); }, 200); }); return; }
    const BLOCK = 10, DURATION = 1800;
    const ring1 = Array.from({ length: 360 }, (_, i) => ({ a: (2 * Math.PI * i) / 360 + (Math.random() * 0.02 - 0.01), f: 0.85 + Math.random() * 0.3 }));
    const ring2 = Array.from({ length: 180 }, (_, i) => ({ a: (2 * Math.PI * i) / 180 + (Math.random() * 0.02 - 0.01), f: 0.92 + Math.random() * 0.12 }));
    let start = null, raf, fired = false;
    const frame = (ts) => {
      start ??= ts;
      const t = Math.min(1, (ts - start) / DURATION);
      const ease = 1 - Math.pow(1 - t, 3);
      const W = canvas.width, H = canvas.height, cx = W / 2, cy = H / 2;
      const r = ease * (Math.sqrt(W * W + H * H) / 2) * 1.15;
      ctx.globalCompositeOperation = 'source-over';
      ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath(); ctx.arc(cx, cy, Math.max(0, r - BLOCK * 2), 0, Math.PI * 2); ctx.fill();
      for (const b of ring1) ctx.fillRect(Math.round(cx + Math.cos(b.a) * r * b.f - BLOCK / 2), Math.round(cy + Math.sin(b.a) * r * b.f - BLOCK / 2), BLOCK, BLOCK);
      const s2 = BLOCK * 0.8;
      for (const b of ring2) ctx.fillRect(Math.round(cx + Math.cos(b.a) * r * b.f - s2 / 2), Math.round(cy + Math.sin(b.a) * r * b.f - s2 / 2), s2, s2);
      if (t > 0.45 && !fired) { fired = true; onComplete(); }  // hero choreography starts as the iris opens
      if (t < 1) raf = requestAnimationFrame(frame); else finish();
    };
    raf = requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------ toasts
  window.tzToast = function (title, text = '', tone = 'cyan') {
    let host = document.getElementById('toastHost');
    if (!host) {
      host = document.createElement('div');
      host.id = 'toastHost'; host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite');
      host.className = 'fixed bottom-5 right-5 z-[60] space-y-2 w-[340px] max-w-[calc(100vw-40px)]';
      body.appendChild(host);
    }
    const tones = { cyan: 'bg-accent', green: 'bg-emerald-400', amber: 'bg-amber-400', red: 'bg-red-500' };
    const t = document.createElement('div');
    t.className = 'toast rounded-xl bg-white text-black p-3.5 flex gap-3 shadow-[0_20px_50px_-15px_rgba(0,0,0,.6)]';
    t.innerHTML = `<span class="w-1.5 self-stretch rounded-full ${tones[tone] || tones.cyan}"></span><div class="min-w-0"><div class="text-[13px] font-semibold">${esc(title)}</div>${text ? `<div class="text-xs text-black/60 mt-0.5">${esc(text)}</div>` : ''}</div>`;
    host.appendChild(t);
    setTimeout(() => { t.style.transition = 'opacity .4s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 4200);
  };

  // ------------------------------------------------------------------ boot
  renderNav();
  renderFooter();
  if (window.lucide) lucide.createIcons();
  initReveal();
  pollStatus(); setInterval(pollStatus, 2500);
  if (body.dataset.splash === 'true' && !sessionStorage.getItem('tz-splash')) {
    sessionStorage.setItem('tz-splash', '1');
    splash(revealReady);
  } else {
    requestAnimationFrame(revealReady);
  }
})();
