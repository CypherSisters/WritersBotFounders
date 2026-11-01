/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/chat/page.tsx and chat/ToolSteps.tsx (adapted:
 * one conversation on the full page and in the side panel; it streams from POST /v1/chat/stream, shows each
 * tool step and each Yes card in the conversation, and moves Puff's mouth while the answer streams). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  const log = el("div", { class: "chat-log", "aria-live": "polite" });
  const input = el("input", { placeholder: "Ask your bot anything", "aria-label": "Message", autocomplete: "off" });
  const send = el("button", { class: "primary", type: "submit" }, [P.icon("send"), "Send"]);
  const form = el("form", { class: "chat-form", autocomplete: "off" }, [input, send]);
  const node = el("div", { class: "chat" }, [log, form]);
  let busy = false;

  function scroll() { log.scrollTop = log.scrollHeight; }
  function me(text) { log.appendChild(el("div", { class: "msg me" }, [el("div", { class: "bubble", text })])); scroll(); }
  function bot(text) {
    const bubble = el("div", { class: "bubble", text: text || "" });
    const box = el("div", {}, [bubble]);
    log.appendChild(el("div", { class: "msg bot" }, [box]));
    scroll();
    return { bubble, box };
  }
  function empty() {
    if (log.childElementCount) return;
    log.appendChild(el("p", { class: "muted", text: "The Chat page and the side panel are one conversation." }));
  }

  async function ask(text) {
    if (busy) return;
    busy = true; send.disabled = true;
    me(text);
    const turn = new L.ChatTurn();
    const view = bot("");
    view.bubble.classList.add("typing");
    const steps = el("ul", { class: "steps" });
    const asks = el("div");
    const opens = el("div", { class: "row" });
    const passages = el("div");
    view.box.append(steps, asks, opens);
    const drawn = new Set();

    function paint() {
      view.bubble.textContent = turn.text || (turn.done ? "" : "Thinking");
      view.bubble.classList.toggle("typing", !turn.done);
      clear(steps);
      turn.steps.forEach((s) => steps.appendChild(el("li", {}, [P.icon(s.state === "done" ? "check_circle" : s.state === "failed" ? "error" : "progress_activity"),
        el("span", { text: (s.line || "Using " + s.tool) + " " + L.stepWords(s.state) })])));
      turn.asks.forEach((a) => {
        if (!a.id || drawn.has(a.id)) return;
        drawn.add(a.id);
        const box = el("div");
        asks.appendChild(box);
        P.consentInline(box, a.id);
      });
      clear(passages).append(...turn.passages.map((p) => P.passageCard(p)));     // D140
      clear(opens);
      turn.opens.forEach((u) => {
        const o = L.networkOpen(u, P.S.pages);     // D130: never a link to a phone's own 127.0.0.1
        opens.appendChild(o.url ? el("button", { on: { click: () => window.open(o.url, "_blank", "noopener") } }, [P.icon("open_in_new"), "Open " + o.url])
          : el("p", { class: "small muted", text: o.line }));
      });
      if (turn.done) {
        if (turn.error) view.bubble.textContent = turn.error;
        else if (!turn.text && turn.notice) view.bubble.textContent = turn.notice;
        if (turn.changed.length) steps.appendChild(el("li", {}, [P.icon("edit_note"), el("span", { text: "Changed: " + turn.changed.join(", ") })]));
      }
      scroll();
    }

    let r;
    try {
      r = await fetch("/v1/chat/stream", { method: "POST", credentials: "same-origin",
        headers: P.authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ text }) });   // D79
    } catch (e) { r = null; }
    if (!r || !r.ok || !r.body) {
      let data = null;
      try { data = r ? await r.json() : null; } catch (e) { data = null; }
      if (r && L.signedOut(r.status, data)) { P.signIn(); data = { error: L.signInWords(P.S.signin).line }; }   // D92
      turn.add({ type: "error", error: !r ? "The Core did not answer." : r.status === 501 ? "Chat is not built yet."
        : (data && data.error) || "The brain did not answer." });
      paint();
    } else {
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        let chunk;
        try { chunk = await reader.read(); } catch (e) { turn.add({ type: "error", error: "The answer stopped coming." }); break; }
        if (chunk.done) break;
        const got = L.ndjson(buf + dec.decode(chunk.value, { stream: true }));
        buf = got.rest;
        got.events.forEach((ev) => {
          turn.add(ev);
          if (ev.type === "token") P.talk(true, String(ev.text || "").length);
        });
        paint();
      }
      if (buf.trim()) L.ndjson(buf + "\n").events.forEach((ev) => turn.add(ev));
      if (!turn.done) turn.add({ type: "done", text: turn.text });
      paint();
    }
    P.talk(false, 0);
    busy = false; send.disabled = false;
    input.focus();
  }

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    const intro = log.querySelector("p.muted");
    if (intro && log.childElementCount === 1) intro.remove();
    ask(text);
  });

  /* The side panel: the same conversation, docked at the right. */
  const panel = document.getElementById("chatpanel");
  function dock() {
    if (!panel.hidden) { document.getElementById("chat-dock").appendChild(node); empty(); }
  }
  document.getElementById("chat-btn").addEventListener("click", () => {
    if (P.S.view === "chat") return;
    panel.hidden = !panel.hidden;
    dock();
    if (!panel.hidden) input.focus();
  });
  document.getElementById("chat-close").addEventListener("click", () => { panel.hidden = true; });
  document.getElementById("chat-full").addEventListener("click", () => { panel.hidden = true; P.go("chat"); });

  P.page("chat", {
    title: "Chat",
    draw(sec) {
      panel.hidden = true;
      if (!sec.firstChild) sec.appendChild(el("div", { class: "tile flat page-chat" }, [el("div", { class: "chat" })]));
      const box = sec.querySelector(".page-chat .chat");
      clear(box).append(log, form);
      empty();
      input.focus();
      scroll();
    },
    dock() { node.append(log, form); dock(); },
    say(text) { bot(text); },
  });
})();
