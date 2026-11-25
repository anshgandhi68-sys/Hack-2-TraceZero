// TraceZero procedural media. Generated in the browser, so the site needs no
// video files and works offline on stage.
// Usage: <canvas data-fx="atmosphere|paleflow|topo|particles|radar"></canvas>
// Each canvas fills its parent, renders only while on screen, and freezes on
// a single still frame under prefers-reduced-motion.
(function () {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------------------------------------------------------------- noise
  const P = new Uint8Array(512);
  (() => { const p = [...Array(256).keys()]; let seed = 1337;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    for (let i = 255; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [p[i], p[j]] = [p[j], p[i]]; }
    for (let i = 0; i < 512; i++) P[i] = p[i & 255]; })();
  const hash = (i, j) => P[(P[i & 255] + j) & 255] / 255;
  function noise(x, y) {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    const a = hash(xi, yi), b = hash(xi + 1, yi), c = hash(xi, yi + 1), d = hash(xi + 1, yi + 1);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  }
  function fbm(x, y, oct = 4) { let s = 0, a = .5, f = 1, n = 0; for (let o = 0; o < oct; o++) { s += a * noise(x * f, y * f); n += a; f *= 2; a *= .5; } return s / n; }

  // ---------------------------------------------------------------- engine
  function mount(canvas, factory) {
    const ctx = canvas.getContext('2d');
    const dprCap = canvas.dataset.dpr ? +canvas.dataset.dpr : 1.5;
    let W = 0, H = 0, scene = null, raf = null, visible = true, t0 = performance.now();
    const resize = () => {
      const box = canvas.parentElement.getBoundingClientRect();
      const dpr = Math.min(devicePixelRatio || 1, dprCap);
      W = Math.max(1, Math.round(box.width)); H = Math.max(1, Math.round(box.height));
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      scene = factory(ctx, W, H);
      if (reduced) { for (let i = 0; i < 90; i++) scene(i * 16); }
    };
    const loop = (now) => { scene && scene(now - t0); raf = requestAnimationFrame(loop); };
    const start = () => { if (!raf && !reduced && visible && !document.hidden) raf = requestAnimationFrame(loop); };
    const stop = () => { if (raf) cancelAnimationFrame(raf); raf = null; };
    new ResizeObserver(() => { resize(); start(); }).observe(canvas.parentElement);
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; visible ? start() : stop(); }, { rootMargin: '100px' }).observe(canvas);
    document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
  }

  // ---------------------------------------------------------------- scenes
  // Nocturnal trace atmosphere: charcoal fog, silver flow ribbons, orange glints
  function atmosphere(ctx, W, H) {
    const N = Math.min(900, Math.round((W * H) / 2200));
    const ps = Array.from({ length: N }, () => spawn({}, true));
    function spawn(p, anywhere) {
      p.x = anywhere ? Math.random() * W : -10 - Math.random() * 40; p.y = Math.random() * H;
      p.v = .35 + Math.random() * 1.1; p.a = .05 + Math.random() * .22; p.life = 200 + Math.random() * 500; return p;
    }
    const glints = Array.from({ length: 14 }, () => ({ x: Math.random() * W, y: Math.random() * H, ph: Math.random() * 6.28, s: .6 + Math.random() }));
    ctx.fillStyle = '#050506'; ctx.fillRect(0, 0, W, H);
    return (t) => {
      ctx.fillStyle = 'rgba(5,5,7,0.085)'; ctx.fillRect(0, 0, W, H);
      // drifting fog banks
      for (let i = 0; i < 3; i++) {
        const fx = W * (.25 + .5 * (0.5 + 0.5 * Math.sin(t * 0.00005 + i * 2.1)));
        const fy = H * (.3 + .4 * (0.5 + 0.5 * Math.cos(t * 0.00004 + i * 1.3)));
        const g = ctx.createRadialGradient(fx, fy, 0, fx, fy, Math.max(W, H) * .45);
        g.addColorStop(0, 'rgba(150,156,168,0.022)'); g.addColorStop(1, 'rgba(150,156,168,0)');
        ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      }
      // flow ribbons
      ctx.lineWidth = 1;
      for (const p of ps) {
        const ang = (fbm(p.x * .0013 + t * .00003, p.y * .0016 - t * .00002, 3) - .5) * Math.PI * 2.4 - .12;
        const nx = p.x + Math.cos(ang) * p.v * 1.4 + .25, ny = p.y + Math.sin(ang) * p.v;
        ctx.strokeStyle = `rgba(205,210,220,${p.a})`;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(nx, ny); ctx.stroke();
        p.x = nx; p.y = ny;
        if (--p.life < 0 || p.x > W + 10 || p.y < -10 || p.y > H + 10) spawn(p, Math.random() < .3);
      }
      // orange sensor glints
      for (const g of glints) {
        const a = Math.pow(Math.max(0, Math.sin(t * .0011 * g.s + g.ph)), 10);
        if (a < .02) continue;
        const r = 7 * g.s;
        const gr = ctx.createRadialGradient(g.x, g.y, 0, g.x, g.y, r);
        gr.addColorStop(0, `rgba(246,67,2,${.9 * a})`); gr.addColorStop(1, 'rgba(246,67,2,0)');
        ctx.fillStyle = gr; ctx.fillRect(g.x - r, g.y - r, r * 2, r * 2);
        ctx.fillStyle = `rgba(255,190,150,${a})`; ctx.fillRect(g.x - .8, g.y - .8, 1.6, 1.6);
        g.x += .15;
        if (g.x > W) g.x = 0;
      }
    };
  }

  // Bright overcast: pale ivory field with soft grey streams sliding diagonally
  function paleflow(ctx, W, H) {
    const N = Math.min(700, Math.round((W * H) / 2600));
    const ps = Array.from({ length: N }, () => ({ x: Math.random() * W, y: Math.random() * H, v: .3 + Math.random() * .8, a: .06 + Math.random() * .12, w: .6 + Math.random() * 1.6 }));
    ctx.fillStyle = '#f0eded'; ctx.fillRect(0, 0, W, H);
    return (t) => {
      ctx.fillStyle = 'rgba(240,237,237,0.07)'; ctx.fillRect(0, 0, W, H);
      for (let i = 0; i < 2; i++) {
        const fx = W * (.2 + .6 * (0.5 + 0.5 * Math.sin(t * 0.00004 + i * 2.6))), fy = H * (.3 + .4 * (0.5 + 0.5 * Math.cos(t * 0.00003 + i)));
        const g = ctx.createRadialGradient(fx, fy, 0, fx, fy, Math.max(W, H) * .5);
        g.addColorStop(0, 'rgba(255,255,255,0.05)'); g.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      }
      for (const p of ps) {
        const ang = -0.45 + (fbm(p.x * .0011 + t * .00002, p.y * .0013, 3) - .5) * 1.6;
        const nx = p.x + Math.cos(ang) * p.v, ny = p.y + Math.sin(ang) * p.v;
        ctx.strokeStyle = `rgba(110,106,112,${p.a})`; ctx.lineWidth = p.w;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(nx, ny); ctx.stroke();
        p.x = nx; p.y = ny;
        if (p.x > W + 20 || p.y < -20) { p.x = Math.random() * W * .8 - W * .2; p.y = H + Math.random() * 20; if (Math.random() < .5) { p.x = -10; p.y = Math.random() * H; } }
      }
    };
  }

  // Monochrome topographic field: contour lines + stippled data texture, slow pan
  function topo(ctx, W, H) {
    const SW = Math.round(W * 1.25), SH = Math.round(H * 1.25), step = 5;
    const off = document.createElement('canvas'); off.width = SW; off.height = SH;
    const o = off.getContext('2d');
    o.fillStyle = '#e9e6e3'; o.fillRect(0, 0, SW, SH);
    const cols = Math.ceil(SW / step) + 1, rows = Math.ceil(SH / step) + 1, v = new Float32Array(cols * rows);
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      const x = i * step, y = j * step;
      v[j * cols + i] = fbm(x * .0042 + 3.1, y * .0042 + 7.7, 5) * .85 + .15 * fbm(x * .02, y * .02, 2);
    }
    // stipple
    for (let k = 0; k < SW * SH / 90; k++) {
      const x = Math.random() * SW, y = Math.random() * SH, val = v[Math.floor(y / step) * cols + Math.floor(x / step)];
      const band = (val * 22) % 1;
      if (band < .35) { o.fillStyle = `rgba(0,0,0,${.06 + band * .3})`; o.fillRect(x, y, 1.1, 1.1); }
    }
    const SEG = { 1: [[3, 2]], 2: [[2, 1]], 3: [[3, 1]], 4: [[0, 1]], 5: [[3, 0], [2, 1]], 6: [[0, 2]], 7: [[3, 0]], 8: [[3, 0]], 9: [[0, 2]], 10: [[0, 1], [3, 2]], 11: [[0, 1]], 12: [[3, 1]], 13: [[2, 1]], 14: [[3, 2]] };
    const LEVELS = 22;
    for (let l = 1; l < LEVELS; l++) {
      const L = .2 + (l / LEVELS) * .65;
      const major = l % 4 === 0;
      o.strokeStyle = major ? 'rgba(0,0,0,.72)' : 'rgba(0,0,0,.38)'; o.lineWidth = major ? 1.4 : .8;
      o.beginPath();
      for (let j = 0; j < rows - 1; j++) for (let i = 0; i < cols - 1; i++) {
        const a = v[j * cols + i], b = v[j * cols + i + 1], c = v[(j + 1) * cols + i + 1], d = v[(j + 1) * cols + i];
        const idx = (a > L) << 3 | (b > L) << 2 | (c > L) << 1 | (d > L);
        if (!idx || idx === 15) continue;
        const x0 = i * step, y0 = j * step, x1 = x0 + step, y1 = y0 + step;
        const pt = (e) => e === 0 ? [x0 + step * (L - a) / (b - a), y0] : e === 1 ? [x1, y0 + step * (L - b) / (c - b)] : e === 2 ? [x0 + step * (L - d) / (c - d), y1] : [x0, y0 + step * (L - a) / (d - a)];
        for (const [e1, e2] of SEG[idx]) { const p1 = pt(e1), p2 = pt(e2); o.moveTo(p1[0], p1[1]); o.lineTo(p2[0], p2[1]); }
      }
      o.stroke();
    }
    // survey marks
    o.fillStyle = 'rgba(0,0,0,.65)'; o.font = '9px "JetBrains Mono", monospace';
    for (let k = 0; k < 7; k++) { const x = 40 + Math.random() * (SW - 80), y = 40 + Math.random() * (SH - 80); o.fillRect(x - 3, y, 7, 1); o.fillRect(x, y - 3, 1, 7); o.fillText(`${(v[Math.floor(y / step) * cols + Math.floor(x / step)] * 1000) | 0}`, x + 6, y - 4); }
    return (t) => {
      const px = (SW - W) * (.5 + .5 * Math.sin(t * .00006)), py = (SH - H) * (.5 + .5 * Math.cos(t * .00005));
      ctx.drawImage(off, -px, -py);
    };
  }

  // Particulate streams through a scanning light plane, sparse orange pulses
  function particles(ctx, W, H) {
    const N = Math.min(700, Math.round((W * H) / 750));
    const ps = Array.from({ length: N }, () => mk({}, true));
    function mk(p, any) {
      p.x = any ? Math.random() * W : -20; p.y = any ? Math.random() * H : Math.random() * H * 1.2;
      p.vx = .5 + Math.random() * 1.3; p.vy = -.15 - Math.random() * .45; p.s = .6 + Math.random() * 1.8; p.hot = Math.random() < .025; p.ph = Math.random() * 6.28; return p;
    }
    ctx.fillStyle = '#030304'; ctx.fillRect(0, 0, W, H);
    return (t) => {
      ctx.fillStyle = 'rgba(3,3,4,0.2)'; ctx.fillRect(0, 0, W, H);
      const px = W * (.5 + .45 * Math.sin(t * .00045));
      const g = ctx.createLinearGradient(px - 70, 0, px + 70, 0);
      g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(.5, 'rgba(220,225,235,0.07)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = g; ctx.fillRect(px - 70, 0, 140, H);
      ctx.fillStyle = 'rgba(235,240,250,0.18)'; ctx.fillRect(px - .5, 0, 1, H);
      for (const p of ps) {
        p.x += p.vx; p.y += p.vy + Math.sin(t * .001 + p.ph) * .15;
        const near = Math.max(0, 1 - Math.abs(p.x - px) / 60);
        if (p.hot) {
          const pulse = .4 + .6 * Math.pow(Math.max(0, Math.sin(t * .003 + p.ph)), 4);
          ctx.fillStyle = `rgba(246,67,2,${Math.min(1, pulse + near)})`;
          ctx.fillRect(p.x, p.y, p.s + 1, p.s + 1);
        } else {
          ctx.fillStyle = `rgba(225,230,240,${.22 + near * .78})`;
          ctx.fillRect(p.x, p.y, p.s * (1 + near), p.s * (1 + near));
        }
        if (p.x > W + 10 || p.y < -10) mk(p, false);
      }
    };
  }

  // Monochrome radar lens with grain, rotating sweep and decaying blips
  function radar(ctx, W, H) {
    const cx = W / 2, cy = H / 2, R = Math.hypot(W, H) * .62;
    const base = document.createElement('canvas'); base.width = W; base.height = H;
    const b = base.getContext('2d');
    b.fillStyle = '#0b0b0c'; b.fillRect(0, 0, W, H);
    const vg = b.createRadialGradient(cx, cy, 0, cx, cy, R);
    vg.addColorStop(0, '#1a1a1c'); vg.addColorStop(1, '#050505'); b.fillStyle = vg; b.fillRect(0, 0, W, H);
    b.strokeStyle = 'rgba(255,255,255,.14)'; b.lineWidth = 1;
    for (let r = 26; r < R; r += 26) { b.beginPath(); b.arc(cx, cy, r, 0, Math.PI * 2); b.stroke(); }
    b.strokeStyle = 'rgba(255,255,255,.1)';
    for (let k = 0; k < 12; k++) { const a = k * Math.PI / 6; b.beginPath(); b.moveTo(cx, cy); b.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R); b.stroke(); }
    for (let k = 0; k < 72; k++) { const a = k * Math.PI / 36, r1 = Math.min(W, H) * .44; b.beginPath(); b.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); b.lineTo(cx + Math.cos(a) * (r1 + (k % 6 ? 4 : 9)), cy + Math.sin(a) * (r1 + (k % 6 ? 4 : 9))); b.stroke(); }
    const grain = document.createElement('canvas'); grain.width = grain.height = 192;
    const gctx = grain.getContext('2d'), id = gctx.createImageData(192, 192);
    for (let i = 0; i < id.data.length; i += 4) { const n = Math.random() * 255; id.data[i] = id.data[i + 1] = id.data[i + 2] = n; id.data[i + 3] = 38; }
    gctx.putImageData(id, 0, 0);
    const blips = Array.from({ length: 22 }, (_, i) => ({ a: Math.random() * Math.PI * 2, r: (.12 + Math.random() * .8) * Math.min(W, H) * .48, hot: i === 0 }));
    const hasConic = typeof ctx.createConicGradient === 'function';
    return (t) => {
      ctx.drawImage(base, 0, 0);
      const ang = (t * .0008) % (Math.PI * 2);
      if (hasConic) {
        const cg = ctx.createConicGradient(ang - .9, cx, cy);
        cg.addColorStop(0, 'rgba(255,255,255,0)'); cg.addColorStop(.143, 'rgba(255,255,255,.26)'); cg.addColorStop(.1435, 'rgba(255,255,255,0)'); cg.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = cg; ctx.fillRect(0, 0, W, H);
      }
      ctx.strokeStyle = 'rgba(255,255,255,.55)'; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ang) * R, cy + Math.sin(ang) * R); ctx.stroke();
      for (const bl of blips) {
        const d = ((ang - bl.a) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
        const a = Math.exp(-d * 1.6);
        if (a < .03) continue;
        const x = cx + Math.cos(bl.a) * bl.r, y = cy + Math.sin(bl.a) * bl.r;
        ctx.fillStyle = bl.hot ? `rgba(246,67,2,${a})` : `rgba(240,240,240,${a * .9})`;
        ctx.beginPath(); ctx.arc(x, y, bl.hot ? 3.2 : 2, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = .9;
      const ox = (Math.random() * 192) | 0, oy = (Math.random() * 192) | 0;
      for (let x = -ox; x < W; x += 192) for (let y = -oy; y < H; y += 192) ctx.drawImage(grain, x, y);
      ctx.globalAlpha = 1;
    };
  }

  const SCENES = { atmosphere, paleflow, topo, particles, radar };
  window.TZFX = { mount, SCENES };
  document.querySelectorAll('canvas[data-fx]').forEach(c => SCENES[c.dataset.fx] && mount(c, SCENES[c.dataset.fx]));
})();
