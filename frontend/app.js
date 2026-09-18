// CIOS frontend — vanilla JS, same-origin API calls.
// CSP-safe: koi inline handler nahi, sab addEventListener. Zero dependencies.
// v2: poora app ek hi API wrapper (window.CIOSApi.api) se backend ko call karta
// hai — B/D studios ko bhi yahi use karna hai, raw network calls mana hain.
"use strict";

/* ============================== tiny utils ============================== */
const $ = id => document.getElementById(id);
const TOKEN_KEY = "cios_token";

function uuid() {
  if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === "x" ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

function sleep(ms) { return new Promise(res => setTimeout(res, ms)); }

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: null,          // {id, email, is_admin}
  authRequired: true,  // /api/v1/auth/me 404 de to false (AUTH_REQUIRED=false mode)
  lastResult: {}       // feature -> {inputs, data, model, cached, at, requestId} (PDF reports)
};

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

// ---------- generic renderer: objects -> key/value, arrays -> bullets, strings -> text ----------
function render(val) {
  if (val === null || val === undefined) return "";
  if (typeof val === "string") return esc(val).replace(/\n/g, "<br>");
  if (Array.isArray(val)) {
    if (!val.length) return "<i>—</i>";
    return "<ul>" + val.map(v => "<li>" + render(v) + "</li>").join("") + "</ul>";
  }
  if (typeof val === "object") {
    return Object.entries(val).map(([k, v]) =>
      `<div><span class="k">${esc(k)}:</span> ${render(v)}</div>`).join("");
  }
  return esc(val);
}

/* ======================= Roman Urdu message catalog ======================= */
const MSGS = {
  network: "🌐 Server se connect nahi ho raha — internet check karo ya thori dair baad try karo.",
  timeout: (sec, spendsQuota) =>
    "⏱️ " + sec + "s me jawab nahi aaya (model busy ho sakta hai). Dobara try karo" +
    (spendsQuota ? " — yaad rahe har naya try 1 quota kharch karta hai." : "."),
  server5xx: rid => "🔧 Server me masla ho gaya. Ye ID support ko bhejo: " + rid,
  quota429: backendMsg => backendMsg + " Kal phir try karo.",
  auth401: "🔑 Session khatam ho gayi — dobara login karo.",
  forbidden403: "⛔ Ijazat nahi hai.",
  cancelled: "⏹️ Cancel kar diya."
  // throttle429: backend message verbatim (neeche messageFor me)
  // validation422: friendly422() (neeche)
};

/* ================================ toasts ================================== */
// #toast-stack: fixed bottom-right, max 4 stacked (purana auto-dismiss),
// click-to-dismiss, error toasts par role="alert". Kabhi silent nahi.
const TOAST_TTL = {error: 9000, warn: 7000, success: 5000, info: 6000};
const TOAST_MAX = 4;

function toast(type, msg, opts) {
  opts = opts || {};
  const stack = $("toast-stack");
  if (!stack) return;
  while (stack.children.length >= TOAST_MAX) stack.firstChild.remove();
  const el = document.createElement("div");
  el.className = "toast toast-" + type;
  if (type === "error") el.setAttribute("role", "alert");

  const m = document.createElement("div");
  m.className = "tmsg";
  m.textContent = msg;
  el.appendChild(m);

  if (opts.requestId && opts.requestId !== "n/a") {
    const r = document.createElement("div");
    r.className = "trid";
    r.textContent = "ID: " + opts.requestId + " — support ke liye ye ID bhejo";
    el.appendChild(r);
  }
  if (typeof opts.retry === "function") {
    // POST kabhi auto-retry nahi — user khud dabaye to hi dobara try.
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tretry";
    b.textContent = "Dobara try karo";
    b.addEventListener("click", ev => { ev.stopPropagation(); dismiss(); opts.retry(); });
    el.appendChild(b);
  }
  let t = null;
  function dismiss() { if (t) clearTimeout(t); el.remove(); }
  el.addEventListener("click", ev => { if (!ev.target.closest(".tretry")) dismiss(); });
  t = setTimeout(dismiss, TOAST_TTL[type] || 6000);
  stack.appendChild(el);
}

