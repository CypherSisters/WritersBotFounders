/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/outside/page.tsx (adapted: the Bridge's outside
 * log, GET /v1/bridge/ledger, in plain words: who, which host, allowed or refused, and why). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  let only = "all";

  async function draw(sec) {
    const [b, led] = await Promise.all([P.api("GET", "/v1/bridge"), P.api("GET", "/v1/bridge/ledger?limit=500"), P.loadRegistry()]);
    const head = P.tile({ icon: "public", title: "The Bridge", wide: true });
    if (b.status === 200) {
      head.head.appendChild(el("span", { class: "stat", text: b.data.state === "open" ? "OPEN" : "CLOSED" }));
      head.body.append(el("p", { text: L.bridgeView(b.data).text }));
      const btn = await P.bridgeButton(b.data, () => { P.redraw("outside"); P.drawChips(); P.drawPrivate(); });
      if (btn) head.body.appendChild(el("div", { class: "row" }, [btn]));
    } else head.body.appendChild(el("p", { class: "fail", text: P.said(b) }));

    const t = P.tile({ icon: "receipt_long", title: "What left your computer", wide: true });
    if (led.status !== 200 || !Array.isArray(led.data)) t.body.appendChild(el("p", { class: "fail", text: P.said(led) }));
    else {
      const all = led.data.map((x) => L.ledgerLine(x, P.names())).reverse();
      const shown = all.filter((x) => only === "all" || (only === "allowed" ? x.allowed === true : x.allowed === false));
      t.head.appendChild(el("span", { class: "stat", text: all.filter((x) => x.allowed === false).length + " REFUSED" }));
      t.body.appendChild(el("div", { class: "tabs" }, [["all", "Everything"], ["allowed", "Allowed"], ["refused", "Refused"]].map(([id, label]) =>
        el("button", { class: only === id ? "on" : "", on: { click: () => { only = id; draw(sec); } } }, [label]))));
      if (!shown.length) t.body.appendChild(el("p", { class: "empty", text: all.length ? "Nothing here with that filter." : "Nothing has tried to leave your computer yet." }));
      t.body.appendChild(el("ul", { class: "lines" }, shown.slice(0, 300).map((x) => el("li", {}, [
        el("span", { class: "dot " + (x.allowed === true ? "ok" : x.allowed === false ? "bad" : "") }),
        el("span", { class: "when", text: x.when.replace("T", " ").slice(0, 19) }), el("span", { text: x.text })]))));
    }
    clear(sec).appendChild(el("div", { class: "tiles" }, [head.node, t.node]));
  }

  P.on("egress.observed", () => P.redraw("outside"));
  P.on("bridge.posture", () => P.redraw("outside"));
  P.page("outside", { title: "The Bridge", draw });
})();
