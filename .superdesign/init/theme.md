# Theme

Captured 2026-10-27T23:43:08.381443+00:00 from the current shared working tree. Local discovery only: application source is frozen. No application edits, remote calls, live-data/credential requests, deployments, branches, worktrees or commits were performed by this init. These discovery files are not an instruction to upload the entire bundle. No `.superdesign/resume.json` is created.

## Part 1 — compact actual token summary

CSS-first Tailwind v4; no Tailwind config.js/ts exists. Values below are from final TinyStudio theme.css contract blocks after the earlier BoardUI defaults/semantic re-exports. Main imports globals.css (Tailwind/theme/typography), studio.css, workspace.css, then workbench.css; feature CSS adds scoped rules. This is source context, not certification of browser-computed styles.

### Palette

| Token | Light :root | Dark .dark, root fallback |
| --- | --- | --- |
| `--color-text-primary` | `#172330` | `#eaf0f7` |
| `--color-text-secondary` | `#586a7d` | `#b8c7d8` |
| `--color-text-tertiary` | `#586a7d` | `#a5b5c8` |
| `--color-text-placeholder` | `#586a7d` | `#a5b5c8` |
| `--color-background-full` | `#eef2f6` | `#111a24` |
| `--color-background-primary-default` | `#ffffff` | `#1b2837` |
| `--color-background-secondary-default` | `#f5f7fa` | `#202f40` |
| `--color-background-secondary-hover` | `#eaf0fe` | `#293d56` |
| `--color-background-tertiary-default` | `#eef2f6` | `#243447` |
| `--color-separator-border` | `#dce3eb` | `#35475d` |
| `--color-border-table` | `#dce3eb` | `#35475d` |
| `--color-border-button-default` | `#cbd5e1` | `#4a5e76` |
| `--color-border-button-hover` | `#82909e` | `#82909e` |
| `--color-border-focus-ring` | `#245bdb` | `#245bdb` |
| `--color-accent-50` | `#eaf0fe` | `#213a60` |
| `--color-accent-100` | `#dce6ff` | `#284775` |
| `--color-accent-500` | `#3268e4` | `#7aa5ff` |
| `--color-accent-600` | `#245bdb` | `#88b0ff` |
| `--color-accent-700` | `#1c49b4` | `#a3c1ff` |
| `--color-chart-1` | `#245bdb` | `#88b0ff` |
| `--color-chart-2` | `#087f8c` | `#59c4ca` |
| `--color-chart-3` | `#a46216` | `#e4b56c` |
| `--color-chart-4` | `#586a7d` | `#b8c7d8` |
| `--color-chart-5` | `#be3f39` | `#f28d87` |
| `--color-button-action` | `#245bdb` | `#245bdb` |
| `--color-button-action-hover` | `#1c49b4` | `#1c49b4` |
| `--color-button-ghost-background` | `transparent` | `transparent` |
| `--color-button-ghost-hover` | `var(--color-background-secondary-hover)` | `var(--color-background-secondary-hover)` |
| `--color-button-ghost-active` | `var(--color-background-tertiary-default)` | `var(--color-background-tertiary-default)` |
| `--color-button-ghost-disabled` | `transparent` | `transparent` |
| `--color-button-ghost-foreground` | `var(--color-text-secondary)` | `var(--color-text-secondary)` |
| `--color-button-ghost-disabled-foreground` | `var(--color-text-tertiary)` | `var(--color-text-tertiary)` |

Additional existing semantic aliases (exact source expressions; resolution uses installed Tailwind primitives):

| Token | Light | Dark |
| --- | --- | --- |
| `--color-text-error-primary` | `var(--color-red-500)` | `var(--color-red-400)` |
| `--color-border-error-default` | `var(--color-red-500)` | `var(--color-red-400)` |
| `--color-status-lime-background` | `var(--color-lime-200)` | `color-mix(in srgb, var(--color-lime-950) 60%, transparent)` |
| `--color-status-lime-text` | `var(--color-lime-800)` | `var(--color-lime-500)` |
| `--color-status-yellow-background` | `var(--color-yellow-200)` | `color-mix(in srgb, var(--color-yellow-950) 60%, transparent)` |
| `--color-status-yellow-text` | `var(--color-yellow-800)` | `var(--color-yellow-500)` |
| `--color-status-purple-background` | `var(--color-purple-100)` | `color-mix(in srgb, var(--color-purple-900) 50%, transparent)` |
| `--color-status-purple-text` | `var(--color-purple-600)` | `var(--color-purple-300)` |
| `--gradient-button-danger-default` | `linear-gradient(180deg, var(--color-red-500) 0%, var(--color-red-600) 100%)` | `linear-gradient(180deg, var(--color-red-500) 0%, var(--color-red-600) 100%)` |

The source-owned BoardUI library still declares purple status aliases. Token presence alone does not prove that a particular visible page uses them.

### Typography

Actual studio root family is `Inter, ui-sans-serif, system-ui, sans-serif`. Theme --font-sans references --font-inter plus platform fallbacks; theme --font-mono references --font-mono-source plus platform fallbacks. studio.css code uses `ui-monospace, monospace`. Outdated Next.js font-loading comments in theme.css are not evidence of active bundled font loading.

BoardUI composite ramp regular metrics (medium/semibold/bold share metrics and use 500/600/700 weights):

| Family | Size | Line height | Letter spacing |
| --- | --- | --- | --- |
| `large-title` | `4rem` | `5rem` | `0` |
| `display-1` | `3.5rem` | `4.5rem` | `0` |
| `display-2` | `3rem` | `4rem` | `0` |
| `display-3` | `2.5rem` | `3.375rem` | `0` |
| `display-4` | `2rem` | `2.75rem` | `0` |
| `title-1` | `1.5rem` | `2.125rem` | `0` |
| `title-2` | `1.25rem` | `1.625rem` | `0` |
| `title-3` | `1.125rem` | `1.5rem` | `0` |
| `headline` | `1rem` | `1.375rem` | `0` |
| `body` | `0.875rem` | `1.25rem` | `0` |
| `body-2` | `0.8125rem` | `1.125rem` | `0` |
| `caption-1` | `0.75rem` | `1rem` | `0.15px` |
| `caption-2` | `0.6875rem` | `0.9375rem` | `0.2px` |

Current workbench overrides page h1 to `1.4rem`, line-height `1.25`, letter-spacing `-.035em`; descriptions/td to `.8125rem`; th/field labels to `.75rem`. These are current mixed-system source rules, not proposed styles.

### Spacing, radii, shadows, breakpoints

- Installed Tailwind spacing unit: `0.25rem`. Workbench page padding is 20px/24px, panel padding 16px, table padding 10px/12px, general button/panel radius 6px, popover radius 8px, location bar 52px, open-work strip 36px.
- BoardUI medium Button: 36px height, 8px container plus 4px nested label padding. Small: 32px height, horizontal 8px/vertical 6px container plus horizontal 2px nested label padding. Workbench overrides button radius and mobile header button min-height to 40px.
- Additional actual theme radii: `--radius-2lg: 10px`; `--radius-2-5xl: 20px`; `--radius-notification-card: 10px`.
- Actual light shadow examples: `--shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.06), 0 1px 3px 0 rgb(0 0 0 / 0.10)`; `--shadow-md: 0 2px 4px -1px rgb(0 0 0 / 0.06), 0 4px 6px -1px rgb(0 0 0 / 0.10)`; `--shadow-lg: 0 4px 6px -2px rgb(0 0 0 / 0.05), 0 10px 15px -3px rgb(0 0 0 / 0.10)`; `--shadow-xl: 0 10px 10px -5px rgb(0 0 0 / 0.04), 0 20px 25px -5px rgb(0 0 0 / 0.10)`; `--shadow-card: 0 1px 1px 0 rgb(0 0 0 / 0.05)`; 
- Dark shadow overrides remain in complete theme source below. Workbench removes panel/button shadows, with popover `0 10px 30px color-mix(in srgb,var(--color-text-primary) 12%,transparent)`.
- Installed Tailwind breakpoint defaults: `--breakpoint-sm: 40rem`; `--breakpoint-md: 48rem`; `--breakpoint-lg: 64rem`; `--breakpoint-xl: 80rem`; `--breakpoint-2xl: 96rem`.
- Workbench-specific media conditions use 1000px/640px; shared records use 850px; data tables also use 1100px/850px; analytical/lab feature files own further conditions listed in pages.md. Workbench has reduced-motion rules.
- Workbench flattens primary gradients to `linear-gradient(var(--color-button-action),var(--color-button-action))`, with action default #245bdb and hover #1c49b4 in both modes. This is distinct from dark-mode accent tokens.

## Part 2 — complete raw source

## `frontend/src/styles/theme.css`

Complete actual source for local discovery; not an unconditional remote payload.

SHA-256: `c2bb2754b5177f59f89eb30f6d4a4bc381bc4144da03b0fffee951d80be64a38`