/* ============================ unified API client =========================== */
// window.CIOSApi.api(path, opts)
// opts: {method='GET', body=null, formData=null, timeoutMs, formKey, idempotency=true}
// - Timeout: POST 125s, GET 30s (AbortController). Cancel button foran abort.
// - Retry: POST KABHI auto-retry nahi (har attempt quota kharch karta hai).
//   GET: max 1 retry, SIRF network-level throw par (koi HTTP response nahi aaya).
//   HTTP status par kabhi retry nahi, AbortError par kabhi retry nahi.
// - Har response se X-Request-ID parho; errors me attach karo.
// - Success -> {body, data, model, cached, requestId, status}; data = j.data.
// - Error -> ApiError{kind, status, message, requestId} throw hota hai.
class ApiError extends Error {
  constructor(kind, status, message, requestId) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;      // network|timeout|server5xx|quota429|throttle429|
                           // auth401|forbidden403|validation422|cancelled|busy|error
    this.status = status;  // HTTP status; 0 = koi response nahi aaya
    this.requestId = requestId || "n/a";
  }
}

// In-flight registry: formKey -> {ctrl, cancelled} — cancel + double-submit guard.
// Har quota-spending POST ka apna formKey hai; doosri submit pending rehte ignore.
const inflight = {};

function cancelRequest(formKey) {
  const entry = inflight[formKey];
  if (entry) {
    entry.cancelled = true;
    if (entry.ctrl) entry.ctrl.abort();
  }
}

function kindFromStatus(status, j) {
  if (status === 401) return "auth401";
  if (status === 403) return "forbidden403";
  if (status === 422) return "validation422";
  if (status === 429) {
    const m = (j && j.error) || "";
    return /bohat zyada requests/i.test(m) ? "throttle429" : "quota429";
  }
  if (status >= 500) return "server5xx";
  return "error";
}

function messageFor(kind, status, j, rid, spendsQuota) {
  const backendMsg = (j && typeof j.error === "string" && j.error) || ("HTTP " + status);
  switch (kind) {
    case "auth401": return MSGS.auth401;
    case "forbidden403": return MSGS.forbidden403;
    case "validation422": return friendly422(j && j.detail);
    case "quota429": return MSGS.quota429(backendMsg);
    case "throttle429": return backendMsg; // backend message verbatim
    case "server5xx": return MSGS.server5xx(rid);
    default: return backendMsg;
  }
}

async function api(path, opts) {
  opts = opts || {};
  const method = (opts.method || "GET").toUpperCase();
  const isPost = method === "POST";
  const formKey = opts.formKey || null;
  const timeoutMs = opts.timeoutMs || (isPost ? 125000 : 30000);

  if (formKey && inflight[formKey]) {
    throw new ApiError("busy", 0, "Pehli request abhi chal rahi hai.", "n/a");
  }
  const entry = {ctrl: null, cancelled: false};
  if (formKey) inflight[formKey] = entry;

  const headers = {};
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (isPost && opts.idempotency !== false) headers["X-Idempotency-Key"] = uuid();

  const initBase = {method: method, headers: headers};
  if (opts.formData) {
    initBase.body = opts.formData; // multipart: browser khud Content-Type lagata hai
  } else {
    headers["Content-Type"] = "application/json";
    if (opts.body != null) initBase.body = JSON.stringify(opts.body);
  }

  try {
    // RETRY-POLICY: GET only, max 1 retry, network-level throws only.
    // POST kabhi auto-retry nahi — har attempt 1 quota kharch karta hai.
    // HTTP status (429/5xx sameet) par kabhi retry nahi — server faisla kar chuka.
    for (let attempt = 0; attempt < 2; attempt++) {
      if (entry.cancelled) throw new ApiError("cancelled", 0, MSGS.cancelled, "n/a");
      const ctrl = new AbortController();
      entry.ctrl = ctrl;
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
      try {
        const r = await fetch(path, {method: initBase.method, headers: initBase.headers,
                                     body: initBase.body, signal: ctrl.signal});
        clearTimeout(timer);
        const rid = r.headers.get("X-Request-ID") || "n/a";
        let j = null;
        try { j = await r.json(); } catch (e) { /* non-JSON body */ }
        if (r.ok && j && j.ok !== false) {
          // CONTRACT (B/D ke liye): result.data hamesha response ka `data` field
          // hai (§A3 ke "j.data" ke barabar). body/model/cached/requestId/status
          // sirf metadata hain — B/D studios result.data use karein.
          return {body: j, data: j.data, model: j.model, cached: j.cached,
                  requestId: rid, status: r.status};
        }
        const kind = kindFromStatus(r.status, j);
        throw new ApiError(kind, r.status, messageFor(kind, r.status, j, rid, isPost), rid);
      } catch (err) {
        clearTimeout(timer);
        if (err instanceof ApiError) throw err; // HTTP response aaya tha — retry kabhi nahi
        if (err && err.name === "AbortError") {
          throw timedOut
            ? new ApiError("timeout", 0, MSGS.timeout(Math.round(timeoutMs / 1000), isPost), "n/a")
            : new ApiError("cancelled", 0, MSGS.cancelled, "n/a");
        }
        // Yahan pohanchna = network-level throw (koi HTTP response nahi).
        if (method === "GET" && attempt === 0 && !entry.cancelled) {
          await sleep(1500 + Math.random() * 500); // backoff + jitter, sirf ek baar
          continue;
        }
        throw new ApiError("network", 0, MSGS.network, "n/a");
      }
    }
    throw new ApiError("network", 0, MSGS.network, "n/a");
  } finally {
    if (formKey && inflight[formKey] === entry) delete inflight[formKey];
  }
}

