/* CIOS Media Studio (ai33.pro) — Builder B.
 *
 * Exposes: window.CIOSMedia = { mount(rootEl), unmount() }
 *
 * Full-screen studio with 7 tabs: TTS, Dialogue, Voice Clone, Music, Image,
 * Video, Tasks. Every backend call goes through window.CIOSApi.api
 * (§A3/§0.1 unified client — never raw fetch to our backend).
 *
 * Polling: our /api/v1/media/tasks/{id} every 5s, max 100 attempts,
 * backoff to 10s after 20 attempts.
 *
 * Billing (always visible): usage spends the user's OWN ai33.pro credits.
 */
(function () {
  "use strict";

  var PROVIDERS = [
    { id: "elevenlabs_", label: "ElevenLabs" },
    { id: "minimax_", label: "MiniMax" },
    { id: "clone_", label: "Meri Clones" },
    { id: "edge_", label: "Edge" },
    { id: "kokoro_", label: "Kokoro" },
    { id: "vbee_", label: "Vbee" },
    { id: "fishaudio_", label: "FishAudio" },
  ];

  var TABS = [
    { id: "tts", label: "🎤 TTS", badge: "LIVE" },
    { id: "dialogue", label: "🗣️ Dialogue", badge: "LIVE" },
    { id: "clone", label: "🎙️ Voice Clone", badge: "LIVE" },
    { id: "music", label: "🎵 Music", badge: "LIVE" },
    { id: "image", label: "🖼️ Image", badge: "LIVE" },
    { id: "video", label: "🎬 Video", badge: "LIVE" },
    { id: "tasks", label: "📋 Tasks", badge: "LIVE" },
  ];

  var root = null;
  var pollTimers = [];
  var voiceCache = {};   // provider -> voices[]
  var pollers = {};

  function api(path, opts) {
    return window.CIOSApi.api(path, opts || {});
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function el(html) {
    var d = document.createElement("div");
    d.innerHTML = html;
    return d.firstElementChild;
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.textContent;
      btn.disabled = true;
      btn.textContent = "⟳ " + (label || "…");
    } else {
      btn.disabled = false;
      if (btn.dataset.label) btn.textContent = btn.dataset.label;
    }
  }

  function flashOk(btn) {
    if (!btn) return;
    var old = btn.textContent;
    btn.textContent = "✅ ho gaya";
    setTimeout(function () { btn.textContent = old; }, 1200);
  }

  function stopPolls() {
    pollTimers.forEach(clearInterval);
    pollTimers = [];
    pollers = {};
  }

  /* ---------------- credit pill ---------------- */
  function refreshCredits() {
    var pill = root && root.querySelector("#ms-credits");
    if (!pill) return;
    api("/api/v1/media/credits", { method: "GET" }).then(function (d) {
      pill.textContent = "💳 " + d.credits + " credits";
    }).catch(function () {
      pill.textContent = "💳 credits n/a";
    });
  }

  /* ---------------- task polling ---------------- */
  function pollTask(taskId, box, opts) {
    opts = opts || {};
    var attempts = 0;
    var timer = null;
    var stateEl = box.querySelector(".ms-pollstate");
    function stop() { if (timer) { clearInterval(timer); timer = null; } }

    function tick() {
      attempts++;
      if (stateEl) stateEl.textContent = "⏳ Ban raha hai… (" + attempts + "/100)";
      api("/api/v1/media/tasks/" + encodeURIComponent(taskId), { method: "GET" })
        .then(function (t) {
          if (t.status === "completed") { stop(); renderDone(t); }
          else if (t.status === "failed") {
            stop();
            if (stateEl) stateEl.textContent = "";
            err(box, "❌ " + (t.error || "Generation fail ho gaya."));
          } else if (attempts >= 100) {
            stop();
            if (stateEl) stateEl.textContent = "⏳ Abhi bhi ban raha hai — Tasks tab me status dekho.";
          } else if (attempts === 20) {
            stop();
            timer = setInterval(tick, 10000); // backoff: 10s after 20 attempts
            pollTimers.push(timer);
          }
        })
        .catch(function (e) {
          stop();
          err(box, "Polling error: " + (e && e.message ? e.message : e));
        });
    }

    function renderDone(t) {
      if (stateEl) stateEl.textContent = "";
      var out = box.querySelector(".ms-result");
      if (!out) return;
      var html = "";
      if (t.audio_url) {
        html += '<audio controls src="' + esc(t.audio_url) + '"></audio><br>' +
          '<a class="ms-dl" href="' + esc(t.audio_url) + '" download>⬇️ Download</a>';
      }
      if (t.image_url) {
        html += '<img class="ms-preview" src="' + esc(t.image_url) + '" alt="generated image"><br>' +
          '<a class="ms-dl" href="' + esc(t.image_url) + '" download>⬇️ Download</a>';
      }
      if (t.video_url) {
        html += '<video class="ms-preview" controls src="' + esc(t.video_url) + '"></video><br>' +
          '<a class="ms-dl" href="' + esc(t.video_url) + '" download>⬇️ Download</a>';
      }
      if (t.transcript) {
        html += '<details class="ms-transcript"><summary>📝 Transcript</summary><pre>' +
          esc(t.transcript) + "</pre></details>";
      }
      if (!html) html = '<div class="ms-note">✅ Complete — lekin result URL nahi mila. Tasks tab me check karo.</div>';
      out.innerHTML = html;
      refreshCredits();
      if (opts.onDone) opts.onDone(t);
    }

    pollers[taskId] = { stop: stop };
    timer = setInterval(tick, 5000);
    pollTimers.push(timer);
    tick();
  }

  function err(box, msg) {
    var f = box.querySelector(".ferr");
    if (f) f.textContent = msg;
  }

  function clearErr(box) {
    var f = box.querySelector(".ferr");
    if (f) f.textContent = "";
  }

  function pollBox() {
    return '<div class="ms-pollstate" aria-live="polite"></div>' +
      '<div class="ms-result"></div><div class="ferr"></div>';
  }

  /* ---------------- voices ---------------- */
  function loadVoices(provider) {
    if (voiceCache[provider]) return Promise.resolve(voiceCache[provider]);
    return api("/api/v1/media/voices?provider=" + encodeURIComponent(provider), { method: "GET" })
      .then(function (d) {
        voiceCache[provider] = d.voices || [];
        return voiceCache[provider];
      })
      .catch(function () { return []; });
  }

  function loadAllVoices() {
    return Promise.all(PROVIDERS.map(function (p) {
      return loadVoices(p.id).then(function (vs) {
        return { provider: p, voices: vs };
      });
    }));
  }

  function voiceOptions(groups, selected) {
    var html = "";
    groups.forEach(function (g) {
      if (!g.voices.length) return;
      html += '<optgroup label="' + esc(g.provider.label) + " (" + g.voices.length + ')">';
      g.voices.forEach(function (v) {
        var sel = v.voice_id === selected ? " selected" : "";
        html += '<option value="' + esc(v.voice_id) + '"' + sel + ">" +
          esc(v.name) + (v.language ? " · " + esc(v.language) : "") + "</option>";
      });
      html += "</optgroup>";
    });
    return html || '<option value="">— koi voice nahi mili —</option>';
  }

  function fillVoiceSelect(sel, labelEl) {
    if (!sel) return;
    sel.innerHTML = '<option value="">⟳ voices load ho rahi hain…</option>';
    loadAllVoices().then(function (groups) {
      sel.innerHTML = voiceOptions(groups, "");
      var n = groups.reduce(function (a, g) { return a + g.voices.length; }, 0);
      if (labelEl) labelEl.textContent = n + " voices available";
    }).catch(function () {
      sel.innerHTML = '<option value="">⚠️ voices load nahi huin</option>';
    });
  }

  /* ---------------- tabs ---------------- */

  function tabTTS() {
    var t = el('<section class="ms-tab" data-tab="tts">' +
      "<h3>🎤 Text-to-Speech</h3>" +
      '<div class="ms-note">Voice picker me saare 7 providers ki voices hain. ' +
      "Estimate: <b>≈588 credits/min</b> (andaza).</div>" +
      '<label>Voice <span class="ms-meta" data-voices-count></span>' +
      '<select class="ms-voice"></select></label>' +
      '<label>Text <span class="ms-meta"><span data-chars>0</span>/20000 · ≈<span data-est>0</span> credits</span>' +
      '<textarea data-text maxlength="20000" rows="6" placeholder="Yahan text likho…"></textarea></label>' +
      '<label>Speed: <span data-speedval>1.0</span>' +
      '<input type="range" data-speed min="0.5" max="1.5" step="0.1" value="1.0"></label>' +
      '<label class="ms-check"><input type="checkbox" data-transcript> Transcript bhi chahiye</label>' +
      '<div class="btnrow"><button type="button" data-gen>🎤 Generate</button></div>' +
      pollBox() + "</section>");

    fillVoiceSelect(t.querySelector(".ms-voice"), t.querySelector("[data-voices-count]"));
    var ta = t.querySelector("[data-text]");
    ta.addEventListener("input", function () {
      t.querySelector("[data-chars]").textContent = ta.value.length;
      t.querySelector("[data-est]").textContent = Math.ceil(ta.value.length / 60 * 588);
    });
    var sp = t.querySelector("[data-speed]");
    sp.addEventListener("input", function () {
      t.querySelector("[data-speedval]").textContent = sp.value;
    });
    t.querySelector("[data-gen]").addEventListener("click", function () {
      var btn = this;
      clearErr(t);
      var voice = t.querySelector(".ms-voice").value;
      var text = ta.value.trim();
      if (!voice) { err(t, "Pehle voice select karo."); return; }
      if (!text) { err(t, "Text khaali hai."); return; }
      setBusy(btn, true, "Generate ho raha hai");
      api("/api/v1/media/tts", {
        method: "POST", formKey: "media-tts",
        body: {
          voice_id: voice, text: text,
          speed: parseFloat(sp.value),
          with_transcript: t.querySelector("[data-transcript]").checked,
        },
      }).then(function (d) {
        setBusy(btn, false);
        t.querySelector(".ms-pollstate").textContent = "✅ Task ban gaya — audio ka wait karo…";
        pollTask(d.task_id, t);
      }).catch(function (e) {
        setBusy(btn, false);
        err(t, e && e.message ? e.message : "Generate fail ho gaya.");
      });
    });
    return t;
  }

  function tabDialogue() {
    var t = el('<section class="ms-tab" data-tab="dialogue">' +
      "<h3>🗣️ Multi-Speaker Dialogue</h3>" +
      '<div class="ms-note">Har line <code>A&gt;</code>, <code>B&gt;</code>, <code>C&gt;</code> se shuru ho. ' +
      "Misal:<br><code>A&gt; Assalam-o-Alaikum!<br>B&gt; Wa Alaikum Assalam!</code></div>" +
      ["A", "B", "C"].map(function (s) {
        return '<label>Speaker ' + s + ' ki voice<select class="ms-voice" data-sp="' + s + '"></select></label>';
      }).join("") +
      '<label>Dialogue text <span class="ms-meta"><span data-chars>0</span>/20000</span>' +
      '<textarea data-text maxlength="20000" rows="6" placeholder="A> Pehli line&#10;B> Dosri line"></textarea></label>' +
      '<label>Speed: <span data-speedval>1.0</span>' +
      '<input type="range" data-speed min="0.5" max="1.5" step="0.1" value="1.0"></label>' +
      '<div class="btnrow"><button type="button" data-gen>🗣️ Generate</button></div>' +
      pollBox() + "</section>");

    t.querySelectorAll(".ms-voice").forEach(function (sel) { fillVoiceSelect(sel, null); });
    var ta = t.querySelector("[data-text]");
    ta.addEventListener("input", function () {
      t.querySelector("[data-chars]").textContent = ta.value.length;
    });
    var sp = t.querySelector("[data-speed]");
    sp.addEventListener("input", function () {
      t.querySelector("[data-speedval]").textContent = sp.value;
    });
    t.querySelector("[data-gen]").addEventListener("click", function () {
      var btn = this;
      clearErr(t);
      var speakers = {};
      var ok = true;
      t.querySelectorAll(".ms-voice").forEach(function (sel) {
        if (sel.value) speakers[sel.dataset.sp] = sel.value;
      });
      if (!Object.keys(speakers).length) { err(t, "Kam az kam ek speaker ki voice select karo."); return; }
      var text = ta.value.trim();
      if (!text) { err(t, "Dialogue text khaali hai."); return; }
      var bad = text.split("\n").map(function (l) { return l.trim(); })
        .filter(function (l) { return l && !/^[A-Z]>/.test(l); });
      if (bad.length) { err(t, "Har line A>, B>, C> … se shuru honi chahiye. Pehli ghalat line: " + bad[0].slice(0, 40)); return; }
      setBusy(btn, true, "Generate ho raha hai");
      api("/api/v1/media/dialogue", {
        method: "POST", formKey: "media-dialogue",
        body: { speakers: speakers, text: text, speed: parseFloat(sp.value) },
      }).then(function (d) {
        setBusy(btn, false);
        pollTask(d.task_id, t);
      }).catch(function (e) {
        setBusy(btn, false);
        err(t, e && e.message ? e.message : "Generate fail ho gaya.");
      });
      if (!ok) return;
    });
    return t;
  }

  function tabClone() {
    var t = el('<section class="ms-tab" data-tab="clone">' +
      "<h3>🎙️ Voice Clone</h3>" +
      '<div class="ms-note">Apni awaz ka sample upload karo (≤10MB) — nayi <code>clone_</code> voice ' +
      "TTS picker me aa jayegi.</div>" +
      '<label>Voice ka naam (2–50 characters)<input data-name maxlength="50" placeholder="Meri Awaz"></label>' +
      '<label>Audio file (≤10MB)<input type="file" data-file accept="audio/*"></label>' +
      '<div class="btnrow"><button type="button" data-upload>⬆️ Clone banao</button>' +
      '<button type="button" class="ghost" data-refresh>🔄 Clones list refresh</button></div>' +
      '<div class="ms-clonelist"></div>' +
      pollBox() + "</section>");

    function refreshList() {
      var box = t.querySelector(".ms-clonelist");
      box.innerHTML = '<div class="ms-note">⟳ clones load ho rahe hain…</div>';
      loadVoices("clone_").then(function (vs) {
        if (!vs.length) { box.innerHTML = '<div class="ms-note">Abhi koi clone nahi — upar se banao.</div>'; return; }
        box.innerHTML = "<h4>Meri clones</h4>" + vs.map(function (v) {
          return '<div class="ms-clonerow"><span>' + esc(v.name) +
            ' <code>' + esc(v.voice_id) + '</code></span>' +
            '<button type="button" class="ghost ms-small" data-del="' + esc(v.voice_id) + '">🗑️ Delete</button></div>';
        }).join("");
        box.querySelectorAll("[data-del]").forEach(function (btn) {
          btn.addEventListener("click", function () { confirmDelete(btn); });
        });
      }).catch(function () {
        box.innerHTML = '<div class="ms-note">⚠️ Clones load nahi huay.</div>';
      });
    }

    function confirmDelete(btn) {
      var vid = btn.dataset.del;
      if (btn.dataset.armed) {
        delete voiceCache["clone_"];
        btn.disabled = true;
        api("/api/v1/media/voice-clone/" + encodeURIComponent(vid), { method: "DELETE", formKey: "media-clone-del" })
          .then(function () { flashOk(btn); refreshList(); fillAllCloneSelects(); })
          .catch(function (e) { btn.disabled = false; err(t, e && e.message ? e.message : "Delete fail."); });
      } else {
        btn.dataset.armed = "1";
        var old = btn.textContent;
        btn.textContent = "Pakka? Haan";
        var no = el('<button type="button" class="ghost ms-small">Nahi</button>');
        no.addEventListener("click", function () {
          delete btn.dataset.armed; btn.textContent = old; no.remove();
        });
        btn.after(no);
      }
    }

    function fillAllCloneSelects() {
      // TTS/dialogue pickers ki clone_ group refresh (dobara mount pe bhi)
      voiceCache["clone_"] = null;
    }

    t.querySelector("[data-refresh]").addEventListener("click", function () {
      delete voiceCache["clone_"];
      refreshList();
    });
    t.querySelector("[data-upload]").addEventListener("click", function () {
      var btn = this;
      clearErr(t);
      var name = t.querySelector("[data-name]").value.trim();
      var file = t.querySelector("[data-file]").files[0];
      if (name.length < 2 || name.length > 50) { err(t, "Voice ka naam 2–50 characters ka ho."); return; }
      if (!file) { err(t, "Audio file select karo."); return; }
      if (file.size > 10 * 1024 * 1024) { err(t, "⚠️ File 10MB se bari hai — choti file do."); return; }
      setBusy(btn, true, "Upload ho raha hai");
      var fd = new FormData();
      fd.append("voice_name", name);
      fd.append("audio_file", file);
      api("/api/v1/media/voice-clone", { method: "POST", formKey: "media-clone", formData: fd })
        .then(function (d) {
          setBusy(btn, false);
          flashOk(btn);
          delete voiceCache["clone_"];
          t.querySelector(".ms-pollstate").textContent = "✅ Clone ban gaya: " + d.voice_id;
          refreshList();
        }).catch(function (e) {
          setBusy(btn, false);
          err(t, e && e.message ? e.message : "Upload fail ho gaya.");
        });
    });
    refreshList();
    return t;
  }

  function tabMusic() {
    var modes = [
      ["wav", "wav — full quality"],
      ["mp3", "mp3 — standard"],
      ["mp3-45", "mp3-45 — 45s version"],
      ["mp3-lite", "mp3-lite — halka/sasta"],
      ["instrumental", "instrumental — bina vocals"],
    ];
    var t = el('<section class="ms-tab" data-tab="music">' +
      "<h3>🎵 Music (Suno)</h3>" +
      '<div class="ms-note">Cost mode ke hisaab se — Generate dabane ke baad credits katega.</div>' +
      '<label>Mode<select data-mode>' +
      modes.map(function (m) { return '<option value="' + m[0] + '">' + m[1] + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Prompt (1–500)<span class="ms-meta"><span data-chars>0</span>/500</span>' +
      '<textarea data-prompt maxlength="500" rows="3" placeholder="Lo-fi beat, soft piano…"></textarea></label>' +
      '<label>Style (optional, ≤200)<input data-style maxlength="200"></label>' +
      '<label>Title (optional, ≤100)<input data-title maxlength="100"></label>' +
      '<div class="btnrow"><button type="button" data-gen>🎵 Generate</button></div>' +
      pollBox() + "</section>");
    var pr = t.querySelector("[data-prompt]");
    pr.addEventListener("input", function () {
      t.querySelector("[data-chars]").textContent = pr.value.length;
    });
    t.querySelector("[data-gen]").addEventListener("click", function () {
      var btn = this;
      clearErr(t);
      var prompt = pr.value.trim();
      if (!prompt) { err(t, "Prompt khaali hai."); return; }
      setBusy(btn, true, "Generate ho raha hai");
      api("/api/v1/media/music", {
        method: "POST", formKey: "media-music",
        body: {
          mode: t.querySelector("[data-mode]").value,
          prompt: prompt,
          style: t.querySelector("[data-style]").value.trim() || undefined,
          title: t.querySelector("[data-title]").value.trim() || undefined,
        },
      }).then(function (d) {
        setBusy(btn, false);
        pollTask(d.task_id, t);
      }).catch(function (e) {
        setBusy(btn, false);
        err(t, e && e.message ? e.message : "Generate fail ho gaya.");
      });
    });
    return t;
  }

  function tabImage() {
    var models = [
      ["gpt-image-1", "gpt-image-1 — text se image"],
      ["image-to-image", "image-to-image — image URL se naya version"],
      ["upscale", "upscale — image bara/saaf karo"],
      ["imagen-3", "imagen-3 — Google model"],
    ];
    var t = el('<section class="ms-tab" data-tab="image">' +
      "<h3>🖼️ Image</h3>" +
      '<div class="ms-note">Estimate: <b>≈500 credits/image</b> (andaza).</div>' +
      '<label>Model<select data-model>' +
      models.map(function (m) { return '<option value="' + m[0] + '">' + m[1] + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Prompt (1–1000)<span class="ms-meta"><span data-chars>0</span>/1000</span>' +
      '<textarea data-prompt maxlength="1000" rows="3" placeholder="A cinematic sunset over mountains…"></textarea></label>' +
      '<label>Image URL (image-to-image / upscale ke liye)<input data-imgurl maxlength="2048" placeholder="https://…"></label>' +
      '<div class="btnrow"><button type="button" data-gen>🖼️ Generate</button></div>' +
      pollBox() + "</section>");
    var pr = t.querySelector("[data-prompt]");
    pr.addEventListener("input", function () {
      t.querySelector("[data-chars]").textContent = pr.value.length;
    });
    t.querySelector("[data-gen]").addEventListener("click", function () {
      var btn = this;
      clearErr(t);
      var model = t.querySelector("[data-model]").value;
      var prompt = pr.value.trim();
      var imgurl = t.querySelector("[data-imgurl]").value.trim();
      if (!prompt) { err(t, "Prompt khaali hai."); return; }
      if ((model === "image-to-image" || model === "upscale") && !imgurl) {
        err(t, model + " ke liye image URL zaroori hai."); return;
      }
      setBusy(btn, true, "Generate ho raha hai");
      api("/api/v1/media/image", {
        method: "POST", formKey: "media-image",
        body: { model: model, prompt: prompt, image_url: imgurl || undefined },
      }).then(function (d) {
        setBusy(btn, false);
        pollTask(d.task_id, t);
      }).catch(function (e) {
        setBusy(btn, false);
        err(t, e && e.message ? e.message : "Generate fail ho gaya.");
      });
    });
    return t;
  }

  function tabVideo() {
    var models = [
      ["veo3", "veo3 — best quality"],
      ["veo3-fast", "veo3-fast — tez/sasta"],
      ["image-to-video", "image-to-video — image se video"],
      ["image-to-video-continue", "image-to-video-continue — video aage barhao"],
      ["image-to-video-frame", "image-to-video-frame — frame-based"],
    ];
    var t = el('<section class="ms-tab" data-tab="video">' +
      "<h3>🎬 Video</h3>" +
      '<div class="ms-note">Duration fixed 8s (docs). Cost model ke hisaab se — ' +
      "Generate dabane ke baad credits katega.</div>" +
      '<label>Model<select data-model>' +
      models.map(function (m) { return '<option value="' + m[0] + '">' + m[1] + "</option>"; }).join("") +
      "</select></label>" +
      '<label>Prompt (1–1000)<span class="ms-meta"><span data-chars>0</span>/1000</span>' +
      '<textarea data-prompt maxlength="1000" rows="3" placeholder="A drone shot over a neon city…"></textarea></label>' +
      '<label>Image URL (optional)<input data-imgurl maxlength="2048" placeholder="https://…"></label>' +
      '<label>Aspect ratio<select data-aspect><option>16:9</option><option>9:16</option></select></label>' +
      '<label class="ms-check"><input type="checkbox" data-genaudio checked> Audio bhi generate karo</label>' +
      '<div class="btnrow"><button type="button" data-gen>🎬 Generate</button></div>' +
      pollBox() + "</section>");
    var pr = t.querySelector("[data-prompt]");
    pr.addEventListener("input", function () {
      t.querySelector("[data-chars]").textContent = pr.value.length;
    });
    t.querySelector("[data-gen]").addEventListener("click", function () {
      var btn = this;
      clearErr(t);
      var prompt = pr.value.trim();
      if (!prompt) { err(t, "Prompt khaali hai."); return; }
      setBusy(btn, true, "Generate ho raha hai");
      api("/api/v1/media/video", {
        method: "POST", formKey: "media-video",
        body: {
          model: t.querySelector("[data-model]").value,
          prompt: prompt,
          image_url: t.querySelector("[data-imgurl]").value.trim() || undefined,
          aspect_ratio: t.querySelector("[data-aspect]").value,
          generate_audio: t.querySelector("[data-genaudio]").checked,
        },
      }).then(function (d) {
        setBusy(btn, false);
        pollTask(d.task_id, t);
      }).catch(function (e) {
        setBusy(btn, false);
        err(t, e && e.message ? e.message : "Generate fail ho gaya.");
      });
    });
    return t;
  }

  function tabTasks() {
    var t = el('<section class="ms-tab" data-tab="tasks">' +
      "<h3>📋 Tasks</h3>" +
      '<div class="btnrow"><button type="button" data-reload>🔄 Refresh</button></div>' +
      '<div class="ms-tasklist"></div>' +
      '<div class="ferr"></div></section>');

    function load() {
      var box = t.querySelector(".ms-tasklist");
      box.innerHTML = '<div class="ms-note">⟳ tasks load ho rahe hain…</div>';
      api("/api/v1/media/tasks?limit=20", { method: "GET" }).then(function (d) {
        var tasks = d.tasks || [];
        if (!tasks.length) {
          box.innerHTML = '<div class="ms-note">Koi task nahi — pehle kuch generate karo.</div>';
          return;
        }
        var tbl = el('<table class="ms-table"><thead><tr>' +
          "<th>Task</th><th>Type</th><th>Status</th><th>Date</th><th></th></tr></thead><tbody></tbody></table>");
        var tb = tbl.querySelector("tbody");
        tasks.forEach(function (task) {
          var tr = el("<tr><td><code>" + esc(String(task.task_id).slice(0, 12)) +
            "…</code></td><td>" + esc(task.type || "—") + "</td><td>" +
            esc(task.status) + "</td><td>" + esc(task.created_at || "—") + "</td><td></td></tr>");
          var del = el('<button type="button" class="ghost ms-small">🗑️ Delete (credits wapas milenge)</button>');
          del.addEventListener("click", function () {
            if (del.dataset.armed) {
              del.disabled = true;
              api("/api/v1/media/tasks/" + encodeURIComponent(task.task_id) + "/delete",
                  { method: "POST", formKey: "media-task-del" })
                .then(function () { flashOk(del); load(); refreshCredits(); })
                .catch(function (e) { del.disabled = false; err(t, e && e.message ? e.message : "Delete fail."); });
            } else {
              del.dataset.armed = "1";
              del.textContent = "Pakka? Haan";
            }
          });
          tr.lastElementChild.appendChild(del);
          tb.appendChild(tr);
        });
        box.innerHTML = "";
        box.appendChild(tbl);
      }).catch(function (e) {
        box.innerHTML = '<div class="ms-note">⚠️ Tasks load nahi huay.</div>';
        err(t, e && e.message ? e.message : "");
      });
    }

    t.querySelector("[data-reload]").addEventListener("click", load);
    t._onShow = load;
    return t;
  }

  /* ---------------- setup panel (no key) ---------------- */
  function setupPanel() {
    var s = el('<div class="ms-setup">' +
      "<h3>🔑 ai33.pro Connect karo</h3>" +
      '<div class="setupbox">' +
      "<b>Steps:</b><br>" +
      '1. <a href="https://ai33.pro" target="_blank" rel="noopener">🌐 ai33.pro kholo</a> → login karo<br>' +
      "2. Dashboard me API key banao (copy karo)<br>" +
      "3. Neeche paste karke <b>Connect</b> dabao — key verify ho kar save hogi<br><br>" +
      "💡 <b>Naye Gmail/Apple signup par 3,333 free trial credits</b> milte hain." +
      "</div>" +
      '<label>ai33.pro API key<input data-key type="password" autocomplete="off" ' +
      'placeholder="key yahan paste karo"></label>' +
      '<div class="btnrow"><button type="button" data-connect>🔌 Connect & Verify</button></div>' +
      '<div class="ferr"></div></div>');

    s.querySelector("[data-connect]").addEventListener("click", function () {
      connectKey(s);
    });
    // Enter se bhi connect (§A2 enter-submits)
    s.querySelector("[data-key]").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); connectKey(s); }
    });
    return s;
  }

  function connectKey(s) {
    var btn = s.querySelector("[data-connect]");
    var f = s.querySelector(".ferr");
    f.textContent = "";
    var key = s.querySelector("[data-key]").value.trim();
    if (key.length < 10 || key.length > 500) {
      f.textContent = "Key 10–500 characters ki honi chahiye.";
      return;
    }
    setBusy(btn, true, "Verify ho raha hai");
    api("/api/v1/media/key", { method: "POST", formKey: "media-key", body: { api_key: key } })
      .then(function (d) {
        setBusy(btn, false);
        flashOk(btn);
        mount(root); // re-render: tabs ab nazar ayenge
      }).catch(function (e) {
        setBusy(btn, false);
        f.textContent = e && e.message ? e.message : "Connect fail ho gaya.";
      });
  }

  /* ---------------- mount / unmount ---------------- */
  function mount(rootEl) {
    if (!rootEl) return;
    unmount();
    root = rootEl;
    root.innerHTML = "";

    if (!window.CIOSApi || !window.CIOSApi.api) {
      root.appendChild(el('<div class="ms-shell"><div class="ms-note">' +
        "⚠️ Studio load nahi hua — page refresh karo.</div></div>"));
      return;
    }

    var shell = el('<div class="ms-shell">' +
      '<div class="ms-head">' +
      "<h2>🎙️ Media Studio</h2>" +
      '<div class="ms-headright">' +
      '<span id="ms-credits" class="ms-pill">💳 …</span>' +
      '<a class="ms-link" href="https://ai33.pro" target="_blank" rel="noopener">🌐 ai33.pro kholo</a>' +
      '<button type="button" class="ghost ms-back">← Wapas</button>' +
      "</div></div>" +
      '<div class="ms-billing">⚠️ ai33.pro paid service hai — har generation aapke apne ' +
      "ai33.pro credits se katega. Hamara $0 sirf CIOS ki hosting par hai. " +
      "Task delete karne par credits wapas milte hain.</div>" +
      '<div class="ms-tabs" role="tablist"></div>' +
      '<div class="ms-panels"></div></div>');
    root.appendChild(shell);

    shell.querySelector(".ms-back").addEventListener("click", function () {
      if (window.CIOSApp && typeof window.CIOSApp.closeStudio === "function") {
        window.CIOSApp.closeStudio();
      }
    });

    // Key hai ya nahi? — status single source of truth
    api("/api/v1/media/status", { method: "GET" }).then(function (st) {
      var tabsBar = shell.querySelector(".ms-tabs");
      var panels = shell.querySelector(".ms-panels");
      if (!st.has_key) {
        panels.appendChild(setupPanel());
        return;
      }
      shell.querySelector("#ms-credits").textContent = "💳 " + (st.credits == null ? "n/a" : st.credits) + " credits";

      var builders = {
        tts: tabTTS, dialogue: tabDialogue, clone: tabClone,
        music: tabMusic, image: tabImage, video: tabVideo, tasks: tabTasks,
      };
      var tabEls = {};
      TABS.forEach(function (tb, i) {
        var b = el('<button type="button" role="tab">' + esc(tb.label) +
          ' <span class="ms-badge">LIVE</span></button>');
        if (i === 0) b.classList.add("active");
        b.addEventListener("click", function () {
          Object.keys(tabEls).forEach(function (k) {
            tabEls[k].btn.classList.toggle("active", k === tb.id);
            tabEls[k].panel.style.display = k === tb.id ? "" : "none";
          });
          var p = tabEls[tb.id].panel;
          if (p._onShow) p._onShow();
        });
        tabsBar.appendChild(b);
        var panel = builders[tb.id]();
        panel.style.display = i === 0 ? "" : "none";
        panels.appendChild(panel);
        tabEls[tb.id] = { btn: b, panel: panel };
      });
    }).catch(function (e) {
      shell.querySelector(".ms-panels").appendChild(
        el('<div class="ms-note">⚠️ Status load nahi hua: ' + esc(e && e.message ? e.message : e) +
          " — page refresh karo.</div>"));
    });

    refreshCredits();
  }

  function unmount() {
    stopPolls();
    voiceCache = {};
    if (root) { root.innerHTML = ""; root = null; }
  }

  window.CIOSMedia = { mount: mount, unmount: unmount };
})();
