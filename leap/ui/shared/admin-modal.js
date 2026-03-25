/**
 * LEAP2 Admin Modals — login and change-password overlays with backdrop blur.
 *
 * Usage: include via <script src="/static/admin-modal.js"></script>
 * Exposes: window.LEAP.showLogin(onSuccess), window.LEAP.showChangePassword()
 */
(function () {
  if (!window.LEAP) window.LEAP = {};

  // ── Inject styles ──
  var style = document.createElement("style");
  style.textContent =
    ".leap-modal-backdrop{position:fixed;inset:0;z-index:9998;background:rgba(28,25,23,0.35);opacity:0;transition:opacity .25s ease;display:none;}" +
    ".leap-modal-backdrop.open{display:block;opacity:1;}" +
    "body.leap-modal-open>*:not(.leap-modal-backdrop):not(.leap-modal){filter:blur(3px) saturate(0.7);transition:filter .25s ease;pointer-events:none;user-select:none;}" +
    "body.leap-modal-open .leap-modal-backdrop,body.leap-modal-open .leap-modal{filter:none!important;pointer-events:auto!important;user-select:auto!important;}" +
    ".leap-modal{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%) scale(0.96);z-index:9999;width:calc(100% - 2rem);max-width:400px;background:color-mix(in srgb,var(--color-surface) 65%,transparent);backdrop-filter:blur(16px) saturate(1.4);-webkit-backdrop-filter:blur(16px) saturate(1.4);border:1px solid color-mix(in srgb,var(--color-border) 60%,transparent);border-radius:var(--radius-lg);box-shadow:0 24px 48px rgba(0,0,0,0.12), 0 0 0 1px rgba(255,255,255,0.05) inset;padding:1.75rem 1.5rem;opacity:0;transition:opacity .3s ease,transform .4s cubic-bezier(0.2,0.8,0.2,1);display:none;}" +
    ".leap-modal.open{display:block;opacity:1;transform:translate(-50%,-50%) scale(1);}" +
    ".leap-modal-close{position:absolute;top:0.75rem;right:0.75rem;background:none;border:none;color:var(--color-text-muted);cursor:pointer;padding:0.25rem;border-radius:3px;display:flex;transition:color .15s;}" +
    ".leap-modal-close:hover{color:var(--color-text);}" +
    ".leap-modal-icon{display:flex;justify-content:center;margin-bottom:0.625rem;color:var(--color-primary);opacity:0.7;}" +
    ".leap-modal-title{text-align:center;font-family:var(--font-display);font-size:1.15rem;font-weight:700;margin-bottom:0.25rem;color:var(--color-text);}" +
    ".leap-modal-desc{text-align:center;font-size:0.8125rem;color:var(--color-text-muted);margin-bottom:1.25rem;}" +
    ".leap-modal .alert{padding:0.625rem 0.75rem;border-radius:var(--radius);font-size:0.8125rem;margin-bottom:0.75rem;}" +
    ".leap-modal .alert-error{background:var(--color-error-bg);color:var(--color-error);border:1px solid #fecaca;}" +
    ".leap-modal .alert-success{background:#f0fdf4;color:var(--color-success);border:1px solid #bbf7d0;}" +
    "html.dark .leap-modal .alert-error{border-color:rgba(248,113,113,0.3);}" +
    "html.dark .leap-modal .alert-success{background:rgba(110,231,160,0.08);border-color:rgba(110,231,160,0.3);}" +
    "html.dark .leap-modal-backdrop{background:rgba(10,10,10,0.55);}" +
    ".leap-modal .form-group{margin-bottom:0.875rem;}" +
    ".leap-modal .form-label{display:block;font-size:0.8125rem;font-weight:500;margin-bottom:0.25rem;color:var(--color-text);}" +
    ".leap-modal .input{width:100%;padding:0.5rem 0.625rem;font-size:0.875rem;font-family:var(--font-sans);border:1px solid var(--color-border);border-radius:var(--radius);background:var(--color-surface);color:var(--color-text);outline:none;transition:border-color var(--transition),box-shadow var(--transition);}" +
    ".leap-modal .input:focus{border-color:var(--color-primary);box-shadow:0 0 0 3px var(--color-primary-light);}" +
    ".leap-modal .pw-bar{height:3px;border-radius:2px;margin-top:0.375rem;background:var(--color-border);overflow:hidden;}" +
    ".leap-modal .pw-bar-fill{height:100%;width:0%;border-radius:2px;transition:width .3s,background .3s;}" +
    ".leap-modal .pw-hint{font-size:0.6875rem;color:var(--color-text-muted);margin-top:0.1875rem;opacity:0.7;}" +
    ".leap-modal .pw-match{font-size:0.6875rem;margin-top:0.1875rem;opacity:0;transition:opacity .2s;}" +
    ".leap-modal .pw-match.visible{opacity:1;}" +
    ".leap-modal .pw-match.ok{color:var(--color-success);}" +
    ".leap-modal .pw-match.mismatch{color:var(--color-error);}" +
    ".leap-modal .input-wrap{position:relative;}" +
    ".leap-modal .input-wrap input{padding-right:2.25rem;}" +
    ".leap-modal .pw-toggle{position:absolute;right:0.375rem;top:50%;transform:translateY(-50%);background:none;border:none;color:var(--color-text-muted);cursor:pointer;padding:0.25rem;border-radius:3px;display:flex;align-items:center;transition:color .15s;}" +
    ".leap-modal .pw-toggle:hover{color:var(--color-text);}" +
    ".leap-modal .btn{display:inline-flex;align-items:center;justify-content:center;padding:0.5rem 1.125rem;min-height:2.5rem;font-size:0.875rem;font-weight:500;font-family:var(--font-sans);border-radius:var(--radius);border:1px solid var(--color-border);background:var(--color-surface);color:var(--color-text);cursor:pointer;transition:all var(--transition);}" +
    ".leap-modal .btn-primary{background:var(--color-success, #059669);color:#fff;border-color:var(--color-success, #059669);}" +
    ".leap-modal .btn-primary:hover{opacity:0.9;}" +
    ".leap-modal .btn-primary:disabled{opacity:0.6;cursor:not-allowed;}" +
    ".leap-modal .btn-block{width:100%;}" +
    "@media(max-width:480px){.leap-modal{padding:1.25rem 1rem;}}";
  document.head.appendChild(style);

  // ── Shared helpers ──
  var backdrop = document.createElement("div");
  backdrop.className = "leap-modal-backdrop";
  document.body.appendChild(backdrop);

  var activeModal = null;

  function closeModal() {
    if (activeModal) {
      var closing = activeModal;
      closing.classList.remove("open");
      backdrop.classList.remove("open");
      document.body.classList.remove("leap-modal-open");
      if (document.activeElement) document.activeElement.blur();
      setTimeout(function () {
        if (!closing.classList.contains("open")) {
          closing.style.display = "none";
        }
      }, 250);
      activeModal = null;
    }
  }

  function openModal(el) {
    closeModal();
    activeModal = el;
    document.body.classList.add("leap-modal-open");
    backdrop.classList.add("open");
    el.style.display = "block";
    // Force reflow then animate
    void el.offsetHeight;
    el.classList.add("open");
    var firstInput = el.querySelector("input");
    if (firstInput) setTimeout(function () { firstInput.focus(); }, 80);
  }

  backdrop.addEventListener("click", closeModal);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });

  var closeSvg = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var lockSvg = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
  var keySvg = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/><circle cx="12" cy="16" r="1"/></svg>';
  var eyeSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';

  function flash(el, msg) {
    el.textContent = msg;
    el.classList.remove("hidden");
    el.style.display = "";
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.style.display = "none"; }, 5000);
  }

  function wireToggle(btn) {
    btn.addEventListener("click", function () {
      var id = btn.getAttribute("data-target");
      var input = document.getElementById(id);
      var on = input.type === "password";
      input.type = on ? "text" : "password";
      btn.style.opacity = on ? "1" : "0.5";
    });
  }

  // ── Login Modal ──
  var loginModal = document.createElement("div");
  loginModal.className = "leap-modal";
  loginModal.innerHTML =
    '<button class="leap-modal-close" aria-label="Close">' + closeSvg + "</button>" +
    '<div class="leap-modal-icon">' + lockSvg + "</div>" +
    '<div class="leap-modal-title">Admin Login</div>' +
    '<div class="leap-modal-desc">Enter the admin password for this LEAP2 instance.</div>' +
    '<div class="alert alert-error" id="lm-error" style="display:none"></div>' +
    '<form id="lm-form" autocomplete="off">' +
    '<div class="form-group">' +
    '<label class="form-label" for="lm-pw">Password</label>' +
    '<div class="input-wrap">' +
    '<input class="input" type="password" id="lm-pw" autocomplete="current-password" placeholder="Enter admin password" required>' +
    '<button type="button" class="pw-toggle" data-target="lm-pw" aria-label="Toggle visibility">' + eyeSvg + "</button>" +
    "</div></div>" +
    '<button type="submit" class="btn btn-primary btn-block" id="lm-submit">Log in</button>' +
    "</form>";
  document.body.appendChild(loginModal);

  loginModal.querySelector(".leap-modal-close").addEventListener("click", closeModal);
  wireToggle(loginModal.querySelector(".pw-toggle"));

  var loginCallback = null;

  loginModal.querySelector("#lm-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var errEl = document.getElementById("lm-error");
    var btn = document.getElementById("lm-submit");
    var pw = document.getElementById("lm-pw");
    errEl.style.display = "none";
    btn.disabled = true;
    btn.textContent = "Logging in...";
    try {
      var res = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ password: pw.value }),
      });
      if (res.ok) {
        closeModal();
        pw.value = "";
        if (loginCallback) loginCallback();
        else window.location.reload();
      } else {
        var d = {};
        try { d = await res.json(); } catch (_) {}
        flash(errEl, d.detail || "Invalid password");
      }
    } catch (err) {
      flash(errEl, "Connection error: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Log in";
    }
  });

  window.LEAP.showLogin = function (onSuccess) {
    loginCallback = onSuccess || null;
    document.getElementById("lm-pw").value = "";
    document.getElementById("lm-error").style.display = "none";
    openModal(loginModal);
  };

  // ── Change Password Modal ──
  var cpModal = document.createElement("div");
  cpModal.className = "leap-modal";
  cpModal.innerHTML =
    '<button class="leap-modal-close" aria-label="Close">' + closeSvg + "</button>" +
    '<div class="leap-modal-icon">' + keySvg + "</div>" +
    '<div class="leap-modal-title">Change Password</div>' +
    '<div class="leap-modal-desc">Update the admin password for this LEAP2 instance.</div>' +
    '<div class="alert alert-error" id="cp-error" style="display:none"></div>' +
    '<div class="alert alert-success" id="cp-success" style="display:none"></div>' +
    '<form id="cp-form" autocomplete="off">' +
    '<div class="form-group">' +
    '<label class="form-label" for="cp-cur">Current password</label>' +
    '<div class="input-wrap">' +
    '<input class="input" type="password" id="cp-cur" autocomplete="current-password" required>' +
    '<button type="button" class="pw-toggle" data-target="cp-cur" aria-label="Toggle visibility">' + eyeSvg + "</button>" +
    "</div></div>" +
    '<div class="form-group">' +
    '<label class="form-label" for="cp-new">New password</label>' +
    '<div class="input-wrap">' +
    '<input class="input" type="password" id="cp-new" autocomplete="new-password" required>' +
    '<button type="button" class="pw-toggle" data-target="cp-new" aria-label="Toggle visibility">' + eyeSvg + "</button>" +
    "</div>" +
    '<div class="pw-bar"><div class="pw-bar-fill" id="cp-str"></div></div>' +
    '<div class="pw-hint" id="cp-hint"></div>' +
    "</div>" +
    '<div class="form-group">' +
    '<label class="form-label" for="cp-confirm">Confirm new password</label>' +
    '<div class="input-wrap">' +
    '<input class="input" type="password" id="cp-confirm" autocomplete="new-password" required>' +
    '<button type="button" class="pw-toggle" data-target="cp-confirm" aria-label="Toggle visibility">' + eyeSvg + "</button>" +
    "</div>" +
    '<div class="pw-match" id="cp-match"></div>' +
    "</div>" +
    '<button type="submit" class="btn btn-primary btn-block" id="cp-submit">Update password</button>' +
    "</form>";
  document.body.appendChild(cpModal);

  cpModal.querySelector(".leap-modal-close").addEventListener("click", closeModal);
  cpModal.querySelectorAll(".pw-toggle").forEach(wireToggle);

  // Strength meter
  var strengthLabels = ["", "Weak", "Fair", "Good", "Strong"];
  var strengthColors = ["", "var(--color-error)", "var(--color-warning)", "#d4a017", "var(--color-success)"];

  function getStrength(pw) {
    var s = 0;
    if (pw.length >= 6) s++;
    if (pw.length >= 10) s++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s++;
    if (/\d/.test(pw)) s++;
    if (/[^a-zA-Z0-9]/.test(pw)) s++;
    return Math.min(4, s);
  }

  var cpNew = function () { return document.getElementById("cp-new"); };
  var cpConfirm = function () { return document.getElementById("cp-confirm"); };
  var cpStr = function () { return document.getElementById("cp-str"); };
  var cpHint = function () { return document.getElementById("cp-hint"); };
  var cpMatch = function () { return document.getElementById("cp-match"); };

  function updateStrength() {
    var v = cpNew().value;
    var lv = v.length === 0 ? 0 : getStrength(v);
    cpStr().style.width = (lv * 25) + "%";
    cpStr().style.background = strengthColors[lv] || "";
    cpHint().textContent = v.length > 0 ? strengthLabels[lv] : "";
    updateMatch();
  }

  function updateMatch() {
    var n = cpNew().value, c = cpConfirm().value;
    var el = cpMatch();
    if (!c) { el.classList.remove("visible"); return; }
    el.classList.add("visible");
    if (n === c) {
      el.textContent = "Passwords match";
      el.className = "pw-match visible ok";
    } else {
      el.textContent = "Passwords do not match";
      el.className = "pw-match visible mismatch";
    }
  }

  // Delegated input events (elements exist in DOM already)
  cpModal.addEventListener("input", function (e) {
    if (e.target.id === "cp-new") updateStrength();
    if (e.target.id === "cp-confirm") updateMatch();
  });

  cpModal.querySelector("#cp-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    var errEl = document.getElementById("cp-error");
    var okEl = document.getElementById("cp-success");
    var btn = document.getElementById("cp-submit");
    errEl.style.display = "none";
    okEl.style.display = "none";

    if (cpNew().value !== cpConfirm().value) {
      flash(errEl, "New passwords do not match.");
      return;
    }
    if (!cpNew().value.trim()) {
      flash(errEl, "New password cannot be empty.");
      return;
    }

    btn.disabled = true;
    btn.textContent = "Updating...";
    try {
      var res = await fetch("/api/admin/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          current_password: document.getElementById("cp-cur").value,
          new_password: cpNew().value,
        }),
      });
      if (res.ok) {
        cpModal.querySelector("#cp-form").reset();
        cpStr().style.width = "0%";
        cpHint().textContent = "";
        cpMatch().classList.remove("visible");
        closeModal();
      } else {
        var d = {};
        try { d = await res.json(); } catch (_) {}
        flash(errEl, d.detail || "Failed to change password.");
      }
    } catch (err) {
      flash(errEl, "Connection error: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Update password";
    }
  });

  window.LEAP.showChangePassword = function () {
    document.getElementById("cp-cur").value = "";
    cpNew().value = "";
    cpConfirm().value = "";
    document.getElementById("cp-error").style.display = "none";
    document.getElementById("cp-success").style.display = "none";
    cpStr().style.width = "0%";
    cpHint().textContent = "";
    cpMatch().classList.remove("visible");
    openModal(cpModal);
  };
})();