window.CIOSApi = {api: api, toast: toast, MSGS: MSGS, ApiError: ApiError,
                  kindFromStatus: kindFromStatus, cancelRequest: cancelRequest};

/* ===================== button state machine (§A2) ===================== */
// setBtn(btn, state): "default" | "loading" | "success"
// - loading: disabled + aria-disabled + "⟳ <label>…" (min-width lock: layout shift nahi)
// - success: "✅ ho gaya" 1.2s (non-AI actions: quota update waghera), phir restore
// - hover/active/focus-visible CSS me (styles.css). B/D window.CIOSApp.setBtn reuse karein.
function setBtn(btn, stateName) {
  if (!btn) return;
  if (stateName === "loading") {
    if (!btn.dataset.origLabel) btn.dataset.origLabel = btn.textContent;
    btn.disabled = true;
    btn.setAttribute("aria-disabled", "true");
    if (btn.dataset.origMinWidth === undefined) {
      btn.dataset.origMinWidth = btn.style.minWidth || "";
      btn.style.minWidth = btn.offsetWidth + "px";
    }
    btn.textContent = "⟳ " + btn.dataset.origLabel + "…";
  } else if (stateName === "success") {
    if (!btn.dataset.origLabel) btn.dataset.origLabel = btn.textContent;
    btn.textContent = "✅ ho gaya";
    setTimeout(() => setBtn(btn, "default"), 1200);
  } else { // "default"
    if (btn.dataset.origLabel) { btn.textContent = btn.dataset.origLabel; delete btn.dataset.origLabel; }
    if (btn.dataset.origMinWidth !== undefined) { btn.style.minWidth = btn.dataset.origMinWidth; delete btn.dataset.origMinWidth; }
    btn.disabled = false;
    btn.removeAttribute("aria-disabled");
  }
}

/* =========================== friendly 422 parsing ========================== */
const FIELD_LABELS = {
  niche: "Niche", audience: "Audience", count: "Kitne ideas", examples: "Examples",
  topic: "Topic", duration_sec: "Duration (sec)", title: "Title",
  email: "Email", password: "Password", user_id: "User ID", cap: "Cap", day: "Day"
};

function friendlyMsg(msg) {
  // FastAPI/pydantic ke common English messages -> Roman Urdu
  let m = String(msg || "");
  let num;
  if (m === "Field required") return "ye field zaroori hai";
  if ((num = m.match(/greater than or equal to (\d+)/))) return "kam az kam " + num[1] + " hona chahiye";
  if ((num = m.match(/less than or equal to (\d+)/))) return "zyada se zyada " + num[1] + " ho sakta hai";
  if ((num = m.match(/at least (\d+) characters?/))) return "kam az kam " + num[1] + " characters likho";
  if ((num = m.match(/at most (\d+) characters?/))) return "zyada se zyada " + num[1] + " characters";
  if (/valid email/i.test(m)) return "sahi email address likho";
  if (/valid integer/i.test(m)) return "poora number likho";
  return m;
}

function friendly422(detail) {
  if (!Array.isArray(detail) || !detail.length) return "Kuch fields ghalat hain — check karo.";
  return detail.map(d => {
    const raw = (Array.isArray(d.loc) && d.loc.length) ? String(d.loc[d.loc.length - 1]) : "";
    const label = FIELD_LABELS[raw] || raw || "field";
    return label + ": " + friendlyMsg(d.msg);
  }).join(" • ");
}

/* ==================== inline field errors / output helpers ================== */
function setFieldErr(baseId, msg) {
  const el = $(baseId + "-err");
  if (el) el.textContent = msg || "";
  return !msg;
}

function setLoading(out, on, msg) {
  if (on) {
    out.classList.add("loading");
    out.textContent = msg || "⏳ soch raha hai…";
  } else {
    out.classList.remove("loading");
  }
}

function showErr(out, msg) {
  out.classList.remove("loading");
  out.innerHTML = `<span class="err">${esc(msg)}</span>`;
}

