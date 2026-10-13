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
/* Delete confirmation.
   Progressive enhancement: with JS off the form still submits, and the server
   re-checks ownership and the CSRF token — so this is a courtesy, not a
   control. (Printing is handled by the PDF route, not window.print().) */
(function () {
  document.addEventListener('submit', function (e) {
    var form = e.target.closest('form[data-confirm]');
    if (!form) return;
    if (!window.confirm(form.getAttribute('data-confirm'))) {
      e.preventDefault();
    }
  });
})();

/* ------------------------------------------------------- admin conveniences */
/* Progressive enhancement, as above: with JS off the forms still submit and the
   server does all the real work. These only shorten the feedback loop. */
(function () {
  'use strict';

  /* Preview a chosen image before uploading, so a wrong file is obvious before
     a round trip. The object URL is revoked once the image has decoded — not
     immediately, or Safari shows a blank frame. */
  var previewPairs = [
    { input: 'photo-input', img: 'photo-preview', placeholder: 'photo-placeholder' },
    { input: 'scan-input',  img: 'scan-preview-img', wrap: 'scan-preview' }
  ];

  previewPairs.forEach(function (pair) {
    var input = document.getElementById(pair.input);
    var img = document.getElementById(pair.img);
    if (!input || !img) return;

    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (!file || !file.type.startsWith('image/')) return;
      var url = URL.createObjectURL(file);
      img.addEventListener('load', function once() {
        URL.revokeObjectURL(url);
        img.removeEventListener('load', once);
      });
      img.src = url;
      img.hidden = false;
      var placeholder = pair.placeholder && document.getElementById(pair.placeholder);
      if (placeholder) placeholder.hidden = true;
      var wrap = pair.wrap && document.getElementById(pair.wrap);
      if (wrap) wrap.hidden = false;
    });
  });

  /* Transcribing a photo takes several seconds with no visible sign that
     anything is happening, which invites a second click and a second API bill.
     Disable on submit and say what's going on. */
  document.querySelectorAll('button[data-busy]').forEach(function (btn) {
    var form = btn.closest('form');
    if (!form) return;
    form.addEventListener('submit', function () {
      // Let the browser's own validation block submission first.
      if (form.checkValidity && !form.checkValidity()) return;
      btn.disabled = true;
      btn.textContent = btn.getAttribute('data-busy');
    });
  });
})();

/* ------------------------------------------------------- unsaved-input guard */
/* Estate link policy §5: unsaved input is protected by a prompt, not by
   opening links in new tabs. A form marked data-unsaved-guard asks before the
   page is left while it holds something to lose: a field that differs from
   what the page loaded with, or — with data-unsaved-draft — anything at all,
   for content that exists only on this page (an import draft, a POST
   re-rendered with errors).

   Submitting ANY form on the page switches the prompt off: saving, sending
   feedback and confirming a delete are all deliberate departures. A submit
   cancelled by the delete confirmation does not count; one that is restored
   from the back/forward cache re-arms. With JS off there is no prompt and
   nothing else changes. */
(function () {
  'use strict';

  var forms = document.querySelectorAll('form[data-unsaved-guard]');
  if (!forms.length) return;

  function isDirty(form) {
    if (form.hasAttribute('data-unsaved-draft')) return true;
    var els = form.elements;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (!el.name || el.disabled) continue;
      var type = el.type;
      if (type === 'hidden' || type === 'submit' || type === 'button' || type === 'reset') continue;
      if (type === 'file') {
        if (el.files && el.files.length) return true;
      } else if (type === 'checkbox' || type === 'radio') {
        if (el.checked !== el.defaultChecked) return true;
      } else if (el.options) {
        for (var j = 0; j < el.options.length; j++) {
          if (el.options[j].selected !== el.options[j].defaultSelected) return true;
        }
      } else if (el.value !== el.defaultValue) {
        return true;
      }
    }
    return false;
  }

  var leaving = false;

  // Registered after the delete-confirmation listener above, so a cancelled
  // confirm has already called preventDefault by the time this runs.
  document.addEventListener('submit', function (e) {
    if (!e.defaultPrevented) leaving = true;
  });
  window.addEventListener('pageshow', function () { leaving = false; });

  window.addEventListener('beforeunload', function (e) {
    if (leaving) return;
    for (var i = 0; i < forms.length; i++) {
      if (isDirty(forms[i])) {
        e.preventDefault();
        e.returnValue = true;  // legacy browsers (Chrome < 119) ignore preventDefault
        return;
      }
    }
  });
})();
