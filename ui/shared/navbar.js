/**
 * LEAP2 shared navbar — injected via <script src="/static/navbar.js"></script>.
 *
 * Renders the navbar with experiment-aware links, hamburger menu,
 * and theme toggle. Reads `data-page` attribute from <body> to
 * know which link to highlight/omit (e.g. data-page="logs").
 *
 * Experiment name is resolved from:
 *   1. URL path: /exp/<name>/...
 *   2. Query param: ?exp=<name>
 *   3. Falls back to "default"
 */
(function () {
  // ── Resolve experiment name ──
  var pathMatch = window.location.pathname.match(/\/exp\/([^/]+)/);
  var expName = pathMatch
    ? pathMatch[1]
    : new URLSearchParams(window.location.search).get("exp") || "default";

  // Expose for other scripts
  window.LEAP_EXP = expName;

  var currentPage = document.body.getAttribute("data-page") || "";

  // ── Build navbar ──
  var nav = document.createElement("nav");
  nav.className = "navbar";
  nav.innerHTML =
    '<a class="navbar-brand" href="/"><span>LEAP</span>2</a>' +
    '<button class="nav-hamburger" id="nav-hamburger" aria-label="Menu">' +
    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button>' +
    '<div class="navbar-links" id="navbar-links"></div>';

  // ── Build links ──
  var linksEl = nav.querySelector("#navbar-links");

  var links = [
    { id: "dash-link", text: "Lab", href: "/static/readme.html?exp=" + expName, cls: "nav-lab", page: "dashboard" },
    { divider: true },
    { id: "students-link", text: "Students", href: "/static/students.html?exp=" + expName, cls: "nav-shared", page: "students" },
    { id: "logs-link", text: "Logs", href: "/static/logs.html?exp=" + expName, cls: "nav-shared", page: "logs" },
    { id: "functions-link", text: "Functions", href: "/static/functions.html?exp=" + expName, cls: "nav-shared", page: "functions" },
    { id: "readme-link", text: "README", href: "/static/readme.html?exp=" + expName, cls: "nav-shared", page: "readme" },
  ];

  links.forEach(function (link) {
    if (link.divider) {
      var span = document.createElement("span");
      span.className = "nav-divider";
      linksEl.appendChild(span);
      return;
    }
    var a = document.createElement("a");
    a.href = link.href;
    a.textContent = link.text;
    a.id = link.id;
    if (link.cls) a.className = link.cls;
    if (link.page === currentPage) a.setAttribute("aria-current", "page");
    linksEl.appendChild(a);
  });

  // ── Theme toggle ──
  var toggleBtn = document.createElement("button");
  toggleBtn.className = "theme-toggle";
  toggleBtn.id = "theme-toggle";
  toggleBtn.setAttribute("aria-label", "Toggle theme");
  toggleBtn.innerHTML =
    '<svg class="icon-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>' +
    '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>' +
    '<line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>' +
    '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>' +
    '<svg class="icon-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
  linksEl.appendChild(toggleBtn);

  // ── Insert at top of body (after skip-to-content if present) ──
  var skip = document.querySelector(".skip-to-content");
  if (skip && skip.nextSibling) {
    document.body.insertBefore(nav, skip.nextSibling);
  } else {
    document.body.insertBefore(nav, document.body.firstChild);
  }

  // ── Enrich links with counts from API ──
  fetch("/api/experiments")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var exp = (d.experiments || []).find(function (e) { return e.name === expName; });
      if (!exp) return;
      var ep = exp.entry_point;
      if (ep && ep !== "readme") {
        document.getElementById("dash-link").href = "/exp/" + expName + "/ui/" + ep;
      } else {
        document.getElementById("dash-link").href = "/static/readme.html?exp=" + expName;
      }
      // ── Render extra experiment pages ──
      var pages = exp.pages || [];
      var divider = linksEl.querySelector(".nav-divider");
      var adminPageEls = [];
      pages.forEach(function (pg) {
        var a = document.createElement("a");
        a.href = "/exp/" + expName + "/ui/" + pg.file;
        a.textContent = pg.name;
        a.className = pg.admin ? "nav-lab nav-lab-admin" : "nav-lab";
        var pageName = pg.file.replace(/\.html$/, "");
        if (pageName === currentPage) a.setAttribute("aria-current", "page");
        if (pg.admin) {
          a.style.display = "none";
          adminPageEls.push(a);
        }
        linksEl.insertBefore(a, divider);
      });
      if (adminPageEls.length) {
        fetch("/api/auth-status", { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.admin) {
              adminPageEls.forEach(function (el) { el.style.display = ""; });
            }
          })
          .catch(function () {});
      }

      var sc = exp.student_count || 0;
      if (sc) document.getElementById("students-link").textContent = "Students (" + sc + ")";
      var fc = exp.function_count || 0;
      if (fc) document.getElementById("functions-link").textContent = "Functions (" + fc + ")";
    })
    .catch(function () {});

  fetch("/exp/" + expName + "/log-options")
    .then(function (r) { return r.json(); })
    .then(function (opts) {
      var lc = opts.log_count || 0;
      if (lc) document.getElementById("logs-link").textContent = "Logs (" + lc + ")";
    })
    .catch(function () {});

  // Hamburger toggle + theme toggle handled by theme-toggle.js (deferred)
})();
