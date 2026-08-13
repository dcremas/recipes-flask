/* Progressive enhancement only — every feature below is optional, and the site
   is fully usable with JS disabled. Replaces the original site's jQuery bundle. */
(function () {
  'use strict';

  var root = document.documentElement;

  /* ------------------------------------------------------------- theme */
  var toggle = document.querySelector('.theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      var current = root.dataset.theme;
      if (current !== 'light' && current !== 'dark') current = systemDark ? 'dark' : 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try { localStorage.setItem('theme', next); } catch (e) {}
    });
  }

  /* --------------------------------------------------- mobile nav toggle */
  var navBtn = document.querySelector('.nav-toggle');
  var nav = document.getElementById('primary-nav');
  if (navBtn && nav) {
    var setOpen = function (open) {
      nav.classList.toggle('is-open', open);
      navBtn.setAttribute('aria-expanded', String(open));
      navBtn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    };
    navBtn.addEventListener('click', function () {
      setOpen(navBtn.getAttribute('aria-expanded') !== 'true');
    });
    // Collapse after choosing a destination, and on Escape.
    nav.addEventListener('click', function (e) {
      if (e.target.closest('a')) setOpen(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && navBtn.getAttribute('aria-expanded') === 'true') {
        setOpen(false);
        navBtn.focus();
      }
    });
  }

  /* ------------------------------------- header shadow once scrolled off */
  var header = document.getElementById('site-header');
  if (header && 'IntersectionObserver' in window) {
    var sentinel = document.createElement('div');
    sentinel.setAttribute('aria-hidden', 'true');
    sentinel.style.cssText = 'position:absolute;top:0;height:1px;width:1px;';
    document.body.prepend(sentinel);
    new IntersectionObserver(function (entries) {
      header.classList.toggle('is-stuck', !entries[0].isIntersecting);
    }).observe(sentinel);
  }

  /* --------------------------------------------------- reveal on scroll */
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduced && 'IntersectionObserver' in window) {
    var targets = document.querySelectorAll('.card, .viz-tile, .stat, .section-head');
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.06 });

    targets.forEach(function (el, i) {
      el.classList.add('reveal');
      el.style.transitionDelay = (Math.min(i % 3, 2) * 70) + 'ms';
      io.observe(el);
    });
  }

  /* ------------------------------ keep sticky header clear of anchor tops */
  var headerH = function () { return header ? header.offsetHeight : 0; };
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var id = a.getAttribute('href');
      if (!id || id === '#') return;
      var target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      var top = target.getBoundingClientRect().top + window.scrollY - headerH() - 12;
      window.scrollTo({ top: top, behavior: reduced ? 'auto' : 'smooth' });
      history.replaceState(null, '', id);
    });
  });
})();

/* ---------------------------------------------------------------- recipes */
/* Print button and delete confirmation.
   Both are progressive enhancement: with JS off, Print simply does nothing
   (Cmd-P still works) and Delete still submits — the server re-checks
   ownership and the CSRF token, so confirmation is a courtesy, not a control. */
(function () {
  document.addEventListener('click', function (e) {
    var printBtn = e.target.closest('[data-print]');
    if (printBtn) { window.print(); }
  });

  document.addEventListener('submit', function (e) {
    var form = e.target.closest('form[data-confirm]');
    if (!form) return;
    if (!window.confirm(form.getAttribute('data-confirm'))) {
      e.preventDefault();
    }
  });
})();
