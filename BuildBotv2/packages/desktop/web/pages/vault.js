/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/vault/page.tsx and vault/RecoveryCodeCeremony.tsx
 * (adapted to lane L3's zone 1 routes). Status, set up, lock and unlock, change the PIN, the recovery code, ways
 * in and the inbox. No route returns a key, so none can show here. The recovery code is the one thing the Core
 * shows, once, at setup and when it is made new; it leaves the page when she says she wrote it down.
 * Lane looks: one status panel and one "Do something" panel, each action a section of a list (not a card each). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  const rd = () => P.redraw("vault");
  let showingCode = false;            // a live redraw never takes the code away before she wrote it down

  /* One section of the "Do something" list: its title (and a short note) opens it. */
  const act = (title, note, body, open) => P.act({ title, note, body, open }).node;

  function form(title, fields, button, send, note, open) {
    const said = el("p", { class: "small" });
    const inputs = fields.map(([name, label, type]) => {
      const i = el("input", { type: type || "password", autocomplete: "off", placeholder: label, "aria-label": label });
      i.dataset.name = name;
      return i;
    });
    const f = el("form", { autocomplete: "off" }, inputs.concat([el("div", { class: "row" }, [el("button", { class: "primary", type: "submit", text: button })]), said]));
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const body = {};
      inputs.forEach((i) => { body[i.dataset.name] = i.value; });
      inputs.forEach((i) => { if (i.type === "password" || i.dataset.name === "code") i.value = ""; });   // a recovery code too
      await send(body, said);
    });
    return act(title, note, [f], open);
  }

  /* The recovery code, shown once (no-store), until she says she wrote it down. */
  function codeCard(box, code, note) {
    const c = el("div", { class: "card note high" }, [el("h3", { text: "Your recovery code" }), el("pre", { class: "box", text: code }),
      el("p", { text: note || "Write this code down now. It is shown once." }),
      el("button", { class: "primary", text: "I wrote it down", on: { click: () => { showingCode = false; c.remove(); rd(); } } })]);
    showingCode = true;
    box.prepend(c);
  }

  async function draw(sec, arg, live) {
    if (showingCode) return;
    const r = await P.api("GET", "/v1/vault/status");
    const t = P.tile({ icon: "lock", title: "Your vault", wide: true });
    const d = P.tile({ icon: "tune", title: "Do something", wide: true });
    const cards = el("div", { class: "actlist" });
    d.body.appendChild(cards);
    clear(sec).append(el("div", { class: "tiles" }, [t.node, d.node]));
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
    const v = L.vaultView(r.data);
    t.head.appendChild(el("span", { class: "stat", text: v.words.toUpperCase() }));
    t.body.append(P.kv([["State", v.words], ["Keys kept", v.items], ["Ways in", v.waysIn.join(", ")], ["Where", v.where], ["Idle lock", v.idle], ["Right now", v.lent],
      ["Inbox", v.inbox ? v.inbox + " sealed, filed at the next unlock" : "empty"], ["Why", v.why]]),
      el("p", { class: "small muted", text: "Archivist holds every key. Apps get a key for one use; no page, route or agent is ever shown one." }));

    if (!v.setUp) {
      cards.appendChild(form("Set up your vault", [["pin", "A PIN or passphrase"]], "Set up", async (b, said) => {
        const x = await P.api("POST", "/v1/vault/setup", { pin: b.pin });
        if (x.status !== 201) { said.textContent = P.said(x); return; }
        codeCard(sec, x.data.recovery_code, x.data.note);
        P.drawPrivate();
      }, "A long PIN or a passphrase. If it is too short or too easy to guess, the vault says why.", true));
      return;
    }
    if (v.open) cards.appendChild(act("Lock it now", "Apps that are running keep the keys they were lent until they stop.",
      [el("div", { class: "row" }, [el("button", { class: "primary", on: { click: async () => { await P.api("POST", "/v1/vault/lock", {}); P.drawPrivate(); rd(); } } }, [P.icon("lock"), "Lock"])])], true));
    else cards.appendChild(form("Unlock", [["secret", "Your PIN"]], "Unlock", async (b, said) => {
      const x = await P.api("POST", "/v1/vault/unlock", { method: "pin", secret: b.secret });
      if (x.status !== 200) { said.textContent = P.said(x); return; }
      P.drawPrivate(); rd();
    }, "", true));

    cards.appendChild(form("Change your PIN", [["pin", "Your PIN now"], ["new_pin", "The new PIN"]], "Change it", async (b, said) => {
      const x = await P.api("POST", "/v1/vault/pin", b);
      said.textContent = x.status === 200 ? "Your PIN is changed." : P.said(x);
    }));
    cards.appendChild(form("Make a new recovery code", [["pin", "Your PIN"]], "Make a new code", async (b, said) => {
      const x = await P.api("POST", "/v1/vault/recovery-code/rotate", b);
      if (x.status !== 200) { said.textContent = P.said(x); return; }
      codeCard(sec, x.data.recovery_code, x.data.note);
    }, "The old code stops working."));
    cards.appendChild(form("Test your recovery code", [["code", "Your recovery code", "text"]], "Test it", async (b, said) => {
      const x = await P.api("POST", "/v1/vault/recovery-code/test", b);
      said.textContent = x.status === 200 ? (x.data.ok ? "That code works." : "That code does not open the vault.") : P.said(x);
    }));
    cards.appendChild(form("Get back in with your recovery code", [["code", "Your recovery code", "text"], ["new_pin", "A new PIN"]], "Get back in", async (b, said) => {
      const x = await P.api("POST", "/v1/vault/recover", b);
      if (x.status !== 200) { said.textContent = P.said(x); return; }
      codeCard(sec, x.data.recovery_code, x.data.note);
    }, "For a forgotten PIN. You get a new recovery code too."));

    // Ways in
    const w = await P.api("GET", "/v1/vault/ways-in");
    const ways = el("div");
    ((w.status === 200 && w.data.ways_in) || []).forEach((way) => {
      const p = P.pin();
      const said = el("span", { class: "small" });
      ways.appendChild(el("div", { class: "row" }, [el("span", { class: "grow", text: way.label + " (" + way.kind + ")" }), p,
        el("button", { class: "danger", text: "Remove", on: { click: async () => {
          const x = await P.api("DELETE", "/v1/vault/ways-in/" + encodeURIComponent(way.id), { pin: p.value });
          p.value = "";
          if (x.status === 200) rd(); else said.textContent = P.said(x);
        } } }), said]));
    });
    const wayCount = ((w.status === 200 && w.data.ways_in) || []).length;
    cards.appendChild(act("Ways in", wayCount + (wayCount === 1 ? " way in" : " ways in"), [ways]));
    cards.appendChild(form("Add a passphrase as a way in", [["label", "A name for it", "text"], ["secret", "The new passphrase"], ["pin", "Your PIN now"]], "Add it",
      async (b, said) => {
        const x = await P.api("POST", "/v1/vault/ways-in", Object.assign({ kind: "passphrase" }, b));
        if (x.status === 201) rd(); else said.textContent = P.said(x);
      }));

    // The inbox: keys sealed while it was locked; a box that would replace a saved key waits for her PIN
    const inbox = el("div", {}, [el("p", { class: "small muted", text: v.inbox ? v.inbox + " sealed box(es) wait. They are filed the next time you unlock." : "Nothing waiting." })]);
    v.conflicts.forEach((name) => {
      const p = P.pin();
      const said = el("span", { class: "small" });
      inbox.appendChild(el("div", { class: "row" }, [el("span", { class: "grow", text: "A new " + name + " would replace the one saved." }), p,
        el("button", { text: "Replace it", on: { click: async () => {
          const x = await P.api("POST", "/v1/vault/inbox/file", { name, pin: p.value });
          p.value = "";
          if (x.status === 200) rd(); else said.textContent = P.said(x);
        } } }), said]));
    });
    cards.appendChild(act("Inbox", v.inbox ? v.inbox + " waiting" : "Nothing waiting", [inbox], v.conflicts.length > 0));
    cards.appendChild(act("Moving and backing up the vault", "Not built yet", [el("p", { class: "small muted", text: L.NOT_BUILT.vaultMove })]));
  }

  ["vault.locked", "vault.unlocked", "vault.stored"].forEach((t) => P.on(t, rd));
  P.page("vault", { title: "Vault", draw });
})();
