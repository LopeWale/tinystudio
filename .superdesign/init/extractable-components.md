# Extractable components

Captured 2026-10-27T23:43:08.381443+00:00 from the current shared working tree. Local discovery only: application source is frozen. No application edits, remote calls, live-data/credential requests, deployments, branches, worktrees or commits were performed by this init. These discovery files are not an instruction to upload the entire bundle. No `.superdesign/resume.json` is created.

Candidate DraftComponent boundaries only: no remote entities/project/draft IDs created. Full source is in components.md and layouts.md. Listed props are source-grounded state/navigation candidates; icons/classes/static labels remain owned by actual implementation.

## WorkspaceBar

- Source: `frontend/src/app/WorkspaceBar.tsx`
- Category: layout
- Description: Project/location, Pages/Recent, open-work/task/assistant controls.
- Extractable state/navigation props: theme/onTheme; route/project/open-work are current hook-owned state.
- Hardcoded elements: Brand, destination labels, Remix icons, CSS selectors.

## Shell

- Source: `frontend/src/app/App.tsx`
- Category: layout
- Description: Authenticated routed viewport and assistant dock.
- Extractable state/navigation props: Route/scope/theme are source-owned controller/context state.
- Hardcoded elements: Skip link, region layout, suspense/error labels.

## AssistantWidget

- Source: `frontend/src/features/assistant/index.tsx`
- Category: layout
- Description: Persistent dock/composer.
- Extractable state/navigation props: Expanded/picking/conversation are controller/context-owned.
- Hardcoded elements: Dock/evidence labels, classes.

## Page

- Source: `frontend/src/components/studio/primitives.tsx`
- Category: layout
- Description: Heading/actions/content wrapper.
- Extractable state/navigation props: Action/description visibility; actual props title/description/actions.
- Hardcoded elements: Page and heading anatomy/classes.

## InspectionWorkspace

- Source: `frontend/src/components/studio/workspace.tsx`
- Category: layout
- Description: Master/detail grid.
- Extractable state/navigation props: inspector presence, accessible label.
- Hardcoded elements: Layout DOM/classes.

## RecordsInspector

- Source: `frontend/src/features/data/records-workspace.tsx`
- Category: layout
- Description: Fixed header and local scrollable detail.
- Extractable state/navigation props: onClose, title/inspected identity.
- Hardcoded elements: Back to results, focus and fade structure.

## WorkspaceDisclosure

- Source: `frontend/src/components/studio/workspace.tsx`
- Category: basic
- Description: Retained collapsible secondary tools.
- Extractable state/navigation props: open/defaultOpen/onToggle.
- Hardcoded elements: Details/summary DOM/classes.

## Button

- Source: `frontend/src/components/studio/primitives.tsx`
- Category: basic
- Description: Studio wrapper delegates to BoardUI.
- Extractable state/navigation props: disabled/loading, command event handlers.
- Hardcoded elements: Variant styles, size mapping, nested label padding.

## BoardButton

- Source: `frontend/src/components/boardui/base/buttons/button.tsx`
- Category: basic
- Description: Installed Button/ButtonLink visual anatomy.
- Extractable state/navigation props: disabled, aria-expanded/pressed, href for link.
- Hardcoded elements: Style matrix, icon/layout, focus and label-wrapper padding.

## Field

- Source: `frontend/src/components/studio/primitives.tsx`
- Category: basic
- Description: Label/hint/error and child association.
- Extractable state/navigation props: Error/hint visibility and child-controlled value.
- Hardcoded elements: Association and wrapper structure/classes.

## Input

- Source: `frontend/src/components/boardui/base/input/input.tsx`
- Category: basic
- Description: BoardUI field/control.
- Extractable state/navigation props: value/onChange, disabled/invalid flags declared in source.
- Hardcoded elements: Field anatomy, icons, border/focus classes.

## Select

- Source: `frontend/src/components/boardui/base/select/select.tsx`
- Category: basic
- Description: React Aria trigger/options.
- Extractable state/navigation props: selectedKey/onSelectionChange/isDisabled and item IDs.
- Hardcoded elements: Chevron/listbox anatomy/classes.

## Checkbox

- Source: `frontend/src/components/boardui/base/checkbox/checkbox.tsx`
- Category: basic
- Description: Independent checked-selection control.
- Extractable state/navigation props: isSelected/onChange/isIndeterminate/isDisabled.
- Hardcoded elements: Glyph/focus/checked anatomy/classes.

## Modal

- Source: `frontend/src/components/studio/primitives.tsx`
- Category: basic
- Description: Portal dialog with modal-stack ownership.
- Extractable state/navigation props: open/onClose/footer visibility/wide.
- Hardcoded elements: Close glyph, backdrop, stack mechanics/classes.

## Records

- Source: `frontend/src/features/data/common.tsx`
- Category: basic
- Description: Shared semantic table scaffold.
- Extractable state/navigation props: Dynamic row children and accessible label.
- Hardcoded elements: data-table structure; header values belong to caller.

## RecordComparison

- Source: `frontend/src/features/data/records-workspace.tsx`
- Category: basic
- Description: Aligned field comparison over ordered selected records.
- Extractable state/navigation props: records/onRemove/onClose; domain field adapter values.
- Hardcoded elements: Different label, close/remove controls and classes.

## FileUploadField

- Source: `frontend/src/components/studio/uploads.tsx`
- Category: basic
- Description: File/drop entry preserving selected identities.
- Extractable state/navigation props: files/onChange/disabled; accept/maxBytes from actual policy.
- Hardcoded elements: Drop/choose/remove anatomy/classes.

## UploadStatus

- Source: `frontend/src/components/studio/uploads.tsx`
- Category: basic
- Description: Per-transfer progress and recovery.
- Extractable state/navigation props: phase/progress/error/onCancel/onRetry.
- Hardcoded elements: Status/progress presentation/classes.

## Badge

- Source: `frontend/src/components/studio/primitives.tsx`
- Category: basic
- Description: Status metadata chip.
- Extractable state/navigation props: Visibility and dynamic content.
- Hardcoded elements: Chip DOM/classes.

## EmptyState

- Source: `frontend/src/components/studio/primitives.tsx`
- Category: basic
- Description: Empty/no-result wrapper.
- Extractable state/navigation props: Visibility and caller-supplied recovery actions.
- Hardcoded elements: Wrapper DOM/classes.

## Responsibility boundaries

Shell owns navigation/project scope; collection controllers own filters/checked identities; inspector owns open identity; comparison adapters own field meaning/missing values; upload controllers own lifecycle; conversation controller owns assistant draft/evidence. Do not extract the entire provider graph as a cosmetic component. Source padding/type/form differences need coordinated design review across the owners in components.md/theme.md; application repair is still frozen.
