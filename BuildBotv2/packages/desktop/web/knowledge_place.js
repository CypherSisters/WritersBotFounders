/* Where her books and downloads are kept, and moving them (D103, GET and POST /v1/knowledge/place): one tile, shown
 * on the Knowledge page and on Developer's Zone 1. It redraws itself while a move runs. */
(function () {
  "use strict";
  const P = window.Puff, D = window.PuffDev, el = P.el, clear = P.clear;

  P.knowledgePlaceTile = function () {
    const t = P.tile({ id: "knowledge-place", icon: "hard_drive", title: "Your books and downloads", wide: true });
    let left = false;                 // she was told the old drive is gone: the next try may leave them there

    async function act(said, path, body) {
      said.textContent = "";
      const x = await P.api("POST", path, body || {});
      if (x.status !== 200 && x.status !== 202) {
        said.textContent = P.said(x);
        left = !!(x.data && x.data.code === "source_away");
        if (left) said.textContent += " Press Move again to use the new place and leave them there.";
        return;
      }
      left = false;
      fill();
    }

    async function fill() {
      const r = await P.api("GET", "/v1/knowledge/place");
      if (P.missing(r)) { clear(t.body).appendChild(P.later("where your books and downloads are kept (GET /v1/knowledge/place)")); return; }
      if (r.status !== 200) { clear(t.body).appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      const v = D.placeView(r.data);
      const said = el("p", { class: "small", "data-said": "knowledge-place" });
      clear(t.body).append(P.kv([["Kept in", v.where], ["Room", v.free]]),
        v.line && !v.moving ? el("p", { class: "fail", "data-place": "line", text: v.line }) : "",
        v.warning ? el("p", { class: "small", text: v.warning }) : "");
      if (v.moving) {
        t.body.append(el("progress", { max: "100", value: String(v.percent), "aria-label": "How far the move is" }),
          el("p", { class: "small", "data-place": "progress", text: v.percent + "%: " + v.progress }),
          v.moveLine ? el("p", { class: "small", "data-place": "move-line", text: v.moveLine }) : "");
        const row = el("div", { class: "row" });
        if (v.canResume) row.appendChild(el("button", { class: "primary", on: { click: () => act(said, "/v1/knowledge/place/resume") } }, ["Pick it up"]));
        if (v.canCancel) row.appendChild(el("button", { on: { click: () => act(said, "/v1/knowledge/place/cancel") } }, ["Cancel the move"]));
        t.body.append(row, said);
        if (v.running) setTimeout(() => { if (t.node.isConnected) fill(); }, 1500);
        return;
      }
      if (v.moveLine) t.body.appendChild(el("p", { class: "small muted", "data-place": "move-line", text: v.moveLine }));
      const path = el("input", { type: "text", placeholder: "E:\\PuffPAI Books", autocomplete: "off", spellcheck: "false", "aria-label": "A folder or drive for your books and downloads" });
      const move = el("button", { on: { click: () => {
        const p = D.folderPath(path.value);
        if (!p.ok) { said.textContent = p.line; return; }
        act(said, "/v1/knowledge/place", { path: p.path, leave_old: left });
      } } }, [P.icon("drive_file_move"), "Move them here"]);
      t.body.append(el("div", { class: "row" }, [path, move]));
      if (!v.isDefault) t.body.appendChild(el("div", { class: "row" }, [el("button", { class: "link", text: "Back to your data folder",
        on: { click: () => act(said, "/v1/knowledge/place", { path: "", leave_old: left }) } })]));
      t.body.append(said, el("p", { class: "faint small", text: "Any folder or drive on this computer. What is there moves too, with progress, and a stop loses nothing. In a terminal: puffpai knowledge move <folder>" }));
    }

    fill();
    return t.node;
  };
})();