```css
/* ---------------------------------------------------------------------------
 * BoardUI theme tokens
 *
 * Layered as Tailwind v4 expects:
 *   @theme { ... }     primitives + ramp overrides → Tailwind utilities
 *   :root { ... }      semantic tokens (light mode) — reference primitives
 *   .dark { ... }      semantic overrides (dark mode) — landed when designed
 *   @theme inline {..} re-export of semantic tokens so they become utilities
 *
 * See docs/FIGMA_WORKFLOW.md for how tokens get added (Figma is source of truth).
 * --------------------------------------------------------------------------- */

@theme {
  /* ----- Color primitives ----------------------------------------------------
   * Almost the entire palette matches Tailwind v4's OKLCH defaults. The four
   * overrides below are the only intentional deviations (see Figma → variables
   * → color/...).
   * --------------------------------------------------------------------------*/
  --color-slate-200:   #ebeff5;
  --color-neutral-100: #f7f7f7;
  --color-neutral-200: #ebebeb;
  --color-neutral-925: #121212;
  --color-blue-400:    #3392ff;

  /* ----- Accent ramp ---------------------------------------------------------
   * The CTA hue. Interactive/selection surfaces (primary button gradient,
   * ghost + link buttons, checkbox, radio, switch, slider, tabs, sidebar
   * selected, focus ring, date-range selection) reference accent-* instead of
   * a raw hue, so the whole system re-tints by overriding these eleven
   * variables at runtime (see components/application/theme/accent.ts) or in
   * a customer project after importing this file. Defaults to the blue
   * primitives above. Content/data blues (charts, status chips, calendar
   * events, info notifications) deliberately keep referencing blue-*.
   * --------------------------------------------------------------------------*/
  --color-accent-50:  var(--color-blue-50);
  --color-accent-100: var(--color-blue-100);
  --color-accent-200: var(--color-blue-200);
  --color-accent-300: var(--color-blue-300);
  --color-accent-400: var(--color-blue-400);
  --color-accent-500: var(--color-blue-500);
  --color-accent-600: var(--color-blue-600);
  --color-accent-700: var(--color-blue-700);
  --color-accent-800: var(--color-blue-800);
  --color-accent-900: var(--color-blue-900);
  --color-accent-950: var(--color-blue-950);

  /* ----- Typography ----------------------------------------------------------
   * Font sizes, weights, line-heights, and tracking all match Tailwind v4
   * defaults. The only override is the family: Inter, loaded via next/font
   * in app/layout.tsx, which exposes --font-inter to the document.
   * --------------------------------------------------------------------------*/
  --font-sans: var(--font-inter), ui-sans-serif, system-ui, sans-serif,
               "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol",
               "Noto Color Emoji";

  /* Monospace ramp for code blocks. Loaded via next/font in app/layout.tsx
   * (--font-mono-source → JetBrains Mono). Figma's spec uses IBM Plex Mono;
   * JetBrains Mono is the closest already-bundled face. */
  --font-mono: var(--font-mono-source), ui-monospace, "SFMono-Regular", Menlo,
               Consolas, "Liberation Mono", monospace;

  /* ----- Radii ---------------------------------------------------------------
   * Extra step between lg (8) and xl (12). Used by medium-size buttons.
   * Figma name: radius/2lg.
   * --------------------------------------------------------------------------*/
  --radius-2lg: 10px;
  /* Figma `radius/2,5xl`: kanban columns and other large inset panels. */
  --radius-2-5xl: 20px;
  --radius-notification-card: 10px;

  /* ----- Shadows -------------------------------------------------------------
   * 2xs, xs, 2xl and the inner shadows already match Tailwind v4. Sizes sm, md,
   * lg, xl follow the Figma 6%/10% recipe (slightly softer than Tailwind v4's
   * 10%/10% default).
   * --------------------------------------------------------------------------*/
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.06), 0 1px 3px 0 rgb(0 0 0 / 0.10);
  --shadow-md: 0 2px 4px -1px rgb(0 0 0 / 0.06), 0 4px 6px -1px rgb(0 0 0 / 0.10);
  --shadow-lg: 0 4px 6px -2px rgb(0 0 0 / 0.05), 0 10px 15px -3px rgb(0 0 0 / 0.10);
  --shadow-xl: 0 10px 10px -5px rgb(0 0 0 / 0.04), 0 20px 25px -5px rgb(0 0 0 / 0.10);

  /* Figma effect style "Background/Sidebar Elevation" — copied 1:1:
   *   DROP_SHADOW #00000005 offset(0,1) radius 0  spread 0  → 0 1px 0px  0 / 0.0196
   *   DROP_SHADOW #0000000F offset(0,1) radius 12 spread 0  → 0 1px 12px 0 / 0.0588
   *   DROP_SHADOW #00000052 offset(0,0) radius 1  spread 0  → 0 0   1px  0 / 0.3216
   * (alpha = hex byte / 255: 05→0.0196, 0F→0.0588, 52→0.3216). */
  --shadow-sidebar:
    0 1px 0 0 rgb(0 0 0 / 0.0196),
    0 1px 12px 0 rgb(0 0 0 / 0.0588),
    0 0 1px 0 rgb(0 0 0 / 0.3216);

  /* Figma effect style "Sidebar selected" — copied 1:1:
   *   DROP_SHADOW  color/blue/500 offset(0,0) radius 0 spread 1 → 0 0 0 1px blue-500
   *   INNER_SHADOW #FFFFFF40       offset(0,1) radius 0 spread 0 → inset 0 1px 0 0 / 0.251
   * (#FFFFFF40 alpha = 0x40/255 = 0.251). */
  --shadow-nav-selected:
    0 0 0 1px var(--color-accent-500),
    inset 0 1px 0 0 rgb(255 255 255 / 0.251);

  /* Checkbox checked/indeterminate surface (from the Figma Checkbox component):
   * inner top highlight + 1px inner blue edge over the primary gradient. */
  --shadow-checkbox-selected:
    inset 0 2px 0 0 rgb(255 255 255 / 0.25),
    inset 0 0 0 1px var(--color-accent-500);

  /* 1px contact shadow used on white cards/dropdown triggers sitting on the
   * secondary background (Figma drop shadow 0 1 1 5%). */
  --shadow-card: 0 1px 1px 0 rgb(0 0 0 / 0.05);

  /* Floating menu/dropdown panel (Figma → sidebar profile menu node 3823:3699):
   *   DROP_SHADOW #0000000A offset(0,1) radius 1 → 0 1px 1px 0 / 0.04
   *   DROP_SHADOW #00000005 offset(0,4) radius 4 → 0 4px 4px 0 / 0.02 */
  --shadow-dropdown:
    0 1px 1px 0 rgb(0 0 0 / 0.04),
    0 4px 4px 0 rgb(0 0 0 / 0.02);

  /* Floating template waitlist (Figma node 4117:32737). This extends the
   * sidebar elevation recipe with the two broad ambient layers used by the
   * signup surface. */
  --shadow-waitlist:
    0 0 6px 0 rgb(0 0 0 / 0.06),
    0 0 64px 0 rgb(0 0 0 / 0.16),
    0 0 1px 0 rgb(0 0 0 / 0.32),
    0 1px 12px 0 rgb(0 0 0 / 0.06),
    0 1px 0 0 rgb(0 0 0 / 0.02);

  /* Inner shadows (Figma names inner-shadow/*; Tailwind utility prefix is
   * inset-shadow-*).
   */
  --inset-shadow-2xs: inset 0 1px 0 0 rgb(0 0 0 / 0.05);
  --inset-shadow-xs:  inset 0 1px 1px 0 rgb(0 0 0 / 0.05);
  --inset-shadow-sm:  inset 0 2px 4px 0 rgb(0 0 0 / 0.05);
}

/* ---------------------------------------------------------------------------
 * Semantic tokens — light mode.
 *
 * All values reference primitives via var(--color-{family}-{shade}). Edit the
 * primitives in @theme above and these update automatically.
 *
 * Figma name on the left, var name on the right:
 *   foreground/full                  → --color-foreground-full
 *   foreground/icon/primary          → --color-foreground-icon-primary
 *   foreground/disabled              → --color-foreground-disabled
 *   foreground/disabled-danger       → --color-foreground-disabled-danger
 *   text/primary                     → --color-text-primary
 *   text/tertiary                    → --color-text-tertiary
 *   text/disabled-danger             → --color-text-disabled-danger
 *   background/primary/default       → --color-background-primary-default
 *   background/primary/hover         → --color-background-primary-hover
 *   background/primary/active        → --color-background-primary-active
 *   background/primary/disabled      → --color-background-primary-disabled
 *   background/secondary/default     → --color-background-secondary-default
 *   border/button/default            → --color-border-button-default
 *   border/button/hover              → --color-border-button-hover
 *   border/button/active             → --color-border-button-active
 *
 * Button gradients (linear, 180°, light stop → dark stop) sourced from the
 * blue/red primitives that each Button/{type}/{state} style references in
 * Figma. Direction inferred — confirm visually against Figma.
 * --------------------------------------------------------------------------- */
:root {
  /* Foreground (icons + on-fill content) */
  --color-foreground-full:               var(--color-white);
  --color-foreground-icon-primary:       var(--color-neutral-950);
  --color-foreground-icon-hover:         var(--color-black);
  --color-foreground-icon-secondary:     var(--color-neutral-500);
  --color-foreground-icon-tertiary:      var(--color-neutral-400);
  --color-foreground-icon-quaternary:    var(--color-neutral-300);
  --color-foreground-icon-error:         var(--color-red-600);
  --color-foreground-icon-disabled:      var(--color-neutral-300);
  --color-foreground-disabled:           var(--color-neutral-400);
  --color-foreground-disabled-danger:    var(--color-red-300);
  --color-input-disabled-foreground:     var(--color-neutral-300);
  --color-icon-button-disabled-foreground: var(--color-neutral-300);
  --color-button-primary-disabled-foreground: var(--color-neutral-400);

  /* Text */
  --color-text-primary:         var(--color-neutral-950);
  --color-text-secondary:       var(--color-neutral-500);
  --color-text-tertiary:        var(--color-neutral-400);
  --color-text-disabled:        var(--color-neutral-300);
  --color-text-disabled-danger: var(--color-red-300);
  --color-text-placeholder:     var(--color-neutral-400);
  --color-text-error-primary:   var(--color-red-500);
  --color-text-destructive: color-mix(in srgb, var(--color-red-700) 80%, var(--color-neutral-600));
  --color-input-disabled-text:  var(--color-neutral-300);
  --color-text-white:           var(--color-white);

  /* Background */
  --color-background-full:              var(--color-white);
  /* Ticket detail, Figma 4509:12272. The raw #fafafa surface matches neutral/50. */
  --color-ticket-detail-surface: var(--color-white);
  --color-ticket-detail-low-background: var(--color-indigo-200);
  --color-ticket-detail-low-hover: var(--color-indigo-300);
  --color-ticket-detail-medium-hover: var(--color-yellow-300);
  --color-ticket-detail-high-hover: var(--color-orange-300);
  --color-ticket-detail-urgent-hover: var(--color-rose-300);
  --color-ticket-detail-low-text: var(--color-indigo-800);
  --color-ticket-detail-resource-background: var(--color-blue-100);
  --color-ticket-detail-resource-text: var(--color-blue-600);
  --color-ticket-detail-favorite: #ffb83c; /* raw Figma fill, no token yet */
  /* Empty-state illustration: raw Figma fills from node 4488:11848. */
  --color-project-board-empty-panel: rgb(242 242 242 / 0.8);
  --color-project-board-empty-front: #d9d9d9;
  --color-project-board-empty-middle: #e5e5e5;
  --color-project-board-empty-back: #ededed;
  --color-project-board-empty-detail: var(--color-white);
  --color-background-primary-default:   var(--color-white);
  --color-background-inner-default:     var(--color-white);
  --color-background-primary-hover:     var(--color-neutral-100);
  --color-background-primary-active:    var(--color-neutral-200);
  --color-background-primary-disabled:  var(--color-neutral-100);
  --color-input-disabled-background:    var(--color-neutral-100);
  --color-background-secondary-default: var(--color-neutral-100);
  --color-background-secondary-hover:   var(--color-neutral-200);
  --color-background-tertiary-default:  var(--color-neutral-200);
  --color-background-tertiary-hover:    var(--color-neutral-300);
  --color-background-tertiary-error:    var(--color-red-100);
  --color-background-recent-hire-role:  var(--color-neutral-100);
  --color-avatar-neutral-background:    var(--color-neutral-300);
  /* Figma background/quaternary/default — first used by the Storage
   * dropzone's upload-icon disc (node 4106:26587). */
  --color-background-quaternary-default: var(--color-neutral-300);
  --color-background-quaternary-hover:   var(--color-neutral-400);
  --color-background-quaternary-error:   var(--color-red-200);
  --color-file-upload-icon-background:   var(--color-neutral-300);
  --color-file-upload-icon-foreground:   var(--color-neutral-400);
  --color-file-upload-icon-foreground-hover: var(--color-neutral-500);

  /* Border */
  --color-border-button-default:   var(--color-neutral-200);
  --color-border-button-hover:     var(--color-neutral-300);
  --color-border-button-active:    var(--color-neutral-400);
  --color-border-button-white:     var(--color-white);
  /* The system had no red border. Matches text/error/primary so an invalid
     field's edge and its message are the same red. */
  --color-border-error-default:    var(--color-red-500);
  --color-border-checkbox-default: var(--color-neutral-300);
  --color-border-checkbox-hover:   var(--color-neutral-400);
  --color-border-checkbox-active:  var(--color-neutral-400);
  --color-border-checkbox-white:   var(--color-white);
  --color-border-component-detail-container: var(--color-neutral-200);
  --color-border-ai-profile-card:             var(--color-neutral-200);
  --color-border-table:            var(--color-neutral-200);
  --color-border-button-group:     var(--color-neutral-200);
  --color-border-sidebar-profile-hover: var(--color-neutral-300);
  --color-separator-border:        var(--color-neutral-200);
  --color-separator-border-strong: var(--color-neutral-200);
  --color-border-focus-ring:       var(--color-accent-500);

  /* State */
  --color-state-success-text: var(--color-lime-800);
  --color-state-success-base: var(--color-lime-200);

  /* Selected control interiors remain light on colored tracks in both themes. */
  --color-control-indicator-background:        var(--color-white);
  --color-control-indicator-background-subtle: var(--color-neutral-100);
  --color-switch-off-chip-start:                var(--color-white);
  --color-switch-off-chip-end:                  var(--color-neutral-100);
  /* Selected switch chip: the Figma comps use hexes a touch deeper than the
   * plain 500→600 ramp (#2473fe → #0450e2 for blue). Expressed as oklab mixes
   * of neighbouring accent stops so the recipe survives a re-tint. */
  --color-switch-on-chip-start:                 color-mix(in oklab, var(--color-accent-500) 63%, var(--color-accent-600));
  --color-switch-on-chip-end:                   color-mix(in oklab, var(--color-accent-600) 16%, var(--color-accent-700));
  --color-stat-card-icon-background:            var(--color-white);
  --color-segmented-control-background:          var(--color-neutral-100);
  --color-segmented-control-selected-background: var(--color-white);
  --color-theme-toggle-sidebar-background:       var(--color-neutral-200);
  --color-theme-toggle-sidebar-selected-background: var(--color-white);
  /* Stepped against the composer: the pill is white and the add button is the
   * subtle grey sitting on it, so the control separates from the surface it
   * shares an edge with. Dark mode keeps its own pair, where the pill is the
   * lighter of the two and the button steps the other way. */
  --color-ai-chat-composer-add-background:       var(--color-neutral-100);
  --color-ai-chat-composer-add-hover-background: var(--color-neutral-200);
  /* Composer Panel (Figma node 4430:12203): the status tab hanging off the
   * card's top edge and the card's plus button are both neutral-200 in light
   * mode. Dark mode steps them up instead, so they still separate from the
   * neutral-800 card they touch. */
  --color-composer-panel-tab-background:         var(--color-neutral-200);
  --color-composer-panel-add-background:         var(--color-neutral-200);
  --color-composer-panel-add-hover-background:   var(--color-neutral-300);
  /* Attachment tile edge: the Figma neutral-200 in light mode; dark mode
   * uses a translucent white so the ring reads on the neutral-800 card
   * (border/button/default is one neutral step away there and vanishes). */
  --color-composer-panel-tile-border:            var(--color-neutral-200);
  /* Model picker provider rail: background/secondary in light mode, at
   * half opacity in dark so it recedes on the neutral-800 panel. */
  --color-composer-panel-rail-background:        var(--color-neutral-100);
  /* Questionnaire (Figma nodes 4435:14749 / 4435:15042): the answer rows share
   * the card's background/primary surface in both modes, separated by their
   * border alone, and the "Other" row uses the stock Input untouched. The
   * single-select number key is background/secondary in light mode and one
   * tone lighter than the row in dark mode (never darker), stepping again
   * while its row is hovered so it stays legible on the lifted fill. */
  --color-questionnaire-key-background:          var(--color-neutral-100);
  --color-questionnaire-key-hover-background:    var(--color-neutral-200);
  --color-agent-progress-ring:                     var(--color-neutral-700);
  --color-button-ghost-background:                var(--color-accent-100);
  --color-button-ghost-hover:                     var(--color-accent-200);
  --color-button-ghost-active:                    var(--color-accent-300);
  --color-button-ghost-disabled:                  var(--color-accent-50);
  --color-button-ghost-foreground:                var(--color-accent-700);
  --color-button-ghost-disabled-foreground:       var(--color-accent-300);
  --color-dropdown-item-hover-background:        var(--color-neutral-100);
  --color-kbd-background:                        var(--color-neutral-300);
  --color-kbd-foreground:                        var(--color-neutral-500);
  --color-docs-file-chip-background:             var(--color-purple-50);
  --color-docs-file-chip-border:                 var(--color-purple-100);
  --color-docs-file-chip-foreground:             var(--color-purple-500);
  --color-docs-command-accent:                    var(--color-purple-400);
  --color-docs-code-background:                   var(--color-background-primary-default);
  --color-docs-install-rail:                      var(--color-border-button-default);

  /* Recently shipped component badge. Kept semantic so its compact blue
   * surface can retain the same hierarchy in both themes. */
  --color-badge-neutral-background: var(--color-neutral-200);
  --color-team-menu-count-background: var(--color-neutral-200);
  --color-team-menu-count-foreground: var(--color-neutral-500);
  --color-badge-new-background: var(--color-accent-100);
  --color-badge-new-text:       var(--color-accent-600);
  --color-tab-count-selected-background: var(--color-accent-100);
  --color-pill-tab-blue-selected-background: var(--color-accent-50);
  --color-pill-tab-blue-hover-background: var(--color-neutral-100);

  /* Notification status-icon surfaces. */
  --color-notification-information-background: var(--color-blue-100);
  --color-notification-information-foreground: var(--color-blue-600);
  --color-notification-success-background:     var(--color-lime-100);
  --color-notification-success-foreground:     var(--color-lime-600);
  --color-notification-error-background:       var(--color-rose-100);
  --color-notification-error-foreground:       var(--color-rose-600);
  --color-notification-center-background:      var(--color-white);

  /* Table status chips. Dark mappings come from the medical dashboard table
   * in Figma (4139:7202, 4139:7240, 4139:7278, 4139:7315). */
  --color-status-lime-background:   var(--color-lime-200);
  --color-status-lime-text:         var(--color-lime-800);
  --color-status-yellow-background: var(--color-yellow-200);
  --color-status-yellow-text:       var(--color-yellow-800);
  --color-status-rose-background:   var(--color-rose-200);
  --color-status-rose-text:         var(--color-rose-800);
  --color-status-cyan-background:   var(--color-cyan-200);
  --color-status-cyan-text:         var(--color-cyan-800);
  --color-status-orange-background: var(--color-orange-200);
  --color-status-orange-text: var(--color-orange-800);
  --color-status-blue-background:   var(--color-blue-200);
  --color-status-blue-text:         var(--color-blue-800);
  --color-status-purple-background: var(--color-purple-100);
  --color-status-purple-text:       var(--color-purple-600);
  --color-status-dot-green-halo:    var(--color-green-100);
  --color-status-dot-yellow-halo:   var(--color-yellow-200);
  --color-status-dot-indigo-halo:   var(--color-indigo-100);

  /* Date range selection: the connecting band and its start/end caps. */
  --color-date-range-background:      var(--color-accent-100);
  --color-date-range-edge-background: var(--color-accent-300);

  /* Calendar event chips. Title/time stay separate so the original light
   * hierarchy remains intact while dark mode can normalize both to /300. */
  --color-calendar-event-blue-background:    var(--color-blue-100);
  --color-calendar-event-blue-title:         var(--color-blue-700);
  --color-calendar-event-blue-time:          var(--color-blue-700);
  --color-calendar-event-pink-background:    var(--color-pink-100);
  --color-calendar-event-pink-title:         var(--color-pink-700);
  --color-calendar-event-pink-time:          var(--color-pink-700);
  --color-calendar-event-purple-background:  var(--color-purple-100);
  --color-calendar-event-purple-title:       var(--color-purple-700);
  --color-calendar-event-purple-time:        var(--color-purple-700);
  --color-calendar-event-lime-background:    var(--color-lime-100);
  --color-calendar-event-lime-title:         var(--color-lime-800);
  --color-calendar-event-lime-time:          var(--color-lime-700);
  --color-calendar-event-emerald-background: var(--color-emerald-100);
  --color-calendar-event-emerald-title:      var(--color-emerald-800);
  --color-calendar-event-emerald-time:       var(--color-emerald-700);

  /* Placeholder colors that don't map cleanly to existing text tokens */
  --color-text-error-placeholder: var(--color-red-400);

  /* Charts (graphs) — shared neutrals + a categorical accent ramp.
   * `track` is the background bar/ring behind a series; `cursor` the hover
   * outline / dashed reference line; `neutral` a data color for "other"-style
   * slices. Each numbered accent pairs a base fill with a one-tone-darker
   * `-active` used for hover emphasis (the 400 → 500 recipe every chart
   * follows). Numbered (not hue-named) so the palette can be retuned without
   * renaming, mirroring the chart-1…n convention common to chart systems. */
  --color-chart-track:    var(--color-neutral-200);
  --color-chart-cursor:   var(--color-neutral-300);
  --color-chart-neutral:  var(--color-neutral-300);
  --color-chart-1:        var(--color-teal-400);
  --color-chart-1-active: var(--color-teal-500);
  --color-chart-2:        var(--color-lime-400);
  --color-chart-2-active: var(--color-lime-500);
  --color-chart-3:        var(--color-pink-400);
  --color-chart-3-active: var(--color-pink-500);
  --color-chart-4:        var(--color-sky-400);
  --color-chart-4-active: var(--color-sky-500);
  --color-chart-5:        var(--color-purple-400);
  --color-chart-5-active: var(--color-purple-500);
  --color-chart-6:        var(--color-blue-400);
  --color-chart-6-active: var(--color-blue-500);
  --color-chart-7:        var(--color-emerald-400);
  --color-chart-7-active: var(--color-emerald-500);
  --color-chart-8:        var(--color-yellow-400);
  --color-chart-8-active: var(--color-yellow-500);
  --color-chart-9:        var(--color-indigo-400);
  --color-chart-9-active: var(--color-indigo-500);
  --color-chart-agents-bar:        var(--color-purple-300);
  --color-chart-agents-bar-active: var(--color-purple-400);

  /* Button gradients */
  --gradient-button-primary-default:  linear-gradient(180deg, var(--color-accent-500)    0%, var(--color-accent-600)    100%);
  --gradient-button-primary-hover:    linear-gradient(180deg, var(--color-accent-400)    0%, var(--color-accent-500)    100%);
  --gradient-button-primary-active:   linear-gradient(180deg, var(--color-accent-600)    0%, var(--color-accent-700)    100%);
  --gradient-button-primary-disabled: linear-gradient(180deg, var(--color-neutral-200) 0%, var(--color-neutral-300) 100%);

  --gradient-button-danger-default:   linear-gradient(180deg, var(--color-red-500) 0%, var(--color-red-600) 100%);
  --gradient-button-danger-hover:     linear-gradient(180deg, var(--color-red-400) 0%, var(--color-red-500) 100%);
  --gradient-button-danger-active:    linear-gradient(180deg, var(--color-red-600) 0%, var(--color-red-700) 100%);
  --gradient-button-danger-disabled:  linear-gradient(180deg, var(--color-red-100) 0%, var(--color-red-200) 100%);
}

.dark {
  /* Foreground */
  --color-foreground-full:            var(--color-neutral-700);
  --color-foreground-icon-primary:    var(--color-neutral-50);
  --color-foreground-icon-hover:      var(--color-white);
  --color-foreground-icon-error:      var(--color-red-400);
  --color-foreground-icon-tertiary:   var(--color-neutral-600);
  --color-foreground-icon-quaternary: var(--color-neutral-700);
  --color-foreground-icon-disabled:   var(--color-neutral-800);
  --color-foreground-disabled:        var(--color-neutral-700);
  --color-foreground-disabled-danger: var(--color-red-400);
  --color-input-disabled-foreground:  var(--color-neutral-600);
  --color-icon-button-disabled-foreground: var(--color-neutral-500);
  --color-button-primary-disabled-foreground: var(--color-neutral-500);

  /* Text */
  --color-text-primary:  var(--color-neutral-50);
  --color-text-tertiary: var(--color-neutral-600);
  --color-text-disabled: var(--color-neutral-800);
  --color-text-error-primary: var(--color-red-400);
  --color-text-destructive: color-mix(in srgb, var(--color-red-500) 80%, var(--color-neutral-500));
  --color-input-disabled-text: color-mix(in srgb, var(--color-neutral-500) 40%, transparent);

  /* Background */
  --color-background-full:              var(--color-neutral-925);
  --color-ticket-detail-surface: var(--color-neutral-925);
  --color-ticket-detail-low-background: color-mix(in srgb, var(--color-indigo-950) 60%, transparent);
  --color-ticket-detail-low-hover: color-mix(in srgb, var(--color-indigo-950) 80%, transparent);
  --color-ticket-detail-medium-hover: color-mix(in srgb, var(--color-yellow-950) 80%, transparent);
  --color-ticket-detail-high-hover: color-mix(in srgb, var(--color-orange-950) 80%, transparent);
  --color-ticket-detail-urgent-hover: color-mix(in srgb, var(--color-rose-950) 80%, transparent);
  --color-ticket-detail-low-text: var(--color-indigo-300);
  --color-ticket-detail-resource-background: color-mix(in srgb, var(--color-blue-950) 60%, transparent);
  --color-ticket-detail-resource-text: var(--color-blue-300);
  --color-project-board-empty-panel: color-mix(in srgb, var(--color-neutral-800) 60%, transparent);
  --color-project-board-empty-front: color-mix(in srgb, var(--color-neutral-800) 75%, var(--color-neutral-700));
  --color-project-board-empty-middle: var(--color-neutral-700);
  --color-project-board-empty-back: color-mix(in srgb, var(--color-neutral-600) 35%, var(--color-neutral-700));
  --color-project-board-empty-detail: var(--color-neutral-500);
  --color-background-primary-default:   var(--color-neutral-800);
  --color-background-inner-default:     color-mix(in srgb, var(--color-neutral-800) 60%, transparent);
  --color-background-primary-hover:     color-mix(in srgb, var(--color-neutral-700) 60%, transparent);
  --color-background-primary-active:    var(--color-neutral-800);
  --color-background-primary-disabled:  var(--color-neutral-800);
  --color-input-disabled-background:    color-mix(in srgb, var(--color-neutral-800) 30%, transparent);
  --color-switch-off-chip-start:        var(--color-background-tertiary-default);
  --color-switch-off-chip-end:          var(--color-background-tertiary-default);
  --color-background-secondary-default: var(--color-neutral-900);
  --color-background-secondary-hover:   var(--color-neutral-800);
  --color-background-tertiary-default:  var(--color-neutral-800);
  --color-background-tertiary-hover:    var(--color-neutral-700);
  --color-background-tertiary-error:    color-mix(in srgb, var(--color-red-950) 60%, transparent);
  --color-background-quaternary-default: var(--color-neutral-700);
  --color-background-quaternary-hover:   var(--color-neutral-600);
  --color-background-recent-hire-role:  color-mix(in srgb, var(--color-neutral-900) 60%, transparent);
  --color-avatar-neutral-background:    var(--color-background-primary-default);
  --color-file-upload-icon-background:  var(--color-neutral-600);
  --color-file-upload-icon-foreground:  var(--color-neutral-400);
  --color-file-upload-icon-foreground-hover: var(--color-neutral-300);
  --color-stat-card-icon-background:    var(--color-neutral-800);
  --color-segmented-control-background:          var(--color-neutral-925);
  --color-segmented-control-selected-background: var(--color-neutral-800);
  --color-theme-toggle-sidebar-background:       var(--color-neutral-800);
  --color-theme-toggle-sidebar-selected-background: var(--color-neutral-700);
  --color-ai-chat-composer-add-background:       var(--color-neutral-700);
  --color-ai-chat-composer-add-hover-background: var(--color-neutral-600);
  --color-composer-panel-tab-background:         color-mix(in srgb, var(--color-neutral-700) 50%, transparent);
  --color-composer-panel-add-background:         var(--color-neutral-700);
  --color-composer-panel-add-hover-background:   var(--color-neutral-600);
  --color-composer-panel-tile-border:            color-mix(in srgb, var(--color-white) 14%, transparent);
  --color-composer-panel-rail-background:        color-mix(in srgb, var(--color-neutral-900) 50%, transparent);
  --color-questionnaire-key-background:          var(--color-neutral-700);
  --color-questionnaire-key-hover-background:    var(--color-neutral-600);
  --color-agent-progress-ring:                     color-mix(in srgb, var(--color-white) 50%, transparent);
  --color-button-ghost-background:                var(--color-accent-900);
  --color-button-ghost-hover:                     var(--color-accent-800);
  --color-button-ghost-active:                    var(--color-accent-700);
  --color-button-ghost-disabled:                  var(--color-neutral-800);
  --color-button-ghost-foreground:                var(--color-accent-300);
  --color-button-ghost-disabled-foreground:       var(--color-neutral-600);
  --color-dropdown-item-hover-background:        color-mix(in srgb, var(--color-neutral-700) 60%, transparent);
  --color-kbd-background:                        var(--color-neutral-700);
  --color-kbd-foreground:                        var(--color-neutral-400);
  --color-docs-file-chip-background:             color-mix(in srgb, var(--color-purple-950) 50%, transparent);
  --color-docs-file-chip-border:                 var(--color-purple-800);
  --color-docs-file-chip-foreground:             var(--color-purple-400);
  --color-docs-code-background:                  var(--color-background-full);
  --color-docs-install-rail:                     color-mix(in srgb, var(--color-neutral-800) 60%, transparent);

  /* Dark surfaces absorb low-alpha shadows more readily than white ones.
   * Preserve the light-mode geometry and raise only the black alpha: tighter
   * contact shadows stay crisp, while higher elevations receive a restrained
   * ambient layer without introducing a grey glow. */
  --shadow-2xs: 0 1px rgb(0 0 0 / 0.16);
  --shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.18);
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.12), 0 1px 3px 0 rgb(0 0 0 / 0.16);
  --shadow-md: 0 2px 4px -1px rgb(0 0 0 / 0.12), 0 4px 6px -1px rgb(0 0 0 / 0.18);
  --shadow-lg: 0 4px 6px -2px rgb(0 0 0 / 0.10), 0 10px 15px -3px rgb(0 0 0 / 0.18);
  --shadow-xl: 0 10px 10px -5px rgb(0 0 0 / 0.08), 0 20px 25px -5px rgb(0 0 0 / 0.20);
  --shadow-card: 0 1px 1px 0 rgb(0 0 0 / 0.14);
  --shadow-dropdown:
    0 1px 1px 0 rgb(0 0 0 / 0.14),
    0 4px 4px 0 rgb(0 0 0 / 0.10);
  --shadow-sidebar:
    0 1px 0 0 rgb(0 0 0 / 0.10),
    0 1px 12px 0 rgb(0 0 0 / 0.16),
    0 0 1px 0 rgb(0 0 0 / 0.42);
  --shadow-waitlist:
    0 0 6px 0 rgb(0 0 0 / 0.12),
    0 0 64px 0 rgb(0 0 0 / 0.22),
    0 0 1px 0 rgb(0 0 0 / 0.42),
    0 1px 12px 0 rgb(0 0 0 / 0.14),
    0 1px 0 0 rgb(0 0 0 / 0.08);

  /* Badges */
  --color-badge-neutral-background: var(--color-neutral-800);
  --color-team-menu-count-background: var(--color-neutral-700);
  --color-team-menu-count-foreground: var(--color-neutral-400);
  --color-badge-new-background: var(--color-accent-950);
  --color-badge-new-text:       var(--color-accent-400);
  --color-tab-count-selected-background: color-mix(in srgb, var(--color-accent-800) 60%, transparent);

  /* Dark status discs use /800 surfaces with /300 foregrounds so the icon
   * remains crisp without reverting to a bright pastel circle. */
  --color-notification-information-background: color-mix(in srgb, var(--color-blue-800) 50%, transparent);
  --color-notification-information-foreground: var(--color-blue-300);
  --color-notification-success-background:     color-mix(in srgb, var(--color-lime-800) 50%, transparent);
  --color-notification-success-foreground:     var(--color-lime-300);
  --color-notification-error-background:       color-mix(in srgb, var(--color-rose-800) 50%, transparent);
  --color-notification-error-foreground:       var(--color-rose-300);
  --color-notification-center-background:      var(--color-neutral-900);

  /* Borders and separators */
  --color-border-button-default:   var(--color-neutral-700);
  --color-border-button-hover:     var(--color-neutral-500);
  --color-border-button-active:    var(--color-neutral-600);
  --color-border-button-white:     var(--color-zinc-800);
  --color-border-error-default:    var(--color-red-400);
  --color-border-checkbox-default: var(--color-neutral-700);
  --color-border-checkbox-hover:   var(--color-neutral-500);
  --color-border-checkbox-active:  var(--color-neutral-500);
  --color-border-component-detail-container: var(--color-neutral-800);
  --color-border-ai-profile-card:             var(--color-neutral-800);
  --color-border-table:            var(--color-neutral-800);
  --color-border-button-group:     color-mix(in srgb, var(--color-neutral-600) 60%, transparent);
  --color-border-sidebar-profile-hover: var(--color-neutral-600);
  --color-separator-border:        var(--color-neutral-800);
  --color-separator-border-strong: color-mix(in srgb, var(--color-neutral-700) 60%, transparent);

  /* Figma's dark table status pairs. */
  --color-status-lime-background:   color-mix(in srgb, var(--color-lime-950) 60%, transparent);
  --color-status-lime-text:         var(--color-lime-500);
  --color-status-yellow-background: color-mix(in srgb, var(--color-yellow-950) 60%, transparent);
  --color-status-yellow-text:       var(--color-yellow-500);
  --color-status-rose-background:   color-mix(in srgb, var(--color-rose-950) 60%, transparent);
  --color-status-rose-text:         var(--color-rose-500);
  --color-status-cyan-background:   color-mix(in srgb, var(--color-cyan-950) 60%, transparent);
  --color-status-cyan-text:         var(--color-cyan-400);
  --color-status-orange-background: color-mix(in srgb, var(--color-orange-950) 60%, transparent);
  --color-status-orange-text: var(--color-orange-500);
  --color-status-blue-background:   color-mix(in srgb, var(--color-blue-950) 60%, transparent);
  --color-status-blue-text:         var(--color-blue-300);
  --color-status-purple-background: color-mix(in srgb, var(--color-purple-900) 50%, transparent);
  --color-status-purple-text:       var(--color-purple-300);
  --color-status-dot-green-halo:    color-mix(in srgb, var(--color-green-800) 40%, transparent);
  --color-status-dot-yellow-halo:   color-mix(in srgb, var(--color-yellow-800) 40%, transparent);
  --color-status-dot-indigo-halo:   color-mix(in srgb, var(--color-indigo-800) 40%, transparent);

  --color-date-range-background:      var(--color-accent-950);
  --color-date-range-edge-background: var(--color-accent-800);

  --color-pill-tab-blue-selected-background: color-mix(in srgb, var(--color-accent-950) 60%, transparent);
  --color-pill-tab-blue-hover-background: var(--color-neutral-800);

  /* Calendar event families share the same dark-mode depth/contrast recipe. */
  --color-calendar-event-blue-background:    var(--color-blue-950);
  --color-calendar-event-blue-title:         var(--color-blue-300);
  --color-calendar-event-blue-time:          var(--color-blue-300);
  --color-calendar-event-pink-background:    var(--color-pink-950);
  --color-calendar-event-pink-title:         var(--color-pink-300);
  --color-calendar-event-pink-time:          var(--color-pink-300);
  --color-calendar-event-purple-background:  var(--color-purple-950);
  --color-calendar-event-purple-title:       var(--color-purple-300);
  --color-calendar-event-purple-time:        var(--color-purple-300);
  --color-calendar-event-lime-background:    var(--color-lime-950);
  --color-calendar-event-lime-title:         var(--color-lime-300);
  --color-calendar-event-lime-time:          var(--color-lime-300);
  --color-calendar-event-emerald-background: var(--color-emerald-950);
  --color-calendar-event-emerald-title:      var(--color-emerald-300);
  --color-calendar-event-emerald-time:       var(--color-emerald-300);

  /* Disabled primary CTA: dark, low-emphasis fill with readable muted text. */
  --gradient-button-primary-disabled: linear-gradient(
    180deg,
    var(--color-neutral-700) 0%,
    var(--color-neutral-800) 100%
  );
  --gradient-button-danger-disabled: linear-gradient(
    180deg,
    var(--color-red-900) 0%,
    var(--color-red-950) 100%
  );

  /* Neutral graph structure; categorical and status colors stay unchanged. */
  --color-chart-track:   var(--color-neutral-800);
  --color-chart-cursor:  var(--color-neutral-700);
  --color-chart-neutral: var(--color-neutral-800);
  --color-chart-agents-bar:        var(--color-purple-500);
  --color-chart-agents-bar-active: var(--color-purple-600);
}

/* Non-default accents (data-accent set by the accent engine) can land on a
 * /950 that glows too loudly against the dark sidebar; halve the NEW badge
 * fill there. Light mode keeps the solid /100 surface. */
.dark[data-accent] {
  --color-badge-new-background: color-mix(in srgb, var(--color-accent-950) 50%, transparent);
}

/* Re-export semantic tokens as Tailwind utilities. The `inline` form means
 * Tailwind reads the *current* computed value of the variable, so light↔dark
 * flips work without rebuild. */
@theme inline {
  --color-foreground-full:            var(--color-foreground-full);
  --color-foreground-icon-primary:    var(--color-foreground-icon-primary);
  --color-foreground-icon-hover:      var(--color-foreground-icon-hover);
  --color-foreground-icon-secondary:  var(--color-foreground-icon-secondary);
  --color-foreground-icon-tertiary:   var(--color-foreground-icon-tertiary);
  --color-foreground-icon-quaternary: var(--color-foreground-icon-quaternary);
  --color-foreground-icon-error:      var(--color-foreground-icon-error);
  --color-foreground-icon-disabled:   var(--color-foreground-icon-disabled);
  --color-foreground-disabled:        var(--color-foreground-disabled);
  --color-foreground-disabled-danger: var(--color-foreground-disabled-danger);
  --color-input-disabled-foreground:  var(--color-input-disabled-foreground);
  --color-icon-button-disabled-foreground: var(--color-icon-button-disabled-foreground);
  --color-button-primary-disabled-foreground: var(--color-button-primary-disabled-foreground);

  --color-text-primary:         var(--color-text-primary);
  --color-text-secondary:       var(--color-text-secondary);
  --color-text-tertiary:        var(--color-text-tertiary);
  --color-text-disabled:        var(--color-text-disabled);
  --color-text-disabled-danger: var(--color-text-disabled-danger);
  --color-text-placeholder:     var(--color-text-placeholder);
  --color-text-error-primary:   var(--color-text-error-primary);
  --color-text-destructive: var(--color-text-destructive);
  --color-input-disabled-text:  var(--color-input-disabled-text);
  --color-text-white:           var(--color-text-white);

  --color-background-full:              var(--color-background-full);
  --color-ticket-detail-surface: var(--color-ticket-detail-surface);
  --color-ticket-detail-low-background: var(--color-ticket-detail-low-background);
  --color-ticket-detail-low-hover: var(--color-ticket-detail-low-hover);
  --color-ticket-detail-medium-hover: var(--color-ticket-detail-medium-hover);
  --color-ticket-detail-high-hover: var(--color-ticket-detail-high-hover);
  --color-ticket-detail-urgent-hover: var(--color-ticket-detail-urgent-hover);
  --color-ticket-detail-low-text: var(--color-ticket-detail-low-text);
  --color-ticket-detail-resource-background: var(--color-ticket-detail-resource-background);
  --color-ticket-detail-resource-text: var(--color-ticket-detail-resource-text);
  --color-ticket-detail-favorite: var(--color-ticket-detail-favorite);
  --color-background-primary-default:   var(--color-background-primary-default);
  --color-background-inner-default:     var(--color-background-inner-default);
  --color-background-primary-hover:     var(--color-background-primary-hover);
  --color-background-primary-active:    var(--color-background-primary-active);
  --color-background-primary-disabled:  var(--color-background-primary-disabled);
  --color-input-disabled-background:    var(--color-input-disabled-background);
  --color-background-secondary-default: var(--color-background-secondary-default);
  --color-background-secondary-hover:   var(--color-background-secondary-hover);
  --color-background-tertiary-default:  var(--color-background-tertiary-default);
  --color-background-tertiary-hover:    var(--color-background-tertiary-hover);
  --color-background-tertiary-error:    var(--color-background-tertiary-error);
  --color-background-recent-hire-role:  var(--color-background-recent-hire-role);
  --color-avatar-neutral-background:    var(--color-avatar-neutral-background);
  --color-background-quaternary-default: var(--color-background-quaternary-default);
  --color-background-quaternary-hover:   var(--color-background-quaternary-hover);
  --color-background-quaternary-error:   var(--color-background-quaternary-error);
  --color-file-upload-icon-background:   var(--color-file-upload-icon-background);
  --color-file-upload-icon-foreground:   var(--color-file-upload-icon-foreground);
  --color-file-upload-icon-foreground-hover: var(--color-file-upload-icon-foreground-hover);

  --color-border-button-default:   var(--color-border-button-default);
  --color-border-button-hover:     var(--color-border-button-hover);
  --color-border-button-active:    var(--color-border-button-active);
  --color-border-button-white:     var(--color-border-button-white);
  --color-border-error-default:    var(--color-border-error-default);
  --color-border-checkbox-default: var(--color-border-checkbox-default);
  --color-border-checkbox-hover:   var(--color-border-checkbox-hover);
  --color-border-checkbox-active:  var(--color-border-checkbox-active);
  --color-border-checkbox-white:   var(--color-border-checkbox-white);
  --color-border-component-detail-container: var(--color-border-component-detail-container);
  --color-border-ai-profile-card:             var(--color-border-ai-profile-card);
  --color-border-table:            var(--color-border-table);
  --color-border-button-group:     var(--color-border-button-group);
  --color-border-sidebar-profile-hover: var(--color-border-sidebar-profile-hover);
  --color-separator-border:        var(--color-separator-border);
  --color-separator-border-strong: var(--color-separator-border-strong);
  --color-border-focus-ring:       var(--color-border-focus-ring);

  --color-state-success-text: var(--color-state-success-text);
  --color-state-success-base: var(--color-state-success-base);
  --color-control-indicator-background:        var(--color-control-indicator-background);
  --color-control-indicator-background-subtle: var(--color-control-indicator-background-subtle);
  --color-switch-off-chip-start:                var(--color-switch-off-chip-start);
  --color-switch-off-chip-end:                  var(--color-switch-off-chip-end);
  --color-switch-on-chip-start:                 var(--color-switch-on-chip-start);
  --color-switch-on-chip-end:                   var(--color-switch-on-chip-end);
  --color-stat-card-icon-background:            var(--color-stat-card-icon-background);
  --color-segmented-control-background:          var(--color-segmented-control-background);
  --color-segmented-control-selected-background: var(--color-segmented-control-selected-background);
  --color-theme-toggle-sidebar-background:       var(--color-theme-toggle-sidebar-background);
  --color-theme-toggle-sidebar-selected-background: var(--color-theme-toggle-sidebar-selected-background);
  --color-ai-chat-composer-add-background:       var(--color-ai-chat-composer-add-background);
  --color-ai-chat-composer-add-hover-background: var(--color-ai-chat-composer-add-hover-background);
  --color-composer-panel-tab-background:         var(--color-composer-panel-tab-background);
  --color-composer-panel-add-background:         var(--color-composer-panel-add-background);
  --color-composer-panel-add-hover-background:   var(--color-composer-panel-add-hover-background);
  --color-composer-panel-tile-border:            var(--color-composer-panel-tile-border);
  --color-composer-panel-rail-background:        var(--color-composer-panel-rail-background);
  --color-questionnaire-key-background:          var(--color-questionnaire-key-background);
  --color-questionnaire-key-hover-background:    var(--color-questionnaire-key-hover-background);
  --color-agent-progress-ring:                     var(--color-agent-progress-ring);
  --color-button-ghost-background:                var(--color-button-ghost-background);
  --color-button-ghost-hover:                     var(--color-button-ghost-hover);
  --color-button-ghost-active:                    var(--color-button-ghost-active);
  --color-button-ghost-disabled:                  var(--color-button-ghost-disabled);
  --color-button-ghost-foreground:                var(--color-button-ghost-foreground);
  --color-button-ghost-disabled-foreground:       var(--color-button-ghost-disabled-foreground);
  --color-dropdown-item-hover-background:        var(--color-dropdown-item-hover-background);
  --color-kbd-background:                        var(--color-kbd-background);
  --color-kbd-foreground:                        var(--color-kbd-foreground);
  --color-docs-file-chip-background:             var(--color-docs-file-chip-background);
  --color-docs-file-chip-border:                 var(--color-docs-file-chip-border);
  --color-docs-file-chip-foreground:             var(--color-docs-file-chip-foreground);
  --color-docs-command-accent:                    var(--color-docs-command-accent);
  --color-docs-code-background:                   var(--color-docs-code-background);
  --color-docs-install-rail:                      var(--color-docs-install-rail);
  --color-badge-neutral-background: var(--color-badge-neutral-background);
  --color-team-menu-count-background: var(--color-team-menu-count-background);
  --color-team-menu-count-foreground: var(--color-team-menu-count-foreground);
  --color-badge-new-background: var(--color-badge-new-background);
  --color-badge-new-text:       var(--color-badge-new-text);
  --color-tab-count-selected-background: var(--color-tab-count-selected-background);
  --color-pill-tab-blue-selected-background: var(--color-pill-tab-blue-selected-background);
  --color-pill-tab-blue-hover-background: var(--color-pill-tab-blue-hover-background);
  --color-notification-information-background: var(--color-notification-information-background);
  --color-notification-information-foreground: var(--color-notification-information-foreground);
  --color-notification-success-background:     var(--color-notification-success-background);
  --color-notification-success-foreground:     var(--color-notification-success-foreground);
  --color-notification-error-background:       var(--color-notification-error-background);
  --color-notification-error-foreground:       var(--color-notification-error-foreground);
  --color-notification-center-background:      var(--color-notification-center-background);
  --color-status-lime-background:   var(--color-status-lime-background);
  --color-status-lime-text:         var(--color-status-lime-text);
  --color-status-yellow-background: var(--color-status-yellow-background);
  --color-status-yellow-text:       var(--color-status-yellow-text);
  --color-status-rose-background:   var(--color-status-rose-background);
  --color-status-rose-text:         var(--color-status-rose-text);
  --color-status-cyan-background:   var(--color-status-cyan-background);
  --color-status-cyan-text:         var(--color-status-cyan-text);
  --color-status-orange-background: var(--color-status-orange-background);
  --color-status-orange-text: var(--color-status-orange-text);
  --color-status-blue-background:   var(--color-status-blue-background);
  --color-status-blue-text:         var(--color-status-blue-text);
  --color-status-purple-background: var(--color-status-purple-background);
  --color-status-purple-text:       var(--color-status-purple-text);
  --color-status-dot-green-halo:    var(--color-status-dot-green-halo);
  --color-status-dot-yellow-halo:   var(--color-status-dot-yellow-halo);
  --color-status-dot-indigo-halo:   var(--color-status-dot-indigo-halo);
  --color-date-range-background:      var(--color-date-range-background);
  --color-date-range-edge-background: var(--color-date-range-edge-background);
  --color-calendar-event-blue-background:    var(--color-calendar-event-blue-background);
  --color-calendar-event-blue-title:         var(--color-calendar-event-blue-title);
  --color-calendar-event-blue-time:          var(--color-calendar-event-blue-time);
  --color-calendar-event-pink-background:    var(--color-calendar-event-pink-background);
  --color-calendar-event-pink-title:         var(--color-calendar-event-pink-title);
  --color-calendar-event-pink-time:          var(--color-calendar-event-pink-time);
  --color-calendar-event-purple-background:  var(--color-calendar-event-purple-background);
  --color-calendar-event-purple-title:       var(--color-calendar-event-purple-title);
  --color-calendar-event-purple-time:        var(--color-calendar-event-purple-time);
  --color-calendar-event-lime-background:    var(--color-calendar-event-lime-background);
  --color-calendar-event-lime-title:         var(--color-calendar-event-lime-title);
  --color-calendar-event-lime-time:          var(--color-calendar-event-lime-time);
  --color-calendar-event-emerald-background: var(--color-calendar-event-emerald-background);
  --color-calendar-event-emerald-title:      var(--color-calendar-event-emerald-title);
  --color-calendar-event-emerald-time:       var(--color-calendar-event-emerald-time);

  --color-text-error-placeholder: var(--color-text-error-placeholder);

  --color-chart-track:    var(--color-chart-track);
  --color-chart-cursor:   var(--color-chart-cursor);
  --color-chart-neutral:  var(--color-chart-neutral);
  --color-chart-1:        var(--color-chart-1);
  --color-chart-1-active: var(--color-chart-1-active);
  --color-chart-2:        var(--color-chart-2);
  --color-chart-2-active: var(--color-chart-2-active);
  --color-chart-3:        var(--color-chart-3);
  --color-chart-3-active: var(--color-chart-3-active);
  --color-chart-4:        var(--color-chart-4);
  --color-chart-4-active: var(--color-chart-4-active);
  --color-chart-5:        var(--color-chart-5);
  --color-chart-5-active: var(--color-chart-5-active);
  --color-chart-6:        var(--color-chart-6);
  --color-chart-6-active: var(--color-chart-6-active);
  --color-chart-7:        var(--color-chart-7);
  --color-chart-7-active: var(--color-chart-7-active);
  --color-chart-8:        var(--color-chart-8);
  --color-chart-8-active: var(--color-chart-8-active);
  --color-chart-9:        var(--color-chart-9);
  --color-chart-9-active: var(--color-chart-9-active);
  --color-chart-agents-bar:        var(--color-chart-agents-bar);
  --color-chart-agents-bar-active: var(--color-chart-agents-bar-active);
}

/* ---------------------------------------------------------------------------
 * Component utilities — Buttons
 *
 * Each variant collapses the four interactive states (default / hover / active
 * / disabled) into a single class. Use them on the component:
 *
 *   <button class="bg-button-primary">…</button>
 *   <button class="bg-button-danger">…</button>
 *
 * Smooth default→hover transition trick:
 *   CSS can't interpolate `background-image`, so swapping gradients on :hover
 *   would snap. Instead we keep the *default* gradient on the element and
 *   layer the *hover* gradient on a `::before` pseudo-element whose opacity
 *   fades 0→1 over `--button-transition-ms`. Active/disabled gradients still
 *   swap instantly (we hide the pseudo to reveal them).
 *
 * Stacking: `isolation: isolate` keeps the pseudo behind the button's text
 * and icons (z-index -1) while staying above the element's own background.
 * `border-radius: inherit` + `overflow: hidden` keeps it within the rounded
 * corners.
 * --------------------------------------------------------------------------- */
@theme {
  --button-transition-ms: 150ms;
  --input-transition-ms:  150ms;
}

/* Keep color feedback quick while the physical press motion remains soft.
 * The transform uses a shorter duration on press and the slower duration only
 * when returning to rest, so hover styles never inherit the 700ms timing. */
@utility button-press-motion {
  transition:
    background-color var(--button-transition-ms) ease,
    border-color var(--button-transition-ms) ease,
    box-shadow var(--button-transition-ms) ease,
    color var(--button-transition-ms) ease,
    transform 420ms cubic-bezier(0.4, 0, 0.2, 1);

  &:active:not(:disabled):not([aria-disabled="true"]) {
    transform: scale(0.98);
    transition-duration: 150ms, 150ms, 150ms, 150ms, 220ms;
  }

  &:disabled,
  &[aria-disabled="true"] {
    transform: none;
  }

  @media (prefers-reduced-motion: reduce) {
    transition: none;
    transform: none;
  }
}

@utility bg-button-primary {
  position: relative;
  isolation: isolate;
  background-image: var(--gradient-button-primary-default);

  &::before {
    content: "";
    position: absolute;
    inset: 0;
    z-index: -1;
    pointer-events: none;
    border-radius: inherit;
    background-image: var(--gradient-button-primary-hover);
    opacity: 0;
    transition: opacity var(--button-transition-ms) ease;
  }
  &:hover:not(:disabled):not([aria-disabled="true"])::before {
    opacity: 1;
  }
  &:active:not(:disabled):not([aria-disabled="true"]) {
    background-image: var(--gradient-button-primary-active);
  }
  &:active:not(:disabled):not([aria-disabled="true"])::before {
    opacity: 0;
  }
  &:disabled,
  &[aria-disabled="true"] {
    background-image: var(--gradient-button-primary-disabled);
  }
  &:disabled::before,
  &[aria-disabled="true"]::before {
    display: none;
  }
}

@utility bg-button-danger {
  position: relative;
  isolation: isolate;
  background-image: var(--gradient-button-danger-default);

  &::before {
    content: "";
    position: absolute;
    inset: 0;
    z-index: -1;
    pointer-events: none;
    border-radius: inherit;
    background-image: var(--gradient-button-danger-hover);
    opacity: 0;
    transition: opacity var(--button-transition-ms) ease;
  }
  &:hover:not(:disabled):not([aria-disabled="true"])::before {
    opacity: 1;
  }
  &:active:not(:disabled):not([aria-disabled="true"]) {
    background-image: var(--gradient-button-danger-active);
  }
  &:active:not(:disabled):not([aria-disabled="true"])::before {
    opacity: 0;
  }
  &:disabled,
  &[aria-disabled="true"] {
    background-image: var(--gradient-button-danger-disabled);
  }
  &:disabled::before,
  &[aria-disabled="true"]::before {
    display: none;
  }
}

/* TinyStudio component-design contract: graphite/cobalt; modes share roles. */
:root {
  --color-text-primary: #172330;
  --color-text-secondary: #586a7d;
  --color-text-tertiary: #586a7d;
  --color-text-placeholder: #586a7d;
  --color-background-full: #eef2f6;
  --color-background-primary-default: #ffffff;
  --color-background-secondary-default: #f5f7fa;
  --color-background-secondary-hover: #eaf0fe;
  --color-background-tertiary-default: #eef2f6;
  --color-separator-border: #dce3eb;
  --color-border-table: #dce3eb;
  --color-border-button-default: #cbd5e1;
  --color-border-button-hover: #82909e;
  --color-border-focus-ring: #245bdb;
  --color-accent-50: #eaf0fe;
  --color-accent-100: #dce6ff;
  --color-accent-500: #3268e4;
  --color-accent-600: #245bdb;
  --color-accent-700: #1c49b4;
  --color-chart-1: #245bdb;
  --color-chart-2: #087f8c;
  --color-chart-3: #a46216;
  --color-chart-4: #586a7d;
  --color-chart-5: #be3f39;
}
.dark {
  --color-text-primary: #eaf0f7;
  --color-text-secondary: #b8c7d8;
  --color-text-tertiary: #a5b5c8;
  --color-text-placeholder: #a5b5c8;
  --color-background-full: #111a24;
  --color-background-primary-default: #1b2837;
  --color-background-secondary-default: #202f40;
  --color-background-secondary-hover: #293d56;
  --color-background-tertiary-default: #243447;
  --color-separator-border: #35475d;
  --color-border-table: #35475d;
  --color-border-button-default: #4a5e76;
  --color-border-button-hover: #82909e;
  --color-accent-50: #213a60;
  --color-accent-100: #284775;
  --color-accent-500: #7aa5ff;
  --color-accent-600: #88b0ff;
  --color-accent-700: #a3c1ff;
  --color-chart-1: #88b0ff;
  --color-chart-2: #59c4ca;
  --color-chart-3: #e4b56c;
  --color-chart-4: #b8c7d8;
  --color-chart-5: #f28d87;
}

:root, .dark { --color-button-action: #245bdb; --color-button-action-hover: #1c49b4; }
:root, .dark {
  --color-button-ghost-background: transparent;
  --color-button-ghost-hover: var(--color-background-secondary-hover);
  --color-button-ghost-active: var(--color-background-tertiary-default);
  --color-button-ghost-disabled: transparent;
  --color-button-ghost-foreground: var(--color-text-secondary);
  --color-button-ghost-disabled-foreground: var(--color-text-tertiary);
}
```