// ---------- missing API key setup box ----------
function showKeySetup() { $("key-setup").hidden = false; }

function looksLikeKeyMissing(err) {
  return err && err.status === 400 && typeof err.message === "string" &&
    /api.?key|openrouter|key_configured/i.test(err.message);
}

/* ================================== auth =================================== */
function showAuth(msg) {
  $("app-view").hidden = true;
  $("auth-view").hidden = false;
  if (msg) setFieldErr("login-form", msg);
}

function enterApp(user) {
  state.user = user || null;
  $("auth-view").hidden = true;
  $("app-view").hidden = false;
  const isAdmin = !!(user && user.is_admin);
  $("nav-admin").hidden = !isAdmin;
  $("admin-badge").hidden = !isAdmin;
  refreshUsage();
  loadHistory(true); // history tab ka pehla load; tab click pe dobara nahi
}

function handle401() {
  // Token invalid/expire -> login par wapas + toast (MSGS catalog).
  logout();
  showAuth(MSGS.auth401);
  toast("error", MSGS.auth401);
}

async function restoreSession() {
  // Session restore: /api/v1/auth/me. 404 = purana backend / no-auth mode.
  try {
    const res = await api("/api/v1/auth/me");
    enterApp((res.data && res.data.user) || null);
  } catch (e) {
    if (e && e.status === 404) { state.authRequired = false; enterApp(null); return; }
    if (e && e.kind === "auth401") {
      state.token = ""; localStorage.removeItem(TOKEN_KEY);
      showAuth(MSGS.auth401);
      return;
    }
    showAuth(e && e.kind === "network" ? MSGS.network : (e && e.message) || "");
  }
}

function wireAuthTabs() {
  const loginTab = $("authtab-login"), signupTab = $("authtab-signup");
  const loginForm = $("login-form"), signupForm = $("signup-form");
  function select(which) {
    const isLogin = which === "login";
    loginTab.classList.toggle("active", isLogin);
    signupTab.classList.toggle("active", !isLogin);
    loginTab.setAttribute("aria-selected", String(isLogin));
    signupTab.setAttribute("aria-selected", String(!isLogin));
    loginForm.hidden = !isLogin;
    signupForm.hidden = isLogin;
  }
  loginTab.addEventListener("click", () => select("login"));
  signupTab.addEventListener("click", () => select("signup"));
}

async function doAuth(mode) {
  // mode: "login" | "signup". v2 envelope: {ok, data:{token, user}}.
  const email = $(mode + "-email").value.trim();
  const pass = $(mode + "-pass").value;
  setFieldErr(mode + "-email", ""); setFieldErr(mode + "-pass", ""); setFieldErr(mode + "-form", "");
  let ok = true;
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { setFieldErr(mode + "-email", "Sahi email likho"); ok = false; }
  // F-2: backend 8 characters mangta hai — client bhi 8 check kare (signup par).
  if (mode === "signup" && pass.length < 8) { setFieldErr(mode + "-pass", "Password kam az kam 8 characters"); ok = false; }
  if (mode === "login" && !pass.length) { setFieldErr(mode + "-pass", "Password likho"); ok = false; }
  if (!ok) return;
  const btn = $(mode + "-btn");
  setBtn(btn, "loading");
  try {
    const res = await api("/api/v1/auth/" + mode, {
      method: "POST", body: {email: email, password: pass}, formKey: "auth-" + mode
    });
    state.token = res.data.token;
    localStorage.setItem(TOKEN_KEY, res.data.token);
    enterApp(res.data.user);
    toast("success", mode === "signup" ? "✅ Account ban gaya — khush aamdeed!" : "✅ Login ho gaya!");
  } catch (e) {
    if (e && e.kind !== "busy") {
      setFieldErr(mode + "-form", e.message);
      if (e.kind !== "validation422") toast("error", e.message, {requestId: e.requestId});
    }
  } finally {
    setBtn(btn, "default");
  }
}

function logout() {
  state.token = ""; state.user = null;
  localStorage.removeItem(TOKEN_KEY);
  ["login-email", "login-pass", "signup-email", "signup-pass"].forEach(id => { $(id).value = ""; });
  setFieldErr("login-form", "");
  showAuth("");
}

/* ============================ main API caller ============================= */
function wireCancelButtons() {
  document.querySelectorAll("[data-cancel]").forEach(c => {
    c.addEventListener("click", () => cancelRequest(c.getAttribute("data-cancel")));
  });
}

