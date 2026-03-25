/**
 * LEAP2 Lab Info Modal — displays lab metadata in a modal overlay.
 *
 * Usage: include via <script src="/static/lab-info-modal.js"></script>
 * Exposes: window.LEAP.showLabInfo(labData)
 *
 * Self-contained: includes all required CSS. Reuses admin-modal backdrop if present.
 */
(function () {
  if (!window.LEAP) window.LEAP = {};

  var style = document.createElement("style");
  style.textContent =
    "#leap-lab-info-backdrop{position:fixed;inset:0;z-index:9998;background:rgba(28,25,23,0.25);backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);display:none;opacity:0;transition:opacity .3s ease;}" +
    "#leap-lab-info-backdrop.open{display:block;opacity:1;}" +
    "html.dark #leap-lab-info-backdrop{background:rgba(0,0,0,0.5);}" +
    "#leap-lab-info-modal{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%) scale(0.92);z-index:9999;width:calc(100% - 2rem);max-width:440px;background:color-mix(in srgb,var(--color-surface) 30%,transparent);backdrop-filter:blur(4px) saturate(1.2);-webkit-backdrop-filter:blur(4px) saturate(1.2);border:1px solid color-mix(in srgb,var(--color-border) 60%,transparent);border-radius:var(--radius-lg);box-shadow:0 24px 48px rgba(0,0,0,0.15), 0 0 0 1px rgba(255,255,255,0.05) inset;padding:1.75rem 1.5rem;display:none;opacity:0;transition:opacity .3s ease,transform .4s cubic-bezier(0.175,0.885,0.32,1.05);}" +
    "#leap-lab-info-modal.open{display:block;opacity:1;transform:translate(-50%,-50%) scale(1);}" +
    ".lab-info-close{position:absolute;top:0.75rem;right:0.75rem;background:none;border:none;color:var(--color-text-muted);cursor:pointer;padding:0.25rem;border-radius:3px;display:flex;transition:color .15s;}" +
    ".lab-info-close:hover{color:var(--color-text);}" +
    ".lab-info-icon{display:flex;justify-content:center;margin-bottom:0.625rem;color:var(--color-primary);opacity:0.7;}" +
    ".lab-info-title{text-align:center;font-family:var(--font-display);font-size:1.15rem;font-weight:700;margin-bottom:0.25rem;color:var(--color-text);}" +
    ".lab-info-desc{text-align:center;font-size:0.8125rem;color:var(--color-text-muted);margin-bottom:1rem;}" +
    ".lab-info-row{display:flex;gap:0.5rem;align-items:baseline;padding:0.375rem 0;border-bottom:1px solid var(--color-border);font-size:0.8125rem;}" +
    ".lab-info-row:last-child{border-bottom:none;}" +
    ".lab-info-label{min-width:5.5rem;font-weight:600;color:var(--color-text-muted);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.04em;flex-shrink:0;}" +
    ".lab-info-value{color:var(--color-text);word-break:break-word;}" +
    ".lab-info-value a{color:var(--color-primary);text-decoration:none;}" +
    ".lab-info-value a:hover{text-decoration:underline;}" +
    ".lab-info-tags{display:flex;flex-wrap:wrap;gap:0.25rem;}" +
    ".lab-info-tag{font-size:0.6875rem;padding:0.125rem 0.5rem;border-radius:999px;background:var(--color-primary-light);color:var(--color-primary);font-weight:500;}" +
    "@media(max-width:480px){#leap-lab-info-modal{padding:1.25rem 1rem;}}";
  document.head.appendChild(style);

  var closeSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var infoSvg = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';

  var backdrop = document.createElement("div");
  backdrop.id = "leap-lab-info-backdrop";
  document.body.appendChild(backdrop);

  var modal = document.createElement("div");
  modal.id = "leap-lab-info-modal";
  document.body.appendChild(modal);

  function close() {
    modal.classList.remove("open");
    backdrop.classList.remove("open");
    setTimeout(function () {
      if (!modal.classList.contains("open")) modal.style.display = "none";
      if (!backdrop.classList.contains("open")) backdrop.style.display = "none";
    }, 250);
  }

  function open() {
    backdrop.style.display = "block";
    modal.style.display = "block";
    void modal.offsetHeight;
    backdrop.classList.add("open");
    modal.classList.add("open");
  }

  backdrop.addEventListener("click", close);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal.classList.contains("open")) close();
  });

  function row(label, value) {
    if (!value) return "";
    return '<div class="lab-info-row"><span class="lab-info-label">' + label + '</span><span class="lab-info-value">' + value + '</span></div>';
  }

  window.LEAP.showLabInfo = function (lab) {
    if (!lab) return;

    var title = lab.display_name || lab.name || "Lab Info";
    var repoHtml = "";
    if (lab.repository) {
      var short = lab.repository.replace(/^https?:\/\//, "").replace(/\.git$/, "");
      repoHtml = '<a href="' + lab.repository + '" target="_blank" rel="noopener">' + short + '</a>';
    }

    var tagsHtml = "";
    if (lab.tags && lab.tags.length) {
      tagsHtml = '<div class="lab-info-tags">' +
        lab.tags.map(function (t) { return '<span class="lab-info-tag">' + t + '</span>'; }).join("") +
        '</div>';
    }

    var labIcons = Array.isArray(lab.icons) ? lab.icons : (lab.icons ? [lab.icons] : []);
    var iconHtml = labIcons.length > 0
      ? '<div class="lab-info-icon" style="display:flex;gap:0.5rem;align-items:center;">' + labIcons.map(function(ic) { return '<img src="' + ic + '" alt="" style="max-width:48px;max-height:48px;object-fit:contain;border-radius:6px;">'; }).join('') + '</div>'
      : '<div class="lab-info-icon">' + infoSvg + '</div>';

    var authorsStr = Array.isArray(lab.authors) ? lab.authors.join(", ") : (lab.authors || "");
    var orgsStr = Array.isArray(lab.organizations) ? lab.organizations.join(", ") : (lab.organizations || "");

    modal.innerHTML =
      '<button class="lab-info-close" aria-label="Close">' + closeSvg + '</button>' +
      iconHtml +
      '<div class="lab-info-title">' + title + '</div>' +
      (lab.description ? '<div class="lab-info-desc">' + lab.description + '</div>' : '') +
      '<div>' +
      row("Authors", authorsStr) +
      row("Organizations", orgsStr) +
      row("Repository", repoHtml) +
      (tagsHtml ? '<div class="lab-info-row"><span class="lab-info-label">Tags</span><span class="lab-info-value">' + tagsHtml + '</span></div>' : '') +
      '</div>';

    modal.querySelector(".lab-info-close").addEventListener("click", close);
    open();
  };
})();