## `frontend/src/styles/typography.css`

Complete actual source for local discovery; not an unconditional remote payload.

SHA-256: `dfd4e9b446998d063efe96a47fc31fd2492639bf641d0da1b60645fe4dcc32a1`

```css
/* ---------------------------------------------------------------------------
 * BoardUI typography
 *
 * Type ramp from Figma → text styles. Each style registers a Tailwind v4
 * text utility — `text-{name}` applies font-size + line-height + letter-spacing
 * + font-weight in one go.
 *
 * Family: Inter for the whole ramp. (The Figma export marks Large Title /
 * Regular as "Inter Display"; we map everything to Inter for consistency since
 * Inter ships a variable optical-size axis. Flag if a true Inter Display load
 * is needed.)
 *
 * Weight mapping (mirrors Figma variables — font/weight/*):
 *   Regular   = 400
 *   Medium    = 500
 *   Semibold  = 600  (Figma variable font/weight/semibold = 600)
 *   Bold      = 700
 *
 * Naming: `<family>-<weight>` in lowercase kebab — e.g. `text-body-medium`,
 * `text-title-1-semibold`, `text-caption-2-bold`.
 * --------------------------------------------------------------------------- */

@theme {
  /* ===== Large Title — 64 / 80 ============================================= */
  --text-large-title-regular: 4rem;
  --text-large-title-regular--line-height: 5rem;
  --text-large-title-regular--letter-spacing: 0;
  --text-large-title-regular--font-weight: 400;
  --text-large-title-medium: 4rem;
  --text-large-title-medium--line-height: 5rem;
  --text-large-title-medium--letter-spacing: 0;
  --text-large-title-medium--font-weight: 500;
  --text-large-title-semibold: 4rem;
  --text-large-title-semibold--line-height: 5rem;
  --text-large-title-semibold--letter-spacing: 0;
  --text-large-title-semibold--font-weight: 600;
  --text-large-title-bold: 4rem;
  --text-large-title-bold--line-height: 5rem;
  --text-large-title-bold--letter-spacing: 0;
  --text-large-title-bold--font-weight: 700;

  /* ===== Display 1 — 56 / 72 =============================================== */
  --text-display-1-regular: 3.5rem;
  --text-display-1-regular--line-height: 4.5rem;
  --text-display-1-regular--letter-spacing: 0;
  --text-display-1-regular--font-weight: 400;
  --text-display-1-medium: 3.5rem;
  --text-display-1-medium--line-height: 4.5rem;
  --text-display-1-medium--letter-spacing: 0;
  --text-display-1-medium--font-weight: 500;
  --text-display-1-semibold: 3.5rem;
  --text-display-1-semibold--line-height: 4.5rem;
  --text-display-1-semibold--letter-spacing: 0;
  --text-display-1-semibold--font-weight: 600;
  --text-display-1-bold: 3.5rem;
  --text-display-1-bold--line-height: 4.5rem;
  --text-display-1-bold--letter-spacing: 0;
  --text-display-1-bold--font-weight: 700;

  /* ===== Display 2 — 48 / 64 =============================================== */
  --text-display-2-regular: 3rem;
  --text-display-2-regular--line-height: 4rem;
  --text-display-2-regular--letter-spacing: 0;
  --text-display-2-regular--font-weight: 400;
  --text-display-2-medium: 3rem;
  --text-display-2-medium--line-height: 4rem;
  --text-display-2-medium--letter-spacing: 0;
  --text-display-2-medium--font-weight: 500;
  --text-display-2-semibold: 3rem;
  --text-display-2-semibold--line-height: 4rem;
  --text-display-2-semibold--letter-spacing: 0;
  --text-display-2-semibold--font-weight: 600;
  --text-display-2-bold: 3rem;
  --text-display-2-bold--line-height: 4rem;
  --text-display-2-bold--letter-spacing: 0;
  --text-display-2-bold--font-weight: 700;

  /* ===== Display 3 — 40 / 54 =============================================== */
  --text-display-3-regular: 2.5rem;
  --text-display-3-regular--line-height: 3.375rem;
  --text-display-3-regular--letter-spacing: 0;
  --text-display-3-regular--font-weight: 400;
  --text-display-3-medium: 2.5rem;
  --text-display-3-medium--line-height: 3.375rem;
  --text-display-3-medium--letter-spacing: 0;
  --text-display-3-medium--font-weight: 500;
  --text-display-3-semibold: 2.5rem;
  --text-display-3-semibold--line-height: 3.375rem;
  --text-display-3-semibold--letter-spacing: 0;
  --text-display-3-semibold--font-weight: 600;
  --text-display-3-bold: 2.5rem;
  --text-display-3-bold--line-height: 3.375rem;
  --text-display-3-bold--letter-spacing: 0;
  --text-display-3-bold--font-weight: 700;

  /* ===== Display 4 — 32 / 44 =============================================== */
  --text-display-4-regular: 2rem;
  --text-display-4-regular--line-height: 2.75rem;
  --text-display-4-regular--letter-spacing: 0;
  --text-display-4-regular--font-weight: 400;
  --text-display-4-medium: 2rem;
  --text-display-4-medium--line-height: 2.75rem;
  --text-display-4-medium--letter-spacing: 0;
  --text-display-4-medium--font-weight: 500;
  --text-display-4-semibold: 2rem;
  --text-display-4-semibold--line-height: 2.75rem;
  --text-display-4-semibold--letter-spacing: 0;
  --text-display-4-semibold--font-weight: 600;
  --text-display-4-bold: 2rem;
  --text-display-4-bold--line-height: 2.75rem;
  --text-display-4-bold--letter-spacing: 0;
  --text-display-4-bold--font-weight: 700;

  /* ===== Title 1 — 24 / 34 ================================================= */
  --text-title-1-regular: 1.5rem;
  --text-title-1-regular--line-height: 2.125rem;
  --text-title-1-regular--letter-spacing: 0;
  --text-title-1-regular--font-weight: 400;
  --text-title-1-medium: 1.5rem;
  --text-title-1-medium--line-height: 2.125rem;
  --text-title-1-medium--letter-spacing: 0;
  --text-title-1-medium--font-weight: 500;
  --text-title-1-semibold: 1.5rem;
  --text-title-1-semibold--line-height: 2.125rem;
  --text-title-1-semibold--letter-spacing: 0;
  --text-title-1-semibold--font-weight: 600;
  --text-title-1-bold: 1.5rem;
  --text-title-1-bold--line-height: 2.125rem;
  --text-title-1-bold--letter-spacing: 0;
  --text-title-1-bold--font-weight: 700;

  /* ===== Title 2 — 20 / 26 ================================================= */
  --text-title-2-regular: 1.25rem;
  --text-title-2-regular--line-height: 1.625rem;
  --text-title-2-regular--letter-spacing: 0;
  --text-title-2-regular--font-weight: 400;
  --text-title-2-medium: 1.25rem;
  --text-title-2-medium--line-height: 1.625rem;
  --text-title-2-medium--letter-spacing: 0;
  --text-title-2-medium--font-weight: 500;
  --text-title-2-semibold: 1.25rem;
  --text-title-2-semibold--line-height: 1.625rem;
  --text-title-2-semibold--letter-spacing: 0;
  --text-title-2-semibold--font-weight: 600;
  --text-title-2-bold: 1.25rem;
  --text-title-2-bold--line-height: 1.625rem;
  --text-title-2-bold--letter-spacing: 0;
  --text-title-2-bold--font-weight: 700;

  /* ===== Title 3 — 18 / 24-26 ============================================== */
  --text-title-3-regular: 1.125rem;
  --text-title-3-regular--line-height: 1.5rem;     /* 24 per Figma */
  --text-title-3-regular--letter-spacing: 0;
  --text-title-3-regular--font-weight: 400;
  --text-title-3-medium: 1.125rem;
  --text-title-3-medium--line-height: 1.625rem;    /* 26 per Figma */
  --text-title-3-medium--letter-spacing: 0;
  --text-title-3-medium--font-weight: 500;
  --text-title-3-semibold: 1.125rem;
  --text-title-3-semibold--line-height: 1.625rem;
  --text-title-3-semibold--letter-spacing: 0;
  --text-title-3-semibold--font-weight: 600;
  --text-title-3-bold: 1.125rem;
  --text-title-3-bold--line-height: 1.625rem;
  --text-title-3-bold--letter-spacing: 0;
  --text-title-3-bold--font-weight: 700;

  /* ===== Headline — 16 / 22 ================================================ */
  --text-headline-regular: 1rem;
  --text-headline-regular--line-height: 1.375rem;
  --text-headline-regular--letter-spacing: 0;
  --text-headline-regular--font-weight: 400;
  --text-headline-medium: 1rem;
  --text-headline-medium--line-height: 1.375rem;
  --text-headline-medium--letter-spacing: 0;
  --text-headline-medium--font-weight: 500;
  --text-headline-semibold: 1rem;
  --text-headline-semibold--line-height: 1.375rem;
  --text-headline-semibold--letter-spacing: 0;
  --text-headline-semibold--font-weight: 600;
  --text-headline-bold: 1rem;
  --text-headline-bold--line-height: 1.375rem;
  --text-headline-bold--letter-spacing: 0;
  --text-headline-bold--font-weight: 700;

  /* ===== Body — 14 / 20 ==================================================== */
  --text-body-regular: 0.875rem;
  --text-body-regular--line-height: 1.25rem;
  --text-body-regular--letter-spacing: 0;
  --text-body-regular--font-weight: 400;
  --text-body-medium: 0.875rem;
  --text-body-medium--line-height: 1.25rem;
  --text-body-medium--letter-spacing: 0;
  --text-body-medium--font-weight: 500;
  --text-body-semibold: 0.875rem;
  --text-body-semibold--line-height: 1.25rem;
  --text-body-semibold--letter-spacing: 0;
  --text-body-semibold--font-weight: 600;
  --text-body-bold: 0.875rem;
  --text-body-bold--line-height: 1.25rem;
  --text-body-bold--letter-spacing: 0;
  --text-body-bold--font-weight: 700;

  /* ===== Body 2 — 13 / 18 ================================================== */
  --text-body-2-regular: 0.8125rem;
  --text-body-2-regular--line-height: 1.125rem;
  --text-body-2-regular--letter-spacing: 0;
  --text-body-2-regular--font-weight: 400;
  --text-body-2-medium: 0.8125rem;
  --text-body-2-medium--line-height: 1.125rem;
  --text-body-2-medium--letter-spacing: 0;
  --text-body-2-medium--font-weight: 500;
  --text-body-2-semibold: 0.8125rem;
  --text-body-2-semibold--line-height: 1.125rem;
  --text-body-2-semibold--letter-spacing: 0;
  --text-body-2-semibold--font-weight: 600;
  --text-body-2-bold: 0.8125rem;
  --text-body-2-bold--line-height: 1.125rem;
  --text-body-2-bold--letter-spacing: 0;
  --text-body-2-bold--font-weight: 700;

  /* ===== Caption 1 — 12 / 16 (tracking 0.15) =============================== */
  --text-caption-1-regular: 0.75rem;
  --text-caption-1-regular--line-height: 1rem;
  --text-caption-1-regular--letter-spacing: 0.15px;
  --text-caption-1-regular--font-weight: 400;
  --text-caption-1-medium: 0.75rem;
  --text-caption-1-medium--line-height: 1rem;
  --text-caption-1-medium--letter-spacing: 0.15px;
  --text-caption-1-medium--font-weight: 500;
  --text-caption-1-semibold: 0.75rem;
  --text-caption-1-semibold--line-height: 1rem;
  --text-caption-1-semibold--letter-spacing: 0.15px;
  --text-caption-1-semibold--font-weight: 600;
  --text-caption-1-bold: 0.75rem;
  --text-caption-1-bold--line-height: 1rem;
  --text-caption-1-bold--letter-spacing: 0.15px;
  --text-caption-1-bold--font-weight: 700;

  /* ===== Caption 2 — 11 / 15 (tracking 0.2) ================================ */
  --text-caption-2-regular: 0.6875rem;
  --text-caption-2-regular--line-height: 0.9375rem;
  --text-caption-2-regular--letter-spacing: 0.2px;
  --text-caption-2-regular--font-weight: 400;
  --text-caption-2-medium: 0.6875rem;
  --text-caption-2-medium--line-height: 0.9375rem;
  --text-caption-2-medium--letter-spacing: 0.2px;
  --text-caption-2-medium--font-weight: 500;
  --text-caption-2-semibold: 0.6875rem;
  --text-caption-2-semibold--line-height: 0.9375rem;
  --text-caption-2-semibold--letter-spacing: 0.2px;
  --text-caption-2-semibold--font-weight: 600;
  --text-caption-2-bold: 0.6875rem;
  --text-caption-2-bold--line-height: 0.9375rem;
  --text-caption-2-bold--letter-spacing: 0.2px;
  --text-caption-2-bold--font-weight: 700;
}
```

