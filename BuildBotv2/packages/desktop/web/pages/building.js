/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/jobs/[id]/page.tsx (adapted: every Toolsmith
 * build and job, live, from GET /v1/toolsmith, builds/<id> and jobs/<id>/next; the Bus refreshes it). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  let poll = null;

  function pillClass(j) {
    if (j.active) return "state-pill live";
    if (L.jobGood(j)) return "state-pill good";
    return j.library ? "state-pill" : "state-pill stop";       // D123: no app yet, nothing stopped
  }

  function jobEl(j) {
    const said = el("div", { class: "small" });
    const parts = [el("div", { class: "head" }, [el("span", { class: "name", text: j.name }), el("span", { class: pillClass(j), text: j.words }),
      el("span", { class: "grow" }),
      j.canNext ? el("button", { on: { click: async () => {
        const r = await P.api("POST", "/v1/toolsmith/jobs/" + encodeURIComponent(j.id) + "/next", {});
        said.textContent = r.status === 200 ? (r.data && r.data.said) || "Toolsmith moves to the next app." : P.said(r);
      } } }, [P.icon("skip_next"), "Try next app"]) : null])];
    if (j.does) parts.push(el("p", { class: "muted small", text: j.does }));
    if (j.reuse) parts.push(el("p", { class: "small", text: "Uses " + j.reuse + ", already on your bot." }));
    if (j.trying) parts.push(el("p", { class: "small", text: "Trying now: " + j.trying }));
    const still = L.stillWorking(j, Date.now());
    if (j.now || still) parts.push(el("p", { class: "now", text: still || j.now }));
    if (j.line) parts.push(el("p", { text: j.line }));
    if (j.tries.length) parts.push(el("details", {}, [el("summary", { class: "small", text: "Apps it may try (" + j.tries.length + ")" }),
      el("ul", { class: "small" }, j.tries.map((t) => el("li", { text: t })))]));
    if (j.tried.length) parts.push(el("p", { class: "faint small", text: "Tried: " + j.tried.join(", ") }));
    if (j.lines.length) parts.push(el("details", {}, [el("summary", { class: "small", text: "What it said" }),
      el("ul", { class: "small" }, j.lines.map((t) => el("li", { text: t })))]));
    if (j.result) parts.push(el("details", {}, [el("summary", { class: "small", text: "The details" }),
      el("pre", { class: "box", text: JSON.stringify(j.result, null, 2) })]));
    parts.push(said);
    return el("div", { class: "job" }, parts);
  }

  /* Lane I18e: "Check again" for a build that stopped with unfinished jobs. The Core decides: it starts them again
   * under her Yes, or raises ONE new Yes card (shown here; her Yes there starts them, as a plan card's does). */
  function againEl(v) {
    const box = el("div", { class: "card note", "data-check-again": v.id });
    const said = el("p", { class: "small" });
    const cardBox = el("div");
    const unlockBox = el("div", { "data-unlock-for": v.id });
    const n = v.unfinished;
    box.append(el("div", { class: "row" }, [
      el("span", { class: "grow", text: n + (n === 1 ? " job" : " jobs") + " did not get a working app. Toolsmith can try "
                                         + (n === 1 ? "it" : "them") + " again. Jobs that work stay as they are." + L.againMore(v) }),
      el("button", { class: "primary", on: { click: async () => {
        if (!(await P.keysReady(v.id, unlockBox, said, null, null))) return;   // D68: a locked vault asks here
        said.textContent = "Asking Toolsmith to check again.";
        const r = await P.api("POST", "/v1/toolsmith/builds/" + encodeURIComponent(v.id) + "/again", {});
        if (r.status !== 200) { said.textContent = P.said(r, "Toolsmith could not check again."); return; }
        const asking = r.data && r.data.again && r.data.again.state === "asking" ? String(r.data.again.consent_id || "") : "";
        if (asking) {
          said.textContent = "Your Yes is needed again: " + String(r.data.again.why || "");
          clear(cardBox);
          P.consentInline(cardBox, asking, () => P.redraw("building"));
          return;
        }
        said.textContent = "Toolsmith is checking again.";
        P.redraw("building");
      } } }, [P.icon("refresh"), "Check again"])]), unlockBox, said, cardBox);
    if (v.againAsking) P.consentInline(cardBox, v.againAsking, () => P.redraw("building"));
    return box;
  }

  /* Lane I19b: the picture model this build uses and what it has read (never "made"), and its library fetch,
   * said plainly as BuildBot downloading into her library on this computer, with the way to open it. */
  function pictureEl(id, pic) {
    return el("div", { class: "card note", "data-picture-model": id }, [el("h3", { text: "Picture model" }),
      el("p", { text: pic.head }), el("p", { class: "small", text: pic.what }),
      pic.use ? el("p", { class: "small", text: pic.use }) : null, pic.now ? el("p", { class: "warn-t small", text: pic.now }) : null]);
  }

  function libraryEl(lib) {
    return el("div", { class: "card note", "data-library": lib.id }, [el("h3", { text: "Library" }),
      el("p", { text: lib.line }), lib.now ? el("p", { class: "now", text: lib.now }) : null,
      el("p", { class: "small", text: lib.note }), lib.end ? el("p", { class: "faint small", text: lib.end }) : null,
      el("div", { class: "row" }, [el("a", { href: lib.href, text: "Open your library" })])]);
  }

  /* D68 (lane I19a): a build that needs its key while the vault is locked asks for her PIN right here. */
  function unlockEl(v) {
    const said = el("p", { class: "small" });
    const box = P.pin("Your PIN");
    const open = async () => {
      const secret = box.value; box.value = "";
      if (!secret) { said.textContent = "Type your PIN first."; return; }
      const x = await P.api("POST", "/v1/vault/unlock", { method: "pin", secret });
      if (x.status !== 200) { said.textContent = P.said(x, "Your vault did not open."); return; }
      said.textContent = "Your vault is open. Toolsmith goes on.";
      P.redraw("building");
    };
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    return el("div", { class: "card note", "data-unlock-for": v.id }, [el("p", { text: L.UNLOCK_WAITING }),
      el("div", { class: "row" }, [box, el("button", { class: "primary", on: { click: open } }, [P.icon("lock_open"), "Unlock"])]), said]);
  }

  /* A PIN she is typing on this page is never wiped by the short poll or a Bus redraw. */
  function typingPin(sec) {
    return Array.from(sec.querySelectorAll('input[type="password"]')).some((i) => i.value || document.activeElement === i);
  }

  function buildEl(v, open, lib, pic) {
    const t = P.tile({ icon: v.active ? "progress_activity" : "inventory_2", title: L.buildTitle(v.goal, v.booksOnly), wide: true,
                       stat: (v.active ? "LIVE, " : "") + v.created.replace("T", " ").slice(0, 16) });
    if (v.now) t.body.appendChild(el("p", { class: "now", text: (v.planning && L.stillWords(v.now, v.nowAt, Date.now())) || v.now }));
    if (v.planning) t.body.appendChild(el("p", { class: "muted", text: L.planningWords(v) }));
    if (v.said) t.body.appendChild(el("p", { class: "warn-t", text: v.said }));
    if (v.active && v.yes && v.unlock) t.body.appendChild(unlockEl(v));
    if (v.planLines.length) t.body.appendChild(el("details", open || !v.yes ? { open: true } : {}, [el("summary", { text: v.yes ? "The plan you said Yes to" : "The plan" }),
      el("ul", {}, v.planLines.map((l) => el("li", { text: l })))]));
    if (v.consentId && !v.yes) { const c = el("div"); t.body.appendChild(c); P.consentInline(c, v.consentId, () => P.redraw("building")); }
    if (v.store.record) {
      t.body.appendChild(el("div", { class: "card note", "data-store-adds": v.id }, [el("h3", { text: "PuffPAI store apps first" })]
        .concat(v.store.lines.map((l) => el("p", { text: l })))));
      if (v.store.asking) { const c = el("div"); t.body.appendChild(c); P.consentInline(c, v.store.asking, () => P.redraw("building")); }
    }
    if (pic) t.body.appendChild(pictureEl(v.id, pic));
    if (lib) t.body.appendChild(libraryEl(lib));
    if (v.setup.length) t.body.appendChild(el("p", { class: "small", text: "Programs it needs first: " + v.setup.join(", ") + ". They are on Your turn." }));
    if (!v.jobs.length && !v.planning && !v.booksOnly) t.body.appendChild(el("p", { class: "empty", text: "No jobs in this build." }));
    if (v.canAgain) t.body.appendChild(againEl(v));
    v.jobs.forEach((j) => t.body.appendChild(jobEl(j)));
    if (v.knowledge.length) t.body.appendChild(el("details", {}, [el("summary", { class: "small", text: "Knowledge for this build (" + v.knowledge.length + "): Wikipedia and Gutenberg are read after your Yes" }),
      el("ul", { class: "small" }, v.knowledge.map((k) => el("li", { text: k.title + (k.source ? ", " + k.source : "") + ": " + k.status })))]));
    if (v.cost) t.body.appendChild(el("p", { class: "faint small", text: "Cloud cost so far: " + v.cost }));
    return t.node;
  }

  async function draw(sec, arg, redraw) {
    if (redraw && typingPin(sec)) { clearTimeout(poll); poll = setTimeout(() => P.redraw("building"), 4000); return; }
    const r = await P.api("GET", "/v1/toolsmith", undefined, L.POLL_WAIT_MS);
    const box = el("div", { class: "tiles" });
    if (r.status !== 200) {
      const retry = L.pollCanRetry(r.status);                // D113: never the end of the page, and what it showed stays
      if (redraw && retry && sec.childElementCount) {
        if (!sec.querySelector("[data-poll-trouble]")) sec.prepend(el("p", { class: "fail", "data-poll-trouble": "1", text: L.POLL_RETRY }));
      } else clear(sec).appendChild(el("p", { class: "fail", text: P.said(r, "Toolsmith did not answer.") + (retry ? " Trying again." : "") }));
      clearTimeout(poll);
      if (retry) poll = setTimeout(() => P.redraw("building"), 4000);
      return;
    }
    const now = L.buildingNow(r.data);
    const head = el("div", { class: "row" }, [el("span", { class: "muted", text: now.where + ". " + now.total + (now.total === 1 ? " build." : " builds.") }), el("span", { class: "grow" }),
      el("button", { class: "primary", on: { click: () => P.go("apps", "add") } }, [P.icon("add_circle"), "Ask for something new"])]);
    const stopped = now.live.length ? null : L.buildStopped(r.data);   // lane I18c: said plainly, first
    if (stopped) box.appendChild(el("p", { class: "warn-t", "data-build-stopped": stopped.id,
      text: stopped.here }));
    const reading = now.live.length ? null : L.buildReading(r.data);   // D123
    if (reading) box.appendChild(el("p", { "data-build-reading": reading.id, text: reading.head + " " + reading.next }));
    const builds = (r.data.builds || []).slice().sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 12);
    if (!builds.length) box.appendChild(el("p", { class: "empty", text: "Nothing built yet. Tell Toolsmith what you want from Apps, Add, or in chat." }));
    let live = false;
    const used = await P.api("GET", "/v1/models/used", undefined, L.POLL_WAIT_MS);   // what the picture model has read (lane I19b)
    for (const [i, b] of builds.entries()) {
      const full = await P.api("GET", "/v1/toolsmith/builds/" + encodeURIComponent(b.id), undefined, L.POLL_WAIT_MS);
      if (full.status !== 200) { live = live || L.pollCanRetry(full.status); continue; }   // D113: the poll goes on
      const v = L.buildView(full.data);
      live = live || v.active || !!(b.library && b.library.state === "fetching");
      box.appendChild(buildEl(v, i === 0, L.libraryLine(b.library, b.goal, b.id), L.pictureModel(full.data, used.status === 200 ? used.data : null)));
    }
    head.classList.add("lede");
    clear(sec).append(head, box);
    const want = arg && !redraw ? sec.querySelector('[data-check-again="' + String(arg).replace(/[^0-9a-f-]/g, "") + '"]') : null;
    if (want) want.scrollIntoView({ block: "center" });      // from Home's "stopped" line (lane I18e)
    clearTimeout(poll);
    if (live) poll = setTimeout(() => P.redraw("building"), 4000);      // its job lines are not on the Bus: a short poll while it runs
  }

  ["consent.granted", "consent.requested", "checklist.changed", "notify.posted", "wizard.progress"].forEach((t) => P.on(t, () => P.redraw("building")));
  P.page("building", { title: "Building", draw });
})();