// feature: "ideas"|"research"|"scripts"|"packaging"|"seo"|"niche"
// inputs: {Label: value} — PDF report ke liye.
async function callApi(feature, path, body, inputs, outId, btnId) {
  const out = $(outId), btn = $(btnId);
  const cancelBtn = document.querySelector(`[data-cancel="${feature}"]`);
  const printBtn = $("print-" + feature);
  setBtn(btn, "loading");
  if (cancelBtn) cancelBtn.hidden = false;
  setLoading(out, true);

  const retry = () => callApi(feature, path, body, inputs, outId, btnId);
  try {
    const res = await api(path, {method: "POST", body: body, formKey: feature});
    const meta = `<div class="meta">model: ${esc(res.model || "?")}${res.cached ? " • cache se (quota bacha)" : ""}</div>`;
    setLoading(out, false);
    out.innerHTML = meta + render(res.data);
    // PDF report: fresh result save (date, inputs, outputs, request id)
    state.lastResult[feature] = {
      inputs: inputs, data: res.data, model: res.model,
      cached: !!res.cached, at: new Date().toISOString(), requestId: res.requestId
    };
    if (printBtn) { printBtn.disabled = false; printBtn.title = "Report print / PDF karo"; }
  } catch (e) {
    if (!e || e.kind === "busy") return; // double-submit guard: khamoshi se ignore
    if (e.kind === "cancelled") {
      // F-4 fix: accounting attempt-based hai (kamyab ho ya na ho, try = 1 quota).
      showErr(out, "⏹️ Cancel kar diya — yaad rahe quota har try pe kharch hota hai, kamyab ho ya na ho.");
      return; // silent-ish: inline note, error toast nahi
    }
    if (e.kind === "auth401") { handle401(); return; }
    showErr(out, (e.kind === "validation422" ? "⚠️ " : "Error: ") + e.message);
    if (looksLikeKeyMissing(e)) showKeySetup();
    // POST failure -> manual "Dobara try karo" wala error toast (auto-retry KABHI nahi).
    toast("error", e.message, {requestId: e.requestId, retry: retry});
  } finally {
    setBtn(btn, "default");
    if (cancelBtn) cancelBtn.hidden = true;
  }
  refreshUsage();
}

/* ================= client-side validation (backend ranges) ================= */
const v = id => $(id).value.trim();

function checkLen(baseId, val, min, max, label) {
  if (val.length < min) return setFieldErr(baseId, label + " kam az kam " + min + " characters");
  if (val.length > max) return setFieldErr(baseId, label + " zyada se zyada " + max + " characters");
  return setFieldErr(baseId, "");
}

function checkInt(baseId, raw, min, max, label) {
  const n = Number(raw);
  if (!Number.isInteger(n)) return setFieldErr(baseId, label + " me poora number likho");
  if (n < min || n > max) return setFieldErr(baseId, label + " " + min + "–" + max + " ke darmiyan");
  setFieldErr(baseId, "");
  return n;
}

function runIdeas() {
  const niche = v("ideas-niche"), audience = v("ideas-audience") || "general", countRaw = v("ideas-count");
  let ok = true;
  ok = checkLen("ideas-niche", niche, 2, 200, "Niche") && ok;
  ok = checkLen("ideas-audience", audience, 0, 200, "Audience") && ok;
  const count = checkInt("ideas-count", countRaw, 3, 20, "Kitne ideas");
  if (!ok || count === false) return;
  callApi("ideas", "/api/v1/ideas", {niche, audience, count},
          {Niche: niche, Audience: audience, "Kitne ideas": String(count)},
          "out-ideas", "btn-ideas");
}

function runResearch() {
  const niche = v("research-niche"), examples = v("research-examples");
  let ok = true;
  ok = checkLen("research-niche", niche, 2, 200, "Niche") && ok;
  ok = checkLen("research-examples", examples, 0, 4000, "Examples") && ok;
  if (!ok) return;
  callApi("research", "/api/v1/research", {niche, examples},
          {Niche: niche, "Sample data": examples || "—"},
          "out-research", "btn-research");
}

function runScript() {
  const topic = v("script-topic"), audience = v("script-aud") || "general", durRaw = v("script-dur");
  let ok = true;
  ok = checkLen("script-topic", topic, 3, 300, "Topic") && ok;
  ok = checkLen("script-aud", audience, 0, 200, "Audience") && ok;
  const dur = checkInt("script-dur", durRaw, 15, 1800, "Duration");
  if (!ok || dur === false) return;
  callApi("scripts", "/api/v1/scripts", {topic, duration_sec: dur, audience},
          {Topic: topic, "Duration (sec)": String(dur), Audience: audience},
          "out-scripts", "btn-scripts");
}

