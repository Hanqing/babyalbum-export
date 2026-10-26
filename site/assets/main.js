(() => {
  'use strict';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const hasIO = 'IntersectionObserver' in window;

  /* ---------- Nav ---------- */
  const nav = $('#nav');
  if (nav) {
    const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  const navDl = $('#navDl');
  const heroCta = $('#heroCta');
  if (hasIO && navDl && heroCta) {
    new IntersectionObserver(([e]) => {
      navDl.classList.toggle('visible', !e.isIntersecting && e.boundingClientRect.top < 0);
    }, { rootMargin: '-60px 0px 0px 0px' }).observe(heroCta);
  }

  /* ---------- Reveal on scroll ---------- */
  const revealEls = $$('[data-reveal]');
  if (hasIO && !reduce) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      }
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.06 });
    revealEls.forEach((el) => io.observe(el));
  } else {
    revealEls.forEach((el) => el.classList.add('in'));
  }

  /* ---------- Phone mock-ups ---------- */
  const icon = (id) => `<svg class="icon"><use href="#${id}"/></svg>`;

  // Small seeded PRNG so every visit draws the same "photos".
  function rng(seed) {
    return () => {
      seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const CLOTH = ['#f4a6a0', '#9cc9f2', '#f6d37a', '#b7e0b4', '#c9b5ef', '#f7b9d3', '#a8d8e0'];
  const BEDS = [['#f6eee5', '#e2d2c1'], ['#eef2f5', '#d3dce2'], ['#f7efe6', '#e8d6c1'], ['#f3eee9', '#ddd1c5'], ['#f2f4ee', '#d9dccd']];
  const DAYS = (() => {
    const r = rng(7);
    const out = [];
    let t = Date.UTC(2026, 9, 24);
    while (out.length < 24) {
      out.push(new Date(t).toISOString().slice(0, 10));
      t -= (r() < 0.3 ? 2 : 1) * 86400000;
    }
    return out;
  })();

  function makeTile(r) {
    const el = document.createElement('div');
    el.className = 'tile' + (r() < 0.37 ? ' night' : '');
    const hx = 34 + r() * 32;
    const hy = 28 + r() * 22;
    const bx = hx + (r() - 0.5) * 10;
    const by = hy + 13 + r() * 8;
    const bed = BEDS[Math.floor(r() * BEDS.length)];
    el.style.cssText = `--hx:${hx.toFixed(1)}%;--hy:${hy.toFixed(1)}%;--bx:${bx.toFixed(1)}%;--by:${by.toFixed(1)}%;` +
      `--cl:${CLOTH[Math.floor(r() * CLOTH.length)]};--b1:${bed[0]};--b2:${bed[1]};--ang:${Math.floor(130 + r() * 60)}deg`;
    el.innerHTML = '<span class="img"></span><i></i>';
    return el;
  }

  function buildGroups(container, seed, count) {
    const r = rng(seed);
    container.textContent = '';
    const groups = [];
    for (let g = 0; g < count; g++) {
      const n = 2 + Math.floor(r() * 7);
      const el = document.createElement('div');
      el.className = 'grp';
      el.innerHTML = `<div class="gh"><span class="cb"></span><b>${DAYS[g]}</b><em>${n} 张</em></div><div class="tiles"></div>`;
      const tiles = el.querySelector('.tiles');
      for (let i = 0; i < n; i++) tiles.appendChild(makeTile(r));
      container.appendChild(el);
      groups.push({ el, n });
    }
    return groups;
  }

  const PHONE = `<div class="phone"><div class="screen">
    <div class="sb"><span>9:41</span><i class="cam"></i><span class="sb-r"><b></b><b></b><b></b></span></div>
    <div class="strip"><span data-strip>第 3 批 · 已下载 300 张 · 勾选照片</span><em>停止</em></div>
    <div class="appbar"><span>取消</span><b>已选择 <span data-count>0</span> 项</b><span></span></div>
    <div class="filter"><span>仅显示未下载的文件</span><i class="sw"></i></div>
    <div class="album"><div class="album-in" data-album></div><div class="empty" data-empty>${icon('i-image')}<span>暂无内容</span></div></div>
    <div class="bbar"><span class="bb-del">${icon('i-trash')}删除</span><span class="bb-dl" data-dl>${icon('i-download')}下载</span></div>
    <div class="dialog" data-dialog><i class="spin"></i><span>下载中</span></div>
    <div class="result" data-result><span class="rc">${icon('i-check')}</span><div><b>完成：待下载列表已清空</b><small>第 13 批 · 本次下载 2,515 张</small></div></div>
  </div></div>`;

  const phones = {};
  $$('[data-phone]').forEach((slot) => {
    slot.innerHTML = PHONE;
    slot.setAttribute('aria-hidden', 'true');
    phones[slot.dataset.phone] = {
      strip: $('[data-strip]', slot),
      stripBox: $('.strip', slot),
      count: $('[data-count]', slot),
      album: $('[data-album]', slot),
      empty: $('[data-empty]', slot),
      dl: $('[data-dl]', slot),
      dialog: $('[data-dialog]', slot),
      result: $('[data-result]', slot),
    };
  });

  function check(group, animate) {
    const cb = group.el.querySelector('.cb');
    cb.classList.add('on');
    if (animate) cb.classList.add('tap');
    group.el.querySelectorAll('.tile').forEach((t) => t.classList.add('sel'));
  }
  const sum = (groups, k) => groups.slice(0, k).reduce((s, g) => s + g.n, 0);

  // Static phones beside the feature copy.
  if (phones.select) {
    const p = phones.select;
    const groups = buildGroups(p.album, 11, 9);
    groups.slice(0, 3).forEach((g) => check(g));
    p.count.textContent = sum(groups, 3);
  }
  if (phones.download) {
    const p = phones.download;
    const groups = buildGroups(p.album, 5, 8);
    groups.slice(0, 4).forEach((g) => check(g));
    p.count.textContent = sum(groups, 4);
    p.strip.textContent = '第 3 批 · 已下载 300 张 · 下载中';
    p.dl.classList.add('press');
    p.dialog.classList.add('show');
  }

  /* ---------- Hero demo ---------- */
  const hero = phones.hero;
  const stage = $('#demo');
  const tabs = $$('.chapter');
  if (hero && stage && tabs.length === 3) {
    const DUR = [6600, 4800, 5200];
    const SEL = 6;
    let groups = [];
    let scene = 0;
    let timers = [];
    let playing = false;
    let onScreen = true;

    const later = (fn, ms) => { timers.push(setTimeout(fn, ms)); };
    const clearTimers = () => { timers.forEach(clearTimeout); timers = []; };
    const setStrip = (text, done) => { hero.strip.textContent = text; hero.stripBox.classList.toggle('done', !!done); };
    const scrollTo = (i, instant) => {
      const g = groups[Math.max(0, i)];
      const y = g ? g.el.offsetTop - groups[0].el.offsetTop : 0;
      if (instant) hero.album.style.transition = 'none';
      hero.album.style.transform = y ? `translateY(${-y}px)` : '';
      if (instant) { void hero.album.offsetHeight; hero.album.style.transition = ''; }
    };

    function reset() {
      groups = buildGroups(hero.album, 3, 16);
      scrollTo(0, true);
      hero.count.textContent = '0';
      setStrip('第 3 批 · 已下载 300 张 · 勾选照片');
      hero.dialog.classList.remove('show');
      hero.empty.classList.remove('show');
      hero.result.classList.remove('show');
      hero.dl.classList.remove('press');
    }
    function selectAll() {
      groups.slice(0, SEL).forEach((g) => check(g));
      hero.count.textContent = sum(groups, SEL);
      scrollTo(SEL - 3, true);
    }
    function afterDownload(instant) {
      const done = 300 + sum(groups, SEL);
      groups.slice(0, SEL).forEach((g) => g.el.classList.add('gone'));
      scrollTo(0, instant);
      hero.count.textContent = '0';
      hero.dl.classList.remove('press');
      hero.dialog.classList.remove('show');
      setStrip(`第 4 批 · 已下载 ${done} 张 · 勾选照片`);
    }
    function finish() {
      groups.forEach((g) => g.el.classList.add('gone'));
      hero.empty.classList.add('show');
      setStrip('第 13 批 · 已下载 2,515 张 · 全部完成', true);
      hero.result.classList.add('show');
    }

    // Each scene either animates (live) or jumps to its end state.
    const SCENES = [
      (live) => {
        reset();
        if (!live) { selectAll(); return; }
        let n = 0;
        for (let i = 0; i < SEL; i++) {
          later(() => {
            check(groups[i], true);
            n += groups[i].n;
            hero.count.textContent = n;
            if (i >= 2) scrollTo(i - 1);
          }, 650 + i * 780);
        }
        later(() => hero.dl.classList.add('press'), 650 + SEL * 780 + 200);
      },
      (live, cont) => {
        if (!cont) { reset(); selectAll(); }
        if (!live) { afterDownload(true); return; }
        hero.dl.classList.add('press');
        later(() => { hero.dialog.classList.add('show'); setStrip('第 3 批 · 已下载 300 张 · 下载中'); }, 250);
        later(() => afterDownload(false), 2700);
      },
      (live, cont) => {
        if (!cont) { reset(); selectAll(); afterDownload(true); }
        if (!live) { finish(); return; }
        groups.forEach((g, i) => later(() => g.el.classList.add('gone'), 60 * i));
        later(() => { hero.empty.classList.add('show'); setStrip('第 13 批 · 已下载 2,515 张 · 全部完成', true); }, 900);
        later(() => hero.result.classList.add('show'), 1600);
      },
    ];

    function show(i, cont) {
      clearTimers();
      scene = i;
      tabs.forEach((t, k) => {
        t.setAttribute('aria-selected', k === i ? 'true' : 'false');
        t.tabIndex = k === i ? 0 : -1;
        t.classList.remove('run');
      });
      SCENES[i](playing, cont);
      if (playing) {
        const t = tabs[i];
        t.style.setProperty('--dur', DUR[i] + 'ms');
        void t.offsetWidth;
        t.classList.add('run');
        later(() => show((i + 1) % 3, true), DUR[i]);
      }
    }
    function setPlaying(on) {
      const next = on && !reduce;
      if (next === playing) return;
      playing = next;
      show(scene, false);
    }

    tabs.forEach((t, k) => {
      t.addEventListener('click', () => show(k, false));
      t.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        e.preventDefault();
        const n = (k + (e.key === 'ArrowRight' ? 1 : 2)) % 3;
        show(n, false);
        tabs[n].focus();
      });
    });

    const sync = () => setPlaying(onScreen && !document.hidden);
    if (hasIO) {
      new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; sync(); }, { threshold: 0.25 }).observe(stage);
    }
    document.addEventListener('visibilitychange', sync);
    playing = !reduce;
    show(0, false);
  }

  /* ---------- Chart tooltips ---------- */
  const tip = $('#tip');
  if (tip) {
    const place = (x, y) => {
      const r = tip.getBoundingClientRect();
      let left = x + 14;
      let top = y + 16;
      if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
      if (top + r.height > window.innerHeight - 8) top = y - r.height - 16;
      tip.style.left = Math.max(8, left) + 'px';
      tip.style.top = Math.max(8, top) + 'px';
    };
    const open = (el, x, y) => {
      tip.textContent = '';
      const v = document.createElement('span');
      v.className = 'tip-v';
      v.textContent = el.dataset.tipValue;
      const n = document.createElement('span');
      n.className = 'tip-n';
      const key = document.createElement('i');
      if (el.closest('.muted')) key.className = 'muted';
      n.append(key, document.createTextNode(el.dataset.tipName));
      const d = document.createElement('span');
      d.className = 'tip-d';
      d.textContent = el.dataset.tipDetail;
      tip.append(v, n, d);
      tip.classList.add('show');
      place(x, y);
    };
    const close = () => tip.classList.remove('show');
    $$('.bar-track').forEach((el) => {
      el.setAttribute('aria-label', `${el.dataset.tipName}：${el.dataset.tipValue}。${el.dataset.tipDetail}`);
      el.addEventListener('pointerenter', (e) => open(el, e.clientX, e.clientY));
      el.addEventListener('pointermove', (e) => place(e.clientX, e.clientY));
      el.addEventListener('pointerleave', close);
      el.addEventListener('focus', () => {
        const r = el.querySelector('.bar-fill').getBoundingClientRect();
        open(el, r.right, r.top);
      });
      el.addEventListener('blur', close);
    });
    window.addEventListener('scroll', close, { passive: true });
  }
})();
