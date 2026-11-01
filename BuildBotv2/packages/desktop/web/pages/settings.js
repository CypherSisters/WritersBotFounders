/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/settings/page.tsx, settings/ToolsmithModelCard.tsx,
 * settings/PairedDevicesCard.tsx and looks/page.tsx (adapted). Settings: one list, one row per setting, each showing what
 * it is now and opening in place (P.act, as on Vault); #settings/<id> opens that row. Who your bot is, You, the brain,
 * Toolsmith's model (its one form, key and Yes are on Developer), the Bridge, what opens at start, Setup, this browser,
 * and the Desktop. The parts a package may fill are on Developer.
 * Looks: one panel: the bot's name and theme as swatches (lanes I10a, I16), the avatar, and day or night. What is not
 * built yet gets one plain line (D92): never a nested box, a route name or developer words. */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  const setupBtn = (step, label) => el("button", { on: { click: () => P.go("setup", step) } }, [P.icon("rocket_launch"), label]);
  const opened = new Set();
  /* One row of the list. It has what a tile has (node, body), and *note* says what the setting is now. */
  function row(id, title) {
    const r = P.act({ id, title, open: opened.has(id) });
    r.node.addEventListener("toggle", () => { if (r.node.open) opened.add(id); else opened.delete(id); });
    return r;
  }

  async function brainTile() {
    const t = row("brain", "The brain");
    const [m, menu] = await Promise.all([P.api("GET", "/v1/model"), P.api("GET", "/v1/model/menu")]);
    const said = el("p", { class: "small" });
    const now = m.status === 200 && m.data.settings ? String(m.data.settings.model || "") : "";
    const useModel = async (id, box) => {
      const r = await P.api("PUT", "/v1/model", { model: id });
      if (r.status === 200) { P.drawChips(); P.redraw("settings"); return; }
      box.textContent = P.said(r);
    };
    if (m.status === 200) {
      const b = L.brainView(m.data);
      t.note.textContent = b.name + ", " + b.words;
      if (b.reason) t.body.appendChild(el("p", { class: "muted", text: b.reason }));
    }
    if (menu.status === 200) {
      const opts = (menu.data.menu && menu.data.menu.options) || [];
      if (opts.length) t.body.appendChild(el("details", {}, [el("summary", { class: "small", text: "Models this computer can run (" + opts.length + ")" }),
        el("ul", { class: "lines" }, opts.slice(0, 12).map((o) => el("li", {}, [el("span", { class: "grow", text: (o.label || o.id) + (o.recommended ? " (Recommended)" : "") }),
          el("span", { class: "faint small", text: (o.installed ? "on this computer" : o.disk_gb ? o.disk_gb + " GB to download" : "") }),
          o.installed && now !== String(o.id) ? el("button", { "data-use-model": o.id, on: { click: () => useModel(o.id, said) } }, ["Use this"]) : null])))]));
    }
    t.body.append(el("p", { class: "small muted", text: L.NOT_BUILT.brain }), said, el("div", { class: "row" }, [setupBtn("brain", "Change it in Setup"),
      el("a", { href: "#developer/brain", text: "Models for each job" })]));
    return t.node;
  }

  /* Toolsmith's model: what it is now, and the way to its one form on Developer (the company, the key, the Yes). */
  async function toolsmithTile() {
    const t = row("toolsmith", "Toolsmith's model");
    const st = await P.api("GET", "/v1/toolsmith");
    if (st.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(st) })); return t.node; }
    t.note.textContent = st.data.where === "cloud" ? (st.data.company || st.data.provider) + ", " + (st.data.model || "") : "This computer's own model";
    t.body.append(el("p", { class: "small muted", text: "Only Toolsmith may use a cloud model, and only if you pick one. Everything else runs on this computer." }),
      el("div", { class: "row" }, [el("a", { href: "#developer/toolsmith", "data-go": "toolsmith", text: "Pick a cloud model and add its key" })]));
    return t.node;
  }

  async function bridgeTile() {
    const t = row("bridge", "The Bridge");
    const r = await P.api("GET", "/v1/bridge");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    t.note.textContent = (r.data.state === "open" ? "Open" : "Closed") + ", Toolsmith's search " + (r.data.search ? "on" : "off");
    t.body.appendChild(el("p", { text: L.bridgeView(r.data).text }));
    const btn = await P.bridgeButton(r.data, () => { P.redraw("settings"); P.drawChips(); P.drawPrivate(); });
    t.body.appendChild(el("div", { class: "row" }, [btn, setupBtn("bridge", "Change what may go out in Setup"),
      el("a", { href: "#outside", text: "What left your computer" })]));
    return t.node;
  }

  /* D130 (from v1's PairedDevicesCard): every paired browser, and removing one. It stops on its very next request. */
  async function devicesTile() {
    const t = row("devices", "Paired devices");
    t.body.append(el("p", { class: "small", text: L.signInWords(await P.signinWhere(), "", true).line }));
    const r = await P.api("GET", "/v1/devices");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const list = r.data.devices || [];
    t.note.textContent = list.length === 1 ? "1 device" : list.length + " devices";
    t.body.appendChild(el("ul", { class: "lines" }, list.map((d) => {
      const said = el("span", { class: "small fail" });
      return el("li", {}, [el("span", { class: "grow", text: (d.label || "A browser") + (d.this ? " (this one)" : "") }), said,
        el("button", { class: "link small", text: "Remove", on: { click: async () => {
          const x = await P.api("DELETE", "/v1/devices/" + encodeURIComponent(d.id));
          if (x.status !== 200) { said.textContent = P.said(x); return; }
          P.redraw("settings");
        } } })]);
    })));
    return t.node;
  }

  /* D130: her home network: on unless she turns it off, in words that say what that means. */
  async function networkTile() {
    const t = row("network", "Your home network");
    const r = await P.api("GET", "/v1/network");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const here = !(P.S.settings && P.S.settings.network);
    const v = L.networkView(r.data, here);
    t.note.textContent = v.note;
    t.body.appendChild(el("p", { text: v.line }));
    if (v.urls.length) t.body.appendChild(el("ul", { class: "lines" }, v.urls.map((u) => el("li", { text: u }))));
    if (v.worth) t.body.appendChild(el("p", { class: "small muted", text: v.worth }));
    if (v.how) t.body.appendChild(el("p", { class: "small muted", text: v.how }));
    const said = el("p", { class: "small", "data-said": "network" });
    const turn = el("button", { class: v.on ? "" : "primary", text: v.button, on: { click: async () => {
      turn.disabled = true;
      const x = await P.api("PUT", "/v1/network", { on: !v.on });
      turn.disabled = false;
      if (x.status !== 200) { said.textContent = P.said(x); return; }
      P.redraw("settings");
    } } });
    const buttons = el("div", { class: "row" }, [turn]);
    if (v.on) buttons.appendChild(el("button", { text: "Show a pairing code", on: { click: async () => {
      const x = await P.api("POST", "/v1/network/code");
      said.textContent = x.status === 200 ? "Type this on the other device: " + x.data.code + ". It works once, for " + x.data.minutes + " minutes." : P.said(x);
    } } }));
    t.body.append(buttons, said);
    return t.node;
  }

  /* Who your bot is (lane I12's GET, PUT /v1/settings/persona and POST .../redo). The owner's edit is checked by the
   * Core (plain text, at most 4000 characters); its 400 shows next to the box. Writing it again asks this
   * computer's brain, which can take tens of seconds, so a working line shows while it runs. */
  async function personaTile() {
    const t = row("persona", "Who your bot is");
    const view = el("div");
    const box = el("textarea", { rows: "10", maxlength: "4000", "aria-label": "Who your bot is, in your words", spellcheck: "true" });
    const count = el("span", { class: "faint small" });
    const fieldSaid = el("p", { class: "fail small", "data-said": "persona-field" });
    const said = el("p", { class: "small", "data-said": "persona" });
    let max = 4000;
    const counted = () => { count.textContent = box.value.length + " of " + max + " characters"; };
    box.addEventListener("input", counted);
    function paint(d) {
      const v = L.personaView(d);
      max = v.max;
      box.setAttribute("maxlength", String(max));
      box.value = v.text;
      counted();
      clear(view);
      t.note.textContent = v.state === "none" ? "Nothing written yet" : v.role || "Written";
      if (v.state === "none") { view.appendChild(el("p", { class: "empty", text: L.PERSONA_NONE })); return; }
      if (v.flagLine) view.appendChild(el("p", { class: "warn-t", text: v.flagLine }));
      if (v.role) view.appendChild(el("p", { class: "persona-role", text: v.role }));
      const part = (head, kids) => kids ? view.append(el("p", { class: "micro", text: head }), kids) : null;
      if (v.helps.length) part("What it can help with", el("ul", { class: "small" }, v.helps.map((x) => el("li", { text: x }))));
      if (v.talks) part("How it talks", el("p", { class: "small", text: v.talks }));
      if (v.questions.length) part("The first questions it asks", el("ol", { class: "small" }, v.questions.map((x) => el("li", { text: x }))));
      if (v.other.length) part("In your words", el("div", {}, v.other.map((x) => el("p", { class: "small", text: x }))));
      if (v.wont.length) part("What it won't do", el("ul", { class: "small" }, v.wont.map((x) => el("li", { text: x }))));
      if (v.from) view.appendChild(el("p", { class: "faint small", text: v.from }));
    }
    const r = await P.api("GET", "/v1/settings/persona");
    if (P.missing(r)) { t.body.appendChild(el("p", { class: "small muted", text: L.NOT_BUILT.tile })); return t.node; }
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    paint(r.data);
    const save = el("button", { class: "primary", on: { click: async () => {
      fieldSaid.textContent = ""; said.textContent = "";
      const x = await P.api("PUT", "/v1/settings/persona", { text: box.value });
      if (x.status === 400) { fieldSaid.textContent = P.said(x); return; }
      if (x.status !== 200) { said.textContent = "Not saved: " + P.said(x, "the Core gave no reason (" + x.status + ")."); return; }
      paint(x.data);
      said.textContent = "Saved. The chat uses it from your next message.";
    } } }, [P.icon("save"), "Save"]);
    const redo = el("button", { on: { click: async () => {
      fieldSaid.textContent = "";
      said.className = "now"; said.textContent = L.PERSONA_REDO;
      redo.disabled = save.disabled = true;
      const x = await P.api("POST", "/v1/settings/persona/redo");
      redo.disabled = save.disabled = false;
      said.className = "small";
      if (x.status !== 200) { said.textContent = P.said(x, "It was not written again."); return; }
      paint(x.data);
      said.textContent = "Written again from your goal.";
    } } }, [P.icon("refresh"), "Write it again"]);
    t.body.append(view, el("details", {}, [el("summary", { class: "small", text: "Change it in your words" }), box,
      el("div", { class: "row" }, [count]), fieldSaid]), el("div", { class: "row" }, [save, redo]), said);
    return t.node;
  }

  /* You (lane I12's GET and PUT /v1/settings/you): kept in the owner's vault. Locked: a plain line, never an empty
   * form. A 400 names its field; it shows next to that field. */
  async function youTile() {
    const t = row("you", "You");
    const r = await P.api("GET", "/v1/settings/you");
    if (P.missing(r)) { t.body.appendChild(el("p", { class: "small muted", text: L.NOT_BUILT.tile })); return t.node; }
    if (r.status === 423) {
      t.note.textContent = "Your vault is locked";
      t.body.append(el("p", { class: "empty", "data-said": "you-locked", text: L.YOU_LOCKED }),
        el("div", { class: "row" }, [el("button", { on: { click: () => P.go("vault") } }, [P.icon("lock_open"), "Open Vault"])]));
      return t.node;
    }
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const d = r.data || {};
    t.note.textContent = d.name || d.call_me || "Kept in your vault";
    const browser = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { return ""; } })();
    const zones = L.timeZoneChoices(typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [], d.timezone, browser);
    const fields = {
      name: el("input", { type: "text", maxlength: "40", autocomplete: "off", value: d.name || "", "aria-label": L.YOU_LABELS.name }),
      call_me: el("input", { type: "text", maxlength: "40", autocomplete: "off", value: d.call_me || "", "aria-label": L.YOU_LABELS.call_me }),
      about: el("textarea", { rows: "4", maxlength: "1000", "aria-label": L.YOU_LABELS.about }),
      timezone: el("select", { "aria-label": L.YOU_LABELS.timezone }, [el("option", { value: "", text: "Not set" })]
        .concat(zones.options.map((z) => el("option", { value: z, text: z.replace(/_/g, " ") })))),
    };
    fields.about.value = d.about || "";
    fields.timezone.value = zones.value;
    const notes = {}, errs = {};
    const said = el("p", { class: "small", "data-said": "you" });
    function flags(x) {
      const f = L.youFlags(x);
      ["call_me", "about"].forEach((k) => { notes[k].textContent = f[k]; });
    }
    const rows = Object.keys(fields).map((k) => {
      notes[k] = el("p", { class: "warn-t small" });
      errs[k] = el("p", { class: "fail small", "data-said": "you-" + k });
      return el("div", { class: "field" }, [el("p", { class: "micro field-label", text: L.YOU_LABELS[k] }), fields[k], notes[k], errs[k]]);
    });
    flags(d);
    const save = el("button", { class: "primary", on: { click: async () => {
      Object.keys(errs).forEach((k) => { errs[k].textContent = ""; });
      said.textContent = "";
      const body = {};
      Object.keys(fields).forEach((k) => { body[k] = fields[k].value; });
      const x = await P.api("PUT", "/v1/settings/you", body);
      if (x.status !== 200) {
        const e = L.youError(P.said(x, "Not saved."));
        if (e.field && errs[e.field]) errs[e.field].textContent = e.text; else said.textContent = e.text;
        return;
      }
      flags(x.data);
      said.textContent = L.youSaved(x.data);
    } } }, [P.icon("save"), "Save"]);
    t.body.append(el("p", { class: "small muted", text: "Kept in your vault. Your bot uses what it calls you and your own words only on this computer's brain, and only while your vault is open." }),
      ...rows, el("div", { class: "row" }, [save]), said);
    return t.node;
  }

  /* The avatar: PUT /v1/settings/avatar. */
  async function avatarParts() {
    const s = await P.loadSettings() || {};
    const rows = (await P.loadRegistry()).filter((r) => r.kind === "avatar" && r.status !== "removed");
    const said = el("p", { class: "small" });
    const av = el("select", { "aria-label": "Avatar" }, [el("option", { value: "", text: "No avatar" })]
      .concat(rows.map((r) => el("option", { value: r.id, text: r.name }))));
    av.value = s.avatar && rows.some((r) => r.id === s.avatar.id) ? s.avatar.id : "";
    av.addEventListener("change", async () => {
      const r = await P.api("PUT", "/v1/settings/avatar", { id: av.value || null });
      said.textContent = r.status === 200 ? "Saved." : P.said(r);
      await P.loadSettings(); P.redraw(P.S.view);
    });
    return [el("p", { class: "micro field-label", text: "Avatar" }), av, said];
  }

  /* The Desktop itself: PUT /v1/settings/ui. Any ui package can replace it; with only this one there is nothing to pick. */
  async function desktopTile() {
    const t = row("desktop", "This Desktop");
    const s = await P.loadSettings() || {};
    t.note.textContent = s.ui || "desktop";
    const said = el("p", { class: "small" });
    if ((s.uis || []).length > 1) {
      const ui = el("select", { "aria-label": "Desktop" }, s.uis.map((id) => el("option", { value: id, text: id })));
      ui.value = s.ui || "desktop";
      ui.addEventListener("change", async () => {
        const r = await P.api("PUT", "/v1/settings/ui", { id: ui.value });
        said.textContent = r.status === 200 ? "Saved. Reload the page to use it." : P.said(r);
      });
      t.body.appendChild(ui);
    }
    t.body.append(el("p", { class: "small muted", text: "A Desktop you choose shows on the computer BuildBot runs on. Other devices get the built-in one." }),
      el("p", { class: "small muted" }, ["Any ui package can replace this Desktop. If one breaks, ",
      el("a", { href: "/v1/recover", text: "the recovery page" }), " always works."]), said);
    return t.node;
  }

  /* The bot's name and theme (lane I10a): GET /v1/settings `bot`, GET /v1/settings/themes, saved with
   * PUT /v1/settings/bot. Picking a theme shows it at once on this page; Save keeps it. */
  async function botTile() {
    const t = P.tile({ icon: "badge", title: "Your bot", wide: true });
    const s = await P.loadSettings() || {};
    const avatar = await avatarParts();
    const themes = await P.loadThemes();
    const bot = s.bot || {};
    let pick = L.themeById(themes, bot.theme || "").id;
    const name = el("input", { type: "text", value: typeof bot.name === "string" ? bot.name : "", placeholder: L.BOT_NAME,
                               maxlength: "40", autocomplete: "off", "aria-label": "Your bot's name" });
    const hello = el("h3");
    const said = el("p", { class: "small" });
    function paint() {
      hello.textContent = L.helloLine(new Date().getHours(), { bot: { name: name.value } });
    }
    name.addEventListener("input", paint);
    const list = P.themePicker(themes, pick, "bot-theme", (id) => { pick = id; P.applyTheme(id); paint(); });
    const save = el("button", { class: "primary", on: { click: async () => {
      const r = await P.api("PUT", "/v1/settings/bot", { name: name.value.trim(), theme: pick });
      if (P.missing(r)) { said.textContent = L.NOT_BUILT.botSave; return; }
      if (r.status !== 200) { said.textContent = P.said(r, "Not saved."); return; }
      await P.loadSettings();
      said.textContent = "Saved.";
    } } }, [P.icon("save"), "Save"]);
    const undo = el("button", { class: "link", on: { click: () => {
      pick = L.themeById(themes, (P.S.settings && P.S.settings.bot && P.S.settings.bot.theme) || "").id;
      list.querySelectorAll("input").forEach((x) => { x.checked = x.value === pick; });
      name.value = (P.S.settings && P.S.settings.bot && P.S.settings.bot.name) || "";
      P.applyTheme(); paint(); said.textContent = "";
    } } }, ["Undo"]);
    paint();
    t.body.append(el("p", { class: "micro field-label", text: "Name" }), name, el("div", { class: "looks-preview" }, [hello]),
      el("p", { class: "micro field-label", text: "Theme" }), list,
      el("div", { class: "row" }, [save, undo]), said,
      ...avatar, ...dayOrNight(),
      el("p", { class: "small muted" }, ["More looks and avatars are in ", el("a", { href: "#apps/available", text: "the PuffPAI store" }), "."]));
    return t.node;
  }
  window.addEventListener("hashchange", () => P.applyTheme());     // a theme only shown, never saved, goes away

  function setupTile() {
    const t = row("setup", "Setup");
    t.note.textContent = "Finished";
    t.body.appendChild(el("div", { class: "row" }, [setupBtn("", "Open Setup")]));
    t.body.prepend(el("p", { class: "small muted", text: "Run any step again to change it." }));
    return t.node;
  }

  function dayOrNight() {
    const cur = window.PuffTheme.choice();
    const out = [el("p", { class: "micro field-label", text: "Day or night" })];
    [["auto", "By the clock: day from 07:00 to 19:00"], ["day", "Always day"], ["night", "Always night"]].forEach(([v, label]) => {
      const r = el("input", { type: "radio", name: "theme", value: v, checked: cur === v });
      r.addEventListener("change", () => window.PuffTheme.set(v));
      out.push(el("label", { class: "opt" }, [r, el("span", { text: label })]));
    });
    return out;
  }

  /* D94: what Start BuildBot.cmd opens (GET /v1/settings `opens`, saved with PUT /v1/settings/opens). The PuffPAI
   * window is the default (the owner, 10-30); the chat and this dashboard are always there either way. */
  async function opensTile() {
    const t = row("opens", "What opens when BuildBot starts");
    const s = await P.loadSettings() || {};
    const cur = s.opens === "dashboard" || s.opens === "chat" ? s.opens : "window";
    t.note.textContent = cur === "dashboard" ? "Your dashboard" : cur === "chat" ? "Chat in a terminal" : "The PuffPAI window";
    const said = el("p", { class: "small" });
    [["window", "The PuffPAI window (default)"], ["chat", "Chat in a terminal"], ["dashboard", "Your dashboard"]].forEach(([v, label]) => {
      const r = el("input", { type: "radio", name: "opens", value: v, checked: cur === v });
      r.addEventListener("change", async () => {
        const x = await P.api("PUT", "/v1/settings/opens", { opens: v });
        said.textContent = x.status === 200 ? "Saved." : P.said(x, "Not saved.");
        if (x.status === 200) { await P.loadSettings(); t.note.textContent = v === "dashboard" ? "Your dashboard" : v === "chat" ? "Chat in a terminal" : "The PuffPAI window"; }
      });
      t.body.appendChild(el("label", { class: "opt" }, [r, el("span", { text: label })]));
    });
    t.body.append(el("p", { class: "faint small", text: "Starting BuildBot opens this. In the chat, /dashboard opens your dashboard." }), said);
    return t.node;
  }

  /* D140: may the bot change her documents? Off until she turns it on. */
  function editsTile() {
    const t = row("edits", "Changing your documents");
    t.note.textContent = "Off until you turn it on";
    t.body.appendChild(P.editsSetting());
    return t.node;
  }

  /* D141: move this bot to another computer. What doesn't travel is said before she does anything. */
  async function moveTile() {
    const t = row("move", "Move this bot to another computer");
    t.note.textContent = "Keys, the vault and chats never go";
    const m = await P.api("GET", "/v1/settings/move");
    const stays = m.status === 200 && Array.isArray(m.data.stays) ? m.data.stays : [];
    const said = el("div", { class: "small", "data-said": "move" });
    const folder = el("input", { class: "grow", type: "text", placeholder: "A folder, like /home/me/my-bot", "aria-label": "A folder", autocomplete: "off", spellcheck: "false" });
    const line = (text, cls) => clear(said).appendChild(el("p", { class: cls || "", text }));
    const bits = (head, list) => list && list.length ? el("p", {}, [el("b", { text: head + ": " }), list.join(", ") + "."]) : null;
    function done(d) {
      clear(said).append(el("p", { text: "Moved here: " + d.name + "." }), ...(d.said || []).map((x) => el("p", { text: x })),
        bits("Link these folders again", d.linked) || "", bits("Pin these documents again", d.pinned) || "", bits("Find these books again on Knowledge", d.books) || "",
        bits("Add these tool servers and APIs again", d.added) || "",
        bits("Set up on another kind of computer, so not added. Ask Toolsmith to try them here", d.not_here) || "");
      (d.left_out || []).forEach((x) => said.appendChild(el("p", { text: x.name + " did not come: " + x.why + "." })));
      (d.apps || []).forEach((a) => {
        const box = el("div", { "data-move-app": a.id });
        said.appendChild(el("div", { class: "row" }, [el("span", { class: "grow", text: a.name + " " + (a.version || "") }), el("button", { text: "Add", on: { click: async () => {
          const r = await P.api("POST", "/v1/packages", { source: a.source });
          clear(box);
          if (r.status !== 202 || !r.data.consent_id) { box.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
          P.consentInline(box, r.data.consent_id);
        } } })]), box);
      });
    }
    t.body.append(el("p", { class: "small", text: "Export makes a folder you can put in a git repo. On the new computer, setup or this box brings it in." }),
      el("p", { class: "small" }, [el("b", { text: "This doesn't travel. It is entered again on the new computer:" })]),
      el("ul", { class: "small", "data-move-stays": "" }, stays.map((x) => el("li", { text: x }))),
      el("div", { class: "row" }, [folder,
        el("button", { "data-move": "export", text: "Export to this folder", on: { click: async () => {
          line("Checking everything for anything private first...", "muted");
          const r = await P.api("POST", "/v1/settings/move/export", { path: folder.value });
          if (r.status !== 200) { line(P.said(r), "fail"); return; }
          line("Exported to " + r.data.folder + ". " + r.data.apps.length + " apps listed." + r.data.left_out.map((x) => " Left out " + x.name + ": " + x.why + ".").join(""));
        } } }),
        el("button", { "data-move": "import", text: "Bring in from this folder", on: { click: async () => {
          const c = await P.api("POST", "/v1/settings/move/check", { path: folder.value });
          if (c.status !== 200) { line(P.said(c), "fail"); return; }
          const r = await P.api("POST", "/v1/settings/move/import", { path: folder.value });
          if (r.status !== 200) { line(P.said(r), "fail"); return; }
          done(r.data);
        } } })]), said);
    return t.node;
  }

  P.page("settings", {
    title: "Settings",
    async draw(sec, arg) {
      const part = String(arg || "").split("/")[0];
      if (part) opened.add(part);
      const list = el("div", { class: "actlist" });
      clear(sec).appendChild(el("section", { class: "tile", "data-settings": "" }, [list]));
      const done = !!(P.S.settings && P.S.settings.wizard_complete);
      (await Promise.all([personaTile(), youTile(), brainTile(), toolsmithTile(), bridgeTile(), opensTile(), editsTile(), moveTile(), done ? setupTile() : null,
        networkTile(), devicesTile(), desktopTile()])).forEach((n) => { if (n) list.appendChild(n); });
      const want = part ? list.querySelector('[data-tile="' + part.replace(/[^a-z-]/g, "") + '"]') : null;
      if (want) want.scrollIntoView({ block: "nearest" });
    },
  });
  P.page("looks", {
    title: "Looks",
    async draw(sec) {
      const box = el("div", { class: "tiles" });
      clear(sec).appendChild(box);
      box.appendChild(await botTile());
    },
  });
})();
