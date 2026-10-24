# Analytic line / circular-arc source

- Source: https://github.com/auenger/SiliRootLogoDesign
- Fixed commit: `677e8f8f1a3d7128d8ab831cae99d7e00ac567ce`
- Imported and extended: 2026-10-24.
- The user confirmed ownership of this source. No new license is assigned here.

`boundary-geometry.js` originates from `public/monogram-mvp/boundary-geometry.js`; its SR data import now points to the adjacent `logo-geometry.js`, copied from original `src/lib/logo-geometry.js` rather than a generated artifact.

Local extensions are explicit: 26 lowercase primitive glyphs; optional spacing with a zero default; preservation of resolved contours through transforms; placement metadata; and an optional boundary side-probe distance used only by the new compositor's local tangent-degeneracy retries. The original uppercase pair path, fitting order and default numerical values remain intact. Independent fixtures compare 144 original cases, including final paths and construction paths/circles/lines/joints. The original 234 closed-edge / exact-radius checks also remain.

`composition-geometry.js` is the new 1–32-letter layer. It combines up to four actual analytic glyphs with union/XOR/dilated cutouts and over/under masks; longer names retain all characters in explicitly separated compact groups. Single-character variants use real negative cuts and geometric recomposition. Lowercase glyphs preserve independent dots, x-height, ascenders and descenders. No font-path engine, text-layout fallback or sampled circle fitting is used.

Original two-uppercase inputs call `generateBoundary` directly. New numerical retries do not change original pair defaults or replace an unsuccessful design with ordinary typesetting; unrecoverable compositions surface an explicit error.