function runPackaging() {
  const topic = v("pack-topic"), audience = v("pack-aud") || "general";
  let ok = true;
  ok = checkLen("pack-topic", topic, 3, 300, "Topic") && ok;
  ok = checkLen("pack-aud", audience, 0, 200, "Audience") && ok;
  if (!ok) return;
  callApi("packaging", "/api/v1/packaging", {topic, audience},
          {Topic: topic, Audience: audience},
          "out-packaging", "btn-packaging");
}

function runSeo() {
  const title = v("seo-title"), topic = v("seo-topic");
  let ok = true;
  ok = checkLen("seo-title", title, 3, 200, "Title") && ok;
  ok = checkLen("seo-topic", topic, 3, 300, "Topic") && ok;
  if (!ok) return;
  callApi("seo", "/api/v1/seo", {title, topic},
          {Title: title, Topic: topic},
          "out-seo", "btn-seo");
}

function runNiche() {
  const niche = v("niche-niche");
  if (!checkLen("niche-niche", niche, 2, 200, "Niche")) return;
  callApi("niche", "/api/v1/niche", {niche},
          {Niche: niche},
          "out-niche", "btn-niche");
}

/* ============================ history (caller ki apni) ====================== */
let historyLoaded = false;

async function loadHistory(force) {
  if (historyLoaded && !force) return; // har tab click pe refetch nahi
  const out = $("out-history");
  setLoading(out, true, "⏳ load ho raha hai…");
  try {
    const res = await api("/api/v1/history?limit=20");
    const items = (res.data && res.data.items) || [];
    setLoading(out, false);
    if (!items.length) { out.innerHTML = "<i>Abhi koi history nahi.</i>"; historyLoaded = true; return; }
    out.innerHTML = items.map(h => {
      const d = h.created_at ? new Date(h.created_at * 1000).toLocaleString() : "";
      const inp = typeof h.input_json === "string" ? h.input_json : JSON.stringify(h.input_json || "");
      return `<div class="hist"><b>${esc(h.feature || "")}</b> <span class="meta">${esc(d)} • ${esc(h.model || "")}</span><br>${esc(inp.slice(0, 140))}…</div>`;
    }).join("");
    historyLoaded = true;
  } catch (e) {
    if (e && e.kind === "auth401") { handle401(); return; }
    if (!e || e.kind === "busy") return;
    showErr(out, e.message);
    toast("error", e.message, {requestId: e.requestId});
  }
}

/* ============================== usage pill ================================ */
async function refreshUsage() {
  const pill = $("usage");
  try {
    const res = await api("/api/v1/usage");
    const used = res.data.used, cap = res.data.cap || 0;
    pill.textContent = `⚡ ${used}/${cap} free requests (aaj)`;
    pill.classList.toggle("warn", cap > 0 && used / cap >= 0.8); // ≥80% amber
  } catch (e) {
    // silent failure ki jagah retry text (401 yahan logout nahi karta)
    pill.textContent = "⚡ retry karo";
    pill.classList.remove("warn");
  }
}

