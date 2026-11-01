/* Lane I18g: the one GitHub block, shown on Developer and on Apps > Add. Link your GitHub (a read-only token kept
 * in the vault as toolsmith.github) and add code from a GitHub project for the build.
 * Words and flow harvested from PuffPAIBuildBot@774e27a4:dashboard/app/_components/GitHub/GitHubConnect.tsx and
 * dashboard/developer/_components/GitHubTile.tsx (adapted to v2's routes: GET/PUT/DELETE /v1/github/key,
 * POST /v1/github/code, GET /v1/toolsmith).
 *
 * THE KEY. It is read from the field once when she presses Link, sent once, and the field is cleared the moment
 * it is sent. It is never written to localStorage, sessionStorage, a cookie, the address bar or the console, and
 * no Core reply carries it back.
 *
 * Not here: "Connected as <login>" and a list of her own projects. Both need a call to api.github.com outside a
 * build, on paths the Core never sends her key to; v2 has no such path, so none is made here.
 *
 * The logic part (no DOM) runs under node too (tests/js/i18g.test.js); block() draws with window.Puff. */
(function (root) {
  "use strict";

  const TOKEN_PAGE = "https://github.com/settings/personal-access-tokens/new";
  const TOKEN_STEPS = [
    "Open the link and sign in to GitHub. A free account is enough.",
    "Give the token a name, pick an end date, and choose read-only access.",
    "Under Repository access, pick Public repositories. BuildBot does not use private projects.",
    "Press Generate token, copy it, and paste it here.",
  ];
  const WHAT_LINE = "Link your GitHub so Toolsmith's searches on GitHub go further. Or add code from a GitHub project for your build.";
  const SENT_LINE = "Your key stays in your vault. Toolsmith adds it only to its own GitHub searches and to reading your project list, and it goes to api.github.com only.";
  const KEPT_LINE = "Kept in your vault, never in this browser.";
  const UNLINK_ASK = "Unlink GitHub? Toolsmith goes back to searching GitHub as a visitor.";
  const CODE_LINE = "Paste the address of a GitHub project. Toolsmith reads it, tries it in a folder of its own, and shows you one plain plan before anything is installed.";
  const UNFINISHED = ["held", "not_yet", "nothing_found"];

  /* The link state from GET /v1/github/key ({status, data}). */
  function keyView(r) {
    const d = r && r.status === 200 && r.data && typeof r.data === "object" ? r.data : null;
    if (!d) return { state: "error", title: "Could not be read", line: "", form: false, unlink: false, setup: false };
    const line = String(d.line || "");
    if (d.linked === true) return { state: "linked", title: "Linked", line: line, form: false, unlink: true, setup: false };
    if (d.linked === false) return { state: "not_linked", title: "Not linked", line: line, form: true, unlink: false, setup: false };
    if (d.vault === "not_set_up") return { state: "no_vault", title: "Not linked", line: line, form: false, unlink: false, setup: true };
    return { state: "cant_tell", title: "Can't tell while your vault is locked", line: line, form: true, unlink: false, setup: false };
  }

  /* A pasted key, checked the way the Core checks it (github_key_ok): a token's characters only, 20 to 255 of
   * them. The text itself is never put in the line. */
  const SHAPE = /^[A-Za-z0-9\-._~+/]{20,255}=*$/;
  function keyShape(text) {
    const t = String(text || "").trim();
    if (!t) return { ok: false, line: "Paste the token from GitHub first." };
    if (t.length > 255 || !SHAPE.test(t)) return { ok: false, line: "That does not look like a GitHub token. Copy the whole token from GitHub and paste it on one line." };
    return { ok: true, line: "" };
  }

  /* A pasted GitHub address: one line, at most 500 characters, naming github.com. The Core reads its form. */
  function addressCheck(text) {
    const t = String(text || "").trim();
    if (!t) return { ok: false, address: "", line: "" };
    if (t.length > 500 || /[\r\n]/.test(t) || !/github\.com/i.test(t)) {
      return { ok: false, address: "", line: "Paste a GitHub project address, like https://github.com/owner/project." };
    }
    return { ok: true, address: t, line: "" };
  }

  /* The jobs of the last build (GET /v1/toolsmith) that are held or found nothing: each may take the address. */
  function heldJobs(status) {
    const builds = (status && Array.isArray(status.builds) ? status.builds : []).filter((b) => b && b.id)
      .slice().sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    const last = builds[0];
    if (!last) return [];
    return (Array.isArray(last.jobs) ? last.jobs : []).filter((j) => j && j.id && UNFINISHED.indexOf(String(j.status)) >= 0)
      .map((j) => ({ id: String(j.id), name: String(j.name || "this job"), build: String(last.id), goal: String(last.goal || "") }));
  }
  function useLabel(job) { return "Use it for: " + job.name; }

  /* ---- the block (DOM, with window.Puff) ---- */
  function block(opts) {
    const P = root.Puff, el = P.el, clear = P.clear;
    opts = opts || {};
    const t = P.tile({ id: "github", icon: "code", title: "GitHub", wide: true });
    t.node.setAttribute("data-github", opts.where || "");
    const linkBox = el("div", { "data-gh": "link" });
    const codeBox = el("div", { "data-gh": "code" });
    t.body.append(el("p", { class: "small muted", text: WHAT_LINE }),
      el("h3", { text: "Link your GitHub" }), linkBox,
      el("h3", { text: "Add code from GitHub" }), codeBox);

    async function drawLink(note) {
      const r = await P.api("GET", "/v1/github/key");
      clear(linkBox);
      if (P.missing(r)) { linkBox.appendChild(P.later("the GitHub key route (GET /v1/github/key, lane I18g)")); return; }
      const v = keyView(r);
      const said = el("p", { class: "small", "data-gh": "said", text: note || "" });
      linkBox.append(el("p", { class: "now", "data-gh-state": v.state }, [el("b", { text: v.title + ". " }), String(v.line || (v.state === "error" ? P.said(r) : "")).replace(v.title + ". ", "")]));
      if (v.setup) linkBox.appendChild(el("div", { class: "row" }, [el("a", { href: "#developer/vault", text: "Set up the vault" })]));
      if (v.unlink) {
        const ask = el("div", { class: "row" });
        const unlink = el("button", { "data-gh": "unlink" }, [P.icon("link_off"), "Unlink"]);
        unlink.addEventListener("click", () => {
          clear(ask).append(el("span", { class: "small grow", text: UNLINK_ASK }),
            el("button", { class: "danger", "data-gh": "unlink-yes", on: { click: async () => {
              const x = await P.api("DELETE", "/v1/github/key");
              drawLink(x.status === 200 ? (x.data && x.data.line) || "Unlinked." : P.said(x));
            } } }, ["Yes, unlink"]),
            el("button", { class: "link", on: { click: () => drawLink() } }, ["Keep it"]));
        });
        ask.appendChild(unlink);
        linkBox.append(el("p", { class: "small muted", text: SENT_LINE }), ask);
      }
      if (v.form) {
        const field = el("input", { type: "password", "aria-label": "GitHub token", placeholder: "Paste the token", autocomplete: "off",
                                    spellcheck: "false", maxlength: "300" });
        const go = el("button", { class: "primary", type: "submit" }, [P.icon("link"), "Link"]);
        const form = el("form", { autocomplete: "off" }, [
          el("div", { class: "row" }, [el("a", { href: TOKEN_PAGE, target: "_blank", rel: "noopener noreferrer", text: "Make a read-only token on GitHub" })]),
          el("ol", { class: "small" }, TOKEN_STEPS.map((s) => el("li", { text: s }))),
          el("div", { class: "row" }, [field, go]),
          el("p", { class: "faint small", text: KEPT_LINE + " " + SENT_LINE })]);
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const shape = keyShape(field.value);
          if (!shape.ok) { field.value = ""; said.textContent = shape.line; return; }
          let value = field.value.trim();
          field.value = "";                             // cleared the moment it is sent
          go.disabled = true;
          const x = await P.api("PUT", "/v1/github/key", { value: value });
          value = "";
          go.disabled = false;
          if (x.status === 200 || x.status === 202) { drawLink((x.data && x.data.line) || "Linked."); return; }
          said.textContent = P.said(x, "That was not saved.");
        });
        linkBox.appendChild(form);
      }
      linkBox.appendChild(said);
    }

    async function drawCode() {
      clear(codeBox);
      const field = el("input", { type: "text", "aria-label": "GitHub project address", placeholder: "https://github.com/owner/project",
                                  autocomplete: "off", spellcheck: "false", maxlength: "500" });
      const said = el("div", { "data-gh": "code-said" });
      const offers = el("div", { class: "row", "data-gh": "for-job" });
      const asNew = el("button", { class: "primary", type: "submit" }, [P.icon("add_circle"), "Add as a new app"]);
      const form = el("form", { autocomplete: "off" }, [el("p", { class: "small muted", text: CODE_LINE }), el("div", { class: "row" }, [field, asNew]), offers]);
      async function send(job) {
        const a = addressCheck(field.value);
        clear(said);
        if (!a.ok) { if (a.line) said.appendChild(el("p", { class: "fail", text: a.line })); return; }
        form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        said.appendChild(el("p", { class: "now", text: job ? "Toolsmith is planning " + job.name + " with that project. This can take a few minutes; Building shows it live."
          : "Toolsmith is reading that project and planning. This can take a few minutes; Building shows it live." }));
        const body = job ? { address: a.address, job_id: job.id } : { address: a.address };
        const r = await P.api("POST", "/v1/github/code", body);
        form.querySelectorAll("button").forEach((b) => { b.disabled = false; });
        clear(said);
        if (P.missing(r)) { said.appendChild(P.later("the add code from GitHub route (POST /v1/github/code, lane I18g)")); return; }
        if (r.status !== 201) { said.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
        if (P.planCard) P.planCard(said, P.L.buildView(r.data));
        else said.appendChild(el("p", { text: "The plan is on Building, with its Yes card on Your turn." }));
      }
      form.addEventListener("submit", (ev) => { ev.preventDefault(); send(null); });
      codeBox.append(form, said);                       // the plan's Yes card sits outside the form
      const st = await P.api("GET", "/v1/toolsmith");
      const jobs = st.status === 200 ? heldJobs(st.data) : [];
      if (jobs.length) {
        offers.appendChild(el("span", { class: "small muted", text: "Or use it for a job of your last build that found no working app:" }));
        jobs.forEach((j) => offers.appendChild(el("button", { type: "button", "data-gh-job": j.id, on: { click: () => send(j) } }, [P.icon("build"), useLabel(j)])));
      }
    }

    drawLink();
    drawCode();
    return t.node;
  }

  const api = { TOKEN_PAGE: TOKEN_PAGE, TOKEN_STEPS: TOKEN_STEPS, SENT_LINE: SENT_LINE, KEPT_LINE: KEPT_LINE, UNLINK_ASK: UNLINK_ASK,
                keyView: keyView, keyShape: keyShape, addressCheck: addressCheck, heldJobs: heldJobs, useLabel: useLabel, block: block };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffGitHub = api;
})(typeof window !== "undefined" ? window : this);
