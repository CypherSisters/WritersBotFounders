// Lane I17: the Developer page's Models section and a model offer ticked on the plan card, as pure logic.
// Called from logic.test.js with the logic module.
const assert = require("assert");

module.exports = function (L) {
  const data = {
    installed: [{ name: "qwen3.5:9b", size_gb: 6.6, used_for: ["chat"] }, { name: "qwen2.5-coder:7b", size_gb: 4.7, used_for: [] }],
    roles: { chat: "qwen3.5:9b", code: "", vision: "", fast: "" },
    catalog: [
      { id: "qwen3.5:9b", label: "Qwen3.5 9B", roles: ["chat"], strength: "Tool model.", min_ram_gb: 10, rec_ram_gb: 16,
        installed: true, fit: "comfortable", fits: true, download: "Already downloaded" },
      { id: "qwen2.5-coder:7b", label: "Qwen2.5 Coder 7B", roles: ["code"], strength: "Small coder.", min_ram_gb: 8, rec_ram_gb: 16,
        installed: true, fit: "comfortable", fits: true, download: "Already downloaded" },
      { id: "qwen2.5-coder:14b", label: "Qwen2.5 Coder 14B", roles: ["code"], strength: "Bigger coder.", min_ram_gb: 12, rec_ram_gb: 24,
        installed: false, fit: "tight", fits: true, download: "9 GB download" },
      { id: "qwen2.5vl:7b", label: "Qwen2.5 VL 7B", roles: ["vision"], strength: "Reads pictures.", min_ram_gb: 10, rec_ram_gb: 16,
        installed: false, fit: "comfortable", fits: true, download: "6 GB download" },
    ],
    recommended: { chat: ["qwen3.5:9b"], code: ["qwen2.5-coder:7b", "qwen2.5-coder:14b"], vision: ["qwen2.5vl:7b"], fast: [] },
    pulls: { "qwen2.5vl:7b": { state: "downloading", line: "Downloading Qwen2.5 VL 7B.", done_gb: 2, size_gb: 6 } },
    bridge: "Ollama downloads models itself.",
  };
  const v = L.modelsView(data);
  assert.deepStrictEqual(v.installed, [{ name: "qwen3.5:9b", size: "6.6 GB", usedFor: "Chat" },
                                       { name: "qwen2.5-coder:7b", size: "4.7 GB", usedFor: "Not used yet" }]);
  assert.deepStrictEqual(v.sections.map((s) => s.role), ["chat", "code", "vision", "fast"]);
  const chat = v.sections[0], code = v.sections[1], vision = v.sections[2], fast = v.sections[3];
  assert.strictEqual(chat.now, "Now: qwen3.5:9b");
  assert.strictEqual(chat.rows[0].using, true);
  assert.strictEqual(code.now, "Now: the chat model");
  assert.deepStrictEqual(code.rows.map((r) => [r.id, r.canAdd, r.canUse]), [["qwen2.5-coder:7b", false, true], ["qwen2.5-coder:14b", true, false]]);
  assert.strictEqual(code.rows[1].facts, "Needs 12 GB of RAM, best with 24 GB. 9 GB download. A tight fit here, so it may be slow.");
  assert.strictEqual(vision.rows[0].canAdd, false);                 // being downloaded: no second Add
  assert.strictEqual(vision.rows[0].said, "Downloading Qwen2.5 VL 7B. (2 of 6 GB)");
  assert.strictEqual(fast.none, "Nothing we list fits this computer for this yet.");
  assert.strictEqual(L.modelsView({}).sections[0].now, "Now: none picked yet");

  // a model offer on the plan card: ticked as "model:<role>" in the same use list
  const card = { id: "c_1", card: { covers: { build: "b1", card: {
    model_offers: [{ value: "model:code", role: "code", model: "qwen2.5-coder:7b", label: "Qwen2.5 Coder 7B",
                     line: "Add a coding model: Qwen2.5 Coder 7B, 5 GB download, fits your computer. Tick to add it." }] } } } };
  const t = L.cardTicks(card);
  assert.strictEqual(t.kind, "offers");
  assert.strictEqual(t.note, L.MODEL_TICK_NOTE);
  assert.deepStrictEqual(t.items.map((i) => [i.value, i.model]), [["model:code", true]]);
  assert.deepStrictEqual(t.body(["model:code"]), { consent_id: "c_1", use: ["model:code"] });
  const both = L.cardTicks({ id: "c_2", card: { covers: { build: "b1", card: { offers: [{ job: 0, line: "Job: use it." }],
    model_offers: card.card.covers.card.model_offers } } } });
  assert.deepStrictEqual(both.items.map((i) => i.value), [0, "model:code"]);
  assert.strictEqual(both.note, L.OFFER_NOTE + " " + L.MODEL_TICK_NOTE);
  assert.strictEqual(L.cardTicks({ id: "c_3", card: { covers: { build: "b1", card: { model_offers: [{ value: "bad" }] } } } }), null);
};