/* ================================== admin ================================== */
function adminTable(rows) {
  if (!rows.length) return "<i>Kuch nahi mila.</i>";
  const cols = Object.keys(rows[0]);
  return `<table class="admintable"><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c] == null ? "" : r[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

async function loadAdminUsers() {
  const out = $("out-admin-users"), btn = $("admin-users-btn");
  setBtn(btn, "loading");
  setLoading(out, true, "⏳ load ho raha hai…");
  try {
    const res = await api("/api/v1/admin/users", {formKey: "admin-users"});
    setLoading(out, false);
    out.innerHTML = adminTable((res.data && res.data.users) || []);
  } catch (e) {
    if (e && e.kind === "auth401") { handle401(); return; }
    if (!e || e.kind === "busy") { setBtn(btn, "default"); return; }
    if (e.kind === "forbidden403") showErr(out, "⛔ Sirf admin dekh sakta hai.");
    else { showErr(out, "Error: " + e.message); toast("error", e.message, {requestId: e.requestId}); }
  } finally {
    setBtn(btn, "default");
  }
}

async function loadAdminUsage() {
  const day = $("admin-day").value;
  setFieldErr("admin-day", "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) { setFieldErr("admin-day", "Date chuno (YYYY-MM-DD)"); return; }
  const out = $("out-admin-usage"), btn = $("admin-usage-btn");
  setBtn(btn, "loading");
  setLoading(out, true, "⏳ load ho raha hai…");
  try {
    const res = await api("/api/v1/admin/usage?day=" + encodeURIComponent(day), {formKey: "admin-usage"});
    setLoading(out, false);
    out.innerHTML = adminTable((res.data && res.data.rows) || []);
  } catch (e) {
    if (e && e.kind === "auth401") { handle401(); return; }
    if (!e || e.kind === "busy") { setBtn(btn, "default"); return; }
    if (e.kind === "forbidden403") showErr(out, "⛔ Sirf admin dekh sakta hai.");
    else { showErr(out, "Error: " + e.message); toast("error", e.message, {requestId: e.requestId}); }
  } finally {
    setBtn(btn, "default");
  }
}

async function updateQuota() {
  const userId = v("quota-user"), capRaw = v("quota-cap");
  setFieldErr("quota-form", "");
  let ok = true;
  ok = setFieldErr("quota-user", userId ? "" : "User ID likho") && ok;
  const cap = checkInt("quota-cap", capRaw, 1, 10000, "Cap");
  if (!ok || cap === false) return;
  const out = $("out-quota"), btn = $("btn-quota");
  setBtn(btn, "loading");
  setLoading(out, true, "⏳ update ho raha hai…");
  try {
    const res = await api("/api/v1/admin/quota", {
      method: "POST", body: {user_id: userId, cap: cap}, formKey: "admin-quota"
    });
    setLoading(out, false);
    out.innerHTML = `<span class="okmsg">✅ Quota update ho gaya — user <b>${esc(userId)}</b> ka naya cap: <b>${esc(res.data.cap)}</b></span>`;
    setBtn(btn, "success"); // non-AI action: success-flash, phir auto-restore
  } catch (e) {
    if (e && e.kind === "auth401") { handle401(); return; }
    if (!e || e.kind === "busy") { setBtn(btn, "default"); return; }
    if (e.kind === "forbidden403") showErr(out, "⛔ Sirf admin kar sakta hai.");
    else { showErr(out, "Error: " + e.message); toast("error", e.message, {requestId: e.requestId}); }
    setBtn(btn, "default");
  }
}

/* ============================ PDF reports (§A4) ============================ */
// Print-optimized report view + @media print + window.print().
// Koi server lib nahi — browser ka native "Save as PDF" use hota hai ($0).
const FEATURE_LABELS = {
  ideas: "Idea Generator", research: "Outlier Research", scripts: "Script Writer",
  packaging: "Title + Thumbnail Pack", seo: "SEO Pack", niche: "Niche Validator"
};
const REPORT_FEATURES = ["ideas", "research", "scripts", "packaging", "seo", "niche"];

function buildReport(feature) {
  const r = state.lastResult[feature];
  if (!r) return false;
  // Report body = tab ke .out ka rendered HTML (single source of truth), .meta hata kar.
  const tmp = document.createElement("div");
  tmp.innerHTML = $("out-" + feature).innerHTML;
  const meta = tmp.querySelector(".meta");
  if (meta) meta.remove();
  const rows = Object.entries(r.inputs || {}).map(([k, val]) =>
    "<tr><th>" + esc(k) + "</th><td>" + esc(val) + "</td></tr>").join("");
  $("report-print").innerHTML =
    '<article class="report">' +
    '<header class="rep-head"><h1>CIOS Report — ' + esc(FEATURE_LABELS[feature] || feature) + "</h1>" +
    '<table class="rep-meta">' +
    "<tr><th>Date</th><td>" + esc(new Date(r.at).toLocaleString()) + "</td></tr>" +
    "<tr><th>Model</th><td>" + esc(r.model || "?") + (r.cached ? " (cache)" : "") + "</td></tr>" +
    "<tr><th>Request ID</th><td>" + esc(r.requestId || "n/a") + "</td></tr>" +
    rows +
    "</table></header>" +
    '<section class="rep-body">' + tmp.innerHTML + "</section>" +
    '<footer class="rep-foot">CIOS $0 edition • ' + esc(new Date(r.at).toLocaleString()) +
    " • Privacy: inputs OpenRouter ko bheje gaye thay (aapki key se).</footer>" +
    "</article>";
  return true;
}

function printReport(feature) {
  if (!buildReport(feature)) {
    toast("info", "Pehle result generate karo, phir PDF banao.");
    return;
  }
  if (typeof window.print !== "function") {
    toast("error", "Is browser me print available nahi hai.");
    return;
  }
  document.body.classList.add("printing");
  window.print();
}

function wirePrintButtons() {
  REPORT_FEATURES.forEach(f => {
    const b = $("print-" + f);
    if (b) b.addEventListener("click", () => printReport(f));
  });
  window.addEventListener("afterprint", () => {
    document.body.classList.remove("printing");
    const host = $("report-print");
    if (host) host.innerHTML = "";
  });
}

/* ========================= v2 studio shells (B / D) ======================== */
// Defensive: nav buttons SIRF tab dikhao jab B/D ka script load ho kar
// window.CIOSMedia / window.CIOSAvatar de de. Mount: studio.mount(el).
function openStudio(which) {
  const isMedia = which === "media";
  const studio = isMedia ? window.CIOSMedia : window.CIOSAvatar;
  const el = $(isMedia ? "studio-media" : "studio-avatar");
  if (!studio || !el || typeof studio.mount !== "function") {
    toast("error", "Studio load nahi hua — page refresh karo.");
    return;
  }
  $("app-view").hidden = true;
  el.hidden = false;
  try {
    studio.mount(el);
  } catch (e) {
    toast("error", "Studio load nahi hua — page refresh karo.");
    el.hidden = true;
    $("app-view").hidden = false;
  }
}

function closeStudio(which) {
  // B/D apne shell me "← Wapas" button render karke ye call karte hain.
  const isMedia = which !== "avatar";
  const studio = isMedia ? window.CIOSMedia : window.CIOSAvatar;
  const el = $(isMedia ? "studio-media" : "studio-avatar");
  try { if (studio && typeof studio.unmount === "function") studio.unmount(); } catch (e) {}
  if (el) el.hidden = true;
  $("app-view").hidden = false;
}

function wireStudios() {
  if (window.CIOSMedia) {
    $("nav-media").hidden = false;
    $("nav-media").addEventListener("click", () => openStudio("media"));
  }
  if (window.CIOSAvatar) {
    $("nav-avatar").hidden = false;
    $("nav-avatar").addEventListener("click", () => openStudio("avatar"));
  }
}

window.CIOSApp = {setBtn: setBtn, openStudio: openStudio, closeStudio: closeStudio,
                  cancelRequest: cancelRequest, toast: toast};

/* ================================== tabs =================================== */
function wireTabs() {
  const btns = document.querySelectorAll("#tabs button[data-tab]");
  btns.forEach(b => {
    b.addEventListener("click", () => {
      btns.forEach(x => { x.classList.remove("active"); x.setAttribute("aria-selected", "false"); });
      document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      b.setAttribute("aria-selected", "true");
      $("tab-" + b.dataset.tab).classList.add("active");
    });
  });
}

/* ============================== health / footer ============================ */
async function initHealth() {
  try {
    const res = await api("/api/v1/health");
    const j = res.body;
    if (!j) return;
    const parts = [];
    if (j.version) parts.push("v" + j.version);
    if (j.models && j.models[0]) parts.push("primary model: " + j.models[0]);
    if (j.key_configured === false) {
      parts.push("⚠️ API key set nahi (.env dekho)");
      showKeySetup(); // missing-key UX
    }
    $("model").textContent = parts.join(" • ");
  } catch (e) {
    $("model").textContent = "";
  }
}

/* ================================== init =================================== */
function init() {
  wireAuthTabs();
  wireTabs();
  wireCancelButtons();
  wirePrintButtons();
  wireStudios();

  $("login-form").addEventListener("submit", e => { e.preventDefault(); doAuth("login"); });
  $("signup-form").addEventListener("submit", e => { e.preventDefault(); doAuth("signup"); });
  $("logout-btn").addEventListener("click", logout);
  $("usage").addEventListener("click", refreshUsage);

  // Enter-to-submit — har form pe submit handler
  $("form-ideas").addEventListener("submit", e => { e.preventDefault(); runIdeas(); });
  $("form-research").addEventListener("submit", e => { e.preventDefault(); runResearch(); });
  $("form-scripts").addEventListener("submit", e => { e.preventDefault(); runScript(); });
  $("form-packaging").addEventListener("submit", e => { e.preventDefault(); runPackaging(); });
  $("form-seo").addEventListener("submit", e => { e.preventDefault(); runSeo(); });
  $("form-niche").addEventListener("submit", e => { e.preventDefault(); runNiche(); });

  $("history-refresh").addEventListener("click", () => loadHistory(true));

  $("admin-users-btn").addEventListener("click", loadAdminUsers);
  $("form-admin-usage").addEventListener("submit", e => { e.preventDefault(); loadAdminUsage(); });
  $("form-quota").addEventListener("submit", e => { e.preventDefault(); updateQuota(); });

  // default day = aaj
  $("admin-day").value = new Date().toISOString().slice(0, 10);

  initHealth();
  restoreSession();
}

document.addEventListener("DOMContentLoaded", init);
