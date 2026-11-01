/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/_components/AsideMenu/index.tsx (adapted:
 * the rail, its private-stuff card and the top bar, as plain JS on v2's Core API).
 *
 * PuffPAI Desktop: the shell. The rules live in logic.js; each page is one file in pages/ that calls
 * Puff.page(id, {...}). The words come from the Core API. Text from packages and the Core is always set
 * as text, never as HTML. Every call carries X-PuffPAI: 1; package pages, widgets and the face live on
 * each package's own origin and never get the owner's login (D49, D53). */
(function () {
  "use strict";
  const L = window.PuffLogic;
  const $ = (id) => document.getElementById(id);
  const S = { rows: [], settings: null, themes: null, pages: "", view: "home", arg: "", wizard: null, transcript: null, step: null,
              ticks: new L.TickMemory(), counts: { yourturn: 0, setup: false }, defs: {}, subs: {}, chatSeen: null,
              signedOut: false, signin: null, signinWhy: "" };

  function el(tag, attrs, kids) {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v === null || v === undefined || v === false) return;
      if (k === "text") n.textContent = v;
      else if (k === "on") Object.entries(v).forEach(([ev, fn]) => n.addEventListener(ev, fn));
      else if (k === "class") n.className = v;
      else n.setAttribute(k, v === true ? "" : v);
    });
    if (tag === "button" && !n.hasAttribute("type")) n.setAttribute("type", "button");   // never a form's submit by accident
    (kids || []).forEach((c) => c && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  const icon = (name) => el("span", { class: "msr", "aria-hidden": "true", text: name });
  /* An app's picture (lane I18c): its own icon, or the plain *glyph* when there is none or it does not load,
   * never a broken picture. */
  function pic(src, glyph) {
    if (!src) return icon(glyph);
    const img = el("img", { src: src, alt: "" });
    img.addEventListener("error", () => { if (img.parentNode) img.parentNode.replaceChild(icon(glyph), img); }, { once: true });
    return img;
  }

  /* D79: her login has two halves. The cookie (set by the Core, HttpOnly) and the second half, which pairing
   * returns in its JSON body and which lives only here, in this origin's localStorage. localStorage is split by
   * port and cookies are not, so a page on another port of puffpai.localhost may get the cookie but never
   * this. Every call sends it as X-PuffPAI-Device; the cookie alone counts for nothing. Never put it in the
   * address bar, the console or an event. */
  const DEVICE_KEY = "puffpai.device_key";
  let deviceKey = null;
  try { deviceKey = localStorage.getItem(DEVICE_KEY) || null; } catch (e) { deviceKey = null; }
  function keepDeviceKey(k) {
    deviceKey = k || null;
    try { localStorage.setItem(DEVICE_KEY, deviceKey || ""); return !!deviceKey; } catch (e) { return false; }
  }
  /* The headers every call to the Core carries: X-PuffPAI: 1 and her login's second half. */
  function authHeaders(extra) {
    const h = Object.assign({ "X-PuffPAI": "1" }, extra || {});
    if (deviceKey) h["X-PuffPAI-Device"] = deviceKey;
    return h;
  }

  /* Every call carries X-PuffPAI: 1. With the device cookie, the Core refuses a state change
   * without it, or from any other origin (D49), so other pages on this computer can't use her login. */
  async function api(method, path, body, waitMs) {
    const init = { method, headers: authHeaders(), credentials: "same-origin" };
    if (waitMs) init.signal = AbortSignal.timeout(waitMs);    // a poll only (D113): no answer by then counts as failed
    if (body instanceof Blob) { init.headers["Content-Type"] = "application/octet-stream"; init.body = body; }   // D134: a file, read as it goes
    else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, init); } catch (e) { return { status: 0, data: { error: "The Core did not answer." } }; }
    let data = null;
    const type = r.headers.get("Content-Type") || "";
    try { data = type.indexOf("json") >= 0 ? await r.json() : await r.text(); } catch (e) { data = null; }
    if (L.signedOut(r.status, data)) {     // D92: the sign-in screen, never the Core's refusal words
      signIn();
      return { status: r.status, data: { error: L.signInWords(S.signin).line } };
    }
    return { status: r.status, data };
  }
  /* The Core's own words for a refusal: its error, line or said, else its reason, message or problems. */
  const said = (r, fallback) => {
    const d = r && r.data && typeof r.data === "object" ? r.data : null;
    const problems = d && Array.isArray(d.problems) ? d.problems.map(String).join(" ") : "";
    return (d && (d.error || d.line || d.said || d.reason || d.message)) || problems ||
      (r && typeof r.data === "string" && r.data.trim() && r.data.length < 300 ? r.data.trim() : "") || fallback || "That did not work.";
  };
  /* A route this build does not have answers 404 (no such route) or 501 (not built yet). */
  const missing = (r) => r.status === 404 && r.data && /openapi|No such|not found/i.test(String(r.data.error || r.data || "")) || r.status === 501 || r.status === 405;

  /* A widget's JSON from its package's own origin (D53): no cookie, no owner login. */
  async function pageJson(src) {
    if (!S.pages || !src) return { status: 0, data: null };
    try {
      const init = { credentials: "omit", headers: { Accept: "application/json" } };
      // D130: on the home network a page port needs its pass, which is an HttpOnly cookie: this page never sees it.
      if (L.isNetwork(S.pages)) init.credentials = "include";
      const r = await fetch(src, init);
      return { status: r.status, data: r.ok ? await r.json() : null };
    } catch (e) { return { status: 0, data: null }; }
  }

  /* ---- signing in: a one-time code from `puffpai pair` ---- */
  async function pairFromHash() {
    const m = location.hash.match(/pair=([A-Za-z0-9-]+)/);
    if (!m) return;
    history.replaceState(null, "", location.pathname);
    const r = await fetch("/v1/pair", { method: "POST", headers: { "Content-Type": "application/json", "X-PuffPAI": "1" },
      body: JSON.stringify({ code: m[1] }), credentials: "same-origin" });
    let d = null;
    try { d = await r.json(); } catch (e) { d = null; }
    if (!r.ok) { S.signinWhy = "expired"; return; }     // said on the sign-in screen, if this browser is not signed in
    if (!keepDeviceKey(d && d.device_key)) {
      notice({ title: "This browser can't keep its sign-in", high: true,
               text: "It does not let BuildBot save site data (a private window, or saved data turned off), so it is " +
                     "signed in only until this tab closes. Next time, sign it in again the same way." });
    }
  }
  /* D92: signed out, the Desktop is ONE plain sign-in screen: no tiles, no rail pages, no chips, and never the
   * Core's refusal words. It names Sign in a browser.cmd and its folder (GET /v1/signin, public). */
  function signIn() {
    if (S.signedOut) return;
    const why = S.signinWhy;
    S.signedOut = true;
    document.querySelectorAll("#main .view, #rail .scroll, #private, #chips, #chatpanel, #chat-btn, #window-btn")
      .forEach((n) => { n.hidden = true; });
    clear($("notices"));
    $("title").textContent = "Sign in";
    const line = el("p", { "data-said": "signin", text: L.signInWords(S.signin, why).line });
    $("notices").appendChild(el("div", { class: "card note high", "data-signin": "" }, [el("h3", { text: L.SIGN_IN_TITLE }), line]));
    signinWhere().then((w) => {
      if (w && w.pair) { location.replace(w.pair); return; }   // D130: on the home network, the pair screen
      line.textContent = L.signInWords(w, why).line;
    });
  }
  /* Where Sign in a browser.cmd is (GET /v1/signin: public, no login needed), asked once; null if no answer. */
  async function signinWhere() {
    if (S.signin) return S.signin;
    try {
      const r = await fetch("/v1/signin", { headers: { "X-PuffPAI": "1" }, credentials: "same-origin", cache: "no-store" });
      if (r.ok) S.signin = await r.json();
    } catch (e) { /* the plain words stay */ }
    return S.signin;
  }
  function notice(n) {
    if (S.signedOut) return;                 // D92: the sign-in screen stays the only card
    const x = el("button", { class: "link", text: "Dismiss" });
    const card = el("div", { class: "card note" + (n.high ? " high" : "") }, [el("h3", { text: n.title }),
      n.text ? el("p", { text: n.text }) : null, el("div", { class: "row end" }, [x])]);
    x.addEventListener("click", () => card.remove());
    $("notices").prepend(card);
  }
  /* Lane looks: notices with the same title fold into the newest one, with a count and Show all. Run on every
   * change to the notices area, so a Dismiss brings the next one up. */
  const foldOpen = new Set();
  function foldNotices() {
    const cards = Array.from($("notices").children).filter((n) => n.classList.contains("card"));
    const titles = cards.map((n) => { const h = n.querySelector("h3"); return h ? h.textContent : ""; });
    L.noticeFolds(titles, foldOpen).forEach((f, i) => {
      const card = cards[i];
      card.hidden = f.hidden;
      const old = card.querySelector(":scope > .fold");
      if (old) old.remove();
      if (!f.more) return;
      const words = L.foldWords(f.more, foldOpen.has(titles[i]));
      card.appendChild(el("div", { class: "fold" }, [el("span", { class: "count", text: words.count }),
        el("button", { class: "link", text: words.button, on: { click: () => {
          if (foldOpen.has(titles[i])) foldOpen.delete(titles[i]); else foldOpen.add(titles[i]);
          foldNotices();
        } } })]));
    });
  }

  /* ---- shared pieces the pages use ---- */
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
  function pin(label) {
    return el("input", { type: "password", inputmode: "numeric", autocomplete: "off", placeholder: label || "Your PIN", "aria-label": label || "PIN" });
  }

  /* "Your turn" card (01 "Checklist", 03 3.5): Copy, Done, Skip, plus Run as administrator. */
  function stepCardEl(c, after) {
    const secret = c.secret ? el("input", { type: "password", autocomplete: "off", placeholder: c.secret.key, "aria-label": c.secret.key }) : null;
    const p = c.needsPin ? pin() : null;
    const says = el("p", { class: "fail", text: c.failed ? "Not done yet: " + c.failed : "" });
    const row = el("div", { class: "row" });
    const card = el("div", { class: "card" }, [
      el("h3", { text: c.title }), c.why ? el("p", { text: c.why }) : null,
      el("div", { class: "from", text: c.raisedBy + (c.from ? ", for " + c.from : "") }),
      c.text ? el("pre", { class: "box", text: c.text }) : null, c.shell ? el("div", { class: "from", text: "In " + c.shell }) : null,
      c.needsPin ? el("p", { class: "small", text: "Running as administrator runs exactly the command above. Type your PIN to allow it." }) : null,
      secret, p, says, row]);
    c.buttons.forEach((b) => {
      const btn = el("button", { class: b.id === "done" ? "primary" : "", text: b.label });
      btn.addEventListener("click", async () => {
        if (b.id === "copy") { await navigator.clipboard.writeText(c.text); btn.textContent = "Copied"; return; }
        if (b.id === "open") {
          const o = L.networkOpen(b.url, S.pages);
          if (o.url) window.open(o.url, "_blank", "noopener"); else notice({ title: "Open it there", text: o.line });
          return;
        }
        if (b.id === "admin") {
          const body = L.adminAnswer(p ? p.value : "");
          if (p) p.value = "";
          const r = await api("POST", "/v1/checklist/" + encodeURIComponent(c.id) + "/run-admin", body);
          says.textContent = r.status === 200 ? "Windows asks you to allow it. When it has run, press Done."
            : (r.data || {}).reason || said(r);
          return;
        }
        if (b.id === "save") {
          const r = await api("PUT", "/v1/vault/secrets/" + encodeURIComponent(c.secret.key), { value: secret.value });
          secret.value = "";
          says.textContent = r.status === 200 || r.status === 201 || r.status === 202 ? "Saved in your vault. Press Done."
            : r.status === 501 ? "The vault is not built yet, so this key can't be saved yet." : said(r, "Not saved.");
          return;
        }
        const r = await api("POST", "/v1/checklist/" + encodeURIComponent(c.id) + "/" + b.id);
        if (r.status === 409) { says.textContent = said(r, ""); return; }
        if (after) after();
      });
      row.appendChild(btn);
    });
    return card;
  }

  /* A Yes card (consent, lanes L1/L4) with its tick boxes (lane I4b). *after(answer)* when answered. */
  function consentEl(c, after) {
    const p = c.needsPin ? pin() : null;
    const says = el("p", { class: "fail" });
    const row = el("div", { class: "row" });
    const unlockBox = c.plan ? el("div", { "data-unlock-for": c.plan.build }) : null;   // D68 (lane I19a)
    const boxes = c.ticks ? c.ticks.items.map((it) => {
      if (it.ticked) S.ticks.seed(c.id, it.value);                                     // the card's own tick (D99)
      const box = el("input", { type: "checkbox", checked: S.ticks.has(c.id, it.value) });   // kept across redraws
      box.addEventListener("change", () => S.ticks.set(c.id, it.value, box.checked));
      return { it, box };
    }) : [];
    const ticks = c.ticks ? [el("p", { class: "small", text: c.ticks.note })].concat(boxes.map(({ it, box }) =>
      el("label", { class: "opt" }, [box, el("span", { text: it.label })]))) : [];
    const card = el("div", { class: "card yes" + (c.floor ? " floor" : "") },
      [el("h3", { text: c.title })].concat(c.lines.map((l) => el("p", { text: l }))).concat(ticks).concat([p, unlockBox, says, row]));
    c.buttons.forEach((b) => {
      const btn = el("button", { class: b.id === "yes" ? "primary" : "", text: b.label });
      btn.addEventListener("click", async () => {
        if (b.id === "yes" && c.ticks) {
          const ticked = boxes.filter(({ box }) => box.checked).map(({ it }) => it.value);
          const t = await api("PUT", c.ticks.route, c.ticks.body(ticked));
          if (t.status >= 400) { says.textContent = said(t, "Your ticks were not taken, so nothing was answered."); return; }
        }
        if (b.id === "yes" && unlockBox && !(await keysReady(c.plan.build, unlockBox, says, p, () => btn.click()))) return;
        const body = L.consentAnswer(c, b.id === "yes", p ? p.value : "");
        if (p) p.value = "";
        const r = await api("POST", "/v1/consent/" + encodeURIComponent(c.id) + "/answer", body);
        if (r.status >= 400) { says.textContent = said(r); return; }
        S.ticks.drop(c.id);
        if (b.id === "yes" && c.plan) afterPlanYes(c);
        if (after) after(b.id);
        refreshCounts();
      });
      row.appendChild(btn);
    });
    if (unlockBox) keysReady(c.plan.build, unlockBox, says, p, null, true);   // locked already: ask up front
    return card;
  }
  /* D68 (lane I19a): her Yes lends Toolsmith its key for the build. When the build needs it and the vault is
   * locked now, the same step asks for her PIN and opens the vault (her usual unlock, POST /v1/vault/unlock),
   * then goes on: one Yes, no trip to the vault page. True: go on. *given*: a PIN box the card has already
   * (a floor card), used for both. *go*: Enter in the PIN box. *peek*: only show the box, never unlock. */
  async function keysReady(build, box, says, given, go, peek) {
    const r = await api("GET", "/v1/toolsmith/builds/" + encodeURIComponent(build));
    if (r.status !== 200 || !L.buildKeys(r.data).unlock) { if (!given) clear(box); return true; }
    let input = given || box.querySelector("input[data-unlock]");
    if (!box.firstChild) {
      if (!given) {
        input = pin("Your PIN");
        input.setAttribute("data-unlock", "1");
        if (go) input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      }
      box.append(el("p", { class: "small", text: L.UNLOCK_FOR_YES }));
      if (!given) box.append(input);
    }
    if (peek) return false;
    const secret = input ? input.value : "";
    if (!secret) { if (says) says.textContent = L.UNLOCK_TYPE_PIN; if (input) input.focus(); return false; }
    const x = await api("POST", "/v1/vault/unlock", { method: "pin", secret });
    if (!given) input.value = "";
    if (x.status !== 200) { if (says) says.textContent = said(x, "Your vault did not open, so nothing was answered."); return false; }
    clear(box);
    if (says) says.textContent = "";
    return true;
  }
  async function pendingConsents() {
    const r = await api("GET", "/v1/consent?status=pending");
    if (r.status !== 200 || !Array.isArray(r.data)) return { status: r.status, cards: [] };
    const cards = r.data.map((c) => L.consentCard(c)).filter((c) => c.pending);
    S.ticks.keep(cards.map((c) => c.id));
    return { status: 200, cards };
  }
  /* One pending Yes card by its id, drawn where it was asked for (chat, Apps, Settings). */
  async function consentInline(box, id, after) {
    const { cards } = await pendingConsents();
    const c = cards.find((x) => x.id === id);
    if (!c) { box.appendChild(el("p", { class: "muted small", text: "A Yes card is waiting on Your turn." })); return; }
    box.appendChild(consentEl(c, (answer) => {
      clear(box).appendChild(el("p", { class: "muted small", text: answer === "yes" ? "You said Yes." : "You said No." }));
      if (after) after(answer);
    }));
  }

  /* After a Yes on a plan card (lane I16). The Core owns "add the ticked PuffPAI store apps, then start the
   * build" (toolsmith_host/store_phase.py), the same for the Desktop, the chat and the wizard: this asks it to
   * start (safe to ask twice; while store apps are being added it starts nothing) and shows the Core's own
   * progress from GET /v1/toolsmith/builds/<id>: each store app's Yes card, its true line, then the start.
   * A reload loses nothing: the Core keeps going, and Building and Your turn show the same progress. */
  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
  async function afterPlanYes(c) {
    const log = el("div");
    const box = el("div");
    // One "Your plan" card per build: a second Yes for the same build redraws its card, never adds one.
    const run = String(Math.random());
    let card = Array.from($("notices").querySelectorAll("[data-plan-yes]")).find((n) => n.getAttribute("data-plan-yes") === c.plan.build);
    if (card) clear(card).append(el("h3", { text: "Your plan" }), log, box);
    else card = el("div", { class: "card note", "data-plan-yes": c.plan.build }, [el("h3", { text: "Your plan" }), log, box]);
    card.setAttribute("data-run", run);
    const mine = () => card.getAttribute("data-run") === run;   // a newer Yes took this card over
    $("notices").prepend(card);
    const path = "/v1/toolsmith/builds/" + encodeURIComponent(c.plan.build);
    const s = await api("POST", "/v1/toolsmith/builds/" + encodeURIComponent(c.plan.build) + "/start", { consent_id: c.plan.consentId });
    if (s.status !== 200) { log.appendChild(el("p", { class: "fail", text: "The build did not start: " + said(s) })); return; }
    let shown = "";
    for (let i = 0; i < 3600; i++) {                 // at most an hour on this page; the Core goes on regardless
      if (!card.isConnected) return;                   // a newer card for this build took its place
      const r = await api("GET", path);
      if (!mine()) return;
      if (r.status !== 200) { await sleep(1000); continue; }
      const p = L.storeProgress(r.data);
      clear(log);
      p.lines.forEach((t) => log.appendChild(el("p", { text: t })));
      if (p.asking !== shown) {                        // the store app's own Yes card, from the Core
        shown = p.asking;
        clear(box);
        if (p.asking) await consentInline(box, p.asking);
      }
      if (p.done) break;
      await sleep(1000);
    }
    if (!mine()) return;
    clear(box);
    const x = el("button", { class: "link", text: "Dismiss", on: { click: () => card.remove() } });
    card.appendChild(el("div", { class: "row end" }, [el("a", { href: "#building", text: "Open Building" }), x]));
    redraw("building");
  }

  /* The theme picker (Looks and the wizard's look step): one swatch per theme, with its night and day
   * previews drawn from its own tokens (already checked by logic.themeTokens) and its line. */
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

  /* ---- the Registry ---- */
  async function loadRegistry() {
    const r = await api("GET", "/v1/registry");
    if (r.status !== 200) return S.rows;
    S.rows = r.data || [];
    drawRailApps();
    return S.rows;
  }
  const names = () => Object.fromEntries(S.rows.map((r) => [r.id, r.name]));

  /* Opening a package's page: built for PuffPAI inside the Desktop, adopted apps in a new tab (D13). */
  function openTarget(item) {
    const t = item.target;
    if (t.kind === "computer") { notice({ title: item.label, text: t.note }); return; }   // D130
    if (t.kind === "tab") { window.open(t.url, "_blank", "noopener"); return; }
    if (t.kind === "program") {
      api("POST", "/v1/packages/" + encodeURIComponent(item.pkg) + "/open/" + encodeURIComponent(t.page || "program")).then((r) => {
        if (r.status >= 400) notice({ title: item.label + " did not open", text: said(r) });
      });
      return;
    }
    if (t.kind === "desktop") go("page", item.pkg + "/" + (t.page || ""));
  }
  function showPackagePage(arg) {
    const [pkg, pageId] = String(arg || "").split("/");
    const item = L.trayItems(S.rows, S.pages).find((i) => i.pkg === pkg && (!pageId || i.target.page === pageId));
    if (!item || item.target.kind !== "desktop" || !S.pages) {
      clear($("page-title")).textContent = S.pages ? "That page is not here." : "The pages address is not known yet.";
      return;
    }
    $("page-title").textContent = item.label;
    const frame = $("page-frame");
    frame.setAttribute("sandbox", L.PAGE_SANDBOX);      // its own origin (D53): its package's rights only
    if (frame.getAttribute("src") !== item.target.src) frame.src = item.target.src;
    $("page-tab").onclick = () => window.open(item.target.src, "_blank", "noopener");
    $("title").textContent = item.label;
  }

  /* ---- the rail (v1 menuAside.ts) ---- */
  function railItem(r, dim) {
    const a = el("a", { class: "item" + (dim ? " dim" : ""), href: "#" + r.id, "data-view": r.id, title: r.label },
      [icon(r.icon), el("span", { class: "label", text: r.label }), r.badge ? el("span", { class: "badge", "data-badge": r.badge, hidden: true }) : null]);
    a.addEventListener("click", closeDrawer);
    return a;
  }
  /* Only the pages every bot has; Setup while it is not finished (then it is on Settings). */
  function drawRail() {
    const g = L.railGroups(L.RAIL, { setupDone: !!(S.settings && S.settings.wizard_complete) });
    clear($("rail-primary")); clear($("rail-secondary")); clear($("rail-settings"));
    g.primary.forEach((r) => $("rail-primary").appendChild(railItem(r, false)));
    g.secondary.forEach((r) => $("rail-secondary").appendChild(railItem(r, true)));
    g.settings.forEach((r) => $("rail-settings").appendChild(railItem(r, false)));
    setBadges();
    markRail();
  }
  function drawRailApps() {
    const nav = clear($("rail-apps"));
    const items = L.trayItems(S.rows, S.pages);
    $("rail-apps-sep").hidden = $("rail-apps-label").hidden = !items.length;
    items.forEach((item) => {
      const a = el("a", { class: "item dim" + (item.down ? " down" : ""), href: "#page/" + item.pkg + "/" + (item.target.page || ""),
                          title: item.down ? item.label + ": not working right now" : item.label, "data-view": "page:" + item.pkg },
        [pic(item.icon, "widgets"), el("span", { class: "label", text: item.label }),
         item.target.kind === "tab" ? el("span", { class: "msr ext", text: "open_in_new" }) : null]);
      a.addEventListener("click", (ev) => { if (item.target.kind !== "desktop") { ev.preventDefault(); openTarget(item); } closeDrawer(); });
      nav.appendChild(a);
    });
    markRail();
  }
  function markRail() {
    const key = S.view === "page" ? "page:" + String(S.arg).split("/")[0] : S.view;
    document.querySelectorAll(".rail a.item").forEach((a) => a.classList.toggle("on", a.dataset.view === key));
  }
  function setBadges() {
    document.querySelectorAll("[data-badge]").forEach((b) => {
      const n = b.dataset.badge === "yourturn" ? S.counts.yourturn : (S.counts.setup ? "!" : 0);
      b.hidden = !n;
      b.textContent = String(n);
    });
  }
  function openDrawer() { $("rail").classList.add("open"); $("scrim").hidden = false; }
  function closeDrawer() { $("rail").classList.remove("open"); $("scrim").hidden = true; }
  $("menu-btn").addEventListener("click", openDrawer);
  $("scrim").addEventListener("click", closeDrawer);

  /* YOUR PRIVATE STUFF (v1 rail card): the vault, the Bridge, what is open to the outside. */
  async function drawPrivate() {
    const [v, b] = await Promise.all([api("GET", "/v1/vault/status"), api("GET", "/v1/bridge")]);
    const vault = L.vaultView(v.status === 200 ? v.data : {});
    const bridge = b.status === 200 ? L.bridgeView(b.data) : null;
    clear($("private")).append(
      el("div", { class: "micro", text: "Your private stuff" }),
      el("div", { class: "what" }, [icon(vault.open ? "lock_open" : "shield_lock"), el("span", { text: "Vault: " + vault.words })]),
      el("div", { class: "sub", text: bridge ? bridge.text : "The Bridge did not answer." }));
  }

  /* ---- the top bar: the Bridge, the brain, setup ---- */
  async function loadSettings() {
    const r = await api("GET", "/v1/settings");
    if (r.status !== 200) return null;
    S.settings = r.data;
    S.pages = typeof r.data.pages === "string" ? r.data.pages.replace(/\/+$/, "") : "";
    if (r.data.network) {       // D130: on the home network, each package's own port, and its pass as a cookie
      const n = await api("GET", "/v1/network/pages");
      S.pages = n.status === 200 ? L.networkBase(location, n.data) : "";
    }
    S.counts.setup = !r.data.wizard_complete;
    drawRail();
    drawName();
    applyTheme();
    drawFaces();
    return S.settings;
  }
  /* The bot's name (lane I10a, GET /v1/settings `bot`): "Puff" until she names it. */
  function drawName() {
    const name = L.botName(S.settings);
    $("botname").textContent = name;
    document.title = name + ", PuffPAI Desktop";
  }
  /* The bot's theme: its tokens on :root, on top of day or night (theme.js re-applies them when day and
   * night switch). No themes route: the Desktop's own. */
  async function loadThemes() {
    const r = await api("GET", "/v1/settings/themes");
    S.themes = L.themeList(r.status === 200 ? r.data : null);
    applyTheme();
    return S.themes;
  }
  function applyTheme(preview) {
    if (!S.themes) return;
    const id = preview || (S.settings && S.settings.bot && S.settings.bot.theme) || "";
    const t = L.themeById(S.themes, id);
    window.PuffTheme.look(t, !preview);     // a "both" theme: night tokens at night, day tokens by day
  }
  async function drawChips() {
    const [b, m] = await Promise.all([api("GET", "/v1/bridge"), api("GET", "/v1/model")]);
    const chips = clear($("chips"));
    if (b.status === 200) {
      const open = b.data.state === "open";
      chips.appendChild(el("a", { class: "chip " + (open ? "open" : "closed"), href: "#outside", title: L.bridgeView(b.data).text },
        [icon(open ? "public" : "public_off"), open ? "The Bridge is open" : "The Bridge is closed"]));
    }
    if (m.status === 200) {
      const brain = L.brainView(m.data);
      chips.appendChild(el("a", { class: "chip " + (brain.ok ? "ok" : "warn"), href: "#settings/brain", title: brain.reason || brain.words },
        [icon("neurology"), brain.name + ": " + brain.words]));
    }
    if (S.settings && !S.settings.wizard_complete)
      chips.appendChild(el("a", { class: "chip warn", href: "#setup" }, [icon("rocket_launch"), "Setup not finished"]));
  }
  function themeIcon() { $("theme-btn").firstChild.textContent = document.documentElement.dataset.puffTheme === "day" ? "light_mode" : "dark_mode"; }
  $("theme-btn").addEventListener("click", () => {
    window.PuffTheme.set(document.documentElement.dataset.puffTheme === "day" ? "night" : "day");
    themeIcon();
  });

  /* The owner, 10-30: she can switch between the window and this dashboard at any time. The Core opens the window
   * (POST /v1/window/open); when it can't open here, its plain line says why, and this page goes on working. */
  $("window-btn").addEventListener("click", async () => {
    if (L.onNetworkHost(location.hostname)) { location.assign("/window/"); return; }   // D130: this browser stands in for it
    const x = await api("POST", "/v1/window/open");
    const d = x.data || {};
    if (x.status !== 200 || !d.ok) notice({ title: "The window did not open", text: d.line || d.error || "BuildBot did not answer." });
  });

  /* ---- the face: Puff (logic.faceFor), on her package's own origin, or the Desktop's own copy ---- */
  function face(cls) {
    const f = L.faceFor(S.settings && S.settings.avatar, S.rows, S.pages);
    if (f.kind !== "face") return el("div", { class: cls + " face-pic" }, [el("img", { src: f.src, alt: f.name, title: f.name })]);
    const frame = el("iframe", { class: cls + " face-frame", src: f.src, sandbox: L.FACE_SANDBOX, title: f.name,
                                 referrerpolicy: "no-referrer", tabindex: "-1" });
    frame.dataset.origin = f.origin;
    return frame;
  }
  function drawFaces() {
    const box = clear($("face-mini"));
    box.appendChild(face("face-mini"));
  }
  /* Puff's mouth while the bot answers: one message, to her face's own origin only. */
  function talk(talking, pulse) {
    document.querySelectorAll("iframe.face-frame").forEach((f) => {
      if (!f.dataset.origin) return;
      try { f.contentWindow.postMessage({ type: "puffpai.avatar", talking: !!talking, pulse: pulse || 0 }, f.dataset.origin); } catch (e) { /* not loaded yet */ }
    });
  }

  /* ---- pages and the router (#home, #setup/brain, #page/<pkg>/<page>) ---- */
  function page(id, def) { S.defs[id] = def; }
  function go(view, arg) { location.hash = "#" + view + (arg ? "/" + arg : ""); }
  function route() {
    if (/^#pair=/.test(location.hash)) { location.reload(); return; }   // D92: a sign-in link opened in this same tab
    const h = decodeURIComponent(location.hash.replace(/^#/, ""));
    const at = h.indexOf("/");
    let view = at < 0 ? h : h.slice(0, at);
    const arg = at < 0 ? "" : h.slice(at + 1);
    if (!view || (!S.defs[view] && view !== "setup" && view !== "page")) view = "home";
    show(view, arg);
  }
  window.addEventListener("hashchange", route);
  function show(view, arg) {
    if (S.signedOut) return;                 // D92: signed out, only the sign-in screen
    S.view = view; S.arg = arg || "";
    document.querySelectorAll("#main .view").forEach((v) => { v.hidden = true; });
    const rail = L.RAIL.find((r) => r.id === view);
    $("title").textContent = rail ? rail.label : view === "page" ? "" : view;
    if (view === "setup") { $("view-setup").hidden = false; loadWizard(arg); }
    else if (view === "page") { $("view-page").hidden = false; showPackagePage(arg); }
    else {
      let sec = $("view-" + view);
      if (!sec) { sec = el("section", { id: "view-" + view, class: "view" }); $("views").appendChild(sec); }
      sec.hidden = false;
      const def = S.defs[view];
      if (def.title) $("title").textContent = def.title;
      try { def.draw(sec, S.arg); } catch (e) { clear(sec).appendChild(el("p", { class: "fail", text: "This page could not be drawn: " + e.message })); }
    }
    if (view !== "chat" && S.defs.chat && S.defs.chat.dock) S.defs.chat.dock();
    markRail();
    $("main").scrollTop = 0;
  }
  function redraw(view) {
    if (S.signedOut || S.view !== view || !S.defs[view]) return;
    const sec = $("view-" + view);
    if (sec && !sec.hidden) S.defs[view].draw(sec, S.arg, true);
  }

  /* ---- live updates from GET /v1/events (the Bus, as server-sent events) ---- */
  const EVENTS = ["checklist.changed", "notify.posted", "consent.requested", "consent.granted", "consent.revoked",
                  "wizard.progress", "bridge.posture", "vault.locked", "vault.unlocked", "vault.stored", "provider.changed",
                  "provider.installed", "egress.observed", "device.changed", "core.selftest"];
  function on(type, fn) { (S.subs[type] = S.subs[type] || []).push(fn); }
  function fire(type, data) { (S.subs[type] || []).concat(S.subs["*"] || []).forEach((fn) => { try { fn(data, type); } catch (e) { /* one page's error stays there */ } }); }
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

  function listen() {
    const es = stream("/v1/events");
    EVENTS.forEach((type) => es.addEventListener(type, (ev) => {
      let data = null;
      try { data = JSON.parse(ev.data); } catch (e) { data = null; }
      fire(type, data);
    }));
  }
  on("checklist.changed", refreshCounts);
  on("consent.requested", refreshCounts);
  on("consent.granted", refreshCounts);
  on("consent.revoked", refreshCounts);
  on("wizard.progress", () => { loadSettings(); drawChips(); if (S.view === "setup") refreshSteps(); });
  on("bridge.posture", () => { drawChips(); drawPrivate(); });
  on("vault.locked", drawPrivate);
  on("vault.unlocked", drawPrivate);
  on("notify.posted", toolsmithToChat);

  async function refreshCounts() {
    const [c, y] = await Promise.all([api("GET", "/v1/checklist"), pendingConsents()]);
    S.counts.yourturn = (c.status === 200 && Array.isArray(c.data) ? c.data.length : 0) + y.cards.length;
    setBadges();
  }
  /* A build asked for in chat says its result in the chat too (the Core keeps it in the conversation). */
  async function toolsmithToChat() {
    const r = await api("GET", "/v1/notify");
    if (r.status !== 200 || !Array.isArray(r.data)) return;
    const seen = S.chatSeen;
    S.chatSeen = new Set(r.data.map((n) => n.id).concat(Array.from(seen || [])));
    if (!seen || !S.defs.chat || !S.defs.chat.say) return;
    r.data.filter((n) => n.package === "toolsmith" && !seen.has(n.id)).reverse().forEach((n) => S.defs.chat.say(n.text || n.title));
  }

  /* ---- the Bridge: open or closed. In safe mode, opening needs the PIN and says why first ---- */
  async function bridgeButton(view, after) {
    const b = L.bridgeView(view);
    if (!b.button) return null;
    const btn = el("button", { class: b.action === "close" ? "" : "primary", text: b.button });
    btn.addEventListener("click", async () => {
      if (b.action === "close") { await api("POST", "/v1/bridge/close", {}); if (after) after(); return; }
      const plan = L.bridgeOpen(view);
      if (plan.needsPin) { bridgePinCard(plan, after); return; }
      const o = await api("POST", "/v1/bridge/open", {});
      if (o.status === 200) { if (after) after(); return; }
      if (o.data && o.data.needs_pin) { bridgePinCard(L.bridgeOpen(o.data), after); return; }
      notice({ title: "The Bridge did not open", text: said(o, "") });
    });
    return btn;
  }
  function bridgePinCard(plan, after) {
    const p = pin();
    const says = el("p", { class: "fail", text: "" });
    const card = el("div", { class: "card note high" }, [el("h3", { text: plan.title })]
      .concat(plan.lines.map((t) => el("p", { text: t })))
      .concat([p, says, el("div", { class: "row" }, [
        el("button", { class: "primary", text: "Open the Bridge", on: { click: async () => {
          const o = await api("POST", "/v1/bridge/open", { pin: p.value });
          p.value = "";
          if (o.status === 200) { card.remove(); if (after) after(); return; }
          says.textContent = said(o, "The Bridge did not open.");
        } } }),
        el("button", { class: "link", text: "Keep it closed", on: { click: () => card.remove() } }),
      ])]));
    $("notices").prepend(card);
    p.focus();
  }

  /* ---- Setup: the browser renderer of the wizard (unchanged rules) ---- */
  async function loadWizard(stepId) {
    const r = await api("GET", "/v1/wizard");
    if (r.status !== 200) { clear($("wiz")).appendChild(el("p", { class: "err", text: said(r, "Setup did not load.") })); return; }
    S.wizard = r.data;
    $("title").textContent = "Setup";
    drawSteps();
    const want = stepId && S.wizard.steps.some((s) => s.id === stepId) ? stepId : null;
    if (want && want !== S.step) startStep(want);
    else if (!S.step) startStep(S.wizard.current || S.wizard.steps[0].id);
    else if (S.pollParked === S.step) { S.pollParked = null; send({}, true); }   // back on this page: its poll goes on (D113)
  }
  function drawSteps() {
    const ol = clear($("wiz-steps"));
    S.wizard.steps.forEach((s) => {
      const li = el("li", { class: s.state + (s.id === S.step ? " cur" : ""), text: s.title });
      li.addEventListener("click", () => startStep(s.id));
      ol.appendChild(li);
    });
  }
  async function startStep(id) {
    clearTimeout(S.pollTimer);
    S.pollParked = null;
    S.step = id;
    S.transcript = new L.WizardTranscript();
    clear($("wiz"));
    if (S.wizard) drawSteps();
    await send({});
  }
  /* *poll*: the build's "ask again", the only send that may go again by itself: it carries no answer. */
  async function send(answers, poll) {
    const step = S.step;
    const r = await api("POST", "/v1/wizard/" + encodeURIComponent(step), { answers, renderer: "browser" }, poll ? L.POLL_WAIT_MS : 0);
    if (poll && step !== S.step) return;
    if (r.status !== 200) {
      if (poll && L.pollCanRetry(r.status)) { drawAlive(null, L.POLL_RETRY); pollSoon(step, S.pollS); return; }
      $("wiz").appendChild(el("p", { class: "err", text: said(r) }));
      return;
    }
    drawScreen(r.data);
  }
  function pollSoon(step, seconds) {
    clearTimeout(S.pollTimer);
    S.pollS = seconds || 2;
    S.pollTimer = setTimeout(() => {
      if (S.step !== step) return;
      if (S.view === "setup") send({}, true); else S.pollParked = step;
    }, S.pollS * 1000);
  }
  /* How things stand now (D113), in one place under the lines, redrawn with each answer: never added up. */
  function drawAlive(sc, trouble) {
    const wiz = $("wiz");
    wiz.querySelectorAll("[data-setup-alive]").forEach((n) => n.remove());
    const now = L.setupAlive(sc && sc.alive, Date.now());
    if (trouble) wiz.appendChild(el("p", { class: "err", "data-setup-alive": "1", text: trouble }));
    if (now.vault) wiz.appendChild(el("p", { class: "line", "data-setup-alive": "1" }, [L.VAULT_WAIT + " ", el("a", { href: "#building", text: L.VAULT_WAIT_HOME })]));
    now.lines.forEach((t) => wiz.appendChild(el("p", { class: "line", "data-setup-alive": "1", text: t })));
  }
  function drawScreen(sc) {
    S.transcript.push(sc);
    const wiz = $("wiz");
    if (sc.clear || sc.show) wiz.querySelectorAll(".code, .code-line").forEach((n) => { n.parentNode.removeChild(n); });
    wiz.querySelectorAll(".q").forEach((n) => n.remove());
    const box = el("div", { class: "screen" }, [wiz.childElementCount ? null : el("h2", { text: sc.title })]);
    (sc.lines || []).forEach((l) => box.appendChild(el("p", { class: "line", text: l })));
    if (sc.show) {
      box.appendChild(el("div", { class: "code", text: sc.show.value }));
      box.appendChild(el("p", { class: "line code-line", text: sc.show.line }));
    }
    if (box.childElementCount) wiz.appendChild(box);
    drawAlive(sc);
    if ((sc.status === "ask" || sc.status === "show") && sc.field) { wiz.appendChild(question(sc.field, sc.error)); return; }
    if (sc.status === "working") { pollSoon(sc.step, sc.poll_s); return; }   // the build goes on by itself: ask again for new lines
    const acts = el("div", { class: "acts" });
    if (sc.next) {
      const b = el("button", { class: "primary", text: "Next: " + titleOf(sc.next) });
      b.addEventListener("click", () => { startStep(sc.next); refreshSteps(); });
      acts.appendChild(b);
    } else {
      const b = el("button", { class: "primary", text: "Open Home" });
      b.addEventListener("click", () => { go("home"); loadSettings(); });
      acts.appendChild(b);
    }
    wiz.appendChild(acts);
    refreshSteps();
  }
  function titleOf(id) { const s = (S.wizard.steps || []).find((x) => x.id === id); return s ? s.title : id; }
  async function refreshSteps() {
    const r = await api("GET", "/v1/wizard");
    if (r.status === 200) { S.wizard = r.data; drawSteps(); }
  }
  function question(f, error) {
    const inp = L.wizardInput(f);
    const q = el("form", { class: "q", autocomplete: "off" }, [
      el("label", { class: "lab", text: f.label }),
      f.help ? el("p", { class: "help", text: f.help }) : null,
      f.example_line && inp.kind !== "text" ? el("p", { class: "help", text: f.example_line }) : null,
      error ? el("p", { class: "err", text: error }) : null]);
    let get;
    if (inp.kind === "choice") {
      const name = "q-" + f.id;
      inp.options.forEach((o) => q.appendChild(el("label", { class: "opt" }, [
        el("input", { type: "radio", name, value: o.value, checked: o.value === inp.value }),
        el("span", {}, [o.label + " ", o.note ? el("small", { class: "muted", text: o.note }) : null])])));
      get = () => { const c = q.querySelector("input[name='" + name + "']:checked"); return c ? c.value : ""; };
    } else if (inp.kind === "theme") {               // the same swatches as Looks (lane I16), shown on this page when picked
      const name = "q-" + f.id;
      const themes = inp.options.map((o) => {
        const t = L.themeById(S.themes || [], o.value);
        return t && t.id === o.value ? Object.assign({}, t, { name: o.label || t.name, line: o.note || t.line })
          : { id: o.value, name: o.label || o.value, mode: o.mode || "both", modeWords: "", tokens: {}, dayTokens: {}, line: o.note || "" };
      });
      q.appendChild(themePicker(themes, inp.value, name, (id) => applyTheme(id)));
      get = () => { const c = q.querySelector("input[name='" + name + "']:checked"); return c ? c.value : ""; };
    } else if (inp.kind === "confirm") {
      let val = null;
      const yes = el("button", { type: "submit", class: "primary", text: "Yes" });
      const no = el("button", { type: "submit", text: "No" });
      yes.addEventListener("click", () => { val = true; });
      no.addEventListener("click", () => { val = false; });
      q.appendChild(el("div", { class: "acts" }, [yes, no]));
      get = () => val;
    } else {
      const box = el("input", { type: inp.kind === "password" ? "password" : inp.kind === "number" ? "number" : "text",
        value: inp.value === undefined || inp.value === null ? "" : String(inp.value), placeholder: inp.placeholder || null,
        autocomplete: inp.kind === "password" ? "new-password" : "off", spellcheck: "false" });
      q.appendChild(box);
      if (f.example_line && inp.kind === "text") q.appendChild(el("p", { class: "help", text: f.example_line }));
      get = () => box.value;
      setTimeout(() => box.focus(), 0);
    }
    if (f.hint) q.appendChild(el("p", { class: "help", text: f.hint }));
    if (inp.kind !== "confirm") q.appendChild(el("div", { class: "acts" }, [el("button", { type: "submit", class: "primary", text: "Next" })]));
    q.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const value = get();
      S.transcript.typed(f, value);
      q.querySelectorAll("input[type=password]").forEach((p) => { p.value = ""; });
      send({ [f.id]: value });
    });
    return q;
  }

  /* ---- start ---- */
  async function start() {
    new MutationObserver(foldNotices).observe($("notices"), { childList: true });   // lane looks: fold repeats
    drawRail();
    themeIcon();
    await pairFromHash();
    await loadSettings();                    // first: it says where package pages live (the pages address)
    if (S.signedOut) return;                 // D92: one sign-in screen, nothing else drawn or polled
    await loadThemes();
    await loadRegistry();
    drawFaces();
    route();
    drawChips();
    drawPrivate();
    refreshCounts();
    toolsmithToChat();
    listen();
    setInterval(async () => { await loadRegistry(); fire("registry.poll", null); }, 30000);  // Caretaker's health: a slow poll keeps it honest
  }

  window.Puff = { L, S, el, clear, icon, pic, api, authHeaders, signIn, signinWhere, stream, said, missing, pageJson, notice, tile, act, later, kv, pin, stepCardEl, consentEl,
                  pendingConsents, consentInline, keysReady, loadRegistry, loadSettings, names, openTarget, page, go, redraw, on,
                  refreshCounts, bridgeButton, face, talk, drawChips, drawPrivate, drawFaces, loadThemes, applyTheme,
                  themePicker, sleep };
  document.addEventListener("DOMContentLoaded", start);
})();
