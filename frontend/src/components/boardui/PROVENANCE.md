# BoardUI source provenance

Installed 2026-10-26 with the official public npm CLI `boardui@0.5.8`:

```
npm exec --yes --package=boardui@0.5.8 -- boardui init -y
npm exec --yes --package=boardui@0.5.8 -- boardui add button input file-upload agent-thinking -y
```

Official installation source: https://www.boardui.com/installation
Public upstream: https://github.com/BoardUI/boardui
License: MIT, copyright 2026 Mertcan Dundar Esmergul (BoardUI), retained in LICENSE.

CLI installed free button, input/hint/label, select, checkbox, textarea, file-upload, agent-thinking,
template-copy and directional-icon/cx/chevron helpers. Additional free form primitives
were installed with `boardui@0.5.8 add select checkbox textarea -y` during integration. It wrote semantic theme,
typography and globals in `src/styles`. The CLI emitted a Next.js warning;
its documented React 19 + Tailwind 4 framework-independent source is consumed
by Vite with configured aliases. No Pro activation, source or license used.

TinyStudio adaptations:
- Standard product Button wraps upstream Button and maps sm/md sizes.
- Neutral graphite button gradients in studio.css; upstream blue accents remain for focus, selection and data.
- File upload is being adapted by the upload worker to controlled file
  selection; upstream demonstration timers must never represent transport.
- Product-owned modal, lifecycle, API/evidence controllers and shell are
  TinyStudio code, not claimed as BoardUI components or Pro blocks.
- DirectionProvider installed separately with the same pinned official CLI.

CLI-generated AI guidance is retained at frontend/AGENTS.md and
frontend/.cursor/rules/boardui.mdc. Product/global instructions take precedence.