## `frontend/src/styles/globals.css`

Complete actual source for local discovery; not an unconditional remote payload.

SHA-256: `61eb0d742c68f3fe7914626d1a31f97a9e242f084191098bbc27379b5d683183`

```css
@import "tailwindcss";
@import "./theme.css";
@import "./typography.css";

/* ---------------------------------------------------------------------------
 * Dark mode strategy
 *
 * We use a class-based dark variant. Toggle dark mode by adding the `.dark`
 * class to <html> (a theme provider like `next-themes` can do this later).
 *
 *   <html class="dark">  →  every `dark:*` utility activates
 *
 * Components should NOT use `dark:` prefixes directly. Instead they reference
 * semantic color tokens (e.g. `bg-bg-primary`, `text-text-primary`) that flip
 * under `.dark` in styles/theme.css.
 * --------------------------------------------------------------------------- */
@custom-variant dark (&:where(.dark, .dark *));

/* Resolve the nearest direction boundary, including nested LTR regions.
 * Tailwind's ancestor-based defaults also match inside an opposing region. */
@custom-variant rtl (&:dir(rtl));
@custom-variant ltr (&:dir(ltr));

/* The next theme is revealed by ThemeToggle from the exact interaction point.
 * Native view-transition crossfades are disabled so only the soft circular
 * clip-path animation runs; unsupported browsers simply ignore these rules. */
::view-transition-old(root),
::view-transition-new(root) {
  animation: none;
  mix-blend-mode: normal;
}

@keyframes ai-chat-text-shimmer {
  from {
    background-position: 200% center;
  }

  to {
    background-position: -100% center;
  }
}

.agent-progress-loading-text {
  color: transparent;
  background-image: linear-gradient(
    100deg,
    var(--color-text-secondary) 16%,
    var(--color-text-secondary) 38%,
    var(--color-text-primary) 50%,
    var(--color-text-secondary) 62%,
    var(--color-text-secondary) 84%
  );
  background-position: 200% center;
  background-size: 300% 100%;
  background-clip: text;
  -webkit-background-clip: text;
  animation: ai-chat-text-shimmer 3.4s linear infinite;
  will-change: background-position;
}

@media (prefers-reduced-motion: reduce) {
  .agent-progress-loading-text {
    color: var(--color-text-secondary);
    background-image: none;
    animation: none;
  }
}

::view-transition-old(root) {
  z-index: 1;
}

::view-transition-new(root) {
  z-index: 2;
}

/* Components often have their own hover/focus color transitions. Freeze them
 * while the browser captures the next theme so the expanding circle contains
 * the fully resolved new palette instead of a frame that is still fading from
 * the old one. This is what makes the click-origin reveal read clearly. */
.theme-transitioning,
.theme-transitioning * {
  transition: none !important;
}

/* Reset + base */
html,
body {
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-variant-ligatures: contextual;
  font-kerning: normal;
}

html {
  color-scheme: light;
  background-color: var(--color-background-full);
}
html.dark {
  color-scheme: dark;
}
body {
  background-color: var(--color-background-full);
  color: var(--color-text-primary);
}

/* Theme-aware monochrome BoardUI mark. Both images keep their intrinsic
 * dimensions while the root theme class decides which one participates in
 * layout, avoiding a light-logo flash during hydration. */
.theme-logo-dark,
.theme-asset-dark {
  display: none;
}
.dark .theme-logo-light,
.dark .theme-asset-light {
  display: none;
}
.dark .theme-logo-dark,
.dark .theme-asset-dark {
  display: block;
}

/* Tailwind expands shadow utilities into `--tw-shadow` at build time. Point
 * those generated utilities back to the live dark elevation tokens so the
 * class-based theme can adjust opacity without component-level overrides. */
.dark .shadow-2xs {
  --tw-shadow: var(--shadow-2xs);
}
.dark .shadow-xs {
  --tw-shadow: var(--shadow-xs);
}
.dark .shadow-sm {
  --tw-shadow: var(--shadow-sm);
}
.dark .shadow-md {
  --tw-shadow: var(--shadow-md);
}
.dark .shadow-lg {
  --tw-shadow: var(--shadow-lg);
}
.dark .shadow-xl {
  --tw-shadow: var(--shadow-xl);
}
.dark .shadow-card {
  --tw-shadow: var(--shadow-card);
}
.dark .shadow-dropdown {
  --tw-shadow: var(--shadow-dropdown);
}
.dark .shadow-sidebar {
  --tw-shadow: var(--shadow-sidebar);
}
.dark .shadow-waitlist {
  --tw-shadow: var(--shadow-waitlist);
}

/* Hide default expand arrow on Safari */
details summary::-webkit-details-marker {
  display: none;
}

/* Hide default number-input spinners */
input::-webkit-outer-spin-button,
input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
input[type="number"] {
  -moz-appearance: textfield;
}

/* Hide default search clear button */
input[type="search"]::-webkit-search-cancel-button {
  -webkit-appearance: none;
}

/* iOS Safari auto-zooms the page when a focused form field's font-size is
 * under 16px — our text-body-regular/medium tokens are 14px by design, so
 * bump just the font-size (not the whole token) on small screens instead of
 * changing the design system's type scale. */
@media (max-width: 639px) {
  input,
  select,
  textarea {
    font-size: 16px;
  }
}

/* Soft fade applied to an animated number each time its target changes */
@keyframes number-fade {
  from {
    opacity: 0.35;
  }
  to {
    opacity: 1;
  }
}
.animate-number-fade {
  animation: number-fade 220ms ease-out;
}

/* Recharts' accessibility layer makes the chart surface and its shapes
 * (bars, sectors, dots) keyboard-focusable, and Chrome paints the UA focus
 * outline around whichever one you click - a blue box around a ring segment
 * or a bar. Every chart card drives its own focus feedback (hover darkening,
 * headline swap), so the outline is noise: hide it across every Recharts
 * chart. Keyboard users still get the cards' own active states. */
.recharts-wrapper {
  /* SVG axes use physical coordinates. Keep their data and numeric labels in
   * order while card headers, legends and surrounding controls follow RTL. */
  direction: ltr;
}

.recharts-wrapper:focus,
.recharts-wrapper:focus-visible,
.recharts-wrapper :focus,
.recharts-wrapper :focus-visible {
  outline: none;
}

/* Liquid glass (landing nav + hero) — the rim light.
 *
 * A fixed conic gradient standing in for a bevel lit from a single direction:
 * bright along the top edge where the light source is, dark down both sides
 * where the glass turns away, and a weaker bounce along the bottom. It does
 * not rotate — a travelling highlight reads as an animation playing on the
 * control, where a real edge just sits there catching the room.
 *
 * Achromatic on purpose: highlights are white, shading is black, nothing in
 * between carries a hue.
 *
 * The two-layer mask (padding-box XOR full box) keeps only the 1.5px ring,
 * letting the gradient paint the stroke without covering the glass. */

.landing-glass-stroke {
  pointer-events: none;
  position: absolute;
  inset: 0;
  border-radius: inherit;
  /* Every value here is set inline by `LiquidGlassSurface` from the shared
   * glass config; the fallbacks are only for a bare use of the class. */
  padding: var(--lg-rim-width, 1.5px);
  background: conic-gradient(
    from var(--lg-rim-angle, 0deg),
    var(--lg-rim-hi, rgb(255 255 255 / 0.9)) 0deg,
    var(--lg-rim-sh, rgb(0 0 0 / 0.14)) 70deg,
    var(--lg-rim-dim, rgb(255 255 255 / 0.1)) 140deg,
    var(--lg-rim-bounce, rgb(255 255 255 / 0.55)) 180deg,
    var(--lg-rim-dim, rgb(255 255 255 / 0.1)) 220deg,
    var(--lg-rim-sh, rgb(0 0 0 / 0.14)) 290deg,
    var(--lg-rim-hi, rgb(255 255 255 / 0.9)) 360deg
  );
  /* Shorthands first, composites last: `-webkit-mask` aliases `mask` in
   * Chromium, so a shorthand placed after a composite silently resets it to
   * `add` — and an un-composited mask paints the full conic wash over the
   * button instead of a ring. */
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  opacity: var(--lg-rim-o, 1);
  transition: opacity var(--lg-ms, 200ms) ease;
}

/* Liquid glass hover.
 *
 * Resting and hover values arrive together as inline custom properties, and
 * the swap happens here in CSS rather than in React state — hover then costs
 * a style recalculation instead of a re-render of everything inside the glass.
 * `.lg-hover` is the opt-in: a surface without it keeps its resting value. */
.lg-tint {
  background-color: var(--lg-tint, transparent);
  transition: background-color var(--lg-ms, 200ms) ease;
}

.lg-sheen {
  opacity: var(--lg-sheen, 0.5);
  transition: opacity var(--lg-ms, 200ms) ease;
}

.lg-host {
  box-shadow: var(--lg-shadow, none);
  transition:
    box-shadow var(--lg-ms, 200ms) ease,
    transform var(--lg-ms, 200ms) ease;
}

.lg-host:hover {
  box-shadow: var(--lg-shadow-hover, var(--lg-shadow, none));
  transform: translateY(var(--lg-lift, 0px)) scale(var(--lg-scale, 1));
}

.group:hover .lg-tint.lg-hover {
  background-color: var(--lg-tint-hover, var(--lg-tint, transparent));
}

.group:hover .lg-sheen.lg-hover {
  opacity: var(--lg-sheen-hover, var(--lg-sheen, 0.5));
}

.group:hover .landing-glass-stroke.lg-hover {
  opacity: var(--lg-rim-o-hover, var(--lg-rim-o, 1));
}

@media (prefers-reduced-motion: reduce) {
  .lg-tint,
  .lg-sheen,
  .lg-host,
  .landing-glass-stroke {
    transition: none;
  }

  .lg-host:hover {
    transform: none;
  }
}

/* Split-flap ("Solari" board) digit, used by the image-generation countdown.
 * Two leaves hinged on the card's centre seam:
 *   flap-fall  the outgoing digit's TOP half swings down over the incoming
 *              bottom half. Eased IN, because a real flap is falling under
 *              gravity — it accelerates — and it darkens as it tips away
 *              from the light.
 *   flap-rise  the incoming digit's BOTTOM half swings up from edge-on,
 *              starting exactly when the fall lands (delay = fall duration,
 *              `both` so it waits at 90deg instead of flashing flat). The
 *              small overshoot past 0deg is the slap against the stop —
 *              that bounce is what makes it read mechanical rather than
 *              like a plain rotation. */
@keyframes flap-fall {
  from {
    transform: rotateX(0deg);
    filter: brightness(1);
  }
  to {
    transform: rotateX(-90deg);
    filter: brightness(0.86);
  }
}
@keyframes flap-rise {
  0% {
    transform: rotateX(90deg);
    filter: brightness(0.86);
  }
  70% {
    transform: rotateX(-9deg);
  }
  86% {
    transform: rotateX(4deg);
  }
  100% {
    transform: rotateX(0deg);
    filter: brightness(1);
  }
}
.animate-flap-fall {
  transform-origin: bottom center;
  backface-visibility: hidden;
  animation: flap-fall 150ms cubic-bezier(0.55, 0, 0.9, 0.35) forwards;
}
.animate-flap-rise {
  transform-origin: top center;
  backface-visibility: hidden;
  animation: flap-rise 230ms cubic-bezier(0.25, 0.7, 0.35, 1) 150ms both;
}

@media (prefers-reduced-motion: reduce) {
  .animate-flap-fall,
  .animate-flap-rise {
    animation-duration: 1ms;
    animation-delay: 0ms;
  }
}

/* New-row entrance for the Storage settings table. Three coordinated
 * pieces (wrapper pairs with `grid grid-rows-[1fr]` + an overflow-hidden
 * middle layer, content carries the slide):
 *   row-grow-in   wrapper's grid track 0fr → 1fr — pushes the rows below
 *                 down smoothly.
 *   row-slide-in  content translates -100% → 0 with the SAME duration and
 *                 easing, so it stays glued to the growing wrapper's bottom
 *                 edge — visually the row slides out from underneath the
 *                 header row above it.
 *   row-fade-in   opacity/blur on a SHORTER clock than the movement — the
 *                 row is fully visible before it finishes settling (fades
 *                 must complete before movement ends). */
@keyframes row-grow-in {
  from {
    grid-template-rows: 0fr;
  }
  to {
    grid-template-rows: 1fr;
  }
}
@keyframes row-slide-in {
  from {
    translate: 0 -100%;
  }
}
@keyframes row-fade-in {
  from {
    opacity: 0;
    filter: blur(2px);
  }
}
.animate-row-grow-in {
  animation: row-grow-in 400ms ease-out;
}
.animate-row-slide-in {
  animation:
    row-slide-in 400ms ease-out,
    row-fade-in 240ms ease-out;
}

/* Reverse of the Storage row entrance. The content rises into the clipped
 * top edge while its grid track collapses, so the following rows move upward
 * continuously and the deleted row disappears beneath the header surface. */
@keyframes row-collapse-out {
  from {
    grid-template-rows: 1fr;
  }
  to {
    grid-template-rows: 0fr;
  }
}
@keyframes row-slide-out {
  to {
    translate: 0 -100%;
  }
}
@keyframes row-fade-out {
  to {
    opacity: 0;
    filter: blur(2px);
  }
}
.animate-row-collapse-out {
  animation: row-collapse-out 225ms ease-in forwards;
}
.animate-row-slide-out {
  animation:
    row-slide-out 225ms ease-in forwards,
    row-fade-out 160ms ease-in forwards;
}

@media (prefers-reduced-motion: reduce) {
  .animate-row-grow-in,
  .animate-row-slide-in,
  .animate-row-collapse-out,
  .animate-row-slide-out {
    animation-duration: 1ms;
  }
}

/* Transitions.dev — "Texts reveal", used by the Storage dropzone's content
 * swaps (idle copy ↔ file name + status line).
 *
 * Entrance (`.is-shown` on the parent): lines rise 12px from below while
 * un-blurring, 500ms on a soft-landing bezier, staggered 40ms per line so
 * the eye lands on the first line first.
 * Exit (`.is-hiding`): a single quiet 200ms opacity fade in place — no Y
 * return, no blur, no stagger — so the disappearance never reads as a
 * reverse reveal.
 *
 * Deviations from the original snippet: custom props are scoped to
 * `.t-stagger` instead of :root, and `.t-stagger-line` doesn't force
 * `display: block` so flex-based lines (the icon disc) keep their layout. */
.t-stagger {
  --stagger-dur: 500ms;
  --stagger-distance: 12px;
  --stagger-stagger: 40ms;
  --stagger-blur: 3px;
  --stagger-ease: cubic-bezier(0.22, 1, 0.36, 1);
}
.t-stagger-line {
  opacity: 0;
  transform: translateY(var(--stagger-distance));
  filter: blur(var(--stagger-blur));
  transition:
    opacity var(--stagger-dur) var(--stagger-ease),
    transform var(--stagger-dur) var(--stagger-ease),
    filter var(--stagger-dur) var(--stagger-ease);
  will-change: transform, opacity, filter;
}
.t-stagger-line--2 {
  transition-delay: var(--stagger-stagger);
}
.t-stagger-line--3 {
  transition-delay: calc(var(--stagger-stagger) * 2);
}
.t-stagger.is-shown .t-stagger-line {
  opacity: 1;
  transform: translateY(0);
  filter: blur(0);
}
.t-stagger.is-hiding .t-stagger-line {
  opacity: 0;
  transform: translateY(0);
  filter: blur(0);
  transition:
    opacity 200ms ease,
    transform 0s linear,
    filter 0s linear;
  transition-delay: 0s;
}
@media (prefers-reduced-motion: reduce) {
  .t-stagger-line {
    transition: none !important;
  }
}

/* Rolling-counter label swap: the old value lifts 5px and fades out while the
 * new one rises in from 5px below and fades in. */
@keyframes label-out {
  from {
    transform: translateY(0);
    opacity: 1;
  }
  to {
    transform: translateY(-10px);
    opacity: 0;
  }
}
@keyframes label-in {
  from {
    transform: translateY(10px);
    opacity: 0;
  }
  to {
    transform: translateY(0);
    opacity: 1;
  }
}
.animate-label-out {
  animation: label-out 220ms ease-out forwards;
}
.animate-label-in {
  animation: label-in 220ms ease-out;
}

/* Checkbox tick: draws the stroke on like it's being ticked by hand.
 * The path sets pathLength=1 so the dash math is resolution-independent. */
@keyframes check-draw {
  from {
    stroke-dashoffset: 1;
  }
  to {
    stroke-dashoffset: 0;
  }
}
.animate-check-draw {
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  animation: check-draw 200ms cubic-bezier(0.65, 0, 0.35, 1) forwards;
}
@media (prefers-reduced-motion: reduce) {
  .animate-check-draw {
    animation: none;
    stroke-dashoffset: 0;
  }
}

/* ---------------------------------------------------------------------------
 * Chart entrance animations (AI profile template & contributions heatmap)
 * ------------------------------------------------------------------------- */

/* Heatmap cells pop in softly — each cell gets a random-looking per-cell
 * animation-delay so the grid fills in a scattered order. */
.contributions-grid {
  --contribution-tier-0: var(--color-chart-neutral);
}
.contributions-grid[data-accent="emerald"] {
  --contribution-tier-1: var(--color-emerald-200); --contribution-tier-2: var(--color-emerald-400); --contribution-tier-3: var(--color-emerald-500); --contribution-tier-4: var(--color-emerald-600); --contribution-tier-5: var(--color-emerald-700);
}
.contributions-grid[data-accent="green"] {
  --contribution-tier-1: var(--color-green-200); --contribution-tier-2: var(--color-green-400); --contribution-tier-3: var(--color-green-500); --contribution-tier-4: var(--color-green-600); --contribution-tier-5: var(--color-green-700);
}
.contributions-grid[data-accent="teal"] {
  --contribution-tier-1: var(--color-teal-200); --contribution-tier-2: var(--color-teal-400); --contribution-tier-3: var(--color-teal-500); --contribution-tier-4: var(--color-teal-600); --contribution-tier-5: var(--color-teal-700);
}
.contributions-grid[data-accent="cyan"] {
  --contribution-tier-1: var(--color-cyan-200); --contribution-tier-2: var(--color-cyan-400); --contribution-tier-3: var(--color-cyan-500); --contribution-tier-4: var(--color-cyan-600); --contribution-tier-5: var(--color-cyan-700);
}
.contributions-grid[data-accent="blue"] {
  --contribution-tier-1: var(--color-blue-200); --contribution-tier-2: var(--color-blue-400); --contribution-tier-3: var(--color-blue-500); --contribution-tier-4: var(--color-blue-600); --contribution-tier-5: var(--color-blue-700);
}
.contributions-grid[data-accent="indigo"] {
  --contribution-tier-1: var(--color-indigo-200); --contribution-tier-2: var(--color-indigo-400); --contribution-tier-3: var(--color-indigo-500); --contribution-tier-4: var(--color-indigo-600); --contribution-tier-5: var(--color-indigo-700);
}
.contributions-grid[data-accent="violet"] {
  --contribution-tier-1: var(--color-violet-200); --contribution-tier-2: var(--color-violet-400); --contribution-tier-3: var(--color-violet-500); --contribution-tier-4: var(--color-violet-600); --contribution-tier-5: var(--color-violet-700);
}
.contributions-grid[data-accent="rose"] {
  --contribution-tier-1: var(--color-rose-200); --contribution-tier-2: var(--color-rose-400); --contribution-tier-3: var(--color-rose-500); --contribution-tier-4: var(--color-rose-600); --contribution-tier-5: var(--color-rose-700);
}
.contributions-grid[data-accent="amber"] {
  --contribution-tier-1: var(--color-amber-200); --contribution-tier-2: var(--color-amber-400); --contribution-tier-3: var(--color-amber-500); --contribution-tier-4: var(--color-amber-600); --contribution-tier-5: var(--color-amber-700);
}

.dark .contributions-grid[data-accent="emerald"] {
  --contribution-tier-1: var(--color-emerald-950); --contribution-tier-2: var(--color-emerald-800); --contribution-tier-3: var(--color-emerald-700); --contribution-tier-4: var(--color-emerald-600); --contribution-tier-5: var(--color-emerald-500);
}
.dark .contributions-grid[data-accent="green"] {
  --contribution-tier-1: var(--color-green-950); --contribution-tier-2: var(--color-green-800); --contribution-tier-3: var(--color-green-700); --contribution-tier-4: var(--color-green-600); --contribution-tier-5: var(--color-green-500);
}
.dark .contributions-grid[data-accent="teal"] {
  --contribution-tier-1: var(--color-teal-950); --contribution-tier-2: var(--color-teal-800); --contribution-tier-3: var(--color-teal-700); --contribution-tier-4: var(--color-teal-600); --contribution-tier-5: var(--color-teal-500);
}
.dark .contributions-grid[data-accent="cyan"] {
  --contribution-tier-1: var(--color-cyan-950); --contribution-tier-2: var(--color-cyan-800); --contribution-tier-3: var(--color-cyan-700); --contribution-tier-4: var(--color-cyan-600); --contribution-tier-5: var(--color-cyan-500);
}
.dark .contributions-grid[data-accent="blue"] {
  --contribution-tier-1: var(--color-blue-950); --contribution-tier-2: var(--color-blue-800); --contribution-tier-3: var(--color-blue-700); --contribution-tier-4: var(--color-blue-600); --contribution-tier-5: var(--color-blue-500);
}
.dark .contributions-grid[data-accent="indigo"] {
  --contribution-tier-1: var(--color-indigo-950); --contribution-tier-2: var(--color-indigo-800); --contribution-tier-3: var(--color-indigo-700); --contribution-tier-4: var(--color-indigo-600); --contribution-tier-5: var(--color-indigo-500);
}
.dark .contributions-grid[data-accent="violet"] {
  --contribution-tier-1: var(--color-violet-950); --contribution-tier-2: var(--color-violet-800); --contribution-tier-3: var(--color-violet-700); --contribution-tier-4: var(--color-violet-600); --contribution-tier-5: var(--color-violet-500);
}
.dark .contributions-grid[data-accent="rose"] {
  --contribution-tier-1: var(--color-rose-950); --contribution-tier-2: var(--color-rose-800); --contribution-tier-3: var(--color-rose-700); --contribution-tier-4: var(--color-rose-600); --contribution-tier-5: var(--color-rose-500);
}
.dark .contributions-grid[data-accent="amber"] {
  --contribution-tier-1: var(--color-amber-950); --contribution-tier-2: var(--color-amber-800); --contribution-tier-3: var(--color-amber-700); --contribution-tier-4: var(--color-amber-600); --contribution-tier-5: var(--color-amber-500);
}

.contribution-cell[data-tier="0"] { background-color: var(--contribution-tier-0); }
.contribution-cell[data-tier="1"] { background-color: var(--contribution-tier-1); }
.contribution-cell[data-tier="2"] { background-color: var(--contribution-tier-2); }
.contribution-cell[data-tier="3"] { background-color: var(--contribution-tier-3); }
.contribution-cell[data-tier="4"] { background-color: var(--contribution-tier-4); }
.contribution-cell[data-tier="5"] { background-color: var(--contribution-tier-5); }

@keyframes cell-pop {
  from {
    opacity: 0;
    transform: scale(0.5);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}
.animate-cell-pop {
  animation: cell-pop 380ms ease-out both;
}

/* Bars grow up from the baseline; a per-bar delay staggers them left→right. */
@keyframes bar-rise {
  from {
    transform: scaleY(0);
  }
  to {
    transform: scaleY(1);
  }
}
.animate-bar-rise {
  transform-origin: bottom;
  animation: bar-rise 360ms cubic-bezier(0.22, 1, 0.36, 1) both;
}

/* Line chart draws in left→right via a clip-path sweep over the whole plot
 * (line + gradient area together). */
@keyframes chart-reveal {
  from {
    clip-path: inset(0 100% 0 0);
  }
  to {
    clip-path: inset(0 0 0 0);
  }
}
.animate-chart-reveal {
  animation: chart-reveal 1800ms cubic-bezier(0.33, 0, 0.15, 1) both;
}

@media (prefers-reduced-motion: reduce) {
  .animate-cell-pop,
  .animate-bar-rise,
  .animate-chart-reveal {
    animation: none;
  }
}

/* ---------------------------------------------------------------------------
 * Table primitive (components/base/table)
 *
 * React Aria's collection components (Column/Row/Cell/…) can't be wrapped in
 * custom components without losing collection behaviour, so the primitive
 * re-exports them untouched and styling lives here, scoped to `.bui-table`.
 * React Aria renders real <table>/<thead>/<th>/<tr>/<td> elements.
 * --------------------------------------------------------------------------- */
.bui-table {
  @apply w-full border-collapse text-start;
}
/* Header background + border-y live on the cells: with border-collapse the
 * browser doesn't paint backgrounds/borders set on the <thead> row group.
 * Backgrounds/borders are set with explicit CSS (theme vars) rather than
 * @apply, which drops border-width/bg on these row-group cells. */
.bui-table th {
  @apply px-3 py-2.5 text-start align-middle text-body-medium text-text-tertiary whitespace-nowrap outline-none;
  background-color: var(--color-background-secondary-default);
  border-top: 1px solid var(--color-separator-border);
  border-bottom: 1px solid var(--color-separator-border);
  transition: padding 200ms ease, font-size 200ms ease;
}
.bui-table td {
  @apply px-3 py-2.5 align-middle text-body-medium text-text-primary outline-none;
  transition: padding 200ms ease, font-size 200ms ease;
}
/* Separators sit *between* rows only: the trailing row's boundary is drawn by
 * whatever follows the table (the container's bottom edge, or the pagination
 * footer's own border-top), so a filtered-down table never doubles up lines. */
.bui-table tbody tr:not(:last-child) {
  border-bottom: 1px solid var(--color-separator-border);
}
.bui-table tbody tr {
  @apply transition-colors duration-150;
}
.bui-table tbody tr[data-focus-visible] {
  @apply ring-2 ring-inset ring-border-focus-ring;
}
.bui-table.bui-table-sm th,
.bui-table.bui-table-sm td {
  @apply px-2.5 py-1.5 text-body-2-medium;
}

/* Agent Thinking (components/application/agent-thinking): the agent thinking state
 * above a chat composer. The label reuses the ai-chat-text-shimmer travel;
 * its base/highlight both derive from the tone the component sets via
 * --bui-agent-thinking-tone, so every tone shimmers consistently. */
.bui-agent-thinking-label {
  color: transparent;
  background-image: linear-gradient(
    100deg,
    color-mix(in oklab, var(--bui-agent-thinking-tone) 55%, transparent) 30%,
    var(--bui-agent-thinking-tone) 50%,
    color-mix(in oklab, var(--bui-agent-thinking-tone) 55%, transparent) 70%
  );
  background-position: 200% center;
  background-size: 300% 100%;
  background-clip: text;
  -webkit-background-clip: text;
  animation: ai-chat-text-shimmer 2.6s linear infinite;
  will-change: background-position;
}

/* Star sparkle: scale + fade in, settle, and vanish; per-star duration and
 * stagger come in via inline animation-duration/-delay. */
@keyframes bui-agent-thinking-star {
  0%,
  100% {
    transform: scale(0);
    opacity: 0;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
  60% {
    transform: scale(0.8);
    opacity: 0.9;
  }
}

.bui-agent-thinking-star {
  transform: scale(0);
  animation-name: bui-agent-thinking-star;
  animation-timing-function: ease-in-out;
  animation-iteration-count: infinite;
}

/* Infinity comet: the dash segment laps the normalized (pathLength=100)
 * figure-eight once per animation-duration. */
@keyframes bui-agent-thinking-dash {
  to {
    stroke-dashoffset: -100;
  }
}

.bui-agent-thinking-comet {
  animation-name: bui-agent-thinking-dash;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
}

@media (prefers-reduced-motion: reduce) {
  .bui-agent-thinking-label {
    color: var(--bui-agent-thinking-tone);
    background-image: none;
    animation: none;
  }

  /* Stars rest visible instead of frozen at scale(0). */
  .bui-agent-thinking-star {
    animation: none;
    transform: none;
    opacity: 0.7;
  }

  .bui-agent-thinking-comet {
    animation: none;
  }
}

/* Star glyphs read a touch heavier than the dot grids on light backgrounds;
 * soften only them (not the label) there. Dark mode keeps the full tone. */
.bui-agent-thinking-stars {
  color: color-mix(in oklab, currentColor 75%, transparent);
}

.dark .bui-agent-thinking-stars {
  color: inherit;
}

/* Composer Loader (components/application/composer-loader): the light band
 * orbiting the composer while the agent works. Dash segments on a
 * pathLength-normalized SVG stroke — the offset laps once per duration. */
@keyframes bui-composer-loader-dash {
  from {
    stroke-dashoffset: 0;
  }

  to {
    stroke-dashoffset: -100;
  }
}

@media (prefers-reduced-motion: reduce) {
  .bui-composer-loader-rect {
    animation: none !important;
  }
}

/* Composer mic listening state (components/application/ai-chat): equalizer
 * bars dancing while voice input is active. Per-bar duration/delay come in
 * inline so the bars drift out of phase like real audio. */
@keyframes bui-composer-mic-bars {
  0%,
  100% {
    transform: scaleY(0.4);
  }

  50% {
    transform: scaleY(1);
  }
}

.bui-composer-mic-bar {
  animation: bui-composer-mic-bars 0.9s ease-in-out infinite;
}

@media (prefers-reduced-motion: reduce) {
  .bui-composer-mic-bar {
    animation: none;
    transform: scaleY(0.7);
  }
}

/* Shimmer sweep on a primary call to action (the Pro prompt card). */
@keyframes bui-badge-shimmer {
  0% {
    transform: translateX(0) skewX(-12deg);
  }
  40%,
  100% {
    transform: translateX(350%) skewX(-12deg);
  }
}

.bui-badge-shimmer {
  animation: bui-badge-shimmer 2.5s ease-in-out infinite;
}

@media (prefers-reduced-motion: reduce) {
  .bui-badge-shimmer {
      animation: none;
      opacity: 0;
    }
}

/* Showcase card (docs index, landing, the starter's catalogue). */
.landing-showcase-card {
  transition: box-shadow 280ms ease;
}

.landing-showcase-card::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 60;
  pointer-events: none;
  border: 1px solid var(--color-border-button-default);
  border-radius: inherit;
  transition:
    border-color 280ms ease,
    box-shadow 280ms ease;
}

.dark .landing-showcase-card::after {
  border-color: rgb(255 255 255 / 0.08);
}

@media (min-width: 640px) and (hover: hover) {
  .landing-showcase-card:hover::after,
  .landing-showcase-card:focus-within::after {
    border-color: var(--color-border-button-hover);
    box-shadow: inset 0 0 0 2px var(--color-border-button-hover);
  }
}
```

