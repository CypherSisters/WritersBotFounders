/* The PuffPAI window (D81, PROPOSED): chat in the middle, a rail on the left, a side pane on the right. A second face
 * of the Desktop's ui package, on the same Core API, looks and login. The rules live in logic.js. Text from packages and
 * the Core is always set as text, never as HTML. Every call carries X-PuffPAI: 1 and her login's second half (D49, D79).
 *
 * Where an app opens is always the frame route's answer (GET /v1/packages/<id>/frame), never a list or a rule here. Kind
 * "page": framed in the side pane, with the sandbox the route gives (D13, D53, D82). Kind "app": never framed, it opens in
 * her real browser, and the pane keeps a card with what the brain read (D83). Kind "none" (a tool with no page): a plain
 * card in the pane, never a browser tab.
 *
 * What she types is not kept in this browser: only the chat ids and titles are, and titles come from the Core's locked
 * copy. The words come back from GET /v1/chat/history, in the locked form (floor X6: no plain store holds a held value). */
(function () {
  "use strict";
  const L = window.PuffLogic, GH = window.PuffGitHub;
  const $ = (id) => document.getElementById(id);
  const PRIVACY = "Nothing leaves your computer without your consent.";
  const SIGNATURE = "PuffPAI by Cypher Sisters. puff puff pass";
  const CHATS_KEY = "puffpai.window.chats", CURRENT_KEY = "puffpai.window.current", TABS_KEY = "puffpai.window.tabs";
  const S = { settings: null, rows: [], apps: [], pages: "", themes: [], tool: null, counts: 0, signin: null, signedOut: false,
              chats: [], chatId: L.MAIN_CHAT, views: new Map(), tabs: [], active: "", ticks: new L.TickMemory(),
              shownCards: [], inChat: new Set(), notesSeen: null, faceKey: "", frames: {}, waiting: new Set(), commands: L.WINDOW_COMMANDS.slice(), sel: 0, toolTimer: null,
              chatHadFocus: true };                // only yes or no: never which field she was in

  /* ---- small DOM helpers (the same shape as desktop.js) ---- */
  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v === null || v === undefined || v === false) return;
      if (k === "text") n.textContent = v;
      else if (k === "on") Object.entries(v).forEach(([ev, fn]) => n.addEventListener(ev, fn));
      else if (k === "class") n.className = v;
      else n.setAttribute(k, v === true ? "" : v);
    });
    if (tag === "button" && !n.hasAttribute("type")) n.setAttribute("type", "button");
    (kids || []).forEach((c) => c && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  /* Before a part of the page goes away: no PIN, key or recovery code stays in a box nothing can see any more. */
  const TYPED_BOXES = "input:not([type=checkbox]):not([type=radio]), textarea";
  const forgetTyped = (n) => n.querySelectorAll(TYPED_BOXES).forEach((i) => { i.value = ""; });
  const btn = (text, on, cls) => el("button", { class: "w-btn" + (cls ? " " + cls : ""), text, on: { click: on } });
  const tool = (text, on) => el("button", { class: "w-tool", text, on: { click: on } });
  const pill = (text, cls) => el("span", { class: "w-pill " + (cls || "dim"), text });
  const spin = () => el("span", { class: "w-spin", "aria-label": "Working" });
  const pinBox = (label) => el("input", { type: "password", inputmode: "numeric", autocomplete: "off", placeholder: label || "Your PIN", "aria-label": label || "PIN" });
  const saved = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key) || "null") || fallback; } catch (e) { return fallback; } };
  const keep = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* this visit only */ } };
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
  }

  /* ---- her login (D79): the cookie and its second half, the same localStorage key as the Desktop. Never in an
   * address, a log or the page. ---- */
  const DEVICE_KEY = "puffpai.device_key";
  let deviceKey = null;
  try { deviceKey = localStorage.getItem(DEVICE_KEY) || null; } catch (e) { deviceKey = null; }
  function keepDeviceKey(k) {
    deviceKey = k || null;
    try { localStorage.setItem(DEVICE_KEY, deviceKey || ""); return !!deviceKey; } catch (e) { return false; }
  }
  function authHeaders(extra) {
    const h = Object.assign({ "X-PuffPAI": "1" }, extra || {});
    if (deviceKey) h["X-PuffPAI-Device"] = deviceKey;
    return h;
  }
  async function raw(method, path, body, waitMs) {
    const init = { method, headers: authHeaders(), credentials: "same-origin", cache: "no-store" };
    if (waitMs) init.signal = AbortSignal.timeout(waitMs);    // a poll only (D113): no answer by then counts as failed
    if (body instanceof Blob) { init.headers["Content-Type"] = "application/octet-stream"; init.body = body; }   // D134: a file, read as it goes
    else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, init); } catch (e) { return { status: 0, data: { error: "The Core did not answer." } }; }
    let data = null;
    const type = r.headers.get("Content-Type") || "";
    try { data = type.indexOf("json") >= 0 ? await r.json() : await r.text(); } catch (e) { data = null; }
    return { status: r.status, data };
  }
  async function api(method, path, body, waitMs) {
    const r = await raw(method, path, body, waitMs);
    if (L.signedOut(r.status, r.data)) { signIn(""); return { status: r.status, data: { error: L.signInWords(S.signin).line } }; }
    return r;
  }
  const said = (r, fallback) => {
    const d = r && r.data && typeof r.data === "object" ? r.data : null;
    const problems = d && Array.isArray(d.problems) ? d.problems.map(String).join(" ") : "";
    return (d && (d.error || d.line || d.said || d.reason || d.message)) || problems ||
      (r && typeof r.data === "string" && r.data.trim() && r.data.length < 300 ? r.data.trim() : "") || fallback || "That did not work.";
  };

  /* The same sign-in as pairFromHash in desktop.js, and the same one plain screen (D92). */
  async function redeem(code) {
    let r = null, d = null;
    try {
      r = await fetch("/v1/pair", { method: "POST", headers: { "Content-Type": "application/json", "X-PuffPAI": "1" },
        body: JSON.stringify({ code }), credentials: "same-origin" });
      d = await r.json();
    } catch (e) { d = null; }
    if (!r || !r.ok) return false;
    if (!keepDeviceKey(d && d.device_key)) {
      toast("This browser can't save its sign-in, so it stays signed in only until this page closes.");
    }
    return true;
  }
  async function signIn(why) {
    if (S.signedOut) return;
    S.signedOut = true;
    forgetTyped($("win"));
    $("win").hidden = true;
    const line = el("p", { text: L.signInWords(null, why).line });
    clear($("signin")).appendChild(el("section", {}, [el("h3", { text: L.SIGN_IN_TITLE }), line, el("p", { class: "w-fine", text: PRIVACY })]));
    $("signin").hidden = false;
    document.title = L.SIGN_IN_TITLE;
    try {
      const r = await fetch("/v1/signin", { headers: { "X-PuffPAI": "1" }, credentials: "same-origin", cache: "no-store" });
      if (r.ok) {
        S.signin = await r.json();
        if (S.signin.pair) { location.replace(S.signin.pair + "?back=window"); return; }   // D130: on the home network, the pair screen; D139: back here
        line.textContent = L.signInWords(S.signin, why).line;
      }
    } catch (e) { /* the plain words stay */ }
  }

  /* ---- every way out of the window: her real browser (D83), by the shell when there is one ---- */
  function shell() {
    const a = window.pywebview && window.pywebview.api;
    return a && typeof a.open_browser === "function" ? a : null;
  }
  function openOutside(url) {
    if (!L.webUrl(url)) { toast("That address can't be opened from here."); return; }
    const o = L.networkOpen(url, S.pages);          // D130: never a link to another device's own 127.0.0.1
    if (!o.url) { toast(o.line); return; }
    const a = shell();
    if (a) { a.open_browser(o.url); return; }
    window.open(o.url, "_blank", "noopener");
  }
  /* *hash*: a dashboard page ("#settings"). The shell opens the dashboard signed in, on its first page. */
  function openDashboard(hash) {
    const a = shell();
    if (a && typeof a.open_dashboard === "function") {
      a.open_dashboard().then((r) => { if (r && r.line) toast(r.line); }, () => {});   // D108: why no sign-in link
      return;
    }
    window.open("/" + (hash || ""), "_blank", "noopener");
  }

  /* The switch (D115). Terminal: the real chat in a console of its own, in the chat she is in. The Core starts it, and
   * takes only that chat's id. */
  async function openTerminal(button) {
    if (L.onNetworkHost(location.hostname)) { openDashboard("#developer/terminal"); return; }   // D130: no console on a far screen
    button.disabled = true;
    const r = await api("POST", "/v1/terminal/open", { conversation: S.chatId });
    button.disabled = false;
    toast(said(r, "The terminal chat did not open."));
  }

  /* ---- the brain's words, drawn as text ---- */
  function inlineNodes(parts) {
    return parts.map((p) => {
      if (p.t === "b") return el("b", { text: p.text });
      if (p.t === "i") return el("i", { text: p.text });
      if (p.t === "code") return el("code", { text: p.text });
      if (p.t === "link") {
        return el("a", { href: p.url, rel: "noopener noreferrer", text: p.text, on: { click: (ev) => { ev.preventDefault(); openOutside(p.url); } } });
      }
      return document.createTextNode(p.text);
    });
  }
  function drawWords(box, text) {
    clear(box);
    L.mdBlocks(text).forEach((b) => {
      if (b.type === "code") box.appendChild(el("pre", {}, [el("code", { text: b.text })]));
      else if (b.type === "ul" || b.type === "ol") box.appendChild(el(b.type, {}, b.items.map((i) => el("li", {}, inlineNodes(i)))));
      else box.appendChild(el(b.type === "h" ? "h5" : "p", {}, inlineNodes(b.inline)));
    });
  }

  /* ---- the chats: the list lives in this browser (the Core has none), the words in the Core ---- */
  const botName = () => L.botName(S.settings);
  function saveChats() { keep(CHATS_KEY, S.chats); }
  function chatView(id) {
    let v = S.views.get(id);
    if (!v) {
      v = { id, node: el("div", { class: "w-log", "aria-live": "polite", hidden: true }), loaded: false, busy: false, turn: null };
      S.views.set(id, v);
      $("logs").appendChild(v.node);
    }
    return v;
  }
  const currentView = () => chatView(S.chatId);
  const atEnd = (v) => { v.node.scrollTop = v.node.scrollHeight; };
  function intro(v) {
    if (v.node.childElementCount) return;
    v.node.appendChild(el("div", { class: "w-empty", "data-intro": "1" }, [
      el("p", { text: "Ask " + botName() + " anything." }),
      el("p", { class: "w-fine", text: (v.id === L.MAIN_CHAT ? "This is the same chat as your dashboard and your terminal. " : "") + PRIVACY })]));
  }
  function dropIntro(v) { const n = v.node.querySelector("[data-intro]"); if (n) n.remove(); }
  function addYou(v, text) {
    dropIntro(v);
    v.node.appendChild(el("div", { class: "w-msg w-you", text }));
    atEnd(v);
  }
  function addBot(v) {
    dropIntro(v);
    const words = el("div", { class: "w-words" });
    const steps = el("ul", { class: "w-steps" });
    const asks = el("div");
    const chips = el("div");
    const changed = el("div");
    const passages = el("div");
    const root = el("div", { class: "w-msg" }, [el("div", { class: "w-who", text: botName() }), words, passages, steps, asks, chips, changed]);
    v.node.appendChild(root);
    atEnd(v);
    return { root, words, passages, steps, asks, chips, changed };
  }
  function say(v, text) {
    const m = addBot(v);
    drawWords(m.words, text);
    atEnd(v);
    return m;
  }
  function changedLine(box, names) {
    if (names && names.length) box.appendChild(el("div", { class: "w-changed", text: "Changed: " + names.join(", ") }));
  }
  /* "Show <app>" when an app was named: built pages show in the pane, outside apps open in her browser. */
  function appChip(a) {
    const out = kindOf(a) === "app";
    return el("button", { class: "w-chipbtn", "data-app": a.pkg, text: out ? "Open " + a.name + " in browser ↗" : "▣ Show " + a.name,
      on: { click: () => openApp(a) } });
  }
  function namedChips(box, text, tools) {
    const have = new Set(Array.from(box.querySelectorAll("[data-app]")).map((n) => n.getAttribute("data-app")));
    L.appsNamed(text, tools, S.apps).forEach((a) => { if (!have.has(a.pkg)) box.appendChild(appChip(a)); });
  }
  async function loadHistory(v) {
    v.loaded = true;
    const r = await api("GET", "/v1/chat/history?conversation=" + encodeURIComponent(v.id));
    const lines = r.status === 200 && Array.isArray(r.data) ? r.data : [];
    if (v.node.childElementCount && !v.node.querySelector("[data-intro]")) { atEnd(v); return; }   // she already sent something
    lines.forEach((m) => {
      if (m.role === "user") { addYou(v, String(m.content || "")); return; }
      const bot = say(v, String(m.content || ""));
      namedChips(bot.chips, String(m.content || ""), m.changed || []);
      changedLine(bot.changed, Array.isArray(m.changed) ? m.changed.map(String) : []);
    });
    intro(v);
    atEnd(v);
  }
  function pickChat(id) {
    S.chatId = id;
    keep(CURRENT_KEY, id);
    S.views.forEach((x) => { x.node.hidden = true; });
    const v = chatView(id);
    v.node.hidden = false;
    if (!v.loaded) loadHistory(v);
    atEnd(v);
    drawRailChats();
    $("send").disabled = v.busy;
    $("input").focus();
  }
  function newChat() {
    const empty = S.chats.find((c) => c.id !== L.MAIN_CHAT && c.title === "New chat" && !S.views.get(c.id));
    const fresh = empty || { id: L.newChatId(), title: "New chat" };
    if (!empty) { S.chats.splice(1, 0, fresh); saveChats(); }
    pickChat(fresh.id);
  }
  /* A chat's title: the first thing she said, in the Core's locked copy (never a held value). */
  async function titleFromCore(v) {
    const c = S.chats.find((x) => x.id === v.id);
    if (!c || c.id === L.MAIN_CHAT || c.title !== "New chat") return;
    const r = await api("GET", "/v1/chat/history?limit=2&conversation=" + encodeURIComponent(v.id));
    const first = r.status === 200 && Array.isArray(r.data) ? r.data.find((m) => m.role === "user") : null;
    if (!first) return;
    c.title = L.chatTitle(first.content) || c.title;
    saveChats();
    drawRailChats();
  }

  /* One turn: POST /v1/chat/stream with this chat's id (NDJSON), read into logic.ChatTurn. */
  async function chatTurn(v, text) {
    v.busy = true;
    if (v.id === S.chatId) $("send").disabled = true;
    addYou(v, text);
    const turn = new L.ChatTurn();
    const m = addBot(v);
    v.turn = m;
    let openDone = 0;
    const paint = () => {
      if (turn.text) drawWords(m.words, turn.text);
      else if (!turn.done) { clear(m.words).appendChild(el("span", { class: "w-thinking", text: "Thinking" })); }
      else clear(m.words);
      clear(m.steps);
      turn.steps.filter((s) => s.state !== "done" || !turn.done).forEach((s) => m.steps.appendChild(el("li", {}, [
        s.state === "running" ? spin() : el("span", { text: s.state === "done" ? "✓" : "✕" }),
        el("span", { text: (s.line || "Using " + s.tool) + " " + L.stepWords(s.state) })])));
      clear(m.passages).append(...turn.passages.map((p) => window.Puff.passageCard(p)));     // D140
      turn.asks.forEach((a) => { if (a.id) cardInto(m.asks, a.id, { inChat: true, view: v }); });
      turn.opens.slice(openDone).forEach((u) => showOpened(u, m.chips));
      openDone = turn.opens.length;
      if (turn.done) {
        if (turn.error) { clear(m.words).appendChild(el("p", { class: "w-failed", text: turn.error })); }
        else if (!turn.text && turn.notice) drawWords(m.words, turn.notice);
      }
      atEnd(v);
    };
    let r;
    try {
      r = await fetch("/v1/chat/stream", { method: "POST", credentials: "same-origin",
        headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ text, conversation: v.id }) });
    } catch (e) { r = null; }
    if (!r || !r.ok || !r.body) {
      let data = null;
      try { data = r ? await r.json() : null; } catch (e) { data = null; }
      if (r && L.signedOut(r.status, data)) signIn("");
      turn.add({ type: "error", error: !r ? "The Core did not answer." : (data && data.error) || "The brain did not answer." });
    } else {
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        let chunk;
        try { chunk = await reader.read(); } catch (e) { turn.add({ type: "error", error: "The answer stopped coming." }); break; }
        if (chunk.done) break;
        const got = L.ndjson(buf + dec.decode(chunk.value, { stream: true }));
        buf = got.rest;
        got.events.forEach((ev) => turn.add(ev));
        paint();
      }
      if (buf.trim()) L.ndjson(buf + "\n").events.forEach((ev) => turn.add(ev));
      if (!turn.done) turn.add({ type: "done", text: turn.text });
    }
    paint();
    namedChips(m.chips, turn.text, turn.steps.map((s) => s.tool).concat(turn.changed));
    changedLine(m.changed, turn.changed);
    v.busy = false; v.turn = null;
    if (v.id === S.chatId) {
      $("send").disabled = false;
      // Only back to where she was. A PIN box a redraw took away leaves the focus nowhere, and what she types next
      // must go nowhere too, never into the chat. An app's frame taking the focus sends this page no focusin, so it is
      // asked here: she may be typing in that app.
      const inFrame = document.activeElement && document.activeElement.matches("iframe");
      if (S.chatHadFocus && !inFrame) $("input").focus();
    }
    atEnd(v);
    titleFromCore(v);
    if (turn.changed.length) { loadRegistry(); reloadChanged(turn.changed); }
  }

  /* The chat's open event (open_app): a built page shows in the pane, anything else opens in her real browser, with
   * a chip left in the chat, since a browser may hold back a new tab it was not clicked for. */
  async function showOpened(url, chips) {
    if (!L.openMatch(url, S.rows, S.pages)) await loadRegistry();
    const row = L.openMatch(url, S.rows, S.pages);
    const a = row && S.apps.find((x) => x.pkg === row.id);
    if (a) {
      const f = await frameOf(a.pkg);
      if (!(f.ok && f.kind === "page")) openOutside(url);
      openTab(appTab(a));
      chips.appendChild(appChip(a));
      return;
    }
    openOutside(url);
    chips.appendChild(el("button", { class: "w-chipbtn", text: "Open in browser \u2197", on: { click: () => openOutside(url) } }));
  }

  /* ---- Yes cards, in the chat or a pane, with real buttons and ticks (the Desktop's rules) ---- */
  async function pendingConsents() {
    const r = await api("GET", "/v1/consent?status=pending");
    if (r.status !== 200 || !Array.isArray(r.data)) return { status: r.status, cards: [] };
    const cards = r.data.map((c) => L.consentCard(c)).filter((c) => c.pending);
    S.ticks.keep(cards.map((c) => c.id));
    S.waiting = new Set(cards.filter((c) => c.plan).map((c) => c.plan.build));
    return { status: 200, cards };
  }
  /* D68: when the build needs its key and the vault is locked, the same step asks for her PIN and opens it. */
  async function keysReady(build, box, says, given, peek) {
    const r = await api("GET", "/v1/toolsmith/builds/" + encodeURIComponent(build));
    if (r.status !== 200 || !L.buildKeys(r.data).unlock) { if (!given) clear(box); return true; }
    let input = given || box.querySelector("input[data-unlock]");
    if (!box.firstChild) {
      if (!given) { input = pinBox("Your PIN"); input.setAttribute("data-unlock", "1"); }
      box.append(el("p", { class: "w-fine", text: L.UNLOCK_FOR_YES }));
      if (!given) box.append(input);
    }
    if (peek) return false;
    const secret = input ? input.value : "";
    if (!secret) { says.textContent = L.UNLOCK_TYPE_PIN; if (input) input.focus(); return false; }
    const x = await api("POST", "/v1/vault/unlock", { method: "pin", secret });
    if (!given) input.value = "";
    if (x.status !== 200) { says.textContent = said(x, "Your vault did not open, so nothing was answered."); return false; }
    clear(box);
    says.textContent = "";
    drawStatus();
    return true;
  }
  /* *o.aside*: a plan card in the chat offers Not now, which answers nothing: it stays on Your turn, /plan shows it. */
  function consentEl(c, after, o) {
    o = o || {};
    const p = c.needsPin ? pinBox() : null;
    const says = el("p", { class: "w-says" });
    const unlockBox = c.plan ? el("div") : null;
    const boxes = c.ticks ? c.ticks.items.map((it) => {
      if (it.ticked) S.ticks.seed(c.id, it.value);       // the card's own tick (D99)
      const box = el("input", { type: "checkbox", checked: S.ticks.has(c.id, it.value) });
      box.addEventListener("change", () => S.ticks.set(c.id, it.value, box.checked));
      return { it, box };
    }) : [];
    const why = el("p", { class: "w-fine", hidden: true,
      text: L.YES_EXPLAIN + (c.floor ? " " + L.YES_EXPLAIN_FLOOR : "") + (c.ticks ? " " + L.YES_EXPLAIN_TICKS : "") });
    async function answer(yes) {
      if (yes && c.ticks) {
        const ticked = boxes.filter((b) => b.box.checked).map((b) => b.it.value);
        const t = await api("PUT", c.ticks.route, c.ticks.body(ticked));
        if (t.status >= 400) { says.textContent = said(t, "Your ticks were not taken, so nothing was answered."); return; }
      }
      if (yes && unlockBox && !(await keysReady(c.plan.build, unlockBox, says, p))) return;
      const body = L.consentAnswer(c, yes, p ? p.value : "");
      if (p) p.value = "";
      const r = await api("POST", "/v1/consent/" + encodeURIComponent(c.id) + "/answer", body);
      if (r.status >= 400) { says.textContent = said(r); return; }
      S.ticks.drop(c.id);
      if (yes && c.plan) afterPlanYes(c);
      if (after) after(yes ? "yes" : "no");
      refreshCounts();
    }
    const row = el("div", { class: "w-btns" });
    c.buttons.forEach((b) => {
      if (b.id === "no" && o.aside && c.plan) return;
      const yes = b.id === "yes";
      row.appendChild(btn(yes && c.plan && !c.needsPin ? "Build this plan" : b.label, () => answer(yes), yes ? "primary" : ""));
    });
    if (o.aside && c.plan) row.appendChild(btn("Not now", () => { if (after) after("aside"); }));
    row.appendChild(btn("What does this mean?", () => { why.hidden = !why.hidden; }));
    const tickWords = boxes.map((b) => b.it.label);            // a line a tick box says already is shown once
    const list = el("ul", {}, c.lines.filter((l) => tickWords.indexOf(l) < 0).map((l) => el("li", { text: l })).concat(boxes.map((b) =>
      el("li", {}, [el("label", {}, [b.box, el("span", { text: b.it.label })])]))));
    const card = el("div", { class: "w-card" + (c.floor ? " w-floor" : ""), "data-card": c.id }, [
      el("h5", {}, [c.title, el("span", { text: "card " + L.cardShort(c.id) })]), list,
      c.ticks && c.ticks.note ? el("p", { class: "w-fine", text: c.ticks.note }) : null,
      c.plan ? el("p", { class: "w-fine", text: PRIVACY }) : null,
      p, unlockBox, says, row, why]);
    if (unlockBox) keysReady(c.plan.build, unlockBox, says, p, true);
    card.answerIt = answer;
    return card;
  }
  /* One card by id into *box*, once. It is told when it stops waiting (syncCards). */
  async function cardInto(box, id, o) {
    o = o || {};
    if (o.inChat && S.inChat.has(id)) return;
    if (o.inChat) S.inChat.add(id);
    const holder = el("div");
    box.appendChild(holder);
    const { cards } = await pendingConsents();
    const c = cards.find((x) => x.id === id);
    if (!c) { holder.appendChild(el("p", { class: "w-fine", text: "A Yes card is waiting on Your turn." })); return; }
    drawCard(holder, c, o);
    if (o.view) atEnd(o.view);
  }
  function drawCard(holder, c, o) {
    const entry = { id: c.id, holder, answered: false, card: null, inChat: !!o.inChat };
    entry.card = consentEl(c, (answer) => {
      entry.answered = true;
      S.shownCards = S.shownCards.filter((e) => e !== entry);
      clear(holder).appendChild(el("p", { class: "w-fine", text: answer === "yes" ? "You said Yes (card " + L.cardShort(c.id) + ")."
        : answer === "no" ? "You said No (card " + L.cardShort(c.id) + ")." : "Set aside. /plan shows it again, and it waits on Your turn." }));
      if (o.after) o.after(answer);
      if (answer !== "aside") refreshOpenTabs();
    }, { aside: !!o.inChat });
    S.shownCards.push(entry);
    clear(holder).appendChild(entry.card);
  }
  function syncCards(cards) {
    S.shownCards = S.shownCards.filter((e) => {
      if (!e.holder.isConnected) return false;
      if (cards.some((c) => c.id === e.id)) return true;
      clear(e.holder).appendChild(el("p", { class: "w-fine", text: "That card is not waiting any more (it was answered or it ended)." }));
      return false;
    });
  }
  /* Cards the chat should show by itself: a plan waiting for her Yes, and, while a turn runs, any new card (a card
   * can be asked for before it has an id). */
  async function surface() {
    const { status, cards } = await pendingConsents();
    if (status !== 200) return;
    syncCards(cards);
    const busy = Array.from(S.views.values()).find((v) => v.busy && v.turn);
    cards.filter((c) => !S.inChat.has(c.id) && (busy || c.plan)).forEach((c) => {
      S.inChat.add(c.id);
      const v = busy || currentView();
      const host = busy ? busy.turn.asks : say(v, "Toolsmith made a plan. It waits for your Yes.").asks;
      const holder = el("div");
      host.appendChild(holder);
      drawCard(holder, c, { inChat: true, view: v });
      atEnd(v);
    });
  }
  /* A card drawn fresh at the end of this chat, wherever it was before. */
  function showCardNow(c, words) {
    S.shownCards.filter((e) => e.id === c.id && e.inChat).forEach((e) => clear(e.holder).appendChild(el("p", { class: "w-fine", text: "Shown again below." })));
    S.shownCards = S.shownCards.filter((e) => !(e.id === c.id && e.inChat));
    S.inChat.add(c.id);
    const v = currentView();
    const holder = el("div");
    say(v, words).asks.appendChild(holder);
    drawCard(holder, c, { inChat: true, view: v });
    atEnd(v);
  }
  /* After a Yes on a plan card (lane I16): the Core adds the ticked store apps, then starts; this follows it. */
  async function afterPlanYes(c) {
    const v = currentView();
    const m = say(v, "Yes recorded (card " + L.cardShort(c.id) + "). Building. You can follow it in the side pane and keep chatting.");
    openTab(buildingTab());
    const log = el("div", { class: "w-fine" }), box = el("div");
    m.asks.append(log, box);
    const path = "/v1/toolsmith/builds/" + encodeURIComponent(c.plan.build);
    const s = await api("POST", path + "/start", { consent_id: c.plan.consentId });
    if (s.status !== 200) { log.appendChild(el("p", { class: "w-failed", text: "The build did not start: " + said(s) })); return; }
    loadTool();
    let shown = "";
    for (let i = 0; i < 3600; i++) {
      const r = await api("GET", path);
      if (S.signedOut) return;
      if (r.status !== 200) { await new Promise((ok) => setTimeout(ok, 1000)); continue; }
      const p = L.storeProgress(r.data);
      clear(log);
      p.lines.forEach((t) => log.appendChild(el("p", { text: t })));
      if (p.asking !== shown) {
        shown = p.asking;
        clear(box);
        if (p.asking) { S.inChat.delete(p.asking); cardInto(box, p.asking, { inChat: true, view: v }); }
      }
      if (p.done) break;
      await new Promise((ok) => setTimeout(ok, 1000));
    }
    clear(box);
    loadTool();
    refreshOpenTabs();
  }

  /* ---- the Registry and the rail ---- */
  async function loadRegistry() {
    const r = await api("GET", "/v1/registry");
    if (r.status !== 200 || !Array.isArray(r.data)) return S.rows;
    S.rows = r.data;
    S.apps = L.windowApps(S.rows);
    const answers = await Promise.all(S.apps.map((a) => frameOf(a.pkg)));
    S.apps.forEach((a, i) => { S.frames[a.pkg] = answers[i]; });
    S.commands = L.WINDOW_COMMANDS.concat(L.appCommands(S.apps, S.rows));
    drawRailApps();
    drawFace();
    return S.rows;
  }
  function drawFace() {
    const f = L.faceFor(S.settings && S.settings.avatar, S.rows, S.pages);
    const key = f.kind + " " + f.src;
    if (key === S.faceKey) return;
    S.faceKey = key;
    const box = clear($("face"));
    if (f.kind === "face") {
      box.appendChild(el("iframe", { src: f.src, sandbox: L.FACE_SANDBOX, title: f.name, referrerpolicy: "no-referrer", tabindex: "-1" }));
    } else box.appendChild(el("img", { src: f.kind === "own" ? "img/puff.webp" : f.src, alt: f.name, title: f.name }));   // D108: never /ui/
  }
  function drawName() {
    const name = botName();
    $("botname").textContent = name;
    drawTitle(S.titleCount || 0);
    $("input").placeholder = "Ask " + name + ", or type / for commands";
  }
  /* The taskbar says it too when something waits on her. */
  function drawTitle(count) {
    S.titleCount = count;
    document.title = (count ? "(" + count + ") " : "") + botName() + ", PuffPAI";
  }
  function drawRailChats() {
    clear($("rail-chats")).append(...S.chats.map((c) => el("button", { class: "w-item" + (c.id === S.chatId ? " on" : ""), title: c.title,
      "data-chat": c.id, on: { click: () => pickChat(c.id) } }, [el("span", { class: "w-name", text: c.title })])));
  }
  function drawRailApps() {
    const box = clear($("rail-apps"));
    if (!S.apps.length) box.appendChild(el("div", { class: "w-empty-line", text: "No apps yet. Ask for one in chat, or open Store." }));
    S.apps.forEach((a) => {
      const pills = kindOf(a) === "app" ? [el("span", { class: "w-grow" }), pill("opens in browser")] : [];
      box.appendChild(el("button", { class: "w-item" + (a.down ? " w-down" : "") + (S.active === "app:" + a.pkg ? " on" : ""),
        title: a.down ? a.name + ": not working right now" : a.name, "data-rail-app": a.pkg, on: { click: () => openApp(a) } },
      [el("span", { class: "w-name", text: a.name })].concat(pills)));
    });
    box.appendChild(el("button", { class: "w-item w-dim", "data-rail": "apps", on: { click: () => openPage("apps") } }, [el("span", { class: "w-name", text: "Add or manage apps" })]));
  }
  function drawRailBuilds() {
    const box = clear($("rail-builds"));
    const builds = ((S.tool && S.tool.builds) || []).slice().sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))).slice(0, 5);
    if (!builds.length) box.appendChild(el("div", { class: "w-empty-line", text: "Nothing built yet." }));
    builds.forEach((b) => {
      const chip = L.buildChip(b, S.waiting.has(b.id));
      box.appendChild(el("button", { class: "w-item", title: b.goal || "A build", "data-rail-build": b.id, on: { click: () => openTab(buildingTab()) } }, [
        el("span", { class: "w-name", text: b.goal || "A build" }), el("span", { class: "w-grow" }),
        chip.tone === "live" ? spin() : pill(chip.words, chip.tone)]));
    });
  }
  function drawRailMore() {
    const pageItem = (view, name) => el("button", { class: "w-item" + (S.active === "page:" + view ? " on" : ""), "data-rail": view, on: { click: () => openPage(view) } }, [el("span", { class: "w-name", text: name })]);
    const parts = [
      pageItem("home", "Home"),
      el("button", { class: "w-item" + (S.active === "store" ? " on" : ""), "data-rail": "store", on: { click: () => showStore() } }, [el("span", { class: "w-name", text: "Store" }),
        el("span", { class: "w-grow" }), pill("apps, tools, looks")]),
      pageItem("knowledge", "Knowledge and documents"),
      el("button", { class: "w-item" + (S.active === "turn" ? " on" : ""), "data-rail": "turn", on: { click: () => openTab(turnTab()) } }, [el("span", { class: "w-name", text: "Your turn" }),
        el("span", { class: "w-grow" }), S.counts ? pill(String(S.counts), "stop") : null]),
      el("button", { class: "w-item" + (S.active === "settings" ? " on" : ""), "data-rail": "settings", on: { click: () => openTab(settingsTab()) } }, [el("span", { class: "w-name", text: "Settings" }),
        el("span", { class: "w-grow" }), pill("keys, GitHub, looks")]),
      pageItem("people", "People"), pageItem("agents", "Agent Team"), pageItem("outside", "What leaves your computer"), pageItem("developer", "Developer"),
      // WritersBot: the native window has no address bar, so a reload needs a place of its own (F5 and Ctrl+R work too).
      el("button", { class: "w-item w-dim", "data-rail": "reload", title: "Reload the window (F5)", on: { click: () => location.reload() } }, [el("span", { class: "w-name", text: "Reload" })])];
    if (S.settings && !S.settings.wizard_complete) {
      parts.push(el("button", { class: "w-item", on: { click: () => openDashboard("#setup") } }, [el("span", { class: "w-name", text: "Setup is not finished" }), el("span", { class: "w-grow" }), pill("↗")]));
    }
    clear($("rail-more")).append(...parts);
  }
  async function drawStatus() {
    const [v, b, m] = await Promise.all([api("GET", "/v1/vault/status"), api("GET", "/v1/bridge"), api("GET", "/v1/model")]);
    if (S.signedOut) return;
    const vault = L.vaultView(v.status === 200 ? v.data : {});
    S.vault = v.status === 200 ? vault : null;
    drawWaiting();
    const open = b.status === 200 && b.data.state === "open";
    const brain = m.status === 200 ? L.brainView(m.data) : null;
    clear($("status")).append(
      el("button", { "data-status": "vault", on: { click: () => openPage("vault") } }, ["Vault ",
        pill(vault.open ? "open" : vault.state === "locked" ? "locked" : vault.words.toLowerCase(), vault.open ? "ok" : "dim")]),
      el("button", { "data-status": "bridge", on: { click: () => openPage("outside") } }, ["Bridge ",
        b.status === 200 ? pill(open ? "open" : "closed", open ? "stop" : "ok") : pill("not known")]),
      el("button", { "data-status": "brain", title: brain ? brain.words + (brain.reason ? ": " + brain.reason : "") : "", on: { click: () => openPage("settings", "brain") }, text: brain
        ? "Brain " + brain.name + (brain.ok ? ", on this computer" : ": " + brain.words.toLowerCase()) : "Brain not known" }),
      el("button", { "data-open-dash": "rail", on: { click: () => openDashboard("") }, text: "Open full dashboard ↗" }));
  }
  async function loadTool() {
    const r = await api("GET", "/v1/toolsmith", undefined, L.POLL_WAIT_MS);
    const again = () => {                       // job lines are not on the Bus
      clearTimeout(S.toolTimer);
      S.toolTimer = setTimeout(() => { loadTool(); redrawTab("building"); }, 4000);
    };
    if (r.status !== 200) { if (L.pollCanRetry(r.status)) again(); return; }   // D113: a failed poll is not the last one
    S.tool = r.data;
    drawWaiting();
    drawRailBuilds();
    clearTimeout(S.toolTimer);
    const live = (r.data.builds || []).some((b) => L.buildChip(b, S.waiting.has(b.id)).tone === "live");
    const vaultWait = L.vaultWaiting(r.data);   // D113: once per wait, Building opens beside the chat, with its PIN box
    if (vaultWait && !S.vaultSaid) {
      S.vaultSaid = true;
      say(currentView(), L.VAULT_WAIT_WINDOW);
      openTab(buildingTab());
      redrawTab("building");
    } else if (!vaultWait) S.vaultSaid = false;
    if (live || vaultWait) again();
    renderTabs();
  }
  async function refreshCounts() {
    const [c, y] = await Promise.all([api("GET", "/v1/checklist"), pendingConsents()]);
    S.counts = (c.status === 200 && Array.isArray(c.data) ? c.data.length : 0) + y.cards.length;
    S.waitSteps = c.status === 200 && Array.isArray(c.data) ? c.data : [];
    S.waitCards = y.cards;
    drawWaiting();
    drawRailMore();
    drawRailBuilds();
    renderAddr();
  }
  /* What is waiting on her (D115): at the top of the window, whatever she is looking at, with the action right there.
   * A row is drawn again only when what it says changes, and the vault's row changes its words in place, so a PIN she is
   * typing stays, with the focus. A card's words are read on the card itself (One Yes), so its row opens the cards; no
   * answer is given from here. */
  const toolsmithWaitsForVault = () => !!L.vaultWaiting(S.tool);    // D113's own rule
  function waitingRows() {
    const rows = [], v = S.vault;
    if (v && !v.open && ["locked", "throttled"].indexOf(v.state) >= 0 && (!S.vaultNotNow || toolsmithWaitsForVault())) rows.push({ key: "vault", node: vaultRow });
    if (v && !v.setUp && S.settings && S.settings.wizard_complete) {
      rows.push({ key: "vault-setup", node: () => el("div", { class: "w-attnrow", "data-waiting": "vault-setup" }, [el("span", { class: "w-grow" }, [el("b", { text: "Your vault is not set up yet." }), " Keys and private details stay out until it is."]),
        btn("Set it up", () => openPage("vault"), "primary")]) });
    }
    const cards = S.waitCards || [], steps = S.waitSteps || [];
    if (cards.length) {
      const c = cards[cards.length - 1];
      rows.push({ key: "cards:" + cards.map((x) => x.id).join(), node: () => el("div", { class: "w-attnrow", "data-waiting": "cards" }, [el("span", { class: "w-grow" }, [el("b", { text: cards.length === 1 ? "Your Yes is needed: " : cards.length + " things need your Yes: " }), c.title]),
        btn(cards.length === 1 ? "Read it" : "Read them", () => openTab(turnTab()), "primary")]) });
    }
    if (steps.length) {
      const s = L.stepCard(steps[0]);
      rows.push({ key: "steps:" + steps.map((x) => x.id).join(), node: () => el("div", { class: "w-attnrow", "data-waiting": "steps" }, [el("span", { class: "w-grow" }, [el("b", { text: steps.length === 1 ? "Your turn: " : "Your turn, " + steps.length + " steps: " }), s.title]),
        btn("Show me", () => openTab(turnTab()), "primary")]) });
    }
    return rows;
  }
  function drawWaiting() {
    const rows = waitingRows(), box = $("attn"), key = rows.map((r) => r.key).join("|");
    drawTitle(rows.length);
    if (box.getAttribute("data-key") !== key) {
      const had = new Map(Array.from(box.children).map((n) => [n.getAttribute("data-row-key"), n]));
      const nodes = rows.map((r) => {
        const n = had.get(r.key) || r.node();
        n.setAttribute("data-row-key", r.key);
        return n;
      });
      box.setAttribute("data-key", key);
      // A row that stays is never taken out and put back: that would take the focus from a PIN being typed.
      Array.from(box.children).filter((n) => !nodes.includes(n)).forEach((n) => { forgetTyped(n); n.remove(); });
      nodes.forEach((n, i) => { if (box.children[i] !== n) box.insertBefore(n, box.children[i] || null); });
    }
    const row = box.querySelector("[data-waiting=vault]");
    if (row) row.saySo();
  }
  /* The PIN: read once, the box cleared at that moment, and sent only to the Core's own unlock route. Never in an
   * attribute, the title, a log, this browser's storage or the chat. */
  function vaultRow() {
    const words = el("span", { class: "w-grow" });
    const says = el("p", { class: "w-says" });
    const box = pinBox("Your PIN");
    const open = async () => {
      const secret = box.value; box.value = "";
      if (!secret) { says.textContent = "Type your PIN first."; return; }
      const x = await api("POST", "/v1/vault/unlock", { method: "pin", secret });
      if (x.status !== 200) { says.textContent = said(x, "Your vault did not open."); return; }
      drawStatus();
    };
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); open(); } });
    const notNow = btn("Not now", () => { S.vaultNotNow = true; drawWaiting(); });
    const row = el("div", { class: "w-attnrow w-stop", "data-waiting": "vault" }, [words, box, btn("Unlock", open, "primary"), notNow, says]);
    let shownFor = null;
    row.saySo = () => {                                 // Not now only while Toolsmith is not waiting for it
      const waits = toolsmithWaitsForVault();
      if (waits === shownFor) return;
      shownFor = waits;
      clear(words).append(el("b", { text: "Your vault is locked." }), waits ? " " + L.UNLOCK_WAITING : "");
      notNow.hidden = waits;
    };
    return row;
  }

  /* A build asked for in chat says its result in the chat too (as the Desktop's chat does). */
  async function toolsmithToChat() {
    const r = await api("GET", "/v1/notify");
    if (r.status !== 200 || !Array.isArray(r.data)) return;
    const seen = S.notesSeen;
    S.notesSeen = new Set(r.data.map((n) => n.id).concat(Array.from(seen || [])));
    if (!seen) return;
    r.data.filter((n) => n.package === "toolsmith" && !seen.has(n.id)).reverse().forEach((n) => {
      const m = say(currentView(), n.text || n.title);
      namedChips(m.chips, n.text || n.title, []);
    });
    loadRegistry();
    loadTool();
  }

  /* ---- the side pane: tabs, each with a view of its own, so a framed app is never reloaded by a switch ---- */
  const appTab = (a) => ({ key: "app:" + a.pkg, kind: "app", title: a.name, app: a });
  const buildingTab = () => ({ key: "building", kind: "building", title: "Building", dash: "#building" });
  const turnTab = () => ({ key: "turn", kind: "turn", title: "Your turn", dash: "#yourturn" });
  const settingsTab = () => ({ key: "settings", kind: "settings", title: "Settings", dash: "#settings", addr: "Settings: the same Core routes as the dashboard and the terminal" });
  const storeTab = () => ({ key: "store", kind: "store", title: "Store", dash: "#apps/available", addr: "The PuffPAI store" });
  const DRAW = { app: drawApp, building: drawBuilding, turn: drawTurn, settings: drawSettings, store: drawStore, page: drawPage };

  function saveTabs() { keep(TABS_KEY, S.tabs.map((t) => t.key)); }
  function openTab(def) {
    let t = S.tabs.find((x) => x.key === def.key);
    if (!t) {
      t = def;
      t.node = el("div", { class: "w-tabview", "data-tabview": t.key, hidden: true });
      $("view").appendChild(t.node);
      S.tabs.push(t);
      DRAW[t.kind](t);
      saveTabs();
    }
    S.active = t.key;
    renderTabs();
    return t;
  }
  function closeTab(key) {
    const t = S.tabs.find((x) => x.key === key);
    if (!t) return;
    // A recovery code is shown once (pages/vault.js): closing here would lose it, and the page stays empty after.
    if (t.node.querySelector(".card.note.high pre.box")) { toast("Write your recovery code down first. It is shown only once."); return; }
    forgetTyped(t.node);
    t.node.remove();
    S.tabs = S.tabs.filter((x) => x !== t);
    if (S.active === key) S.active = S.tabs.length ? S.tabs[S.tabs.length - 1].key : "";
    saveTabs();
    renderTabs();
  }
  function renderTabs() {
    $("win").classList.toggle("w-nopane", !S.tabs.length);
    const live = S.tool && (S.tool.builds || []).some((b) => L.buildChip(b, S.waiting.has(b.id)).tone === "live");
    clear($("tabs")).append(el("div", { class: "w-tablist" }, S.tabs.map((t) => el("div", { class: "w-tab" + (t.key === S.active ? " on" : ""), "data-tab": t.key }, [
      el("button", { class: "w-tabname", on: { click: () => { const back = S.active !== t.key; S.active = t.key; renderTabs(); if (back && t.kind === "page") drawPageAgain(t); } } },
        [t.title, t.kind === "building" && live ? spin() : null]),
      el("button", { class: "w-x", "aria-label": "Close " + t.title, text: "✕", on: { click: () => closeTab(t.key) } })]))),
    el("span", { class: "w-grow" }), el("button", { class: "w-tool", "data-open-dash": "tabs", text: "Open full dashboard ↗", on: { click: () => openDashboard("") } }));
    S.tabs.forEach((t) => { t.node.hidden = t.key !== S.active; });
    const here = S.tabs.find((t) => t.key === S.active);
    if (here && here.kind === "page") S.view = here.view;
    const on = $("tabs").querySelector(".w-tab.on");
    if (on) on.scrollIntoView({ block: "nearest", inline: "nearest" });
    renderAddr();
    drawRailApps();
    drawRailMore();
  }
  function renderAddr() {
    const t = S.tabs.find((x) => x.key === S.active);
    const box = clear($("addr"));
    $("addr").hidden = !t || t.kind === "page";          // a page says what it is by itself
    if (!t) return;
    let text = t.addr || "";
    if (t.kind === "turn") text = S.counts ? S.counts + (S.counts === 1 ? " step" : " steps") + " waiting on you" : "Nothing is waiting on you";
    if (t.kind === "building") {
      const n = ((S.tool && S.tool.builds) || []).length;
      text = n + (n === 1 ? " build" : " builds");
    }
    if (t.kind === "app") text = t.url || t.title;
    box.appendChild(el("code", { text }));
    if (t.dash) box.appendChild(tool("Open in dashboard ↗", () => openDashboard(t.dash)));
    if (t.url) box.appendChild(tool("Open in browser ↗", () => openOutside(t.url)));
  }
  function redrawTab(key) {
    const t = S.tabs.find((x) => x.key === key);
    if (t) DRAW[t.kind](t, true);
  }
  function refreshOpenTabs() { redrawTab("turn"); redrawTab("building"); }
  /* The brain changed something in an app: its page in the pane shows it, by a reload of that frame only. */
  function reloadChanged(tools) {
    S.tabs.filter((t) => t.kind === "app" && tools.some((x) => String(x).indexOf(t.app.pkg + ".") === 0)).forEach((t) => {
      const frame = t.node.querySelector("iframe");
      if (frame) frame.setAttribute("src", frame.getAttribute("src"));
    });
  }

  const frameOf = async (pkg) => {
    const r = await api("GET", "/v1/packages/" + encodeURIComponent(pkg) + "/frame");
    return r.status === 200 && r.data ? r.data : { ok: false, url: "", kind: "none", line: said(r, "Its page did not answer.") };
  };
  const kindOf = (a) => (S.frames[a.pkg] || {}).kind || "";
  /* Opening an app from the rail, a chip or a command: the frame route says where (D13, D83). */
  async function openApp(a) {
    const f = await frameOf(a.pkg);
    S.frames[a.pkg] = f;
    if (f.kind === "app") {
      if (f.url) openOutside(f.url); else toast(f.line || a.name + " has no address to open.");
    }
    openTab(appTab(a));
    closeRail();
  }
  /* WritersBot: F5 and Ctrl+R reload the page, as a browser would; the native window swallows neither, and the
   * page is the only place that can do it. Inside a framed app the key goes to that frame, which is fine. */
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "F5" || ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && (ev.key === "r" || ev.key === "R"))) {
      ev.preventDefault();
      location.reload();
    }
  });
  /* WritersBot: a framed page asks this window to open a sibling app (Manuscript's "See in Maps"). Only from a frame
   * this window holds, only an installed app, and only a query string, never an address: the frame route still says
   * where that app opens. */
  window.addEventListener("message", (ev) => {
    const m = ev.data;
    if (!m || m.puffpai !== "open" || typeof m.pkg !== "string") return;
    const frames = Array.from(document.querySelectorAll("iframe.w-frame"));
    if (!frames.some((fr) => fr.contentWindow === ev.source)) return;
    const a = S.apps.find((x) => x.pkg === m.pkg);
    if (!a) { toast("That app is not installed."); return; }
    S.query = S.query || {};
    S.query[a.pkg] = typeof m.query === "string" ? m.query.replace(/^[?&]+/, "").replace(/[^A-Za-z0-9%&=._~+-]/g, "").slice(0, 500) : "";
    openApp(a).then(() => { const t = S.tabs.find((x) => x.key === "app:" + a.pkg); if (t) drawApp(t); });
  });
  /* One tab's view, from the frame route's answer. Drawing never opens a browser tab (a restored tab, say). */
  async function drawApp(t) {
    const a = t.app;
    const f = await frameOf(a.pkg);
    S.frames[a.pkg] = f;
    if (!S.tabs.includes(t)) return;
    t.url = f.url || "";
    const row = S.rows.find((r) => r.id === a.pkg) || {};
    t.dash = f.kind === "app" ? "" : f.kind === "page" ? "#page/" + a.pkg + "/" + (a.firstPage || "") : "#apps";
    if (f.ok && f.kind === "page") {
      t.node.classList.add("w-framed");
      const extra = (S.query || {})[a.pkg] || "";          // WritersBot: a query a sibling page asked for (See in Maps)
      clear(t.node).appendChild(el("iframe", { class: "w-frame", src: extra ? f.url + (f.url.indexOf("?") >= 0 ? "&" : "?") + extra : f.url, sandbox: L.frameSandbox(f.sandbox || L.PAGE_SANDBOX),
        title: a.name, referrerpolicy: "no-referrer" }));
    } else if (f.kind === "app") {
      drawOutside(t);
    } else {
      const btns = el("div", { class: "w-btns" });
      if (a.program) btns.appendChild(btn("Open", async () => {
        const r = await api("POST", "/v1/packages/" + encodeURIComponent(a.pkg) + "/open/program");
        toast(r.status >= 400 ? a.name + " did not open: " + said(r) : a.name + " opens in its own window.");
      }, "primary"));
      if (f.running === false) btns.appendChild(btn("Start it", async () => {
        const s = await api("POST", "/v1/packages/" + encodeURIComponent(a.pkg) + "/start", {});
        if (s.status >= 400) { toast(a.name + " did not start: " + said(s)); return; }
        await loadRegistry();
        drawApp(t);
      }, a.program ? "" : "primary"));
      // WritersBot: a tool with no page still gets a real card: what it can do, and a way to ask for it in chat.
      const abilities = Array.isArray(row.abilities) ? row.abilities.filter((x) => x && typeof x.tool === "string") : [];
      const can = abilities.length ? el("ul", { class: "w-can" }, abilities.slice(0, 12).map((x) => {
        const short = x.tool.indexOf(a.pkg + ".") === 0 ? x.tool.slice(a.pkg.length + 1) : x.tool;
        const what = String(x.description || x.what || x.title || "").trim();
        return el("li", {}, [el("b", { text: short.replace(/_/g, " ") }), what ? el("span", { text: ": " + what }) : null]);
      })) : null;
      btns.appendChild(btn("Ask in chat", () => {
        $("input").value = "Use " + a.name + " to ";
        $("input").focus();
        closeRail();
      }, "primary"));
      clear(t.node).appendChild(el("div", { class: "w-card", "data-plain-card": a.pkg }, [
        el("h5", {}, [a.name, pill(f.running === false ? "stopped" : "running", f.running === false ? "dim" : "ok")]),
        row.description ? el("p", { text: String(row.description) }) : null,
        can ? el("p", { class: "w-fine", text: "What it can do:" }) : null, can,
        el("p", { class: "w-fine", text: "It works from the chat. Say what you want in your own words and your bot uses it." }), btns]));
    }
    renderAddr();
  }
  /* An outside app (D83): a card with what the brain read, by the same read_app it uses in chat, and Open in browser. */
  async function drawOutside(t) {
    const a = t.app;
    const read = el("div", { class: "w-card w-read" });
    clear(t.node).append(el("div", { class: "w-empty" }, [
      el("p", { text: a.name + " is an outside app, so it opens in your real browser, in a new tab. Ask me anything about it: I can read it." }),
      el("div", { class: "w-btns" }, [t.url ? btn("Open in browser ↗", () => openOutside(t.url), "primary") : null])]), read);
    renderAddr();
    readApp(a, read);
  }
  async function readApp(a, box) {
    const again = btn("Read it again", () => readApp(a, box));
    const now = await api("GET", "/v1/registry/" + encodeURIComponent(a.pkg));   // the row now: the list may be 30 s old
    if (now.status !== 200 || !now.data || now.data.status !== "running") {       // reading a stopped app starts it, and that asks a Yes: only from chat
      clear(box).append(el("p", { class: "w-fine", text: a.name + " is not running, so nothing was read. Ask me to read it in chat, and I will start it first." }),
        el("div", { class: "w-btns" }, [again]));
      return;
    }
    clear(box).append(el("p", { class: "w-fine" }, [spin(), " " + botName() + " is reading its page..."]));
    const r = await api("POST", "/v1/chat/tool", { tool: "read_app", args: { id: a.pkg } });
    const got = r.data && typeof r.data === "object" ? r.data : {};
    const res = got.result && typeof got.result === "object" ? got.result : null;
    if (r.status !== 200 || !res || res.ok === false) {
      clear(box).append(el("p", { class: "w-fine", text: (res && res.said) || said(r, "Its page could not be read just now.") }), el("div", { class: "w-btns" }, [again]));
      return;
    }
    const text = String(res.text || "");
    clear(box).append(el("p", { class: "w-fine", text: botName() + " read its page just now (read_app):" }),
      res.title ? el("h5", { text: String(res.title) }) : null,
      el("div", { class: "w-readtext", text: text.length > 1500 ? text.slice(0, 1500) + "..." : text }),
      el("div", { class: "w-btns" }, [again]));
  }

  /* Building: every build, live (the Desktop's rules; its picture model and library lines stay on the dashboard). */
  function typingPin(node) {
    return Array.from(node.querySelectorAll('input[type="password"]')).some((i) => i.value || document.activeElement === i);
  }
  function jobEl(j) {
    const stopped = ["held", "not_yet", "nothing_found", "said_no", "no_answer"].indexOf(j.status) >= 0;
    const cls = L.jobGood(j) ? "ok" : stopped ? "stop" : j.status.indexOf("building") === 0 ? "" : "wait";
    const mark = L.jobGood(j) ? el("span", { class: "w-mark", text: "✓" }) : stopped ? el("span", { class: "w-mark", text: "✕" })
      : j.status.indexOf("building") === 0 ? el("span", { class: "w-mark" }, [spin()]) : el("span", { class: "w-mark", text: "·" });
    const said2 = el("p", { class: "w-jobmore" });
    const out = [el("div", { class: "w-jobrow " + cls }, [mark, el("span", { text: j.name + ": " + j.words })])];
    [j.trying ? "Trying now: " + j.trying : "", L.stillWorking(j, Date.now()) || j.now, j.line].filter(Boolean).forEach((t) => out.push(el("p", { class: "w-jobmore", text: t })));
    if (j.canNext) out.push(el("div", { class: "w-jobmore" }, [btn("Try next app", async () => {
      const r = await api("POST", "/v1/toolsmith/jobs/" + encodeURIComponent(j.id) + "/next", {});
      said2.textContent = r.status === 200 ? (r.data && r.data.said) || "Toolsmith moves to the next app." : said(r);
    })]));
    out.push(said2);
    return out;
  }
  function againEl(v) {
    const says = el("p", { class: "w-says" }), cardBox = el("div"), unlock = el("div");
    return el("div", { class: "w-card" }, [
      el("p", { text: v.unfinished + (v.unfinished === 1 ? " job" : " jobs") + " did not get a working app. Toolsmith can try again. Jobs that work stay as they are." + L.againMore(v) }),
      el("div", { class: "w-btns" }, [btn("Check again", async () => {
        if (!(await keysReady(v.id, unlock, says, null))) return;
        says.textContent = "Asking Toolsmith to check again.";
        const r = await api("POST", "/v1/toolsmith/builds/" + encodeURIComponent(v.id) + "/again", {});
        if (r.status !== 200) { says.textContent = said(r, "Toolsmith could not check again."); return; }
        const asking = r.data && r.data.again && r.data.again.state === "asking" ? String(r.data.again.consent_id || "") : "";
        if (asking) { says.textContent = "Your Yes is needed again: " + String(r.data.again.why || ""); cardInto(cardBox, asking, {}); return; }
        says.textContent = "Toolsmith is checking again.";
        loadTool();
      }, "primary")]), unlock, says, cardBox]);
  }
  function unlockEl() {
    const says = el("p", { class: "w-says" });
    const box = pinBox("Your PIN");
    const open = async () => {
      const secret = box.value; box.value = "";
      if (!secret) { says.textContent = "Type your PIN first."; return; }
      const x = await api("POST", "/v1/vault/unlock", { method: "pin", secret });
      if (x.status !== 200) { says.textContent = said(x, "Your vault did not open."); return; }
      says.textContent = "Your vault is open. Toolsmith goes on.";
      drawStatus();
    };
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); open(); } });
    return el("div", { class: "w-card" }, [el("p", { text: L.UNLOCK_WAITING }), box, el("div", { class: "w-btns" }, [btn("Unlock", open, "primary")]), says]);
  }
  function buildEl(b, full, cards) {
    const v = L.buildView(full);
    const chip = L.buildChip(b, S.waiting.has(b.id));
    const started = v.created ? v.created.replace("T", " ").slice(0, 16) : "";
    const out = [el("h5", { text: "Build \"" + (v.goal || "a build") + "\"" + (started ? ", started " + started : "") })];
    if (v.planning) out.push(el("p", { class: "w-fine", text: "Toolsmith is planning: finding apps for each job." }));
    if (v.now) out.push(el("p", { class: "w-fine", text: (v.planning && L.stillWords(v.now, v.nowAt, Date.now())) || v.now }));
    if (v.said) out.push(el("p", { class: "w-fine", text: v.said }));
    if (chip.tone === "wait" && v.consentId && !v.yes) {
      const c = cards.find((x) => x.id === v.consentId);
      out.push(el("p", { class: "w-fine", text: "This build is waiting for your Yes on the plan card." }));
      if (c) out.push(el("div", { class: "w-btns" }, [btn("Show the card in the chat", () => showCardNow(c, "Here is the plan card again."))]));
    }
    if (v.active && v.yes && v.unlock) out.push(unlockEl());
    if (v.store.record) v.store.lines.forEach((t) => out.push(el("p", { class: "w-fine", text: t })));
    if (v.setup.length) out.push(el("p", { class: "w-fine", text: "Programs it needs first: " + v.setup.join(", ") + ". They are on Your turn." }));
    if (!v.jobs.length && !v.planning) out.push(el("p", { class: "w-fine", text: "No jobs in this build." }));
    v.jobs.forEach((j) => out.push(...jobEl(j)));
    if (v.canAgain) out.push(againEl(v));
    if (v.cost) out.push(el("p", { class: "w-fine", text: "Cloud cost so far: " + v.cost }));
    return el("div", { class: "w-build", "data-build": v.id }, out);
  }
  async function drawBuilding(t, again) {
    if (again && typingPin(t.node)) return;
    const [r, pend] = await Promise.all([api("GET", "/v1/toolsmith", undefined, L.POLL_WAIT_MS), pendingConsents()]);
    if (r.status !== 200) {
      if (again && L.pollCanRetry(r.status) && t.node.childElementCount) {    // D113: what it showed stays
        if (!t.node.querySelector("[data-poll-trouble]")) t.node.prepend(el("p", { class: "w-says", "data-poll-trouble": "1", text: L.POLL_RETRY }));
      } else clear(t.node).appendChild(el("p", { class: "w-empty", text: said(r, "Toolsmith did not answer.") }));
      return;
    }
    const builds = (r.data.builds || []).slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 12);
    const parts = [];
    if (!builds.length) parts.push(el("p", { class: "w-empty", text: "Nothing built yet. Tell Toolsmith what you want in chat, or with /build." }));
    for (const b of builds) {
      const full = await api("GET", "/v1/toolsmith/builds/" + encodeURIComponent(b.id), undefined, L.POLL_WAIT_MS);
      if (full.status === 200) parts.push(buildEl(b, full.data, pend.cards));
    }
    clear(t.node).append(...parts);
  }

  /* Your turn: every Yes card, the Checklist and the notices (the Desktop's rules). */
  function stepEl(c, after) {
    const secret = c.secret ? el("input", { type: "password", autocomplete: "off", placeholder: c.secret.key, "aria-label": c.secret.key }) : null;
    const p = c.needsPin ? pinBox() : null;
    const says = el("p", { class: "w-says", text: c.failed ? "Not done yet: " + c.failed : "" });
    const row = el("div", { class: "w-btns" });
    c.buttons.forEach((b) => {
      const b2 = btn(b.label, async () => {
        if (b.id === "copy") { await navigator.clipboard.writeText(c.text); b2.textContent = "Copied"; return; }
        if (b.id === "open") { openOutside(b.url); return; }
        if (b.id === "admin") {
          const body = L.adminAnswer(p ? p.value : "");
          if (p) p.value = "";
          const r = await api("POST", "/v1/checklist/" + encodeURIComponent(c.id) + "/run-admin", body);
          says.textContent = r.status === 200 ? "Windows asks you to allow it. When it has run, press Done." : (r.data || {}).reason || said(r);
          return;
        }
        if (b.id === "save") {
          const r = await api("PUT", "/v1/vault/secrets/" + encodeURIComponent(c.secret.key), { value: secret.value });
          secret.value = "";
          says.textContent = r.status === 200 || r.status === 201 || r.status === 202 ? "Saved in your vault. Press Done." : said(r, "Not saved.");
          return;
        }
        const r = await api("POST", "/v1/checklist/" + encodeURIComponent(c.id) + "/" + b.id);
        if (r.status === 409) { says.textContent = said(r, ""); return; }
        if (after) after();
      }, b.id === "done" ? "primary" : "");
      row.appendChild(b2);
    });
    return el("div", { class: "w-card" }, [el("h5", { text: c.title }), c.why ? el("p", { class: "w-fine", text: c.why }) : null,
      el("p", { class: "w-fine", text: c.raisedBy + (c.from ? ", for " + c.from : "") }),
      c.text ? el("pre", { text: c.text }) : null, c.shell ? el("p", { class: "w-fine", text: "In " + c.shell }) : null,
      c.needsPin ? el("p", { class: "w-fine", text: "Running as administrator runs exactly the command above. Type your PIN to allow it." }) : null,
      secret, p, says, row]);
  }
  async function drawTurn(t, again) {
    if (again && typingPin(t.node)) return;
    const [cons, steps, notes] = await Promise.all([pendingConsents(), api("GET", "/v1/checklist"), api("GET", "/v1/notify")]);
    const parts = [el("div", { class: "w-section", text: "Waiting for your Yes" })];
    if (!cons.cards.length) parts.push(el("p", { class: "w-fine", text: "No Yes cards waiting." }));
    cons.cards.forEach((c) => parts.push(consentEl(c, () => { redrawTab("turn"); surface(); })));
    parts.push(el("div", { class: "w-section", text: "Things to do" }));
    const list = steps.status === 200 && Array.isArray(steps.data) ? steps.data : [];
    if (steps.status !== 200) parts.push(el("p", { class: "w-says", text: said(steps) }));
    else if (!list.length) parts.push(el("p", { class: "w-fine", text: "Nothing for you to do right now." }));
    list.forEach((s) => parts.push(stepEl(L.stepCard(s), () => { redrawTab("turn"); refreshCounts(); })));
    if (notes.status === 200 && Array.isArray(notes.data) && notes.data.length) {
      parts.push(el("div", { class: "w-section", text: "Notices" }));
      notes.data.slice(0, 20).forEach((n) => parts.push(el("div", { class: "w-card" }, [el("h5", { text: n.title }), n.text ? el("p", { text: n.text }) : null,
        el("div", { class: "w-btns" }, [el("span", { class: "w-fine w-grow", text: "From " + n.package }),
          btn("Dismiss", async () => { await api("DELETE", "/v1/notify/" + encodeURIComponent(n.id)); redrawTab("turn"); })])])));
    }
    clear(t.node).append(...parts);
  }

  /* ---- Settings: looks, Toolsmith's cloud model, keys, GitHub, the same Core routes as the Desktop and the terminal.
   * A key is typed only into a password field, sent once, cleared at once and never shown back (D36(a), D81(d)). ---- */
  function applyLook(preview) {
    if (!S.themes.length) return;
    const id = preview || (S.settings && S.settings.bot && S.settings.bot.theme) || "";
    window.PuffTheme.look(L.themeById(S.themes, id), !preview);
  }
  async function loadSettings() {
    const r = await api("GET", "/v1/settings");
    if (r.status !== 200) return null;
    S.settings = r.data;
    S.pages = typeof r.data.pages === "string" ? r.data.pages.replace(/\/+$/, "") : "";
    if (r.data.network) {       // D130: on the home network, each package's own port, and its pass as a cookie
      const n = await api("GET", "/v1/network/pages");
      S.pages = n.status === 200 ? L.networkBase(location, n.data) : "";
    }
    drawName();
    return S.settings;
  }
  async function loadThemes() {
    const r = await api("GET", "/v1/settings/themes");
    S.themes = L.themeList(r.status === 200 ? r.data : null);
    applyLook();
    return S.themes;
  }
  function drawLooks(box) {
    const cur = L.themeById(S.themes, (S.settings && S.settings.bot && S.settings.bot.theme) || "");
    const part = document.documentElement.getAttribute("data-puff-theme");
    clear(box).appendChild(el("div", { class: "w-looks" }, S.themes.map((th, i) => {
      const t = L.themeTokensFor(th, part);
      const sw = el("span", { class: "w-sw" }, [el("i"), el("i"), el("i"), el("i")]);
      Object.keys(t).forEach((k) => sw.style.setProperty(k, t[k]));    // its own tokens, checked by logic.themeTokens
      return el("button", { class: "w-look" + (cur && cur.id === th.id ? " on" : ""), "data-look": th.id, on: { click: async () => {
        const r = await api("PUT", "/v1/settings/bot", { theme: th.id });
        if (r.status !== 200) { toast(said(r, "That look was not saved.")); return; }
        await loadSettings();
        applyLook();
        drawLooks(box);
      } } }, [sw, el("b", { text: th.name }), el("small", { text: th.id.indexOf("look-") === 0 ? th.id : i === 0 ? "built in, the default" : "built in" })]);
    }).concat([el("button", { class: "w-look", "data-add-look": "1", on: { click: () => showStore("Looks") } },
      [el("b", { text: "+ Add a look" }), el("small", { text: "from the store, or your own package" })])])));
  }
  function dayNight(after) {
    const cur = window.PuffTheme.choice();
    return el("div", { class: "w-seg", role: "group", "aria-label": "Day or night" }, [["auto", "By the clock"], ["day", "Day"], ["night", "Night"]].map(([v, label]) =>
      el("button", { class: cur === v ? "on" : "", "data-daynight": v, text: label, on: { click: () => { window.PuffTheme.set(v); after(); } } })));
  }
  /* Toolsmith's cloud model: its own plain section. Company, its consent sentence, model, key and cost cap. */
  async function drawCloudModel(box) {
    const [st, prov, keys] = await Promise.all([api("GET", "/v1/toolsmith"), api("GET", "/v1/toolsmith/providers"), api("GET", "/v1/toolsmith/keys")]);
    clear(box);
    if (st.status !== 200) { box.appendChild(el("p", { class: "w-says", text: said(st) })); return; }
    const providers = prov.status === 200 && Array.isArray(prov.data) ? prov.data : [];
    const kept = keys.status === 200 && Array.isArray(keys.data) ? keys.data : [];
    const cloud = st.data.where === "cloud";
    const nowName = cloud ? (st.data.company || st.data.provider) + ", " + (st.data.model || "") : "This computer. Builds use the brain on this computer.";
    const says = el("p", { class: "w-says", "data-said": "cloud" });
    const company = el("select", { class: "w-field", "data-cloud": "company", "aria-label": "Which company" }, [el("option", { value: "", text: "This computer" })]
      .concat(providers.map((p) => el("option", { value: p.id, text: p.company + (p.problems && p.problems.length ? " (not ready)" : "") }))));
    company.value = cloud ? st.data.provider || "" : "";
    const detail = el("div");
    let read = () => ({ where: "local" }), send = async () => true;
    function paint() {
      clear(detail);
      read = () => ({ where: "local" });
      send = async () => true;
      const p = providers.find((x) => x.id === company.value);
      if (!p) return;
      const here = cloud && st.data.provider === p.id;
      const model = el("select", { class: "w-field", "data-cloud": "model", "aria-label": "Model" }, p.models.map((m) => el("option", { value: m.id, text: m.name })));
      model.value = here ? st.data.model : p.default_model || (p.models[0] || {}).id;
      const capNow = here && st.data.cost_cap_usd !== undefined ? st.data.cost_cap_usd : p.cost_cap_usd;
      const cap = el("input", { class: "w-field", "data-cloud": "cap", type: "number", min: "0", step: "0.5", value: String(capNow), "aria-label": "Cost cap for each build, in US dollars" });
      const agree = el("input", { type: "checkbox", "data-cloud": "agree" });
      const k = kept.find((x) => x.provider === p.id) || { kept: null };
      const keyField = el("input", { class: "w-field w-wide", "data-cloud": "key", type: "password", autocomplete: "off", spellcheck: "false", maxlength: "4000",
                                     placeholder: "Paste your key", "aria-label": p.company + " key" });
      const keyWords = k.kept === true ? "A key is saved." : k.kept === false ? "No key saved yet." : "Your vault is locked, so this can't tell if a key is saved. A key pasted here is sealed for your vault.";
      const remove = k.kept === true ? btn("Remove it", async () => {
        const x = await api("DELETE", "/v1/toolsmith/keys/" + encodeURIComponent(p.id));
        says.textContent = x.status === 200 ? (x.data && x.data.line) || "The key is removed." : said(x);
        if (x.status === 200) drawCloudModel(box);
      }) : null;
      detail.append(
        here && st.data.consent_changed ? el("div", { class: "w-row2" }, [el("div", { class: "w-fine", "data-cloud": "consent-changed", text: st.data.consent_changed + " Toolsmith sends it nothing until you tick Yes and press Save." })]) : "",
        el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "What is sent" }), el("div", { class: "w-fine", "data-cloud": "consent", text: p.consent })])]),
        el("label", { class: "w-row2" }, [el("span", { class: "w-grow" }, [agree, " Yes to that: this is what Toolsmith sends to " + p.company + "."])]),
        el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "Model" })]), model]),
        el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "Key" }), el("div", { class: "w-fine", "data-cloud": "key-state", text: keyWords }),
          el("div", { class: "w-fine", text: "It goes into your vault as " + p.key.vault_name + ", never into chat. Nothing is sent to the company before you say Yes to a plan." })]),
        keyField, remove, p.key.get_it ? btn("Get a key ↗", () => openOutside(p.key.get_it)) : null]),
        el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "Cost cap" }), el("div", { class: "w-fine", text: "For each build, in US dollars. Toolsmith pauses and asks before it goes over." })]), cap]));
      read = () => ({ where: "cloud", provider: p.id, model: model.value, cost_cap_usd: Number(cap.value), consent_sha: agree.checked ? p.consent_sha : "", same: here && !st.data.consent_changed && model.value === st.data.model, agreed: agree.checked });
      send = async () => {
        const value = keyField.value.trim();
        keyField.value = "";                     // cleared the moment it is sent
        if (!value) return true;
        const x = await api("PUT", "/v1/toolsmith/keys/" + encodeURIComponent(p.id), { value });
        if (x.status !== 200 && x.status !== 202) { says.textContent = said(x, "The key was not saved."); return false; }
        return true;
      };
    }
    company.addEventListener("change", paint);
    paint();
    const save = btn("Save", async () => {
      says.textContent = "";
      const b = read();
      if (!(await send())) return;
      let r;
      if (b.where === "cloud" && b.same && !b.agreed) {            // no new Yes: only a lower cap, at once; the Core refuses a higher one
        r = await api("PUT", "/v1/toolsmith/cap", { cost_cap_usd: b.cost_cap_usd });
      } else {
        r = await api("PUT", "/v1/toolsmith/model", b.where === "cloud"
          ? { where: "cloud", provider: b.provider, model: b.model, cost_cap_usd: b.cost_cap_usd, consent_sha: b.consent_sha } : { where: "local" });
      }
      if (r.status !== 200) { says.textContent = r.data && r.data.code === "needs_yes" ? "A higher cap needs your Yes to what is sent. Tick it and press Save." : said(r, "Not saved."); return; }
      await drawCloudModel(box);
      const again = box.querySelector("[data-said=cloud]");
      if (again) again.textContent = r.data.where === "cloud" ? "Saved, at most $" + r.data.cost_cap_usd + " for each build." : "Saved: this computer.";
    }, "primary");
    save.setAttribute("data-cloud", "save");
    box.append(
      el("p", { class: "w-fine", text: "Toolsmith is the only agent that may use a cloud model, and only if you pick one here. Everything else runs on this computer. " + PRIVACY }),
      el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "Which company" }), el("div", { class: "w-fine", "data-cloud": "now", text: "Now: " + nowName })]), company]),
      detail, el("div", { class: "w-btns" }, [save]), says);
  }
  /* Keys an app asks for: its Checklist step (enter_secret), kept in the vault. */
  async function drawAppKeys(box) {
    const r = await api("GET", "/v1/checklist");
    const steps = r.status === 200 && Array.isArray(r.data) ? r.data.filter((s) => s.kind === "enter_secret") : [];
    clear(box);
    if (!steps.length) box.appendChild(el("div", { class: "w-row2" }, [el("div", { class: "w-fine", text: "No app is asking for a key right now." })]));
    steps.forEach((s) => {
      const c = L.stepCard(s);
      const says = el("p", { class: "w-says" });
      const field = el("input", { class: "w-field w-wide", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "Paste it", "aria-label": c.secret.key, hidden: true });
      const go = btn("Add key", async () => {
        if (field.hidden) { field.hidden = false; field.focus(); return; }
        const value = field.value;
        field.value = "";                       // cleared the moment it is sent
        if (!value) { says.textContent = "Paste the key first."; return; }
        const x = await api("PUT", "/v1/vault/secrets/" + encodeURIComponent(c.secret.key), { value });
        if (x.status !== 200 && x.status !== 201 && x.status !== 202) { says.textContent = said(x, "Not saved."); return; }
        const d = await api("POST", "/v1/checklist/" + encodeURIComponent(c.id) + "/done");
        says.textContent = d.status === 409 ? said(d, "") : "A key is saved in your vault.";
        field.hidden = true;
        refreshCounts();
      });
      box.appendChild(el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: c.secret.key }),
        el("div", { class: "w-fine", text: (c.from ? c.from + " asks for it. " : "") + "Goes into the vault, never into chat." }), says]), field, go]));
    });
  }
  /* GitHub: the rules of github.js (its keyView, keyShape and addressCheck), in the pane's own rows. */
  async function drawGitHub(box, note) {
    const r = await api("GET", "/v1/github/key");
    const v = GH.keyView(r);
    clear(box);
    const says = el("p", { class: "w-says", text: note || "" });
    const row = el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "GitHub" }), el("div", { class: "w-fine", "data-gh": v.state, text: v.line || v.title + "." })])]);
    if (v.unlink) {
      row.appendChild(btn("Unlink", async () => {
        const x = await api("DELETE", "/v1/github/key");
        drawGitHub(box, x.status === 200 ? (x.data && x.data.line) || "Unlinked." : said(x));
      }));
    }
    let form = null;
    if (v.form) {
      const field = el("input", { class: "w-field w-wide", type: "password", autocomplete: "off", spellcheck: "false", maxlength: "300", placeholder: "Paste the token", "aria-label": "GitHub token", hidden: true });
      const help = el("div", { hidden: true }, [el("ol", { class: "w-fine" }, GH.TOKEN_STEPS.map((s) => el("li", { text: s }))),
        el("p", { class: "w-fine", text: GH.KEPT_LINE + " " + GH.SENT_LINE }), btn("Make a read-only token on GitHub ↗", () => openOutside(GH.TOKEN_PAGE))]);
      const link = btn("Link GitHub", async () => {
        if (field.hidden) { field.hidden = false; help.hidden = false; field.focus(); return; }
        const shape = GH.keyShape(field.value);
        if (!shape.ok) { field.value = ""; says.textContent = shape.line; return; }
        const value = field.value.trim();
        field.value = "";                       // cleared the moment it is sent
        const x = await api("PUT", "/v1/github/key", { value });
        if (x.status === 200 || x.status === 202) { drawGitHub(box, (x.data && x.data.line) || "Linked."); return; }
        says.textContent = said(x, "That was not saved.");
      });
      row.append(field, link);
      form = help;
    } else if (v.setup) {
      row.appendChild(btn("Set up the vault ↗", () => openDashboard("#vault")));
    }
    const address = el("input", { class: "w-field w-wide", type: "text", autocomplete: "off", spellcheck: "false", maxlength: "500", placeholder: "Paste a GitHub address to add its code", "aria-label": "GitHub project address" });
    const add = btn("Add", async () => {
      const a = GH.addressCheck(address.value);
      if (!a.ok) { says.textContent = a.line; return; }
      add.disabled = true;
      says.textContent = "Toolsmith is reading that project and planning. This can take a few minutes; Building shows it live.";
      const x = await api("POST", "/v1/github/code", { address: a.address });
      add.disabled = false;
      if (x.status !== 201) { says.textContent = said(x); return; }
      says.textContent = "The plan is in the chat, with its one Yes card.";
      address.value = "";
      planInChat(L.buildView(x.data));
    }, "primary");
    box.append(row, form, el("div", { class: "w-row2" }, [address, add]), says);
  }
  function drawSettings(t) {
    const chatLine = el("div", { class: "w-fine", text: L.chatWords(S.signin) });
    api("GET", "/v1/signin").then((r) => { if (r.status === 200 && r.data) chatLine.textContent = L.chatWords(r.data); }).catch(() => {});
    const looks = el("div"), cloud = el("div"), keysBox = el("div"), github = el("div");
    const redrawLooks = () => { drawLooks(looks); clear(day).appendChild(dayNight(redrawLooks)); };
    const day = el("div");
    clear(t.node).appendChild(el("div", { class: "w-set" }, [
      el("h4", { id: "set-look", text: "Look" }), looks,
      el("div", { class: "w-row2" }, [el("div", {}, [el("b", { text: "Day or night" }), el("div", { class: "w-fine", text: "Kept on this browser." })]), day]),
      el("h4", { id: "set-cloud", text: "Toolsmith's cloud model" }), cloud,
      el("h4", { id: "set-keys", text: "Keys and APIs" }), keysBox,
      el("h4", { id: "set-documents", text: "Your documents" }), window.Puff.editsSetting(),
      el("h4", { id: "set-github", text: "GitHub" }), github,
      el("h4", { text: "Also here" }),
      el("div", { class: "w-row2" }, [chatLine]),
      el("div", { class: "w-row2" }, [el("div", { class: "w-fine", text: "Who your bot is, you, the brain, the Bridge, which view opens first, avatars." }),
        btn("More settings", () => openPage("settings")), btn("Looks and avatars", () => openPage("looks")), btn("Vault", () => openPage("vault"))]),
      el("p", { class: "w-sign", text: PRIVACY + " " + SIGNATURE })]));
    redrawLooks();
    drawCloudModel(cloud);
    drawAppKeys(keysBox);
    drawGitHub(github);
  }
  function showSettings(section) {
    const t = openTab(settingsTab());
    const n = t.node.querySelector("#" + section);
    if (n) n.scrollIntoView({ block: "start" });
  }

  /* ---- The PuffPAI store (D115): the dashboard's one panel (pages/apps.js) in the side pane. Every kind of package, on
   * the same routes, the same rules (logic.storeShelf) and the same one Yes card. D96: the store is read only because
   * she opened this tab (so restoreTabs never brings it back), and the built-in rows never wait for the network. ---- */
  function storeJump(t) {
    const n = t.jump ? t.node.querySelector('[data-store-section="' + t.jump + '"]') : null;
    if (n) n.scrollIntoView({ block: "start" });
  }
  /* *section*: a shelf's own title in logic.js ("Looks"), never words from outside. */
  function showStore(section) {
    const t = openTab(storeTab());
    t.jump = section || "";
    storeJump(t);
    return t;
  }
  function storePic(p) {
    const glyph = () => el("span", { class: "msr", "aria-hidden": "true", text: p.glyph || "deployed_code" });
    if (!p.icon) return glyph();
    const img = el("img", { src: p.icon, alt: "" });     // storeBuiltIn lets only a data: picture through; a store row has none
    img.addEventListener("error", () => { if (img.parentNode) img.parentNode.replaceChild(glyph(), img); }, { once: true });
    return img;
  }
  async function storeAdd(p, add, said2) {
    add.disabled = true;
    clear(said2).appendChild(el("p", { class: "w-fine", text: "Checking it first..." }));
    const r = await api("POST", "/v1/packages", { source: p.add || p.id, link: false });
    clear(said2);
    if (r.status !== 202 || !r.data || !r.data.consent_id) {
      said2.appendChild(el("p", { class: "w-says", text: said(r) }));
      add.disabled = false;
      return;
    }
    S.inChat.add(r.data.consent_id);                     // its card is here, so surface() does not draw it in a chat too
    cardInto(said2, r.data.consent_id, { after: async (answer) => {
      if (answer !== "yes") { add.disabled = false; return; }
      clear(said2).appendChild(el("p", { class: "w-fine", text: "Installing..." }));
      const id = r.data.id;
      let done = L.installState(null);
      for (let i = 0; id && i < 1800 && !done.final && !S.signedOut; i++) {
        const now = await api("GET", "/v1/registry/" + encodeURIComponent(id));
        done = L.installState(now.status === 200 ? now.data : null);
        if (!done.final) await new Promise((ok) => setTimeout(ok, 1000));
      }
      await loadRegistry();
      clear(said2);
      if (!done.final) { said2.appendChild(el("p", { class: "w-fine", text: "It is still installing. It joins your apps when it is ready." })); return; }
      if (!done.ok) { said2.appendChild(el("p", { class: "w-failed", text: done.line })); return; }
      add.remove();
      said2.appendChild(el("p", { class: "w-fine", text: p.name + " is installed." }));
      const a = S.apps.find((x) => x.pkg === id);
      if (a) said2.appendChild(appChip(a));
      if (p.kind === "look") {
        await loadThemes();
        redrawTab("settings");
        said2.appendChild(el("button", { class: "w-chipbtn", text: "Pick it in Settings", on: { click: () => showSettings("set-look") } }));
      }
    } });
  }
  function storeRowEl(p) {
    const said2 = el("div", { class: "w-storesaid" });
    const add = btn("Add", () => storeAdd(p, add, said2), "primary");
    add.setAttribute("data-store-add", p.id);
    return el("div", { class: "w-storerow", "data-store-row": p.id }, [
      el("div", { class: "w-storepic" }, [storePic(p)]),
      el("div", {}, [el("b", { text: p.name }), p.line ? el("div", { class: "w-fine", text: p.line }) : null,
        p.store && p.by ? el("div", { class: "w-fine", text: p.by + "." }) : null,
        p.tierLine ? el("div", { class: "w-fine", text: p.tierLine }) : null,
        p.from ? el("div", { class: "w-fine", text: p.from }) : null,
        p.pulled ? el("p", { class: "w-failed", text: p.pulled }) : null,
        p.about ? el("details", {}, [el("summary", { text: "About" }), el("p", { text: p.about })]) : null]),
      p.pulled ? el("span") : add, said2]);
  }
  /* The store's sources: the official one, always on, then hers, each with Remove, and adding one. */
  function storeSourcesEl(sources, t) {
    const said2 = el("p", { class: "w-says" });
    const out = [el("h4", { text: "Where the store reads from" })];
    sources.forEach((s) => {
      out.push(el("div", { class: "w-row2" }, [el("div", { class: "w-fine", text: s.official ? "The official PuffPAI store. Always on." : s.source + ". What it lists installs as Community." }),
        s.removable ? btn("Remove", async () => {
          const r = await api("DELETE", "/v1/store/sources?source=" + encodeURIComponent(s.source));
          if (r.status === 200) drawStore(t); else said2.textContent = said(r);
        }) : null]));
    });
    const text = el("input", { class: "w-field w-wide", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "https://example.com/index.json, or a folder on this computer", "aria-label": "A store source" });
    const form = el("form", { class: "w-storeform" }, [text, el("button", { class: "w-btn", type: "submit", text: "Add a source" })]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!text.value.trim()) return;
      const r = await api("POST", "/v1/store/sources", { source: text.value.trim() });
      if (r.status === 201) drawStore(t); else said2.textContent = said(r);
    });
    return out.concat([form, said2]);
  }
  async function drawStore(t, fresh) {
    const r = await api("GET", "/v1/packages/available");
    if (!S.tabs.includes(t)) return;
    if (r.status !== 200) { clear(t.node).appendChild(el("p", { class: "w-says", text: said(r) })); return; }
    const stat = pill("");
    const body = el("div");
    clear(t.node).appendChild(el("div", { class: "w-set", "data-store": "1" }, [el("h4", { class: "w-store-head" }, ["PuffPAI store", stat]), body]));
    let pressed = false;                                 // a press on a row: the rows stay as they are under her hand
    body.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("button")) pressed = true; });
    const draw = (answer, note, read) => {
      const shelf = L.storeShelf(r.data, answer);
      clear(body);
      stat.textContent = shelf.count + " ready to add";
      body.appendChild(el("p", { class: "w-fine", text: "Adding one shows its Yes card first. " + L.STORE_LINE }));
      if (note) body.appendChild(el("p", { class: "w-fine", text: note }));
      shelf.lines.forEach((line) => body.appendChild(el("p", { class: "w-fine", "data-store-line": "1", text: line })));
      if (!shelf.sections.some((s) => s.title === "Apps")) {
        body.appendChild(el("p", { class: "w-fine", text: shelf.lines.length || note ? "No app to add right now." : "Every app in the PuffPAI store is installed." }));
      }
      shelf.sections.forEach((s) => {
        body.appendChild(el("h4", { text: s.title, "data-store-section": s.title }));
        if (s.line) body.appendChild(el("p", { class: "w-fine", text: s.line }));
        s.items.forEach((p) => body.appendChild(storeRowEl(Object.assign({}, p, s.kindWord ? {} : { line: p.kind + ". " + p.line }))));
      });
      if (read) body.appendChild(el("div", { class: "w-btns" }, [btn("Check the store again", () => drawStore(t, true))]));
      if (answer) storeSourcesEl(shelf.sources, t).forEach((n) => body.appendChild(n));
      storeJump(t);
    };
    draw(null, "Reading the store...");
    const s = await api("GET", "/v1/store" + (fresh ? "?fresh=1" : ""));
    if (!body.isConnected) return;                       // she closed it, or a newer draw took its place
    if (pressed) { body.insertBefore(el("p", { class: "w-fine", text: "The store's own rows show when you open Store again." }), body.firstChild); return; }
    if (s.status === 200) { draw(s.data, "", true); return; }
    // The listing failed: she can still remove the source that may be the cause.
    const src = await api("GET", "/v1/store/sources");
    if (!body.isConnected || pressed) return;
    draw(src.status === 200 && src.data ? { packages: [], sources: src.data.sources } : null, "The store could not be read: " + said(s), true);
  }

  /* ---- commands, typed or from the / list. They run on the same Core routes; none goes to the brain ---- */
  function planInChat(v) {
    const c = currentView();
    const m = say(c, v.consentId && !v.yes ? "Toolsmith made a plan." : v.said || "Toolsmith has no plan for this yet.");
    if (v.consentId && !v.yes) { S.inChat.delete(v.consentId); cardInto(m.asks, v.consentId, { inChat: true, view: c }); }
    loadTool();
    openTab(buildingTab());
    redrawTab("building");
  }
  async function askPlan(view, plan, words) {
    say(view, words);
    const r = await api("POST", "/v1/toolsmith/plan", plan);
    if (r.status !== 201) { say(view, "No plan: " + said(r, "Toolsmith could not make a plan this time.")); return; }
    planInChat(L.buildView(r.data));
  }
  async function addFrom(view, source) {
    say(view, "Checking it first...");
    const r = await api("POST", "/v1/packages", { source, link: false });
    if (r.status !== 202 || !r.data || !r.data.consent_id) { say(view, said(r)); return; }
    const m = say(view, "Here is what adding it means.");
    S.inChat.delete(r.data.consent_id);
    cardInto(m.asks, r.data.consent_id, { inChat: true, view, after: async (answer) => {
      if (answer !== "yes") return;
      const status = say(view, "Installing...");
      const id = r.data.id;
      let done = L.installState(null);
      for (let i = 0; id && i < 1800 && !done.final; i++) {
        done = L.installState((await loadRegistry()).find((x) => x.id === id));
        if (!done.final) await new Promise((ok) => setTimeout(ok, 1000));
      }
      drawWords(status.words, done.final ? (done.ok ? "It is installed." : done.line) : "It is still installing.");
    } });
  }
  function vaultCard(view) {
    const m = say(view, "");
    const box = el("div", { class: "w-card" });
    m.asks.appendChild(box);
    const draw = async () => {
      const r = await api("GET", "/v1/vault/status");
      const v = L.vaultView(r.status === 200 ? r.data : {});
      clear(box).appendChild(el("h5", { text: "Vault: " + v.words }));
      const says = el("p", { class: "w-says" });
      if (v.open) {
        box.append(el("div", { class: "w-btns" }, [btn("Lock it now", async () => {
          const x = await api("POST", "/v1/vault/lock", {});
          if (x.status >= 400) { says.textContent = said(x); return; }
          draw(); drawStatus();
        })]), says);
      } else if (!v.setUp) {
        box.append(el("p", { class: "w-fine", text: "Set it up in the dashboard." }), el("div", { class: "w-btns" }, [btn("Open in dashboard ↗", () => openDashboard("#vault"))]));
      } else {
        const p = pinBox("Your PIN");
        const open = async () => {
          const secret = p.value; p.value = "";
          if (!secret) { says.textContent = "Type your PIN first."; return; }
          const x = await api("POST", "/v1/vault/unlock", { method: "pin", secret });
          if (x.status !== 200) { says.textContent = said(x, "Your vault did not open."); return; }
          draw(); drawStatus();
        };
        p.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); open(); } });
        box.append(p, el("div", { class: "w-btns" }, [btn("Unlock", open, "primary")]), el("p", { class: "w-fine", text: "The PIN never goes into the chat history." }), says);
        p.focus();
      }
      atEnd(view);
    };
    draw();
  }
  function bridgeCard(view) {
    const m = say(view, "");
    const box = el("div", { class: "w-card" });
    m.asks.appendChild(box);
    const draw = async () => {
      const r = await api("GET", "/v1/bridge");
      if (r.status !== 200) { clear(box).appendChild(el("p", { text: said(r, "The Bridge did not answer.") })); return; }
      const v = L.bridgeView(r.data);
      const says = el("p", { class: "w-says" });
      const pinFor = L.bridgeOpen(r.data);
      const p = v.action === "open" && pinFor.needsPin ? pinBox() : null;
      clear(box).append(el("h5", { text: r.data.state === "open" ? "The Bridge is open" : "The Bridge is closed" }), el("p", { text: v.text }));
      if (p) pinFor.lines.forEach((l) => box.appendChild(el("p", { class: "w-fine", text: l })));
      if (p) box.appendChild(p);
      if (v.button) box.appendChild(el("div", { class: "w-btns" }, [btn(v.button, async () => {
        const x = v.action === "close" ? await api("POST", "/v1/bridge/close", {}) : await api("POST", "/v1/bridge/open", p ? { pin: p.value } : {});
        if (p) p.value = "";
        if (x.status !== 200) { says.textContent = said(x, "That did not work."); return; }
        draw(); drawStatus();
      }, v.action === "close" ? "" : "primary")]));
      box.append(says, el("p", { class: "w-fine", text: PRIVACY }));
      atEnd(view);
    };
    draw();
  }
  const PANE_PAGES = { "/know": ["knowledge"], "/link": ["knowledge"],"/people": ["people"], "/dev": ["developer"] };
  async function runCommand(name, rest) {
    const view = currentView();
    addYou(view, ["/unlock", "/vault", "/lock"].indexOf(name) >= 0 ? name : (name + (rest ? " " + rest : "")));   // never a PIN typed after it
    if (name === "/dashboard") { openDashboard(""); say(view, "That opens in the dashboard, in your browser."); return; }
    if (name in PANE_PAGES) { openPage(...PANE_PAGES[name]); say(view, "It is open in the side pane."); return; }
    if (name === "/show") {
      const t = L.showTarget(rest, S.apps);
      if (t.kind === "building") openTab(buildingTab());
      else if (t.kind === "turn") openTab(turnTab());
      else if (t.kind === "settings") openTab(settingsTab());
      else if (t.kind === "store") showStore();
      else if (t.kind === "app") { openApp(t.app); say(view, kindOf(t.app) === "app" ? t.app.name + " is an outside app, so it opens in a new tab of your browser. What I read from it is in the side pane."
        : "Showing " + t.app.name + " in the side pane."); }
      else if (t.kind === "many") say(view, "More than one app matches: " + t.apps.map((a) => a.name).join(", ") + ".");
      else say(view, rest ? "I couldn't find that. Try /apps." : "Say what to show, like /show building. /apps lists the apps.");
      return;
    }
    if (name === "/build") {
      if (!rest) { say(view, "Say what to build, like /build a recipe box for my family."); return; }
      const s = L.addSource(rest);
      await askPlan(view, s.plan || { goal: rest.slice(0, 600) }, "Toolsmith is finding apps for that and planning. This can take a few minutes.");
      return;
    }
    if (name === "/plan") {
      const { cards } = await pendingConsents();
      const plan = cards.filter((c) => c.plan).pop();
      if (plan) showCardNow(plan, "Here is the plan card again."); else say(view, "No plan is waiting for your Yes. /build <goal> makes one.");
      return;
    }
    if (name === "/yes" || name === "/no") {
      const { cards } = await pendingConsents();
      syncCards(cards);
      const seen = S.shownCards.filter((e) => view.node.contains(e.holder) && e.card).pop();
      if (seen) { seen.card.answerIt(name === "/yes"); return; }     // a card she can see, the same as its button
      const newest = cards[cards.length - 1];
      if (newest) showCardNow(newest, "Read this card first, then press its button or type " + name + ".");
      else say(view, "No Yes card is waiting.");
      return;
    }
    if (name === "/watch") { openTab(buildingTab()); say(view, "Building is open in the side pane."); return; }
    if (name === "/turn") { openTab(turnTab()); say(view, "Your turn is open in the side pane."); return; }
    if (name === "/apps") {
      const m = say(view, S.apps.length ? "Your apps:" : "No apps yet. Ask for one, or use /add.");
      S.apps.forEach((a) => m.chips.appendChild(appChip(a)));
      m.chips.appendChild(el("button", { class: "w-chipbtn", text: "▣ The PuffPAI store", on: { click: () => showStore() } }));
      return;
    }
    if (name === "/store") { showStore(); say(view, "The store is open in the side pane. Adding one shows its Yes card first."); return; }
    if (name === "/add") {
      const s = L.addSource(rest);
      if (s.kind === "empty") { say(view, "Say what to add: a folder, a GitHub address, or a package id."); return; }
      if (s.kind === "folder" || s.kind === "builtin") { await addFrom(view, s.source); return; }
      await askPlan(view, s.plan, "Toolsmith is reading that and planning. This can take a few minutes.");
      return;
    }
    if (name === "/open") {
      const t = L.showTarget(rest, S.apps);
      if (t.kind !== "app") { say(view, t.kind === "many" ? "More than one app matches: " + t.apps.map((a) => a.name).join(", ") + "." : "There is no app by that name. /apps lists them."); return; }
      const url = (await frameOf(t.app.pkg)).url;
      if (url) { openOutside(url); say(view, "Opened " + t.app.name + " in a new tab of your browser."); } else say(view, t.app.name + " has no address to open.");
      return;
    }
    if (name === "/vault" || name === "/unlock" || name === "/lock") { vaultCard(view); return; }
    if (name === "/bridge") { bridgeCard(view); return; }
    if (name === "/brain") {
      const r = await api("GET", "/v1/model");
      const b = L.brainView(r.status === 200 ? r.data : {});
      say(view, "The brain is **" + b.name + "**: " + b.words + (b.reason ? ", " + b.reason : "") + ". It runs on this computer. /dashboard changes it in Setup.");
      return;
    }
    if (name === "/look") { showSettings("set-look"); say(view, "Settings are open in the side pane."); return; }
    if (name === "/keys") { showSettings("set-cloud"); say(view, "Settings are open in the side pane. Keys go into the vault there, never through chat."); return; }
    if (name === "/github") { showSettings("set-github"); say(view, "Settings are open in the side pane. Keys go into the vault there, never through chat."); return; }
    if (name === "/settings") { showSettings("set-look"); say(view, "Settings are open in the side pane."); return; }
    if (name === "/clear") { clear(view.node); intro(view); return; }
    if (name === "/help") {
      const hits = L.commandHits(rest, S.commands);
      say(view, hits.length ? hits.map((c) => "- `" + c.name + (c.args ? " " + c.args : "") + "` " + c.what).join("\n") : "No command matches that. Ask me in plain words instead.");
    }
  }
  const isCommand = (name) => L.WINDOW_COMMANDS.some((c) => c.name === name);
  function fillFrom(c) { $("input").value = c.name + " " + (c.from ? c.args : ""); drawPalette(""); $("input").focus(); }
  function hitsFor(text) {
    const p = L.commandParse(text);
    if (p && p.rest && isCommand(p.name)) return null;       // the command is typed: words after it are its own
    return L.commandHits(text, S.commands);
  }
  function drawPalette(text) {
    const box = $("palette");
    if (!text.startsWith("/")) { box.hidden = true; return; }
    const hits = hitsFor(text);
    if (!hits) { box.hidden = true; return; }
    S.sel = Math.min(S.sel, Math.max(hits.length - 1, 0));
    const w = text.slice(1).trim();
    clear(box).appendChild(el("div", { class: "w-head", text: hits.length + (hits.length === 1 ? " command" : " commands") + (w ? " matching \"" + w + "\"" : "") + ". Apps add their own (right column says who)." }));
    hits.forEach((c, i) => box.appendChild(el("div", { class: "w-row" + (i === S.sel ? " sel" : ""), "data-cmd": c.name, on: { click: () => fillFrom(c) } }, [
      el("span", { class: "w-cmd", text: c.name + (c.args ? " " + c.args : "") }), el("span", { class: "w-desc", text: c.what }), el("span", { class: "w-from", text: c.from || "Core" })])));
    if (!hits.length) box.appendChild(el("div", { class: "w-row" }, [el("span", { class: "w-desc", text: "No command. Press Enter to ask the bot instead." })]));
    box.hidden = false;
    const on = box.querySelector(".sel");
    if (on) on.scrollIntoView({ block: "nearest" });
  }
  function submit() {
    const input = $("input");
    const text = input.value.trim();
    if (!text) return;
    const v = currentView();
    const p = L.commandParse(text);
    if (p && isCommand(p.name)) { input.value = ""; drawPalette(""); runCommand(p.name, p.rest); return; }
    if (text.startsWith("/")) {
      const hits = hitsFor(text);
      const c = hits && hits[S.sel];
      if (c) {
        if (c.args && !c.from) { fillFrom(c); return; }
        input.value = ""; drawPalette(""); runCommand(c.name, c.from ? c.args : "");
        return;
      }
    }
    if (v.busy) return;
    input.value = "";
    drawPalette("");
    chatTurn(v, text);
  }

  /* ---- live updates from GET /v1/events, read with fetch so it can carry her login's headers (D79) ---- */
  function listen() {
    const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
    const handle = (block) => {
      let type = "message", text = "";
      block.split("\n").forEach((line) => {
        if (line.indexOf("event:") === 0) type = line.slice(6).trim();
        else if (line.indexOf("data:") === 0) text += line.slice(5).trim();
      });
      if (type === "message") return;
      let data = null;
      try { data = JSON.parse(text); } catch (e) { data = null; }
      fire(type, data);
      if (type === "consent.requested" || type === "consent.granted" || type === "consent.revoked") { surface(); refreshCounts(); refreshOpenTabs(); loadTool(); }
      if (type === "checklist.changed") { refreshCounts(); redrawTab("turn"); }
      if (type === "notify.posted") { toolsmithToChat(); redrawTab("turn"); redrawTab("building"); }
      if (type === "vault.locked" || type === "vault.unlocked") S.vaultNotNow = false;
      if (type === "bridge.posture" || type === "vault.locked" || type === "vault.unlocked") drawStatus();
      if (type === "wizard.progress") { loadSettings(); drawRailMore(); }
    };
    (async () => {
      while (!S.signedOut) {
        let r = null;
        try { r = await fetch("/v1/events", { headers: authHeaders({ Accept: "text/event-stream" }), credentials: "same-origin", cache: "no-store" }); } catch (e) { r = null; }
        if (!r || !r.ok || !r.body) {
          if (r && r.status === 401) { signIn(""); return; }
          await wait(3000);
          continue;
        }
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = "";
        try {
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf = (buf + dec.decode(value, { stream: true })).replace(/\r\n/g, "\n");
            let i;
            while ((i = buf.indexOf("\n\n")) >= 0) { handle(buf.slice(0, i)); buf = buf.slice(i + 2); }
          }
        } catch (e) { /* the stream ended; it comes back below */ }
        await wait(1000);
      }
    })();
  }

  /* ---- the dashboard's own pages in the side pane (D115). pages/*.js draw through window.Puff here as they do on the
   * dashboard: the same files, the same Core routes, no second copy, so a page she opens here is the page she knows.
   * Only what changes with the place is the window's own: a page is a tab, a link goes to a tab, and a Yes card, a step
   * or a line about an app uses the window's rules (the frame route says where an app opens, D100). ---- */
  const icon = (name) => el("span", { class: "msr", "aria-hidden": "true", text: name });
  function pic(src, glyph) {
    if (!src) return icon(glyph);
    const img = el("img", { src: src, alt: "" });
    img.addEventListener("error", () => { if (img.parentNode) img.parentNode.replaceChild(icon(glyph), img); }, { once: true });
    return img;
  }
  const missing = (r) => r.status === 404 && r.data && /openapi|No such|not found/i.test(String(r.data.error || r.data || "")) || r.status === 501 || r.status === 405;
  async function pageJson(src) {
    if (!S.pages || !src) return { status: 0, data: null };
    try {
      const init = { credentials: "omit", headers: { Accept: "application/json" } };
      if (L.isNetwork(S.pages)) init.credentials = "include";     // D130: a page port's pass, an HttpOnly cookie
      const r = await fetch(src, init);
      return { status: r.status, data: r.ok ? await r.json() : null };
    } catch (e) { return { status: 0, data: null }; }
  }
  function tile(o) {
    const body = el("div", { class: "tile-body" });
    const head = el("div", { class: "tile-head" }, [o.icon ? icon(o.icon) : null, el(o.h || "h2", { text: o.title || "" }),
      o.stat ? el("span", { class: "stat", text: o.stat }) : null]);
    const node = el("section", { class: "tile" + (o.wide ? " wide" : "") + (o.cls ? " " + o.cls : ""), "data-tile": o.id || null },
                    [head, body]);
    return { node, body, head };
  }
  /* One section of a list of sections (Vault, Settings): its title and a short note open it. *o.id* is kept in
   * data-tile, so a link can open it. */
  function act(o) {
    const note = el("span", { class: "note", text: o.note || "" });
    const body = el("div", { class: "act-body" }, o.body || []);
    const node = el("details", { class: "act", open: o.open || null, "data-tile": o.id || null },
      [el("summary", {}, [el(o.h || "h3", { text: o.title }), note]), body]);
    return { node, body, note };
  }
  /* "Coming in a later build": one plain card, naming the missing part. Never a fake control. */
  function later(missingPart, more) {
    const c = L.comingLater(missingPart, more);
    return el("div", { class: "card later" }, [el("h3", { text: c.title }), el("p", { text: c.line }),
      c.more ? el("p", { class: "muted", text: c.more }) : null]);
  }
  function kv(pairs) {
    const dl = el("dl", { class: "kv" });
    pairs.forEach(([k, v]) => { if (v === undefined || v === null || v === "") return; dl.appendChild(el("dt", { text: k })); dl.appendChild(el("dd", { text: String(v) })); });
    return dl;
  }
  function themePicker(themes, pick, name, onPick) {
    return el("div", { class: "swatches" }, themes.map((th) => {
      const r = el("input", { type: "radio", name, value: th.id, checked: th.id === pick });
      r.addEventListener("change", () => { if (onPick) onPick(th.id); });
      const views = L.swatchParts(th).map((part) => {
        const t = part.tokens || {};
        const mini = el("span", { class: "swatch-mini", title: part.label }, [el("b", { text: "Aa" }),
          el("span", { class: "dots" }, ["--pink", "--sky", "--mint"].map((k) => {
            const i = el("i");
            if (t[k]) i.style.background = t[k];
            return i;
          })), el("small", { text: part.label })]);
        if (t["--bg"]) mini.style.background = t["--bg2"] ? "linear-gradient(135deg, " + t["--bg"] + ", " + t["--bg2"] + ")" : t["--bg"];
        if (t["--text"]) mini.style.color = t["--text"];
        if (t["--line"]) mini.style.borderColor = t["--line"];
        mini.style.borderRadius = "calc(10px * " + (t["--round"] !== undefined ? t["--round"] : 1) + ")";  // lane looks: its corners
        if (t["--display"]) mini.querySelector("b").style.fontFamily = t["--display"];                  // and its font
        return mini;
      });
      return el("label", { class: "swatch" }, [r, el("span", { class: "swatch-views", "aria-hidden": "true" }, views),
        el("span", { class: "swatch-words" }, [el("b", { text: th.name }), el("small", { class: "muted", text: " " + th.modeWords }),
          th.line ? el("span", { class: "small muted swatch-line", text: th.line }) : null])]);
    }));
  }
  /* Server-sent events with her login's headers (D79): EventSource can't send a header, so this reads the
   * stream with fetch. The same shape as an EventSource (addEventListener, onerror, readyState, close), and it
   * comes back by itself with Last-Event-ID when the stream ends. A 4xx answer closes it (readyState 2). */
  function stream(path) {
    const es = { readyState: 0, onerror: null, subs: {}, closed: false, lastId: null, ctl: null,
      addEventListener(type, fn) { (this.subs[type] = this.subs[type] || []).push(fn); },
      close() { this.closed = true; this.readyState = 2; if (this.ctl) this.ctl.abort(); } };
    const wait = (ms) => new Promise((res) => setTimeout(res, ms));
    const dispatch = (block) => {
      let type = "message", id = null;
      const data = [];
      block.split("\n").forEach((line) => {
        if (!line || line[0] === ":") return;
        const c = line.indexOf(":");
        const field = c < 0 ? line : line.slice(0, c);
        let value = c < 0 ? "" : line.slice(c + 1);
        if (value[0] === " ") value = value.slice(1);
        if (field === "event") type = value; else if (field === "data") data.push(value); else if (field === "id") id = value;
      });
      if (id !== null) es.lastId = id;
      if (!data.length) return;
      const ev = { type, data: data.join("\n"), lastEventId: es.lastId };
      (es.subs[type] || []).forEach((fn) => { try { fn(ev); } catch (e) { /* one listener's error stays there */ } });
    };
    (async () => {
      while (!es.closed) {
        es.ctl = new AbortController();
        const headers = authHeaders({ Accept: "text/event-stream" });
        if (es.lastId !== null) headers["Last-Event-ID"] = es.lastId;
        let r = null;
        try { r = await fetch(path, { headers, credentials: "same-origin", cache: "no-store", signal: es.ctl.signal }); } catch (e) { r = null; }
        if (es.closed) return;
        if (!r || !r.ok || !r.body) {
          if (r && r.status === 401) signIn();
          if (r && r.status >= 400 && r.status < 500) { es.closed = true; es.readyState = 2; if (es.onerror) es.onerror(); return; }
          if (es.onerror) es.onerror();
          await wait(3000);
          continue;
        }
        es.readyState = 1;
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = "";
        try {
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf = (buf + dec.decode(value, { stream: true })).replace(/\r\n/g, "\n");
            let i;
            while ((i = buf.indexOf("\n\n")) >= 0) { dispatch(buf.slice(0, i)); buf = buf.slice(i + 2); }
          }
        } catch (e) { /* the stream ended; it comes back below */ }
        if (es.closed) return;
        es.readyState = 0;
        await wait(1000);
      }
    })();
    return es;
  }

  const DEFS = {}, SUBS = {};
  const on = (type, fn) => { (SUBS[type] = SUBS[type] || []).push(fn); };
  function fire(type, data) { (SUBS[type] || []).concat(SUBS["*"] || []).forEach((fn) => { try { fn(data, type); } catch (e) { /* one page's error stays there */ } }); }
  const pageTitle = (view) => (DEFS[view] && DEFS[view].title) || view;
  const pageTab = (view, arg) => ({ key: "page:" + view, kind: "page", view, arg: arg || "", title: PAGE_NAMES[view] || pageTitle(view), dash: "#" + view + (arg ? "/" + arg : "") });
  const PAGE_NAMES = { outside: "What leaves", settings: "More settings", agents: "Agent Team" };
  function drawPage(t, live) {
    if (!live) forgetTyped(t.node);                     // live: a page may keep what it has, and say so by returning
    if (!t.sec) { t.sec = el("section", { class: "view", id: "view-" + t.view }); t.node.appendChild(t.sec); }
    const drawn = (e) => clear(t.sec).appendChild(el("p", { class: "fail", text: "This page could not be drawn: " + e.message }));
    S.view = t.view;
    try {
      const got = DEFS[t.view].draw(t.sec, t.arg, live);
      if (got && got.catch) got.catch(drawn);
    } catch (e) { drawn(e); }
  }
  /* Back on a page: it shows what is true now, as the dashboard's does, unless she has typed in it. What a box was drawn
   * holding is not typing (addapi's test word). A draw put off stays put off. */
  function drawPageAgain(t) {
    const typed = Array.from(t.node.querySelectorAll(TYPED_BOXES)).some((i) => i.value && i.value !== i.defaultValue);
    if (!typed) { t.stale = false; drawPage(t); }
  }
  function openPage(view, arg) {
    if (!DEFS[view]) { openDashboard("#" + view); return null; }
    const had = S.tabs.find((x) => x.key === "page:" + view);
    const back = had && S.active !== had.key;
    const t = openTab(pageTab(view, arg));
    if (had && arg !== undefined && had.arg !== arg) { had.arg = arg; drawPage(had); }
    else if (back) drawPageAgain(had);
    closeRail();
    return t;
  }
  /* A page asks to go somewhere: the same names as the dashboard's addresses (#building, #apps/add, #page/<id>/<page>). */
  function go(view, arg) {
    if (view === "building") openTab(buildingTab());
    else if (view === "yourturn") openTab(turnTab());
    else if (view === "chat") $("input").focus();
    else if (view === "apps" && arg === "available") showStore();
    else if (view === "page") {
      const a = S.apps.find((x) => x.pkg === String(arg || "").split("/")[0]);
      if (a) openApp(a);
    } else openPage(view, arg);
  }
  /* A page changed something: draw it again now if she is looking at it, else when she does. Never over a PIN or a
   * recovery code being typed: that would take her box away. By its value, not the focus: once sent the box is empty,
   * and the page must draw again. */
  function redraw(view) {
    const t = S.tabs.find((x) => x.key === "page:" + view);
    if (!t || S.signedOut) return;
    const typing = Array.from(t.node.querySelectorAll('input[type=password], input[data-name="code"]')).some((i) => i.value);
    if (t.key === S.active && !typing) drawPage(t, true); else t.stale = true;
  }
  /* A page's notice: on top of what she is looking at. */
  function notice(n) {
    if (S.signedOut) return;
    const t = S.tabs.find((x) => x.key === S.active);
    const host = t ? t.node : currentView().node;
    const x = btn("Dismiss", () => card.remove());
    const card = el("div", { class: "w-card" + (n.high ? " w-floor" : ""), "data-notice": "1" }, [el("h5", { text: n.title }), n.text ? el("p", { text: n.text }) : null, el("div", { class: "w-btns" }, [x])]);
    host.prepend(card);
    host.scrollTop = 0;
  }
  async function signinWhere() {
    if (S.signin) return S.signin;
    try {
      const r = await fetch("/v1/signin", { headers: { "X-PuffPAI": "1" }, credentials: "same-origin", cache: "no-store" });
      if (r.ok) S.signin = await r.json();
    } catch (e) { /* the plain words stay */ }
    return S.signin;
  }
  async function consentInline(box, id, after) {
    S.inChat.add(id);                                   // its card is here, so surface() does not draw it in a chat too
    return cardInto(box, id, { after });
  }
  function openTarget(item) {
    const a = S.apps.find((x) => x.pkg === item.pkg);
    if (a) openApp(a); else if (item.target && item.target.url) openOutside(item.target.url);
  }
  function face(cls) {
    const f = L.faceFor(S.settings && S.settings.avatar, S.rows, S.pages);
    if (f.kind !== "face") return el("div", { class: cls + " face-pic" }, [el("img", { src: f.kind === "own" ? "img/puff.webp" : f.src, alt: f.name, title: f.name })]);   // D108: never /ui/
    return el("iframe", { class: cls + " face-frame", src: f.src, sandbox: L.FACE_SANDBOX, title: f.name, referrerpolicy: "no-referrer", tabindex: "-1" });
  }
  /* The Bridge: open or closed. In safe mode, opening needs the PIN and says why first (the dashboard's rule). */
  function bridgePinCard(plan, after) {
    const p = pinBox();
    const says = el("p", { class: "w-says" });
    const card = el("div", { class: "w-card w-floor", "data-bridge-pin": "1" }, [el("h5", { text: plan.title })]
      .concat(plan.lines.map((t) => el("p", { text: t })))
      .concat([p, says, el("div", { class: "w-btns" }, [
        btn("Open the Bridge", async () => {
          const pin = p.value; p.value = "";
          if (!pin) { says.textContent = "Type your PIN first."; return; }
          const o = await api("POST", "/v1/bridge/open", { pin });
          if (o.status === 200) { card.remove(); if (after) after(); return; }
          says.textContent = said(o, "The Bridge did not open.");
        }, "primary"),
        btn("Keep it closed", () => { p.value = ""; card.remove(); })])]));
    const t = S.tabs.find((x) => x.key === S.active);
    (t ? t.node : currentView().node).prepend(card);
    p.focus();
  }
  async function bridgeButton(view, after) {
    const b = L.bridgeView(view);
    if (!b.button) return null;
    return el("button", { class: b.action === "close" ? "" : "primary", text: b.button, on: { click: async () => {
      if (b.action === "close") { await api("POST", "/v1/bridge/close", {}); if (after) after(); return; }
      const plan = L.bridgeOpen(view);
      if (plan.needsPin) { bridgePinCard(plan, after); return; }
      const o = await api("POST", "/v1/bridge/open", {});
      if (o.status === 200) { if (after) after(); return; }
      if (o.data && o.data.needs_pin) { bridgePinCard(L.bridgeOpen(o.data), after); return; }
      notice({ title: "The Bridge did not open", text: said(o, "") });
    } } });
  }
  window.Puff = { oneWindow: true, L, S, el, clear, icon, pic, api, authHeaders, signIn, signinWhere, stream, said, missing, pageJson, notice, tile, act, later, kv,
                  pin: pinBox, stepCardEl: stepEl, consentEl, pendingConsents, consentInline,
                  keysReady: (build, box, says, given, _go, peek) => keysReady(build, box, says, given, peek),
                  loadRegistry, loadSettings, names: () => Object.fromEntries(S.rows.map((r) => [r.id, r.name])), openTarget,
                  page: (id, def) => { DEFS[id] = def; }, go, redraw, on, refreshCounts, bridgeButton, face, talk: () => {}, drawChips: drawStatus,
                  drawPrivate: drawStatus, drawFaces: drawFace, loadThemes, applyTheme: applyLook, themePicker,
                  sleep: (ms) => new Promise((ok) => setTimeout(ok, ms)) };

  /* ---- start ---- */
  function closeRail() { $("rail").classList.remove("open"); }
  function wire() {
    document.addEventListener("focusin", (e) => { S.chatHadFocus = $("composer").contains(e.target); });
    $("new-chat").addEventListener("click", newChat);
    $("menu-btn").addEventListener("click", () => $("rail").classList.toggle("open"));
    const terminal = document.querySelector("[data-switch=terminal]");
    /* D139: on another device a console on the bot's computer helps nobody, so Terminal is the Developer page's own
     * terminal, here in the pane. Its commands run on the bot's computer. */
    terminal.addEventListener("click", () => { if (L.onNetworkHost(location.hostname)) openPage("developer", "terminal"); else openTerminal(terminal); });
    if (L.onNetworkHost(location.hostname)) terminal.title = "A command line on your bot's computer, here in the side pane";
    document.querySelector("[data-switch=dashboard]").addEventListener("click", () => openDashboard(""));
    $("composer").addEventListener("submit", (ev) => { ev.preventDefault(); submit(); });
    const input = $("input");
    input.addEventListener("input", () => { S.sel = 0; drawPalette(input.value); });
    input.addEventListener("keydown", (e) => {
      const box = $("palette");
      const hits = !box.hidden ? hitsFor(input.value) : null;
      if (e.key === "Escape") { box.hidden = true; return; }
      if (!hits || !hits.length) return;
      if (e.key === "ArrowDown") { S.sel = (S.sel + 1) % hits.length; drawPalette(input.value); e.preventDefault(); }
      else if (e.key === "ArrowUp") { S.sel = (S.sel - 1 + hits.length) % hits.length; drawPalette(input.value); e.preventDefault(); }
      else if (e.key === "Tab") { fillFrom(hits[S.sel]); e.preventDefault(); }
    });
    window.addEventListener("hashchange", () => { if (/^#pair=/.test(location.hash)) location.reload(); });
    // A page's form has read its fields by now. Passwords it clears itself; a recovery code it leaves in its text box,
    // where it would stay readable and hold off every redraw.
    $("view").addEventListener("submit", (ev) => { ev.target.querySelectorAll('input[data-name="code"]').forEach((i) => { i.value = ""; }); });
    $("view").addEventListener("click", (ev) => {
      const a = ev.target.closest && ev.target.closest('a[href^="#"]');
      if (!a) return;
      ev.preventDefault();
      const [view, ...rest] = a.getAttribute("href").slice(1).split("/");
      go(view, rest.join("/"));
    });
  }
  function restoreTabs() {
    const saved2 = saved(TABS_KEY, null);
    const wanted = Array.isArray(saved2) ? saved2 : null;
    (wanted || ["page:home"]).forEach((key) => {        // a new bot opens on Home: what it has, what is happening
      if (key === "building") openTab(buildingTab());
      else if (key === "turn") openTab(turnTab());
      else if (key === "settings") openTab(settingsTab());
      else if (typeof key === "string" && key.indexOf("page:") === 0) { if (DEFS[key.slice(5)]) openTab(pageTab(key.slice(5))); }
      else if (typeof key === "string" && key.indexOf("app:") === 0) {
        const a = S.apps.find((x) => x.pkg === key.slice(4));
        if (a) openTab(appTab(a));                 // drawing a tab never opens a browser tab
      }
    });
    if (S.tabs.length) { S.active = S.tabs[0].key; }
    renderTabs();
  }
  async function start() {
    wire();
    /* The sign-in code leaves the address at once. Already signed in: it is not used up. */
    const code = L.pairCode(location.hash);
    if (/pair=/.test(location.hash)) history.replaceState(null, "", location.pathname);
    let why = "";
    let first = deviceKey ? await raw("GET", "/v1/settings") : null;
    if (!first || first.status !== 200) {
      /* Only when signed out: a passing failure with a good key must not use up a code or add a device. */
      if (code && (!first || first.status === 401)) {
        if (await redeem(code)) first = await raw("GET", "/v1/settings");
        else why = "expired";
      }
      if (!first || first.status !== 200) { signIn(why); return; }
    }
    await loadSettings();
    if (S.signedOut) return;
    await loadThemes();
    await loadRegistry();
    $("win").hidden = false;
    S.chats = L.chatList(saved(CHATS_KEY, []));
    saveChats();
    const cur = saved(CURRENT_KEY, L.MAIN_CHAT);
    pickChat(S.chats.some((c) => c.id === cur) ? cur : L.MAIN_CHAT);
    await loadTool();
    restoreTabs();
    if (S.vaultSaid) openTab(buildingTab());    // D113: the restored tabs do not hide it
    drawStatus();
    await refreshCounts();
    await surface();
    toolsmithToChat();
    listen();
    setInterval(loadRegistry, 30000);           // Caretaker's health: a slow poll keeps it honest
  }
  document.addEventListener("DOMContentLoaded", start);  // the pages' files come after this one
})();
