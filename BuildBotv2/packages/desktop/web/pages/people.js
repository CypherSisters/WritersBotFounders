/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/profile/_components/ProfileForm.tsx (adapted: the
 * household's people, one shared list, through GET and PUT /v1/people, D12). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;

  async function draw(sec) {
    const r = await P.api("GET", "/v1/people");
    const t = P.tile({ icon: "group", title: "Who lives here", wide: true });
    clear(sec).appendChild(el("div", { class: "tiles" }, [t.node]));
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
    const people = (r.data || []).map((p) => Object.assign({}, p));
    const said = el("p", { class: "small" });
    const list = el("div");

    function row(p, i) {
      const name = el("input", { value: p.name, "aria-label": "Name", maxlength: "60" });
      const emoji = el("input", { value: p.emoji || "", "aria-label": "Emoji", maxlength: "8", size: "4" });
      const role = el("select", { "aria-label": "Adult or child" }, [el("option", { value: "adult", text: "Adult" }), el("option", { value: "child", text: "Child" })]);
      role.value = p.role || "adult";
      name.addEventListener("input", () => { p.name = name.value; });
      emoji.addEventListener("input", () => { p.emoji = emoji.value; });
      role.addEventListener("change", () => { p.role = role.value; });
      return el("div", { class: "app-row" }, [el("div", { class: "icon" }, [p.emoji ? el("span", { text: p.emoji }) : P.icon("person")]),
        el("div", { class: "row" }, [name, emoji, role]),
        el("div", { class: "acts" }, [el("button", { class: "danger", on: { click: () => { people.splice(i, 1); paint(); } } }, [P.icon("person_remove"), "Remove"])])]);
    }
    function paint() {
      clear(list);
      if (!people.length) list.appendChild(el("p", { class: "empty", text: "No one yet." }));
      people.forEach((p, i) => list.appendChild(row(p, i)));
    }
    const add = el("button", { on: { click: () => {
      people.push({ id: L.personId("person", people.map((p) => p.id)), name: "", emoji: "", role: "adult" });
      paint();
    } } }, [P.icon("person_add"), "Add someone"]);
    const save = el("button", { class: "primary", on: { click: async () => {
      people.forEach((p) => { if (/^person(-\d+)?$/.test(p.id) && p.name.trim()) p.id = L.personId(p.name, people.filter((x) => x !== p).map((x) => x.id)); });
      const s = await P.api("PUT", "/v1/people", people);
      said.textContent = s.status === 200 ? "Saved." : P.said(s);
      if (s.status === 200) { people.splice(0, people.length, ...s.data); paint(); }
    } } }, [P.icon("save"), "Save"]);
    t.body.append(el("p", { class: "small muted", text: "Every app reads this one list." }), list,
      el("div", { class: "row" }, [add, save, said]));
    paint();
  }

  P.page("people", { title: "People", draw });
})();
