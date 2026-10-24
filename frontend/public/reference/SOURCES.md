# Fixed SR reference study

Source repository: https://github.com/auenger/SiliRootLogoDesign
Fixed commit: 677e8f8f1a3d7128d8ab831cae99d7e00ac567ce
Copied/adapted on: 2026-10-24

- `sr-original.png`: byte-for-byte copy of `public/logo-reference.png`.
- `upstream-logo-geometry.js`: byte-for-byte copy of `public/logo-geometry.js`, retained for provenance; not executed by the application.
- `sr-geometry.json`: resolved `W`, `H`, `parts`, and the designated smooth-joint points exported by that exact geometry module. Coordinates retain full JSON numeric precision. Tangency errors are recalculated in `src/lib/reference-lab.ts` rather than copied from the original page's fixed label.
- `src/components/ReferenceLab.vue` and `src/lib/reference-lab.ts`: Vue adaptation of `public/logo-lab.html`'s polygon base / circular cap construction, reference-image crop, and comparison modes. All three original SR parts are retained. The 1-source-pixel chord overlap for antialias seams is retained.

This is the original SiliRoot fixed SR prototype and reference artwork, not a generated reference for arbitrary input. It does not change the current user wordmark. It is used under the repository owner's explicit authorization in the integration task; original provenance remains recorded here. No original source headers have been removed from copied files.

SHA-256 of copied source assets:

- sr-original.png: `252d4a7c1069fecc47d578017c7d9775fc84840a9719edd3d65d2b27667dc9ad`
- upstream-logo-geometry.js: `54fbe56011b44209e9c6a6a55189bf60119adbe3e40a3dc72f16bfda46e48668`
