/* Avatar Studio — HeyGen connector UI (Builder D, V2-DESIGN §D5).
 *
 * Contract (§0.1): exposes window.CIOSAvatar = { mount(rootEl), unmount() }.
 * Dashboard SECTION (not full-screen). Backend calls ONLY via
 * window.CIOSApi.api — no raw fetch to our backend. Key never touches the
 * browser beyond the input field at Connect time.
 *
 * Roman Urdu UI. LIVE badges = working controls. No alert() — inline .ferr
 * + toasts only.
 */
window.CIOSAvatar = (function () {
  "use strict";

  var POLL_MS = 5000;
  var MAX_POLLS = 60; // §D3: 5s x 60 = ~5 min
  var SCRIPT_MAX = 1500;
  var PROMPT_MAX = 10000;

  var mounted = false;
  var rootEl = null;
  var timers = [];
  var els = {};
  var previewAudio = null; // ek waqt me sirf ek preview
  var previewBtn = null;
  var state = {
    hasKey: null,
    avatars: [],
    voices: [],
    selAvatar: null, // look id
    selVoice: null,  // voice_id
    history: [],
  };

  /* ---------------- helpers ---------------- */

  function api() {
    var a = window.CIOSApi && window.CIOSApi.api;
    if (!a) throw new Error("CIOSApi missing");
    return a;
  }

  function el(tag, cls, html) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    if (html != null) d.innerHTML = html;
    return d;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // Button state: prefer A's state machine, fallback local.
  function setBtn(btn, mode, label) {
    if (window.CIOSApp && typeof window.CIOSApp.setBtn === "function") {
      window.CIOSApp.setBtn(btn, mode, label);
      return;
    }
    if (mode === "loading") {
      btn.disabled = true;
      btn.dataset.orig = btn.dataset.orig || btn.textContent;
      btn.textContent = "⟳ " + (label || btn.dataset.orig) + "…";
    } else if (mode === "success") {
      btn.textContent = "✅ ho gaya";
      setTimeout(function () {
        btn.disabled = false;
        btn.textContent = btn.dataset.orig || label || "";
      }, 1200);
    } else {
      btn.disabled = false;
      if (label) btn.textContent = label;
      else if (btn.dataset.orig) btn.textContent = btn.dataset.orig;
    }
  }

  function toast(type, msg) {
    var stack = document.getElementById("toast-stack");
    if (!stack) {
      // A ka toast-stack abhi nahi — inline fallback.
      var f = el("div", "ferr", esc(msg));
      if (els.errors) els.errors.appendChild(f);
      return;
    }
    var t = el("div", "toast av-toast-" + type, esc(msg));
    t.setAttribute("role", type === "error" ? "alert" : "status");
    t.style.cursor = "pointer";
    t.addEventListener("click", function () { t.remove(); });
    stack.appendChild(t);
    while (stack.children.length > 4) stack.removeChild(stack.firstChild);
    setTimeout(function () { if (t.parentNode) t.remove(); },
               type === "error" ? 9000 : 6000);
  }

  function errText(e) {
    // Backend already Roman Urdu bhejta hai (§D4) — verbatim dikhao.
    if (e && e.message) return e.message;
    if (!e || typeof e.status === "undefined")
      return "🌐 Server se connect nahi ho raha — internet check karo.";
    var map = {
      400: "Request me masla hai — input check karo.",
      401: "🔑 HeyGen API key ghalat ya expire — Connect me dobara key dalo.",
      402: "💳 HeyGen credits khatam — app.heygen.com/billing pe top-up karo.",
      429: "⏳ Limit lag gayi — thori dair baad try karo.",
      503: "Service abhi tayyar nahi — thori dair baad try karo.",
    };
    return map[e.status] || "Kuch ghalat ho gaya — dobara try karo.";
  }

  function fmtDate(ts) {
    try { return new Date(ts * 1000).toLocaleString(); }
    catch (x) { return ""; }
  }

  function liveBadge() {
    return '<span class="av-live">LIVE</span>';
  }

  /* ---------------- key section ---------------- */

  function renderKeyRow() {
    var box = els.keyrow;
    box.innerHTML = "";
    if (state.hasKey === null) {
      box.appendChild(el("div", "av-muted", "⏳ Key status check ho rahi hai…"));
      return;
    }
    if (state.hasKey) {
      var ok = el("div", "av-keyok",
        "✅ <b>Connected</b> — key server par mehfooz hai (browser me kabhi nahi aati).");
      var dis = el("button", "ghost av-dis", "🔌 Disconnect");
      dis.type = "button";
      var armed = false;
      dis.addEventListener("click", function () {
        if (!armed) {
          armed = true;
          dis.textContent = "Pakka? Haan, disconnect karo";
          dis.classList.add("av-danger-arm");
          setTimeout(function () {
            armed = false; dis.textContent = "🔌 Disconnect";
            dis.classList.remove("av-danger-arm");
          }, 4000);
          return;
        }
        setBtn(dis, "loading", "Disconnect");
        api()("/api/v1/avatar/key", { method: "DELETE" }).then(function () {
          state.hasKey = false;
          renderAll();
          toast("success", "🔌 Key disconnect ho gayi.");
        }).catch(function (e) {
          setBtn(dis, "default");
          toast("error", errText(e));
        });
      });
      var row = el("div", "av-keyrow-inner");
      row.appendChild(ok); row.appendChild(dis);
      box.appendChild(row);
      return;
    }
    // No key → SETUP INSTRUCTIONS, not dead controls (§D5).
    var setup = el("div", "setupbox",
      "<b>🔑 HeyGen API key connect karo</b><br>" +
      "1. <b>app.heygen.com</b> kholo → login karo → <b>Settings → API</b> me jao<br>" +
      "2. Wahan se API key banao (key <code>sk_V2_…</code> se shuru hoti hai) aur neeche paste karo<br>" +
      "3. <b>Connect &amp; Verify</b> dabao — key pehle verify hogi, phir save hogi<br>" +
      "<span class=\"av-note\">Trial plan me ~5 watermarked videos/day ki limit ho sakti hai. " +
      "Video banane par aapke apne HeyGen credits use honge.</span>");
    var form = el("form", "av-keyform");
    form.setAttribute("autocomplete", "off");
    var inp = el("input");
    inp.type = "password"; inp.placeholder = "sk_V2_…";
    inp.setAttribute("aria-label", "HeyGen API key");
    inp.maxLength = 500;
    var btn = el("button", "", "🔗 Connect & Verify");
    btn.type = "submit";
    var ferr = el("div", "ferr");
    form.appendChild(inp); form.appendChild(btn); form.appendChild(ferr);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var k = inp.value.trim();
      ferr.textContent = "";
      if (k.length < 10) {
        ferr.textContent = "Key bohat chhoti hai — poori key paste karo.";
        return;
      }
      setBtn(btn, "loading", "Verify");
      api()("/api/v1/avatar/key", {
        method: "POST", body: { api_key: k }, formKey: "avatar-key",
      }).then(function (d) {
        inp.value = ""; // key turant field se hatao (Gate 4)
        if (d && d.has_key) {
          state.hasKey = true;
          setBtn(btn, "success");
          renderAll();
          toast("success", "✅ Key verify ho gayi aur save ho gayi.");
        }
      }).catch(function (e) {
        setBtn(btn, "default");
        ferr.textContent = errText(e);
      });
    });
    box.appendChild(setup); box.appendChild(form);
  }

  function refreshKey() {
    state.hasKey = null;
    renderKeyRow();
    return api()("/api/v1/avatar/key").then(function (d) {
      state.hasKey = !!(d && d.has_key);
    }).catch(function (e) {
      if (e && e.status === 503) { state.hasKey = null; }
      else { state.hasKey = false; }
      toast("error", errText(e));
    }).then(renderAll);
  }

  /* ---------------- fetch: avatars + voices ---------------- */

  function renderFetchRow() {
    var box = els.fetchrow;
    box.innerHTML = "";
    var dis = !state.hasKey;
    var hint = dis ? '<div class="av-hint">🔒 Pehle API key connect karo — phir avatars/voices load honge.</div>' : "";

    var sec = el("div", "av-sec" + (dis ? " av-disabled" : ""),
      "<h3>🎭 Avatars " + liveBadge() + "</h3>" + hint);
    var bAv = el("button", "", "🔄 Avatars lao");
    bAv.type = "button"; bAv.disabled = dis;
    bAv.addEventListener("click", function () {
      setBtn(bAv, "loading", "Avatars");
      api()("/api/v1/avatar/avatars?limit=20").then(function (d) {
        state.avatars = (d && d.looks) || [];
        setBtn(bAv, "success");
        renderAvatarGrid();
        if (!state.avatars.length) toast("info", "Koi avatar nahi mila — HeyGen dashboard check karo.");
      }).catch(function (e) {
        setBtn(bAv, "default");
        toast("error", errText(e));
      });
    });
    sec.appendChild(bAv);
    sec.appendChild(els.avatarGrid = el("div", "av-grid"));

    var sec2 = el("div", "av-sec" + (dis ? " av-disabled" : ""),
      "<h3>🎙️ Voices " + liveBadge() + "</h3>" + hint);
    var bVo = el("button", "", "🔄 Voices lao");
    bVo.type = "button"; bVo.disabled = dis;
    bVo.addEventListener("click", function () {
      setBtn(bVo, "loading", "Voices");
      api()("/api/v1/avatar/voices?limit=50").then(function (d) {
        state.voices = (d && d.voices) || [];
        setBtn(bVo, "success");
        renderVoiceList();
        if (!state.voices.length) toast("info", "Koi voice nahi mili.");
      }).catch(function (e) {
        setBtn(bVo, "default");
        toast("error", errText(e));
      });
    });
    sec2.appendChild(bVo);
    sec2.appendChild(els.voiceList = el("div", "av-voices"));

    box.appendChild(sec); box.appendChild(sec2);
    if (state.avatars.length) renderAvatarGrid();
    if (state.voices.length) renderVoiceList();
  }

  function renderAvatarGrid() {
    var g = els.avatarGrid;
    g.innerHTML = "";
    state.avatars.forEach(function (a) {
      var card = el("div", "av-card" + (state.selAvatar === a.id ? " sel" : ""));
      card.setAttribute("role", "radio");
      card.setAttribute("aria-checked", state.selAvatar === a.id ? "true" : "false");
      card.tabIndex = 0;
      var img = a.preview_image_url
        ? '<img src="' + esc(a.preview_image_url) + '" alt="" loading="lazy">'
        : '<div class="av-noimg">🖼️</div>';
      card.innerHTML = img + '<div class="av-name">' + esc(a.name || a.id) + "</div>";
      card.title = a.id;
      function pick() {
        state.selAvatar = a.id;
        renderAvatarGrid();
        if (els.avatarPick) els.avatarPick.textContent = "🎭 " + (a.name || a.id);
      }
      card.addEventListener("click", pick);
      card.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); }
      });
      g.appendChild(card);
    });
  }

  function renderVoiceList() {
    var list = els.voiceList;
    list.innerHTML = "";
    state.voices.forEach(function (v) {
      var row = el("div", "av-voice" + (state.selVoice === v.voice_id ? " sel" : ""));
      var meta = esc(v.name || v.voice_id) +
        ' <span class="av-meta">' + esc([v.language, v.gender].filter(Boolean).join(" · ")) + "</span>";
      row.innerHTML = '<div class="av-vinfo">' + meta + "</div>";
      var acts = el("div", "av-vacts");
      if (v.preview_audio_url) {
        var pv = el("button", "ghost av-pv", "▶ Suno");
        pv.type = "button";
        pv.title = "Preview suno";
        pv.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (previewBtn === pv && previewAudio) {
            previewAudio.pause(); // toggle off
            previewAudio = null; previewBtn = null;
            pv.textContent = "▶ Suno";
            return;
          }
          if (previewAudio) { // doosri preview band karo
            previewAudio.pause();
            if (previewBtn) previewBtn.textContent = "▶ Suno";
          }
          var au = new Audio(v.preview_audio_url);
          previewAudio = au; previewBtn = pv;
          pv.textContent = "⏸ Rokho";
          au.onended = function () {
            pv.textContent = "▶ Suno";
            if (previewAudio === au) { previewAudio = null; previewBtn = null; }
          };
          au.onerror = function () {
            pv.textContent = "▶ Suno";
            if (previewAudio === au) { previewAudio = null; previewBtn = null; }
            toast("error", "Preview nahi chal saka.");
          };
          au.play().catch(function () {
            pv.textContent = "▶ Suno";
            if (previewAudio === au) { previewAudio = null; previewBtn = null; }
            toast("error", "Preview nahi chal saka.");
          });
        });
        acts.appendChild(pv);
      }
      var pick = el("button", "", state.selVoice === v.voice_id ? "✅ Chuni hui" : "Chuno");
      pick.type = "button";
      pick.addEventListener("click", function () {
        state.selVoice = v.voice_id;
        renderVoiceList();
        if (els.voicePick) els.voicePick.textContent = "🎙️ " + (v.name || v.voice_id);
      });
      acts.appendChild(pick);
      row.appendChild(acts);
      list.appendChild(row);
    });
  }

  /* ---------------- create form ---------------- */

  function renderCreateForm() {
    var box = els.createform;
    box.innerHTML = "";
    var dis = !state.hasKey;

    var sec = el("div", "av-sec" + (dis ? " av-disabled" : ""),
      "<h3>🎬 Video banao " + liveBadge() + "</h3>" +
      (dis ? '<div class="av-hint">🔒 Pehle API key connect karo.</div>' : ""));
    var form = el("form", "av-form");

    var pickRow = el("div", "av-picks");
    pickRow.appendChild(els.avatarPick = el("div", "av-pick", "🎭 Avatar: <i>koi nahi chuna</i>"));
    pickRow.appendChild(els.voicePick = el("div", "av-pick", "🎙️ Voice: <i>default</i>"));
    form.appendChild(pickRow);

    var lab = el("label", "", "Script (" + SCRIPT_MAX + " characters max — credits bachat ke liye)");
    var ta = el("textarea");
    ta.maxLength = SCRIPT_MAX; ta.rows = 5;
    ta.placeholder = "Video me avatar kya bolega…";
    ta.disabled = dis;
    var cnt = el("div", "av-count", "0/" + SCRIPT_MAX);
    ta.addEventListener("input", function () { cnt.textContent = ta.value.length + "/" + SCRIPT_MAX; });
    lab.appendChild(ta);
    form.appendChild(lab); form.appendChild(cnt);

    var row2 = el("div", "av-row2");
    var eLab = el("label", "", "Engine");
    var eSel = el("select");
    eSel.disabled = dis;
    [["avatar_iv", "avatar_iv (default)"],
     ["avatar_v", "avatar_v (behtareen quality — andaza: zyada cost)"],
     ["avatar_iii", "avatar_iii (sasta — andaza: kam cost)"]
    ].forEach(function (o) {
      var op = document.createElement("option");
      op.value = o[0]; op.textContent = o[1];
      eSel.appendChild(op);
    });
    eLab.appendChild(eSel);
    var aLab = el("label", "", "Aspect ratio");
    var aSel = el("select");
    aSel.disabled = dis;
    ["16:9", "9:16"].forEach(function (r) {
      var op = document.createElement("option");
      op.value = r; op.textContent = r;
      aSel.appendChild(op);
    });
    aLab.appendChild(aSel);
    row2.appendChild(eLab); row2.appendChild(aLab);
    form.appendChild(row2);

    var cLab = el("label", "av-check", "");
    var cBox = el("input");
    cBox.type = "checkbox"; cBox.disabled = dis;
    cLab.appendChild(cBox);
    cLab.appendChild(document.createTextNode(" Captions (srt sidecar)"));
    form.appendChild(cLab);

    var tLab = el("label", "", "Title (optional)");
    var tInp = el("input");
    tInp.maxLength = 120; tInp.disabled = dis;
    tInp.placeholder = "Meri pehli avatar video";
    tLab.appendChild(tInp);
    form.appendChild(tLab);

    var ferr = el("div", "ferr");
    var sub = el("button", "", "🎬 Video banao");
    sub.type = "submit"; sub.disabled = dis;
    form.appendChild(sub); form.appendChild(ferr);
    form.appendChild(els.progress = el("div", "av-prog"));
    form.appendChild(els.preview = el("div", "av-preview"));

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      ferr.textContent = "";
      els.preview.innerHTML = "";
      var script = ta.value.trim();
      if (!state.selAvatar) { ferr.textContent = "Pehle oopar se avatar chuno."; return; }
      if (!script) { ferr.textContent = "Script khaali hai — kuch likho."; return; }
      setBtn(sub, "loading", "Video");
      var payload = {
        avatar_id: state.selAvatar,
        script: script,
        engine: eSel.value,
        aspect_ratio: aSel.value,
        caption: cBox.checked,
      };
      if (state.selVoice) payload.voice_id = state.selVoice;
      if (tInp.value.trim()) payload.title = tInp.value.trim();
      api()("/api/v1/avatar/videos", {
        method: "POST", body: payload, formKey: "avatar-create-" + Date.now(),
      }).then(function (d) {
        if (d && d.video_id) {
          pollVideo(d.video_id, sub);
        } else {
          setBtn(sub, "default");
          ferr.textContent = "Video ID nahi mila — dobara try karo.";
        }
      }).catch(function (e) {
        setBtn(sub, "default");
        ferr.textContent = errText(e);
      });
    });

    sec.appendChild(form);
    box.appendChild(sec);
  }

  function pollVideo(videoId, subBtn) {
    var n = 0;
    els.progress.innerHTML = "";
    var st = el("div", "av-status loading", "⏳ Ban rahi hai… (koshish 0/" + MAX_POLLS + ")");
    els.progress.appendChild(st);
    var t = setInterval(function () {
      n++;
      st.textContent = "⏳ Ban rahi hai… (koshish " + n + "/" + MAX_POLLS + ")";
      api()("/api/v1/avatar/videos/" + encodeURIComponent(videoId)).then(function (d) {
        var s = d && d.status;
        if (s === "completed" && d.video_url) {
          clearInterval(t);
          st.textContent = "✅ Video tayyar hai!";
          renderPreview(d);
          if (subBtn) setBtn(subBtn, "success");
          loadHistory();
        } else if (s === "failed") {
          clearInterval(t);
          st.innerHTML = "";
          st.appendChild(el("div", "ferr", esc(d.error || "Video fail ho gayi — dobara try karo.")));
          if (subBtn) setBtn(subBtn, "default");
        } else if (n >= MAX_POLLS) {
          clearInterval(t);
          st.textContent = "⏳ Video abhi bhi ban rahi hai — History me status dekho.";
          if (subBtn) setBtn(subBtn, "default");
        }
        // pending/processing/waiting → bas wait (kabhi complete misreport nahi)
      }).catch(function (e) {
        clearInterval(t);
        st.innerHTML = "";
        st.appendChild(el("div", "ferr", esc(errText(e))));
        if (subBtn) setBtn(subBtn, "default");
      });
    }, POLL_MS);
    timers.push(t);
  }

  function renderPreview(d) {
    var p = els.preview;
    p.innerHTML = "";
    var v = el("video");
    v.controls = true; v.src = d.video_url; v.className = "av-video";
    if (d.thumbnail_url) v.poster = d.thumbnail_url;
    p.appendChild(v);
    var a = el("a", "av-dl", "⬇️ MP4 download");
    a.href = d.video_url;
    a.target = "_blank"; a.rel = "noopener";
    p.appendChild(a);
  }

  /* ---------------- video agent ---------------- */

  function renderAgent() {
    var box = els.agent;
    box.innerHTML = "";
    var dis = !state.hasKey;
    var sec = el("div", "av-sec" + (dis ? " av-disabled" : ""),
      "<h3>🤖 Video Agent " + liveBadge() + "</h3>" +
      '<div class="av-note">Prompt se seedha video — mode <code>generate</code> (one-shot) fixed hai.</div>' +
      (dis ? '<div class="av-hint">🔒 Pehle API key connect karo.</div>' : ""));
    var form = el("form", "av-form");
    var lab = el("label", "", "Prompt (" + PROMPT_MAX + " characters max)");
    var ta = el("textarea");
    ta.maxLength = PROMPT_MAX; ta.rows = 4; ta.disabled = dis;
    ta.placeholder = "Ek khush-mizaaj host jo…";
    lab.appendChild(ta);
    form.appendChild(lab);
    var note = el("div", "av-note",
      "Avatar/voice override: oopar Fetch section me jo chunoge wahi use hoga (optional).");
    form.appendChild(note);
    var ferr = el("div", "ferr");
    var sub = el("button", "", "🤖 Agent se video banao");
    sub.type = "submit"; sub.disabled = dis;
    form.appendChild(sub); form.appendChild(ferr);
    var st = el("div", "av-prog");
    form.appendChild(st);

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      ferr.textContent = "";
      var prompt = ta.value.trim();
      if (!prompt) { ferr.textContent = "Prompt khaali hai — kuch likho."; return; }
      setBtn(sub, "loading", "Agent");
      var payload = { prompt: prompt };
      if (state.selVoice) payload.voice_id = state.selVoice;
      if (state.selAvatar) payload.avatar_id = state.selAvatar;
      api()("/api/v1/avatar/agents", {
        method: "POST", body: payload, formKey: "avatar-agent-" + Date.now(),
      }).then(function (d) {
        if (d && d.session_id) pollAgent(d.session_id, sub, st);
        else { setBtn(sub, "default"); ferr.textContent = "Session nahi bani — dobara try karo."; }
      }).catch(function (e) {
        setBtn(sub, "default");
        ferr.textContent = errText(e);
      });
    });

    sec.appendChild(form);
    box.appendChild(sec);
  }

  function pollAgent(sessionId, subBtn, stBox) {
    var n = 0;
    stBox.innerHTML = "";
    var st = el("div", "av-status loading", "⏳ Agent kaam kar raha hai… (koshish 0/" + MAX_POLLS + ")");
    stBox.appendChild(st);
    var t = setInterval(function () {
      n++;
      st.textContent = "⏳ Agent kaam kar raha hai… (koshish " + n + "/" + MAX_POLLS + ")";
      api()("/api/v1/avatar/agents/" + encodeURIComponent(sessionId)).then(function (d) {
        if (d && d.video_id) {
          clearInterval(t);
          st.textContent = "🎬 Video ban rahi hai…";
          // Agent ki video ready → normal video polling
          els.progress.innerHTML = "";
          els.preview.innerHTML = "";
          pollVideo(d.video_id, subBtn);
        } else if (d && d.status === "failed") {
          clearInterval(t);
          st.innerHTML = "";
          st.appendChild(el("div", "ferr", "Agent fail ho gaya — prompt badal kar dobara try karo."));
          setBtn(subBtn, "default");
        } else if (n >= MAX_POLLS) {
          clearInterval(t);
          st.textContent = "⏳ Agent abhi bhi kaam kar raha hai — History me status dekho.";
          setBtn(subBtn, "default");
        }
      }).catch(function (e) {
        clearInterval(t);
        st.innerHTML = "";
        st.appendChild(el("div", "ferr", esc(errText(e))));
        setBtn(subBtn, "default");
      });
    }, POLL_MS);
    timers.push(t);
  }

  /* ---------------- history ---------------- */

  function renderHistory() {
    var box = els.history;
    box.innerHTML = "";
    var dis = !state.hasKey;
    var sec = el("div", "av-sec" + (dis ? " av-disabled" : ""),
      "<h3>🗂️ History " + liveBadge() + "</h3>" +
      (dis ? '<div class="av-hint">🔒 Pehle API key connect karo.</div>' : ""));
    var bRef = el("button", "ghost", "🔄 Refresh");
    bRef.type = "button"; bRef.disabled = dis;
    bRef.addEventListener("click", function () { loadHistory(); });
    sec.appendChild(bRef);
    sec.appendChild(els.histList = el("div", "av-hist"));
    box.appendChild(sec);
    if (state.history.length) paintHistory();
  }

  function loadHistory() {
    if (!state.hasKey) return;
    api()("/api/v1/avatar/videos?limit=20").then(function (d) {
      state.history = (d && d.videos) || [];
      paintHistory();
    }).catch(function (e) {
      toast("error", errText(e));
    });
  }

  function paintHistory() {
    var list = els.histList;
    if (!list) return;
    list.innerHTML = "";
    if (!state.history.length) {
      list.appendChild(el("div", "av-muted", "Abhi koi video nahi — oopar se banao."));
      return;
    }
    state.history.forEach(function (v) {
      var row = el("div", "av-hrow");
      var thumb = v.thumbnail_url
        ? '<img src="' + esc(v.thumbnail_url) + '" alt="" loading="lazy" class="av-thumb">'
        : '<div class="av-thumb av-noimg">🎬</div>';
      var stTxt = { completed: "✅ Tayyar", processing: "⏳ Ban rahi", failed: "❌ Fail", deleted: "🗑️ Deleted" }[v.status] || esc(v.status || "?");
      row.innerHTML = thumb +
        '<div class="av-hinfo"><b>' + esc(v.title || v.video_id) + "</b>" +
        '<div class="av-meta">' + stTxt + " · " + esc(fmtDate(v.created_at)) + "</div></div>";
      var acts = el("div", "av-hacts");
      if (v.video_url && v.status === "completed") {
        var a = el("a", "av-dl", "⬇️");
        a.href = v.video_url; a.target = "_blank"; a.rel = "noopener";
        a.title = "Download / dekho";
        acts.appendChild(a);
      } else if (v.status === "processing") {
        var chk = el("button", "ghost", "🔄 Status");
        chk.type = "button";
        chk.title = "Abhi ka status dekho";
        chk.addEventListener("click", function () {
          setBtn(chk, "loading", "Status");
          var vid = String(v.video_id);
          if (vid.indexOf("agent:") === 0) {
            api()("/api/v1/avatar/agents/" + encodeURIComponent(v.session_id || vid.slice(6))).then(function (d) {
              setBtn(chk, "default");
              toast("info", "Agent status: " + (d.status || "?") + (d.video_id ? " — video mil gayi, Video banao section me dekho." : ""));
              loadHistory();
            }).catch(function (e) { setBtn(chk, "default"); toast("error", errText(e)); });
          } else {
            api()("/api/v1/avatar/videos/" + encodeURIComponent(vid)).then(function () {
              setBtn(chk, "success"); loadHistory();
            }).catch(function (e) { setBtn(chk, "default"); toast("error", errText(e)); });
          }
        });
        acts.appendChild(chk);
      }
      var del = el("button", "ghost av-del", "🗑️");
      del.type = "button";
      del.title = "Delete";
      var armed = false;
      del.addEventListener("click", function () {
        if (v.video_id.indexOf("agent:") === 0) {
          toast("info", "Agent session HeyGen dashboard se manage karo.");
          return;
        }
        if (!armed) {
          armed = true; del.textContent = "Pakka?";
          del.classList.add("av-danger-arm");
          setTimeout(function () { armed = false; del.textContent = "🗑️"; del.classList.remove("av-danger-arm"); }, 4000);
          return;
        }
        api()("/api/v1/avatar/videos/" + encodeURIComponent(v.video_id), { method: "DELETE" }).then(function () {
          toast("success", "🗑️ Video delete ho gayi.");
          loadHistory();
        }).catch(function (e) { toast("error", errText(e)); });
      });
      acts.appendChild(del);
      row.appendChild(acts);
      list.appendChild(row);
    });
  }

  /* ---------------- shell ---------------- */

  function renderAll() {
    if (!rootEl) return;
    renderKeyRow();
    renderFetchRow();
    renderCreateForm();
    renderAgent();
    renderHistory();
    if (state.hasKey) loadHistory();
  }

  function mount(elm) {
    if (mounted) return;
    rootEl = elm;
    rootEl.innerHTML = "";
    try { api(); }
    catch (x) {
      rootEl.appendChild(el("div", "ferr", "Studio load nahi hua — page refresh karo."));
      return;
    }

    var wrap = el("div", "av-wrap");
    // ← Wapas: app.js openStudio() #app-view hide karta hai, is liye wapas
    // ka rasta LAZMI (integration fix 2026-09-18: pehle koi back button nahi tha).
    var headrow = el("div", "av-headrow");
    headrow.appendChild(el("h2", "", "🎭 Avatar Studio"));
    var backBtn = el("button", "ghost av-back");
    backBtn.type = "button";
    backBtn.textContent = "← Wapas";
    backBtn.addEventListener("click", function () {
      if (window.CIOSApp && typeof window.CIOSApp.closeStudio === "function") {
        window.CIOSApp.closeStudio("avatar");
      } else {
        // Fallback: CIOSApp na ho to khud hi wapas.
        unmount();
        var appView = document.getElementById("app-view");
        if (appView) appView.hidden = false;
        var av = document.getElementById("studio-avatar");
        if (av) av.hidden = true;
      }
    });
    headrow.appendChild(backBtn);
    wrap.appendChild(headrow);
    // 1. Billing banner — HAMESHA sab se oopar (§D5).
    wrap.appendChild(el("div", "av-bill",
      "⚠️ <b>video banane par aapke HeyGen credits use honge</b> — HeyGen paid API hai " +
      "(trial plan me ~5 watermarked videos/day ki limit ho sakti hai). " +
      "CIOS ka $0 sirf hamari hosting par hai."));
    wrap.appendChild(els.keyrow = el("div", "av-block"));
    wrap.appendChild(els.errors = el("div", "av-block"));
    wrap.appendChild(els.fetchrow = el("div", "av-block"));
    wrap.appendChild(els.createform = el("div", "av-block"));
    wrap.appendChild(els.agent = el("div", "av-block"));
    wrap.appendChild(els.history = el("div", "av-block"));
    rootEl.appendChild(wrap);

    mounted = true;
    refreshKey();
  }

  function unmount() {
    timers.forEach(clearInterval);
    timers = [];
    if (previewAudio) { previewAudio.pause(); previewAudio = null; previewBtn = null; }
    mounted = false;
    if (rootEl) rootEl.innerHTML = "";
    rootEl = null;
    els = {};
    state = { hasKey: null, avatars: [], voices: [], selAvatar: null, selVoice: null, history: [] };
  }

  return { mount: mount, unmount: unmount };
})();