## `frontend/src/styles/studio.css`

Complete actual source for local discovery; not an unconditional remote payload.

SHA-256: `ae32fea6f3c8e82ff53e2b9d05c01529fa9fd15f0cf521d12182604034feb3b3`

```css
/* Shared base styles; semantic theme lives in theme.css. */
:root {
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  color: var(--color-text-primary);
  background: var(--color-background-full);
  font-synthesis: none;
}
body {
  margin: 0;
}
* {
  box-sizing: border-box;
}
button,
input,
select,
textarea {
  font: inherit;
}
a {
  color: var(--color-accent-600);
}
h1,
h2,
h3,
p {
  margin-block-start: 0;
}
h1 {
  font-size: 1.375rem;
  line-height: 1.25;
  letter-spacing: -0.035em;
}
h2 {
  font-size: 1rem;
}
p {
  line-height: 1.6;
}
button,
a,
input,
select,
textarea {
  outline-offset: 3px;
}
input,
select,
textarea {
  width: 100%;
  min-height: 2.5rem;
  border: 1px solid var(--color-border-button-default);
  border-radius: 0.375rem;
  background: var(--color-background-primary-default);
  color: var(--color-text-primary);
  padding: 0.55rem 0.75rem;
}
textarea {
  min-height: 6rem;
  resize: vertical;
}
input[type="checkbox"],
input[type="radio"] {
  width: auto;
  min-height: 0;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}
th,
td {
  padding: 0.7rem;
  text-align: start;
  border-bottom: 1px solid var(--color-border-table);
}
th {
  color: var(--color-text-secondary);
  font-weight: 600;
}
pre,
code {
  font-family: ui-monospace, monospace;
  font-size: 0.8rem;
}
pre {
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
svg {
  max-width: 100%;
}
.studio-shell {
  display: grid;
  grid-template-columns: 184px minmax(0, 1fr);
  height: 100dvh;
  grid-template-rows: minmax(0, 1fr) auto;
  overflow: hidden;
}
.studio-sidebar {
  grid-row: 1 / -1;
  position: sticky;
  top: 0;
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 0.75rem 0.5rem;
  border-inline-end: 1px solid var(--color-separator-border);
  background: var(--color-background-primary-default);
}
.brand {
  font-size: 1rem;
  font-weight: 650;
  letter-spacing: -0.035em;
  color: var(--color-text-primary);
  text-decoration: none;
}
.project-label,
.studio-sidebar footer label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  display: grid;
  gap: 0.4rem;
}
.studio-sidebar nav {
  display: grid;
  gap: 0.75rem;
}
.studio-sidebar nav h2 {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-text-tertiary);
  margin: 0.25rem 0.7rem;
}
.studio-sidebar nav a {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2rem;
  padding: 0.3rem 0.5rem;
  margin: 0.1rem 0;
  border-radius: 0.25rem;
  text-decoration: none;
  font-size: 0.84rem;
  color: var(--color-text-secondary);
}
.studio-sidebar nav a:hover {
  background: var(--color-background-secondary-hover);
}
.studio-sidebar nav a.active {
  background: var(--color-accent-50);
  color: var(--color-accent-700);
  font-weight: 600;
}
.studio-sidebar footer {
  display: grid;
  gap: 0.75rem;
  margin-block-start: auto;
}
.studio-shell > main {
  grid-column: 2;
  grid-row: 1;
  min-width: 0;
  padding: 1.25rem 1.5rem;
  overflow: auto;
  height: 100%;
  outline: none;
}
.studio-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 1rem;
  min-width: 0;
}
.studio-page > * {
  min-width: 0;
}
.page-heading > *,
.panel-heading > * {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.page-heading,
.panel-heading,
.actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.page-heading h1 {
  margin-block-end: 0.5rem;
}
.page-heading p {
  margin: 0;
  max-width: 65ch;
}
.studio-panel {
  background: var(--color-background-primary-default);
  border: 1px solid var(--color-border-button-default);
  border-radius: 0.375rem;
  padding: 1rem;
  min-width: 0;
  overflow-x: auto;
}
.panel-heading {
  margin-block-end: 1rem;
}
.panel-heading h2 {
  margin: 0;
}
.muted {
  color: var(--color-text-tertiary);
}
.error,
[role="alert"] {
  color: var(--color-text-error-primary);
}
.studio-field {
  display: grid;
  gap: 0.4rem;
  margin-block-end: 1rem;
}
.studio-field > label {
  font-size: 0.8rem;
  font-weight: 600;
}
.empty-state {
  text-align: center;
  padding: 3rem 1rem;
  color: var(--color-text-tertiary);
}
.badge {
  display: inline-flex;
  padding: 0.2rem 0.55rem;
  border-radius: 2rem;
  font-size: 0.72rem;
  background: var(--color-background-secondary-default);
  color: var(--color-text-secondary);
}
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: var(--color-background-overlay, rgba(0, 0, 0, 0.4));
}
.studio-modal {
  background: var(--color-background-primary-default);
  border: 1px solid var(--color-border-button-default);
  border-radius: 1.25rem;
  width: min(100%, 32rem);
  max-height: calc(100dvh - 2rem);
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-xl);
}
.studio-modal.wide {
  width: min(100%, 64rem);
}
.studio-modal > header,
.studio-modal > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  gap: 1rem;
}
.studio-modal > header h2 {
  margin: 0;
}
.studio-modal > footer {
  justify-content: flex-end;
  border-block-start: 1px solid var(--color-separator-border);
}
.modal-content {
  padding: 1rem 1.25rem;
  overflow-y: auto;
}
.task-item {
  border-block-end: 1px solid var(--color-separator-border);
  padding-block: 1rem;
}
.auth-layout {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 1rem;
}
.auth-card {
  width: min(100%, 26rem);
  padding: 2rem;
  background: var(--color-background-primary-default);
  border: 1px solid var(--color-border-button-default);
  border-radius: 1.5rem;
}
.auth-card > .brand {
  display: block;
  margin-block-end: 2rem;
}
.auth-card h1 {
  font-size: 1.4rem;
}
.toast-region {
  position: fixed;
  inset-block-end: 1rem;
  inset-inline-end: 1rem;
  z-index: 200;
  display: grid;
  gap: 0.5rem;
  max-width: min(90vw, 28rem);
}
.toast {
  padding: 0.75rem 1rem;
  border-radius: 0.75rem;
  background: var(--color-background-primary-default);
  border: 1px solid var(--color-border-button-default);
  box-shadow: var(--shadow-md);
  display: flex;
  gap: 1rem;
  justify-content: space-between;
}
.toast button {
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
}
.skip-link {
  position: fixed;
  inset-block-start: -100px;
  z-index: 300;
  padding: 1rem;
  background: var(--color-background-primary-default);
}
.skip-link:focus {
  inset-block-start: 0;
}
.mobile-header {
  display: none;
}
.table-wrap {
  overflow-x: auto;
}
.grid {
  display: grid;
  gap: 1rem;
}
.grid-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.row {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  flex-wrap: wrap;
}
.stack {
  display: grid;
  gap: 1rem;
}
@media (max-width: 760px) {
  .studio-shell {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: 3.25rem minmax(0, 1fr) auto;
  }
  .mobile-header {
    display: flex;
    position: sticky;
    top: 0;
    z-index: 30;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0.75rem;
    background: var(--color-background-primary-default);
    border-bottom: 1px solid var(--color-separator-border);
  }
  .studio-sidebar {
    display: none;
  }
  .studio-sidebar.open {
    display: flex;
    position: fixed;
    inset-block-start: 3.25rem;
    inset-inline-start: 0;
    z-index: 40;
    width: 232px;
    height: calc(100dvh - 3.25rem);
    box-shadow: var(--shadow-xl);
  }
  .studio-shell > main {
    grid-column: 1;
    grid-row: 2;
    padding: 1rem;
    height: 100%;
  }
  .page-heading {
    align-items: flex-start;
  }
  .page-heading h1 {
    font-size: 1.4rem;
  }
  .studio-panel {
    padding: 1rem;
  }
  .grid-2 {
    grid-template-columns: 1fr;
  }
  .modal-overlay {
    align-items: flex-end;
    padding: 0.5rem;
  }
  .studio-modal {
    max-height: calc(100dvh - 1rem);
  }
  .auth-card {
    padding: 1.5rem;
  }
}
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
.task-drawer {
  position: fixed;
  z-index: 70;
  inset-inline-end: 1rem;
  inset-block-end: 1rem;
  width: min(28rem, calc(100vw - 2rem));
  max-height: 70dvh;
  overflow-y: auto;
  padding: 1.25rem;
  background: var(--color-background-primary-default);
  border: 1px solid var(--color-border-button-default);
  border-radius: 1.25rem;
  box-shadow: var(--shadow-xl);
}

.playground-message {
  padding: 1rem 0;
  border-block-end: 1px solid var(--color-separator-border);
}
.playground-message > strong {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}

/* The shell owns viewport height; each workspace owns its internal scroll. */
.sidebar-brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.25rem;
  min-height: 2rem;
}
.sidebar-brand .brand {
  white-space: nowrap;
}
.nav-collapse {
  padding: 0.25rem;
  min-width: 1.5rem;
}
.studio-sidebar nav a svg {
  flex: 0 0 auto;
}
.workspace-utilities {
  position: relative;
}
.workspace-utilities > summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  cursor: pointer;
  list-style: none;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.utility-content {
  display: grid;
  gap: 0.5rem;
  padding: 0.5rem;
}
.utility-content nav {
  gap: 0;
}
.studio-sidebar footer {
  gap: 0.5rem;
}
.nav-collapsed {
  grid-template-columns: 56px minmax(0, 1fr);
}
.nav-collapsed .sidebar-brand {
  flex-direction: column;
}
.nav-collapsed .nav-label,
.nav-collapsed .project-label,
.nav-collapsed nav h2 {
  display: none;
}
.nav-collapsed .studio-sidebar nav a {
  justify-content: center;
  padding-inline: 0.25rem;
}
.nav-collapsed .workspace-utilities[open] .utility-content {
  position: fixed;
  inset-inline-start: 56px;
  inset-block-end: 0.5rem;
  width: 200px;
  z-index: 50;
  background: var(--color-background-primary-default);
  border: 1px solid var(--color-separator-border);
  box-shadow: var(--shadow-md);
}
.nav-collapsed .utility-content nav a {
  justify-content: flex-start;
}
.studio-shell[data-workspace="conversation"] > main {
  overflow: hidden;
}
.playground-workspace {
  height: 100%;
  min-height: 0;
  grid-template-rows: auto minmax(0, 1fr);
}
.playground-workspace:has(> [role="alert"]) {
  grid-template-rows: auto auto minmax(0, 1fr);
}
.playground-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 1rem;
  min-height: 0;
  position: relative;
}
.playground-grid.inspector-open {
  grid-template-columns: minmax(0, 1fr) 272px;
}
.playground-conversation {
  grid-column: 1;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  padding: 0;
}
.playground-conversation > .panel-heading {
  padding: 0.75rem 1rem;
  margin: 0;
  border-block-end: 1px solid var(--color-separator-border);
}
.playground-toolbar {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  border-block-end: 1px solid var(--color-separator-border);
}
.playground-toolbar .studio-field {
  margin: 0;
  max-width: 32rem;
  flex: 1;
}
.playground-toolbar .studio-field small {
  display: none;
}
.playground-inspector {
  grid-column: 2;
  grid-row: 1;
  overflow-y: auto;
}
.playground-inspector .row {
  align-items: stretch;
}
.playground-inspector .row > * {
  flex: 1;
  min-width: 90px;
}
.playground-inspector textarea {
  min-height: 5rem;
}
.playground-transcript-frame {
  position: relative;
  flex: 1;
  min-height: 0;
}
.playground-transcript {
  height: 100%;
  overflow-y: auto;
  padding: 0 1rem;
  overscroll-behavior: contain;
}
.playground-transcript .empty-state {
  height: 100%;
  display: grid;
  place-content: center;
  text-align: center;
}
.playground-message {
  padding-block: 1rem;
}
.playground-message p {
  margin-block: 0.5rem 0;
}
.playground-composer {
  flex: 0 0 auto;
  padding: 0.75rem 1rem;
  border-block-start: 1px solid var(--color-separator-border);
  background: var(--color-background-primary-default);
}
.playground-composer .studio-field {
  margin-block-end: 0.5rem;
}
.playground-composer textarea {
  min-height: 3.5rem;
  max-height: 25dvh;
}
.playground-composer .actions {
  flex-direction: row-reverse;
}
.playground-conversation > [role="alert"] {
  margin: 0;
  padding: 0.5rem 1rem;
}
.scroll-edge {
  position: absolute;
  inset-block-start: 0;
  inset-inline: 0;
  height: 24px;
  pointer-events: none;
  z-index: 1;
}
.scroll-edge i {
  display: block;
  position: absolute;
  inset: 0;
  mask-image: linear-gradient(black, transparent);
}
.scroll-edge i:nth-child(1) {
  backdrop-filter: blur(1px);
}
.scroll-edge i:nth-child(2) {
  height: 16px;
  backdrop-filter: blur(4px);
}
.scroll-edge i:nth-child(3) {
  background: linear-gradient(
    var(--color-background-primary-default),
    transparent
  );
}
@media (max-width: 760px) {
  .nav-collapsed {
    grid-template-columns: minmax(0, 1fr);
  }
  .nav-collapsed .nav-label,
  .nav-collapsed .project-label,
  .nav-collapsed nav h2 {
    display: revert;
  }
  .nav-collapsed .sidebar-brand {
    flex-direction: row;
  }
  .nav-collapse {
    display: none;
  }
  .nav-collapsed .studio-sidebar nav a {
    justify-content: flex-start;
  }
  .nav-collapsed .workspace-utilities[open] .utility-content {
    position: static;
    width: auto;
  }
  .playground-grid.inspector-open {
    grid-template-columns: minmax(0, 1fr);
  }
  .playground-inspector {
    position: absolute;
    inset-block: 0;
    inset-inline-end: 0;
    width: min(100%, 300px);
    z-index: 3;
    box-shadow: var(--shadow-lg);
  }
  .playground-toolbar {
    padding: 0.5rem 0.75rem;
    gap: 0.5rem;
  }
  .playground-toolbar > button {
    font-size: 0.75rem;
  }
  .playground-composer {
    padding: 0.75rem;
  }
  .playground-composer .actions small {
    font-size: 0.65rem;
  }
}
```

