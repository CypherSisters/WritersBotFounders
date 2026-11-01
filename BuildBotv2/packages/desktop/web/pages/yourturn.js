/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/page.tsx (adapted: its consent queue and
 * announcements, as one full page). Your turn: every pending Yes card with its ticks, the whole Checklist
 * (Copy, Done, Skip, Run as administrator), and notices. Each card is wide enough to read in full. */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;

  async function draw(sec) {
    const [cons, steps, notes, tool] = await Promise.all([P.pendingConsents(), P.api("GET", "/v1/checklist"), P.api("GET", "/v1/notify"),
      P.api("GET", "/v1/toolsmith")]);
    const parts = [];
    const halted = tool.status === 200 && !L.buildingNow(tool.data).live.length ? L.buildStopped(tool.data) : null;
    if (halted) parts.push(el("div", { class: "card note high", "data-build-stopped": halted.id }, [el("h3", { text: "Your last build stopped" }),
      el("p", { text: halted.head }), el("div", { class: "row" }, [el("a", { href: "#building", text: "Open Building to see why" })])]));

    if (cons.cards.length) {
      parts.push(el("h2", { class: "micro", text: "Waiting for your Yes" }));
      const yes = el("div", { class: "cards" });
      cons.cards.forEach((c) => yes.appendChild(P.consentEl(c, () => P.redraw("yourturn"))));
      parts.push(yes);
    }
    const list = steps.status === 200 && Array.isArray(steps.data) ? steps.data : [];
    if (steps.status !== 200) parts.push(el("p", { class: "fail", text: P.said(steps) }));
    if (list.length) {
      parts.push(el("h2", { class: "micro", text: "Things to do" }));
      const todo = el("div", { class: "cards" });
      list.forEach((s) => todo.appendChild(P.stepCardEl(L.stepCard(s), () => P.redraw("yourturn"))));
      parts.push(todo);
    }
    if (steps.status === 200 && !list.length && !cons.cards.length)
      parts.push(el("p", { class: "empty", text: halted ? "Nothing else for you to do right now." : "Nothing for you to do right now." }));

    if (notes.status === 200 && Array.isArray(notes.data) && notes.data.length) {
      parts.push(el("h2", { class: "micro", text: "Notices" }));
      const nb = el("div", { class: "cards" });
      notes.data.slice(0, 20).forEach((n) => {
        const x = el("button", { class: "link", text: "Dismiss", on: { click: async () => { await P.api("DELETE", "/v1/notify/" + encodeURIComponent(n.id)); P.redraw("yourturn"); } } });
        nb.appendChild(el("div", { class: "card note " + (n.urgency || "") }, [el("h3", { text: n.title }), n.text ? el("p", { text: n.text }) : null,
          el("div", { class: "from" }, ["From " + n.package + " ", x])]));
      });
      parts.push(nb);
    }
    clear(sec).append(...parts);
    P.refreshCounts();
  }

  ["checklist.changed", "consent.requested", "consent.granted", "consent.revoked", "notify.posted"].forEach((t) => P.on(t, () => P.redraw("yourturn")));
  P.page("yourturn", { title: "Your turn", draw });
})();
