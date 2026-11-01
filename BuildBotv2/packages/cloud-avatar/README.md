# Puff, the cloud avatar

Puff is Puff, hand drawn by Cypher Sisters: pink, with a black ink outline, purple closed smiling eyes with lashes, a small smile and two little cloud hands. Art and code by Cypher Sisters, Apache-2.0. It is her own work, with no third-party license. This is the one avatar that ships; owners can make or upload their own.

## PuffPAI

**What it does.** A `kind: avatar` package: a face for the bot. The Desktop shows the active avatar in the top bar, on Home and in Looks; the owner picks it in Settings or Looks.

**What it needs.** Nothing. It runs nothing and uses no internet.

**Setup steps.** None.

**Files.**
- `puff.webp`: her drawing as she made it, 2000 x 2000 with a transparent background around the cloud. Never redrawn or changed.
- `preview.png`: the same picture at full size, as a lossless PNG (the package format wants SVG or PNG for a preview).
- `face.html`: the live face. It shows her drawing as it is. While the bot answers in chat, a small mouth over her drawn smile opens and closes, in her own ink (`#000000`) and the deep purple of her eyes (`#500078`), on the curve of her smile measured in the drawing (corners at x 860 and 961, y 830; lowest point x 910, y 863). When she stops, the mouth goes away and her drawn smile shows. She bobs gently, as Puff did in today's BuildBot (`PuffCloudAvatar.tsx`, whose attack and decay the mouth also keeps).

The Desktop keeps its own copy of the drawing (`packages/desktop/web/img/puff.webp`), so before setup, when this package is not installed yet, it still shows her and never a broken picture.

**How the Desktop drives the face (PROPOSED convention, lane I8b).** An avatar package may declare a page with the id `face`. The Desktop frames it on the package's own origin with `sandbox="allow-scripts allow-same-origin"` (its origin is its own, never the Core's; no owner login, D49, D53) and posts it one kind of message, to that origin only: `{"type": "puffpai.avatar", "talking": true | false, "pulse": <characters just streamed>}`. The face reads nothing else and sends nothing. An avatar without a `face` page shows its preview picture.

There is no `.glb` 3D model: a `.glb` is optional for an avatar (D48a).