## `frontend/src/styles/workspace.css`

Complete actual source for local discovery; not an unconditional remote payload.

SHA-256: `0753b86ecc8374a40cea3c312e17d93a472d00bce0c968887adedfc3604d192b`

```css
/* Shared information layouts: one working surface, optional tools and inspectors. */
.workspace-disclosure {
  border: 1px solid var(--color-separator-border);
  border-radius: 0.5rem;
  background: var(--color-background-primary-default);
  min-width: 0;
}
.workspace-disclosure > summary {
  padding: 0.8rem 1rem;
  cursor: pointer;
  font-weight: 600;
  font-size: 0.85rem;
}
.workspace-disclosure > summary small {
  font-weight: 400;
  color: var(--color-text-tertiary);
  margin-inline-start: 1rem;
}
.workspace-disclosure[open] > summary {
  border-bottom: 1px solid var(--color-separator-border);
}
.workspace-disclosure-body {
  padding: 1rem;
  min-width: 0;
  overflow-x: auto;
}
.workspace-disclosure-body > .row {
  margin-block-end: 1rem;
}
.inspection-workspace {
  display: grid;
  min-width: 0;
  align-items: start;
  gap: 1rem;
}
.inspection-workspace.has-inspector {
  grid-template-columns: minmax(0, 1fr) minmax(280px, 36%);
}
.inspection-master {
  min-width: 0;
  overflow: auto;
}
.inspection-detail {
  min-width: 0;
  border-inline-start: 1px solid var(--color-separator-border);
  padding-inline-start: 1rem;
  position: sticky;
  top: 1rem;
}
.inspection-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-text-tertiary);
}
.inspection-detail pre {
  max-height: 32vh;
  overflow: auto;
}
.metrics-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-block: 1px solid var(--color-separator-border);
}
.metric-cell {
  padding: 1rem;
  border-inline-end: 1px solid var(--color-separator-border);
  min-height: 88px;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.metrics-strip > :nth-child(5n) .metric-cell {
  border-inline-end: 0;
}
.metrics-strip > :nth-child(n + 6) .metric-cell {
  border-block-start: 1px solid var(--color-separator-border);
}
.metric-label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
.metric-cell .data-stat {
  font-size: 1.5rem;
  line-height: 1.15;
  letter-spacing: -0.04em;
  font-variant-numeric: tabular-nums;
}
.project-directory {
  display: grid;
  gap: 0;
  border: 1px solid var(--color-separator-border);
  border-radius: 0.5rem;
  overflow: hidden;
}
.project-directory .studio-panel {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(180px, 2fr) auto auto;
  gap: 1rem;
  align-items: center;
  border: 0;
  border-radius: 0;
  border-block-end: 1px solid var(--color-separator-border);
  padding: 0.8rem 1rem;
}
.project-directory > :last-child .studio-panel {
  border-block-end: 0;
}
.project-directory .panel-heading,
.project-directory p {
  margin: 0;
}
.project-directory .panel-heading {
  grid-column: 1;
}
.project-directory .studio-panel > button {
  grid-column: 4;
  grid-row: 1 / span 2;
}
.project-directory .studio-panel > p:last-of-type {
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  grid-column: 2;
}
.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}
.settings-grid .studio-panel {
  min-width: 0;
}
.settings-grid .studio-panel > p {
  font-size: 0.8rem;
}
.settings-grid .studio-panel [data-sensitive] {
  padding-block: 0.5rem;
  border-block-start: 1px solid var(--color-separator-border);
}
.settings-grid .data-muted {
  margin-block: 0 0.2rem;
  font-size: 0.7rem;
}
.models-workspace .studio-panel {
  overflow-x: auto;
}
.models-workspace td .actions {
  gap: 0.35rem;
}
.models-workspace td .actions > a {
  font-size: 0.75rem;
}
.traces-workspace td {
  vertical-align: top;
}
.trace-preview {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
  max-width: 24rem;
  white-space: pre-wrap;
}
.trace-selected {
  background: var(--color-background-secondary-default);
}
.trace-review-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.trace-promotion {
  display: flex;
  align-items: end;
  gap: 1rem;
  flex-wrap: wrap;
}
.trace-promotion .studio-field {
  max-width: 20rem;
  flex: 1;
}
@media (max-width: 1100px) {
  .project-directory .studio-panel {
    grid-template-columns: minmax(140px, 1fr) minmax(160px, 2fr) auto;
  }
  .project-directory .studio-panel > button {
    grid-column: 3;
  }
  .metrics-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .metric-cell {
    border-block-end: 1px solid var(--color-separator-border);
  }
}
@media (max-width: 700px) {
  .inspection-workspace.has-inspector,
  .settings-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .inspection-detail {
    position: static;
    border-inline-start: 0;
    border-block-start: 1px solid var(--color-separator-border);
    padding: 1rem 0;
  }
  .inspection-workspace.has-inspector .inspection-detail {
    grid-row: 1;
  }
  .metrics-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .metric-cell {
    padding: 0.75rem;
  }
  .workspace-disclosure > summary small {
    display: block;
    margin: 0.25rem 0 0 1rem;
  }
  .project-directory .studio-panel {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.5rem;
  }
  .project-directory .studio-panel > p {
    grid-column: 1 / -1;
  }
  .project-directory .studio-panel > p:last-of-type {
    grid-column: 1;
  }
  .project-directory .studio-panel > button {
    grid-column: 2;
    grid-row: 1;
  }
}
.users-workspace td .data-actions {
  flex-wrap: nowrap;
  gap: 0.35rem;
}
.users-workspace td .studio-field {
  margin: 0;
}
.users-workspace td .studio-field > label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
.settings-workspace .data-actions {
  border-block-start: 1px solid var(--color-separator-border);
  padding-block-start: 0.75rem;
}
```

