/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/agents/page.tsx (adapted: v2's seven agents from
 * docs/09_AGENT_SECURITY.md section 1, with live lines from the routes that have them: Caretaker's health on the
 * Registry, Toolsmith's builds, Drawbridge's latest checks in the audit log, the vault, the brain). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  const ICONS = { archivist: "lock", concierge: "chat_bubble", steward: "play_circle", caretaker: "monitor_heart",
                  majordomo: "checklist", drawbridge: "fort", toolsmith: "construction" };

  async function draw(sec) {
    const [rows, vault, model, steps, tool, audit, bridge] = await Promise.all([P.loadRegistry(), P.api("GET", "/v1/vault/status"),
      P.api("GET", "/v1/model"), P.api("GET", "/v1/checklist"), P.api("GET", "/v1/toolsmith"), P.api("GET", "/v1/audit?limit=200"),
      P.api("GET", "/v1/bridge")]);
    const live = {
      running: L.runningSummary(rows),
      vault: vault.status === 200 ? vault.data : null,
      brain: model.status === 200 ? L.brainView(model.data) : null,
      steps: steps.status === 200 && Array.isArray(steps.data) ? steps.data.length : undefined,
      building: tool.status === 200 ? L.buildingNow(tool.data) : null,
      checks: audit.status === 200 ? L.drawbridgeChecks(audit.data.lines, 5) : null,
      safe: bridge.status === 200 && bridge.data.safe_mode ? bridge.data.safe_mode.reasons : [],
    };
    const list = L.agentRows(live).map((a) => el("div", { class: "agent-row", "data-agent": a.id }, [P.icon(ICONS[a.id]),
      el("div", {}, [el("div", { class: "name", text: a.name }), el("div", { class: "where", text: a.zone.split(", ").pop() })]),
      el("div", {}, [el("div", { text: a.does }), a.now ? el("p", { class: "now", text: a.now }) : null,
        a.id === "drawbridge" && live.checks && live.checks.length > 1
          ? el("ul", { class: "small" }, live.checks.slice(1).map((c) => el("li", { text: c.text }))) : null])]));
    clear(sec).append(el("p", { class: "lede", text: "Only Toolsmith may use a cloud model, and only if you pick one." }),
      el("section", { class: "tile" }, list));
  }

  P.page("agents", { title: "Agent Team", draw });
})();
