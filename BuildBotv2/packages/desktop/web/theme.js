/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/layout.tsx (adapted: the inline theme script)
 * Day or night before the first paint: day 07:00 to 19:00, night otherwise, unless she picked one on
 * this browser (Looks or Settings). Loaded after logic.js, in <head>.
 * Lane I10a: the bot's theme (Looks) sets its CSS variables on :root, on top of day or night. The last
 * one used is kept on this browser too, so it shows before the first paint. A "both" theme sets its
 * night tokens at night and its day tokens by day, again each time day and night switch (lane I14). */
(function () {
  "use strict";
  var KEY = "puffpai.theme", LOOK = "puffpai.themeLook";
  var set = [], look = null;
  function choice() { try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; } }
  function apply() {
    var part = window.PuffLogic.themeFor(new Date().getHours(), choice());
    document.documentElement.setAttribute("data-puff-theme", part);
    paint(part);
  }
  /* The look's tokens for *part* (day or night) on :root; checked again here (logic.themeTokens). */
  function paint(part) {
    var root = document.documentElement.style;
    set.forEach(function (k) { root.removeProperty(k); });
    var safe = window.PuffLogic.themeTokens({ tokens: window.PuffLogic.themeTokensFor(look, part) });
    set = Object.keys(safe);
    set.forEach(function (k) { root.setProperty(k, safe[k]); });
  }
  /* *theme*: {mode, tokens, dayTokens} (logic.themeList), or null for the Desktop's own palettes. */
  function setLook(theme, keep) {
    look = theme && typeof theme === "object"
      ? { mode: theme.mode, tokens: theme.tokens || {}, dayTokens: theme.dayTokens || {} } : null;
    paint(document.documentElement.getAttribute("data-puff-theme"));
    if (keep) { try { localStorage.setItem(LOOK, JSON.stringify(look)); } catch (e) { /* this visit only */ } }
  }
  /* *map*: {"--name": "value"}, set day and night. */
  function tokens(map, keep) { setLook({ mode: "night", tokens: map || {} }, keep); }
  window.PuffTheme = {
    choice: choice,
    set: function (c) { try { localStorage.setItem(KEY, c); } catch (e) { /* private window: this visit only */ } apply(); },
    apply: apply,
    tokens: tokens,
    look: setLook,
  };
  try { look = JSON.parse(localStorage.getItem(LOOK) || "null"); } catch (e) { look = null; /* none kept */ }
  apply();
  setInterval(apply, 60000);                   // 07:00 and 19:00 switch by themselves
})();
