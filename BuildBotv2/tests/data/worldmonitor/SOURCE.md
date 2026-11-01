A hand-made test fixture, written 2026-10-23. It is modeled on the shape of GitHub's public API answers
for koala73/worldmonitor (AGPL-3.0-only) and for the repositories named in the search results. No text
from those projects is copied: every description and README.fixture.md are our own short words. The repo
names, stars, languages, topics, licenses and result order are facts kept as GitHub showed them that day.

- repo.json: shaped like https://api.github.com/repos/koala73/worldmonitor, only the fields Toolsmith's
  finder reads.
- README.fixture.md: a stand-in README with the words the finder's subject check needs and a Quick start
  (npm install, npm run dev, port 3000). Not the project's README.
- search_<words>.json: shaped like https://api.github.com/search/repositories?q=<words>+in:name,description,topics+stars:>=20+archived:false+fork:false&sort=stars&order=desc&per_page=20

GitHub's search needs every word to match, so "world news map" and "news map" do not return
worldmonitor (its description and topics never say map); "world news" returns it first.
Each description keeps the same finder words (world, news, map, events, happening) as the one GitHub
showed. Descriptions are short, except for the four results whose real descriptions were over 1000
characters (the three china-dictatorship repos and bermufine/dcmp): those are one short line of ours
plus repeated filler words, so the finder still drops them as "a description stuffed with words".
These files are data for tests only; nothing in them is run.

Added 2026-10-23 (lane I6a, from the third fresh-install proof, where the brain's phrases for "World news
dashboard" were "rss news reader" and "api news display", and neither search returned worldmonitor):
- search_rss_news_reader.json and search_api_news_display.json: the repo names, stars, languages, branches,
  licenses and topics are facts as GitHub showed them for those phrases that day (some topics left out);
  every description is our own short line. The "api news display" answer is cut to its 3 news results (the
  real one had 12, the rest about other things), so it plays a thin answer.
- search_news_dashboard.json: worldmonitor first, then NewsBlur, the same two items as in
  search_world_news.json. Modeled on GitHub answering "news dashboard" with worldmonitor first; not a copy
  of a real answer.
