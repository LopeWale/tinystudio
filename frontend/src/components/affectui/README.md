# AffectUI source adoption

Source-owned adaptations of the public [AffectUI repository](https://github.com/flohoeller/affectui). Original copyright and full MIT license are preserved in LICENSE. provenance.json records the exact public download URL and SHA-256 of each **unmodified upstream input**. The hashes intentionally do not describe the locally adapted files. No Pro screen or block source was used.

Local adaptations:

- All component colors resolve through existing TinyStudio semantic tokens; the upstream hardcoded light/dark palettes were removed. Focus rings use the semantic focus token. Compact shapes and source interaction structure are preserved; coarse-pointer action controls are enlarged.
- Button uses shared cx class merging, native disabled behavior while loading, safe animation capability checks, and logical icon padding. Studio's existing Button adapter keeps its primary/secondary/ghost/danger variants, sm/md sizes, loading label, native event/aria props and default button type. `danger` maps to upstream `destructive`.
- Button labels and icons stay visible without entrance animations. Native Safari review exposed persistent invisible content while their AX names and click targets remained available; removing the opacity/blur entrances restored the content. Width morph, loading shimmer and spinner remain. This isolates the affected animation path without claiming an exact browser-engine cause.
- Filter group disclosure and segmented navigation follow computed RTL direction; filter collapsed panels are hidden from assistive technology, empty filter lists cannot crash keyboard navigation, and spacing uses logical properties. Physical measured geometry remains physical.
- Segmented scrolling accounts for negative RTL scroll offsets; a selected disabled option never becomes the only tab stop.
- `controls.tsx` exposes controlled FilterMenu and SegmentedControl wrappers: feature controllers own values, queries and persistence. Only focus/disclosure/measurement are local; neither wrapper makes API calls.
- Studio templates compose slots, not domain data or controllers. Their responsive inspector remains in document order at narrow sizes. Public screen previews inform composition only.

The original components embed their scoped CSS as React 19 style resources. Keep their unique href keys and retain the MIT notice in distributions. Browser visual, touch and assistive-technology acceptance remains a separate check from unit contract tests.
