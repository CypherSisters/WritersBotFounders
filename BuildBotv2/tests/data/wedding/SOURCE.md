A hand-made test fixture, written 2026-10-25 (lane I8a), from the owner's live test of a wedding planner bot.
Her bot's cached GitHub answers showed which projects came up for its three jobs (Vendor Tracker, Budget
Monitor, Theme Designer). No text from those projects is copied: every description and README here is our own
short words. The repo names, stars, languages, default branches, licenses and result order are facts kept as
GitHub showed them that day, for the repositories that came from GitHub's search.

- jobs.json: the three jobs as her bot's brain wrote them from her goal (names, what each does), and search
  phrases chosen so the awesome-selfhosted rows below match as they did live.
- search_vendor_tracking.json, search_budget_monitor.json: shaped like
  https://api.github.com/search/repositories?q=<words>+in:name,description,topics+stars:>=20+archived:false+fork:false&sort=stars&order=desc&per_page=20
  Cut to the results the finder reads past its first checks.
- search_theme_designer.json: one made-up color-theme app (the live run found its two candidates in the list only).
- awesome.fixture.md: rows shaped like the awesome-selfhosted list, for the six projects that came from it
  live (Swetrix, HitKeep, Apache Airflow, Kestra, Middleware, NeonLink). Our own one-line descriptions, each
  keeping the word that matched the search live (track, tracking, monitor, designed, design).
- readmes.json: "readmes": stand-in READMEs, one per project, keyed by "owner/repo". Each keeps only the words
  that let the project through the subject check live ("them", "across", "limit", "design", "color", "you",
  "vendor"), plus a Quick start the finder reads. Not the projects' READMEs. "run_files": which projects
  answer for a compose file or a Dockerfile (as the live candidates did). "repos": the repository facts for
  nonomu/Wedding-Planner, the address she pasted; its README had no run steps live. Its language and branch
  here are assumed for the test (its README says React), not read from GitHub.

Made up for the test, not on GitHub as written: example/wedding-vendors (an app that does the vendor job),
example/vendor-book (a plain vendor manager, no wedding words), example/envelope-budget (an envelope-budgeting
app), example/palette-board (a color-theme app), and their READMEs. search_theme_designer.json holds only
palette-board. blessyjoybj18/budget_tracker and erennyuksell/ag-multi-account-switchboard are real names from
the live answers with our own words; the first plays an app that fits the budget job, the second one that only
shares the word "budget". The brain check only orders: a no is tried after the others, never dropped.

These files are data for tests only; nothing in them is run.