## `frontend/src/styles/workbench.css`

Complete actual source for local discovery; not an unconditional remote payload.

SHA-256: `cefd7f26d472b4ebe5e737cabf31254f1da0e5b86b85562c4e9350547e781c64`

```css
/* Shared page frame. Work stays inside the viewport; only the active surface scrolls. */
:root, .dark {
  --gradient-button-primary-default: linear-gradient(var(--color-button-action),var(--color-button-action));
  --gradient-button-primary-hover: linear-gradient(var(--color-button-action-hover),var(--color-button-action-hover));
  --gradient-button-primary-active: linear-gradient(var(--color-button-action-hover),var(--color-button-action-hover));
}
html,body,#root { min-height:100%; }
.workbench-shell { display:grid; grid-template-columns:minmax(0,1fr); grid-template-rows:auto minmax(0,1fr); height:100dvh; min-height:0; overflow:hidden; }
.workbench-shell #main-content { grid-column:1; grid-row:2; min-height:0; min-width:0; overflow:auto; padding:0; outline:none; }
.workbench-shell .studio-page { width:100%; max-width:none; margin:0; padding:20px 24px 24px; min-height:100%; align-content:start; }
.workbench-shell .page-heading { min-height:52px; margin:0 0 16px; gap:16px; align-items:start; }
.workbench-shell .page-heading h1 { font-size:1.4rem; letter-spacing:-.035em; line-height:1.25; margin:0; }
.workbench-shell .page-heading p { font-size:.8125rem; line-height:1.5; margin:4px 0 0; max-width:70ch; }
.workbench-shell .studio-panel { border-radius:6px; box-shadow:none; padding:16px; min-width:0; }
.workbench-shell .panel-heading { margin-block-end:12px; }
.workbench-shell .panel-heading h2 { font-size:.875rem; line-height:1.4; }
.workbench-shell .studio-button { border-radius:6px; box-shadow:none; }
.workbench-shell button { border-radius:6px; }
.workbench-shell th { font-size:.75rem; font-weight:500; background:var(--color-background-secondary-default); white-space:nowrap; }
.workbench-shell td { font-size:.8125rem; }
.workbench-shell th,.workbench-shell td { padding:10px 12px; }
.workbench-shell tbody tr:hover { background:var(--color-background-secondary-default); }
.workbench-shell .badge { border-radius:4px; font-size:.6875rem; line-height:1.6; padding:2px 6px; }
.workbench-shell .studio-field { margin-block-end:12px; }
.workbench-shell .studio-field>label { font-size:.75rem; }
.workbench-shell .studio-field input,.workbench-shell .studio-field select { min-height:34px; font-size:.8125rem; padding:6px 9px; }
.workspace-bar { grid-column:1 / -1; grid-row:1; position:relative; z-index:35; background:var(--color-background-primary-default); border-bottom:1px solid var(--color-separator-border); min-width:0; }
.workspace-location { min-height:52px; display:flex; align-items:center; padding:8px 16px; gap:12px; }
.workspace-location .brand { font-size:1.125rem; font-weight:700; letter-spacing:-.05em; text-decoration:none; color:var(--color-text-primary); }
.workspace-project { width:156px; min-width:90px; border-inline-start:1px solid var(--color-separator-border); padding-inline-start:12px; }
.history-buttons { display:flex; gap:0; }
.workspace-location-title { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:.8125rem; color:var(--color-text-secondary); }
.workspace-global-actions { display:flex; align-items:center; gap:6px; margin-inline-start:auto; }
.workspace-bar button { box-shadow:none; font-size:.8125rem; min-height:32px; gap:5px; }
.open-work { display:flex; overflow:auto; min-height:36px; gap:2px; padding:0 16px; scrollbar-width:thin; border-top:1px solid var(--color-separator-border); }
.work-tab { flex:none; display:flex; align-items:center; gap:4px; max-width:260px; border-bottom:2px solid transparent; color:var(--color-text-secondary); }
.work-tab.current { border-color:var(--color-accent-600); color:var(--color-text-primary); background:var(--color-background-secondary-default); }
.work-tab>a { min-width:0; padding:8px 8px 8px 12px; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; font-size:.75rem; color:inherit; text-decoration:none; }
.work-tab>button { padding:2px; min-height:24px; width:24px; margin-inline-end:4px; }
.destination-popover,.recent-popover { position:absolute; inset-block-start:52px; inset-inline-start:16px; max-width:calc(100vw - 32px); border:1px solid var(--color-border-button-default); background:var(--color-background-primary-default); border-radius:8px; box-shadow:0 10px 30px color-mix(in srgb,var(--color-text-primary) 12%,transparent); padding:16px; z-index:40; }
.destination-popover>nav { display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:20px; }
.destination-popover h2,.recent-popover h2 { font-size:.75rem; color:var(--color-text-secondary); font-weight:600; margin:0 0 8px; }
.destination-popover nav a { display:block; padding:8px; font-size:.8125rem; text-decoration:none; color:var(--color-text-primary); border-radius:4px; }
.destination-popover nav a:hover,.destination-popover nav a.active,.recent-popover a:hover { background:var(--color-background-secondary-hover); }
.destination-popover footer { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-block-start:16px; padding-block-start:12px; border-top:1px solid var(--color-separator-border); font-size:.75rem; }
.destination-popover footer label { display:flex; gap:8px; align-items:center; }
.destination-popover select { min-height:32px; width:100px; padding:4px 8px; }
.recent-popover { inset-inline-start:auto; inset-inline-end:16px; width:380px; max-height:70vh; overflow:auto; }
.recent-popover p { font-size:.75rem; color:var(--color-text-secondary); }
.recent-popover a { display:flex; justify-content:space-between; gap:12px; text-decoration:none; color:var(--color-text-primary); font-size:.8125rem; padding:10px 6px; border-top:1px solid var(--color-separator-border); }
.recent-popover a span { font-size:.6875rem; color:var(--color-text-secondary); flex:none; }
.workbench-shell:has(.assistant-widget[data-expanded="true"]):not(:has(.assistant-widget[data-picking="true"])) #main-content { margin-inline-end:420px; }
.workbench-shell .inspection-detail { top:0; max-height:calc(100dvh - 192px); overflow:auto; }
.workbench-shell .task-drawer { inset-block-start:88px; }
@media(min-width:1000px) { .workbench-shell .studio-page.playground-workspace { min-height:0; height:100%; } }
@media(max-width:1000px) {
 .workspace-location-title { display:none; }
 .workbench-shell:has(.assistant-widget[data-expanded="true"]):not(:has(.assistant-widget[data-picking="true"])) #main-content { margin-inline-end:0; }
 .destination-popover>nav { grid-template-columns:repeat(3,minmax(120px,1fr)); }
}
@media(max-width:640px) {
 .workspace-location { padding:6px 10px; gap:6px; flex-wrap:wrap; }
 .workspace-location .brand { font-size:1rem; }
 .workspace-project { width:132px; }
 .history-buttons { order:4; }
 .workspace-global-actions { gap:2px; }
 .workspace-global-actions button { font-size:.75rem; }
 .workspace-global-actions button span { display:none; }
 .workbench-shell .studio-page { padding:16px 12px; }
 .workbench-shell .page-heading { flex-wrap:wrap; gap:10px; }
 .destination-popover>nav { grid-template-columns:repeat(2,minmax(105px,1fr)); }
 .destination-popover { inset-block-start:100%; max-height:75dvh; overflow:auto; }
 .destination-popover footer { flex-wrap:wrap; }
 .workspace-bar button,.workbench-shell .page-heading button { min-height:40px; }
 .workbench-shell .inspection-detail { max-height:none; }
 .open-work { padding:0 8px; }
 .work-tab>a { padding-block:12px; }
}
@media(prefers-reduced-motion:reduce) { .workbench-shell * { scroll-behavior:auto!important; transition:none!important; } }
```

