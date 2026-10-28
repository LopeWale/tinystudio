# Pages and recursive local dependency trees

Captured 2026-10-27T23:43:08.381443+00:00 from the current shared working tree. Local discovery only: application source is frozen. No application edits, remote calls, live-data/credential requests, deployments, branches, worktrees or commits were performed by this init. These discovery files are not an instruction to upload the entire bundle. No `.superdesign/resume.json` is created.

Ten grouped trees cover all 21 routed page components. Every discovered static local dependency is recursively expanded once per group, including relative/alias imports, barrel re-exports, literal dynamic imports and CSS @import/@reference. Repeated nodes use `already expanded` and back-edges use `cycle`, pointing to the same file expanded elsewhere in that group. External package internals are excluded. This is conservative file-level reachability, not a claim that every export executes.

The shared main/App/WorkspaceBar/StudioProvider/AssistantProvider/PlaygroundProvider envelope has full code in layouts.md and a recursive tree below. Inherited global styles are not fabricated as page direct imports. Every file is a candidate for scoped context selection, not a demand to transmit the dependency tree.

## Data collections

Routes: `/datasets, /datasets/:name, /repositories`

Entries: `frontend/src/features/data/datasets.tsx`, `frontend/src/features/data/repositories.tsx`

```text
- frontend/src/features/data/datasets.tsx
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx
      - frontend/src/api/client.ts
        - frontend/src/controllers/workspaceScope.ts
      - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
      - frontend/src/app/evidence.ts
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/components/studio/uploads.tsx
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/components/boardui/base/file-upload/file-upload.tsx
      - frontend/src/components/boardui/foundations/template-copy/template-copy.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/controllers/uploads.ts
  - frontend/src/features/data/common.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/training.tsx
    - frontend/src/features/data/analytical-resource.ts
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/features/data/training-chart.tsx
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
      - frontend/src/features/data/common.tsx [already expanded in this group]
      - frontend/src/features/data/analytical-workspace.css
    - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
    - frontend/src/features/assistant/actions.tsx [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/data/evaluations.tsx
      - frontend/src/features/data/analytical-resource.ts [already expanded in this group]
      - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
      - frontend/src/features/assistant/actions.tsx [already expanded in this group]
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
      - frontend/src/features/data/common.tsx [already expanded in this group]
    - frontend/src/features/data/common.tsx [already expanded in this group]
  - frontend/src/components/boardui/base/checkbox/checkbox.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/checkbox/checkbox-glyph.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/records-workspace.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/records-workspace.css
    - frontend/src/styles/globals.css
      - frontend/src/styles/theme.css
      - frontend/src/styles/typography.css
- frontend/src/features/data/repositories.tsx
  - frontend/src/features/assistant/actions.tsx [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/components/studio/uploads.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
  - frontend/src/components/boardui/base/checkbox/checkbox.tsx [already expanded in this group]
  - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/records-workspace.tsx [already expanded in this group]
  - frontend/src/features/data/records-workspace.css [already expanded in this group]
```

Unique local files in this group: 30.

## Fine tuning

Routes: `/finetuning, /finetuning/:job`

Entries: `frontend/src/features/data/training.tsx`

```text
- frontend/src/features/data/training.tsx
  - frontend/src/features/data/analytical-resource.ts
    - frontend/src/app/StudioContext.tsx
      - frontend/src/api/client.ts
        - frontend/src/controllers/workspaceScope.ts
      - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
      - frontend/src/app/evidence.ts
  - frontend/src/features/data/training-chart.tsx
    - frontend/src/components/studio/primitives.tsx
      - frontend/src/components/boardui/base/buttons/button.tsx
        - frontend/src/components/boardui/utils/cx.ts
        - frontend/src/components/boardui/utils/directional-icon.ts
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/app/modalStack.ts
    - frontend/src/features/data/common.tsx
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/data/analytical-workspace.css
  - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/evaluations.tsx
    - frontend/src/features/data/analytical-resource.ts [already expanded in this group]
    - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
    - frontend/src/features/assistant/actions.tsx [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/data/common.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
```

Unique local files in this group: 17.

## Evaluation and metrics

Routes: `/evaluations, /evaluations/:name, /metrics`

Entries: `frontend/src/features/data/evaluations.tsx`, `frontend/src/features/data/metrics.tsx`

```text
- frontend/src/features/data/evaluations.tsx
  - frontend/src/features/data/analytical-resource.ts
    - frontend/src/app/StudioContext.tsx
      - frontend/src/api/client.ts
        - frontend/src/controllers/workspaceScope.ts
      - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
      - frontend/src/app/evidence.ts
  - frontend/src/features/data/analytical-workspace.css
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/features/data/common.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
- frontend/src/features/data/metrics.tsx
  - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
```

Unique local files in this group: 16.

## Laboratory

Routes: `/lab`

Entries: `frontend/src/features/lab/index.tsx`

```text
- frontend/src/features/lab/index.tsx
  - frontend/src/app/StudioContext.tsx
    - frontend/src/api/client.ts
      - frontend/src/controllers/workspaceScope.ts
    - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
    - frontend/src/app/evidence.ts
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/components/boardui/base/input/input.tsx
    - frontend/src/components/boardui/base/input/label.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/input/hint-text.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/components/boardui/base/textarea/textarea.tsx
    - frontend/src/components/boardui/base/input/hint-text.tsx [already expanded in this group]
    - frontend/src/components/boardui/base/input/label.tsx [already expanded in this group]
    - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/components/boardui/base/select/select.tsx
    - frontend/src/components/boardui/base/dropdown/menu-styles.ts
    - frontend/src/components/boardui/foundations/icons/chevrons.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/utils/use-dismiss-on-outside-press.ts
  - frontend/src/features/lab/definition.ts
  - frontend/src/features/lab/lab.css
  - frontend/src/features/lab/inputs.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/uploads.tsx
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
      - frontend/src/components/boardui/base/file-upload/file-upload.tsx
        - frontend/src/components/boardui/foundations/template-copy/template-copy.tsx
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/controllers/uploads.ts
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/lab/definition.ts [already expanded in this group]
    - frontend/src/components/boardui/base/select/select.tsx [already expanded in this group]
  - frontend/src/features/lab/graph.tsx
    - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
    - frontend/src/components/boardui/base/textarea/textarea.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/lab/inputs.tsx [already expanded in this group]
    - frontend/src/features/lab/definition.ts [already expanded in this group]
  - frontend/src/features/lab/replay.tsx
    - frontend/src/components/boardui/base/select/select.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
    - frontend/src/features/lab/graph.tsx [already expanded in this group]
    - frontend/src/features/lab/definition.ts [already expanded in this group]
  - frontend/src/components/studio/uploads.tsx [already expanded in this group]
  - frontend/src/features/lab/operations.ts
    - frontend/src/api/client.ts [already expanded in this group]
    - frontend/src/features/lab/definition.ts [already expanded in this group]
```

Unique local files in this group: 28.

## Interpretability

Routes: `/interp, /interp/c/:capture, /interp/d/:comparison`

Entries: `frontend/src/features/interpretability/index.tsx`

```text
- frontend/src/features/interpretability/index.tsx
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx
      - frontend/src/api/client.ts
        - frontend/src/controllers/workspaceScope.ts
      - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
      - frontend/src/app/evidence.ts
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/boardui/utils/cx.ts
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/features/interpretability/selection.ts
  - frontend/src/features/interpretability/plots.tsx
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/interpretability/selection.ts [already expanded in this group]
  - frontend/src/features/interpretability/interp.css
```

Unique local files in this group: 15.

## Models

Routes: `/models`

Entries: `frontend/src/features/workspace/models.tsx`

```text
- frontend/src/features/workspace/models.tsx
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx
      - frontend/src/api/client.ts
        - frontend/src/controllers/workspaceScope.ts
      - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
      - frontend/src/app/evidence.ts
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/components/studio/workspace.tsx
    - frontend/src/components/boardui/utils/cx.ts
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/components/boardui/base/checkbox/checkbox.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/checkbox/checkbox-glyph.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/common.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/records-workspace.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/records-workspace.css
    - frontend/src/styles/globals.css
      - frontend/src/styles/theme.css
      - frontend/src/styles/typography.css
  - frontend/src/features/workspace/serving-records.css
    - frontend/src/styles/globals.css [already expanded in this group]
```

Unique local files in this group: 22.

## Playground

Routes: `/playground`

Entries: `frontend/src/features/workspace/playground.tsx`

```text
- frontend/src/features/workspace/playground.tsx
  - frontend/src/features/assistant/scrollArea.tsx
    - frontend/src/components/boardui/utils/cx.ts
  - frontend/src/features/workspace/playground-workspace.css
  - frontend/src/app/StudioContext.tsx
    - frontend/src/api/client.ts
      - frontend/src/controllers/workspaceScope.ts
    - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
    - frontend/src/app/evidence.ts
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/app/PlaygroundProvider.tsx
    - frontend/src/controllers/playground.ts
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/controllers/playground.ts [already expanded in this group]
```

Unique local files in this group: 16.

## Traces

Routes: `/traces`

Entries: `frontend/src/features/workspace/traces.tsx`

```text
- frontend/src/features/workspace/traces.tsx
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx
      - frontend/src/api/client.ts
        - frontend/src/controllers/workspaceScope.ts
      - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
      - frontend/src/app/evidence.ts
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/components/studio/workspace.tsx
    - frontend/src/components/boardui/utils/cx.ts
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/components/boardui/base/checkbox/checkbox.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/checkbox/checkbox-glyph.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/common.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/records-workspace.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/data/records-workspace.css
    - frontend/src/styles/globals.css
      - frontend/src/styles/theme.css
      - frontend/src/styles/typography.css
  - frontend/src/features/workspace/serving-records.css
    - frontend/src/styles/globals.css [already expanded in this group]
```

Unique local files in this group: 22.

## Workspace administration

Routes: `/projects, /api-keys, /stats, /settings, /users`

Entries: `frontend/src/features/workspace/projects.tsx`, `frontend/src/features/workspace/keys.tsx`, `frontend/src/features/workspace/stats.tsx`, `frontend/src/features/workspace/settings.tsx`, `frontend/src/features/workspace/users.tsx`

```text
- frontend/src/features/workspace/projects.tsx
  - frontend/src/features/workspace/admin-workspace.css
  - frontend/src/app/StudioContext.tsx
    - frontend/src/api/client.ts
      - frontend/src/controllers/workspaceScope.ts
    - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
    - frontend/src/app/evidence.ts
  - frontend/src/components/studio/primitives.tsx
    - frontend/src/components/boardui/base/buttons/button.tsx
      - frontend/src/components/boardui/utils/cx.ts
      - frontend/src/components/boardui/utils/directional-icon.ts
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/modalStack.ts
  - frontend/src/features/data/common.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
- frontend/src/features/workspace/keys.tsx
  - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
  - frontend/src/components/studio/workspace.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
- frontend/src/features/workspace/stats.tsx
  - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/features/data/analytical-resource.ts
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
- frontend/src/features/workspace/settings.tsx
  - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
- frontend/src/features/workspace/users.tsx
  - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
  - frontend/src/app/StudioContext.tsx [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/features/data/common.tsx [already expanded in this group]
```

Unique local files in this group: 20.

## Assistant

Routes: `/assistant and shared dock`

Entries: `frontend/src/features/assistant/index.tsx`

```text
- frontend/src/features/assistant/index.tsx
  - frontend/src/app/StudioContext.tsx
    - frontend/src/api/client.ts
      - frontend/src/controllers/workspaceScope.ts
    - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
    - frontend/src/app/evidence.ts
  - frontend/src/components/boardui/application/agent-thinking/agent-thinking.tsx
    - frontend/src/components/boardui/foundations/template-copy/template-copy.tsx
    - frontend/src/components/boardui/utils/cx.ts
  - frontend/src/components/studio/uploads.tsx
    - frontend/src/components/studio/primitives.tsx
      - frontend/src/components/boardui/base/buttons/button.tsx
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/components/boardui/utils/directional-icon.ts
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/app/modalStack.ts
    - frontend/src/components/boardui/base/file-upload/file-upload.tsx
      - frontend/src/components/boardui/foundations/template-copy/template-copy.tsx [already expanded in this group]
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/controllers/uploads.ts
  - frontend/src/controllers/conversation.ts
    - frontend/src/api/client.ts [already expanded in this group]
    - frontend/src/app/evidence.ts [already expanded in this group]
    - frontend/src/controllers/conversation-sync.js
  - frontend/src/controllers/uploads.ts [already expanded in this group]
  - frontend/src/components/studio/primitives.tsx [already expanded in this group]
  - frontend/src/components/boardui/base/select/select.tsx
    - frontend/src/components/boardui/base/dropdown/menu-styles.ts
    - frontend/src/components/boardui/foundations/icons/chevrons.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/utils/use-dismiss-on-outside-press.ts
  - frontend/src/components/boardui/base/checkbox/checkbox.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/checkbox/checkbox-glyph.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/components/boardui/base/textarea/textarea.tsx
    - frontend/src/components/boardui/base/input/hint-text.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/input/label.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/base/input/input.tsx
      - frontend/src/components/boardui/base/input/label.tsx [already expanded in this group]
      - frontend/src/components/boardui/base/input/hint-text.tsx [already expanded in this group]
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/assistant/assistant.css
  - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/assistant/scrollArea.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/features/assistant/messageContent.tsx
    - frontend/src/features/assistant/messageContent.css
    - frontend/src/features/assistant/markdownBody.tsx
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/features/assistant/scrollArea.tsx [already expanded in this group]
  - frontend/src/features/assistant/context.ts
    - frontend/src/app/evidence.ts [already expanded in this group]
    - frontend/src/controllers/conversation.ts [already expanded in this group]
  - frontend/src/features/assistant/uiBridge.ts
    - frontend/src/controllers/componentActions.ts
      - frontend/src/app/evidence.ts [already expanded in this group]
    - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/features/assistant/display.tsx
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/assistant/assistant.css [already expanded in this group]
    - frontend/src/controllers/conversation.ts [already expanded in this group]
  - frontend/src/controllers/componentActions.ts [already expanded in this group]
  - frontend/src/features/assistant/actions.tsx
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/controllers/componentActions.ts [already expanded in this group]
```

Unique local files in this group: 37.

## Shared envelope recursive tree

```text
- frontend/src/main.tsx
  - frontend/src/visual-truth.generated.ts
  - frontend/src/app/App.tsx
    - frontend/src/app/WorkspaceBar.tsx
      - frontend/src/components/studio/primitives.tsx
        - frontend/src/components/boardui/base/buttons/button.tsx
          - frontend/src/components/boardui/utils/cx.ts
          - frontend/src/components/boardui/utils/directional-icon.ts
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/app/StudioContext.tsx
          - frontend/src/api/client.ts
            - frontend/src/controllers/workspaceScope.ts
          - frontend/src/controllers/workspaceScope.ts [already expanded in this group]
          - frontend/src/app/evidence.ts
        - frontend/src/app/modalStack.ts
      - frontend/src/components/boardui/base/select/select.tsx
        - frontend/src/components/boardui/base/dropdown/menu-styles.ts
        - frontend/src/components/boardui/foundations/icons/chevrons.tsx
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/components/boardui/utils/use-dismiss-on-outside-press.ts
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/controllers/navigation.ts
      - frontend/src/features/assistant/index.tsx
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/boardui/application/agent-thinking/agent-thinking.tsx
          - frontend/src/components/boardui/foundations/template-copy/template-copy.tsx
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/components/studio/uploads.tsx
          - frontend/src/components/studio/primitives.tsx [already expanded in this group]
          - frontend/src/components/boardui/base/file-upload/file-upload.tsx
            - frontend/src/components/boardui/foundations/template-copy/template-copy.tsx [already expanded in this group]
            - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
          - frontend/src/controllers/uploads.ts
        - frontend/src/controllers/conversation.ts
          - frontend/src/api/client.ts [already expanded in this group]
          - frontend/src/app/evidence.ts [already expanded in this group]
          - frontend/src/controllers/conversation-sync.js
        - frontend/src/controllers/uploads.ts [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/select/select.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/checkbox/checkbox.tsx
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
          - frontend/src/components/boardui/base/checkbox/checkbox-glyph.tsx
            - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/components/boardui/base/textarea/textarea.tsx
          - frontend/src/components/boardui/base/input/hint-text.tsx
            - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
          - frontend/src/components/boardui/base/input/label.tsx
            - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
          - frontend/src/components/boardui/base/input/input.tsx
            - frontend/src/components/boardui/base/input/label.tsx [already expanded in this group]
            - frontend/src/components/boardui/base/input/hint-text.tsx [already expanded in this group]
            - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/assistant/assistant.css
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/assistant/scrollArea.tsx
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/assistant/messageContent.tsx
          - frontend/src/features/assistant/messageContent.css
          - frontend/src/features/assistant/markdownBody.tsx
            - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
            - frontend/src/features/assistant/scrollArea.tsx [already expanded in this group]
        - frontend/src/features/assistant/context.ts
          - frontend/src/app/evidence.ts [already expanded in this group]
          - frontend/src/controllers/conversation.ts [already expanded in this group]
        - frontend/src/features/assistant/uiBridge.ts
          - frontend/src/controllers/componentActions.ts
            - frontend/src/app/evidence.ts [already expanded in this group]
          - frontend/src/app/evidence.ts [already expanded in this group]
        - frontend/src/features/assistant/display.tsx
          - frontend/src/components/studio/primitives.tsx [already expanded in this group]
          - frontend/src/features/assistant/assistant.css [already expanded in this group]
          - frontend/src/controllers/conversation.ts [already expanded in this group]
        - frontend/src/controllers/componentActions.ts [already expanded in this group]
        - frontend/src/features/assistant/actions.tsx
          - frontend/src/app/StudioContext.tsx [already expanded in this group]
          - frontend/src/controllers/componentActions.ts [already expanded in this group]
    - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/features/assistant/actions.tsx [already expanded in this group]
    - frontend/src/app/RouteErrorBoundary.tsx
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
      - frontend/src/app/lazyRoute.ts
    - frontend/src/app/PlaygroundProvider.tsx
      - frontend/src/controllers/playground.ts
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
    - frontend/src/app/lazyRoute.ts [already expanded in this group]
    - frontend/src/components/studio/primitives.tsx [already expanded in this group]
    - frontend/src/features/assistant/index.tsx [already expanded in this group]
    - frontend/src/features/lab/index.tsx
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
      - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
      - frontend/src/components/boardui/base/textarea/textarea.tsx [already expanded in this group]
      - frontend/src/components/boardui/base/select/select.tsx [already expanded in this group]
      - frontend/src/features/lab/definition.ts
      - frontend/src/features/lab/lab.css
      - frontend/src/features/lab/inputs.tsx
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/uploads.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/lab/definition.ts [already expanded in this group]
        - frontend/src/components/boardui/base/select/select.tsx [already expanded in this group]
      - frontend/src/features/lab/graph.tsx
        - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/textarea/textarea.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/lab/inputs.tsx [already expanded in this group]
        - frontend/src/features/lab/definition.ts [already expanded in this group]
      - frontend/src/features/lab/replay.tsx
        - frontend/src/components/boardui/base/select/select.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
        - frontend/src/features/lab/graph.tsx [already expanded in this group]
        - frontend/src/features/lab/definition.ts [already expanded in this group]
      - frontend/src/components/studio/uploads.tsx [already expanded in this group]
      - frontend/src/features/lab/operations.ts
        - frontend/src/api/client.ts [already expanded in this group]
        - frontend/src/features/lab/definition.ts [already expanded in this group]
    - frontend/src/features/data/index.ts
      - frontend/src/features/data/data.css
      - frontend/src/features/data/datasets.tsx
        - frontend/src/features/assistant/actions.tsx [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/studio/uploads.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx
          - frontend/src/app/StudioContext.tsx [already expanded in this group]
          - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/training.tsx
          - frontend/src/features/data/analytical-resource.ts
            - frontend/src/app/StudioContext.tsx [already expanded in this group]
          - frontend/src/features/data/training-chart.tsx
            - frontend/src/components/studio/primitives.tsx [already expanded in this group]
            - frontend/src/features/data/common.tsx [already expanded in this group]
            - frontend/src/features/data/analytical-workspace.css
          - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
          - frontend/src/features/assistant/actions.tsx [already expanded in this group]
          - frontend/src/app/StudioContext.tsx [already expanded in this group]
          - frontend/src/components/studio/primitives.tsx [already expanded in this group]
          - frontend/src/features/data/evaluations.tsx
            - frontend/src/features/data/analytical-resource.ts [already expanded in this group]
            - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
            - frontend/src/features/assistant/actions.tsx [already expanded in this group]
            - frontend/src/app/StudioContext.tsx [already expanded in this group]
            - frontend/src/components/studio/primitives.tsx [already expanded in this group]
            - frontend/src/features/data/common.tsx [already expanded in this group]
          - frontend/src/features/data/common.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/checkbox/checkbox.tsx [already expanded in this group]
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/data/records-workspace.tsx
          - frontend/src/app/StudioContext.tsx [already expanded in this group]
          - frontend/src/components/studio/primitives.tsx [already expanded in this group]
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/data/records-workspace.css
          - frontend/src/styles/globals.css
            - frontend/src/styles/theme.css
            - frontend/src/styles/typography.css
      - frontend/src/features/data/repositories.tsx
        - frontend/src/features/assistant/actions.tsx [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/studio/uploads.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/checkbox/checkbox.tsx [already expanded in this group]
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/data/records-workspace.tsx [already expanded in this group]
        - frontend/src/features/data/records-workspace.css [already expanded in this group]
      - frontend/src/features/data/training.tsx [already expanded in this group]
      - frontend/src/features/data/evaluations.tsx [already expanded in this group]
      - frontend/src/features/data/metrics.tsx
        - frontend/src/features/data/analytical-workspace.css [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
    - frontend/src/features/interpretability/index.tsx
      - frontend/src/features/assistant/actions.tsx [already expanded in this group]
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
      - frontend/src/components/studio/primitives.tsx [already expanded in this group]
      - frontend/src/features/interpretability/selection.ts
      - frontend/src/features/interpretability/plots.tsx
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/interpretability/selection.ts [already expanded in this group]
      - frontend/src/features/interpretability/interp.css
    - frontend/src/features/workspace/index.ts
      - frontend/src/features/workspace/models.tsx
        - frontend/src/features/assistant/actions.tsx [already expanded in this group]
        - frontend/src/components/studio/workspace.tsx
          - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/checkbox/checkbox.tsx [already expanded in this group]
        - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
        - frontend/src/features/data/records-workspace.tsx [already expanded in this group]
        - frontend/src/features/data/records-workspace.css [already expanded in this group]
        - frontend/src/features/workspace/serving-records.css
          - frontend/src/styles/globals.css [already expanded in this group]
      - frontend/src/features/workspace/playground.tsx
        - frontend/src/features/assistant/scrollArea.tsx [already expanded in this group]
        - frontend/src/features/workspace/playground-workspace.css
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/features/assistant/actions.tsx [already expanded in this group]
        - frontend/src/app/PlaygroundProvider.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/controllers/playground.ts [already expanded in this group]
      - frontend/src/features/workspace/traces.tsx
        - frontend/src/features/assistant/actions.tsx [already expanded in this group]
        - frontend/src/components/studio/workspace.tsx [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/checkbox/checkbox.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
        - frontend/src/features/data/records-workspace.tsx [already expanded in this group]
        - frontend/src/features/data/records-workspace.css [already expanded in this group]
        - frontend/src/features/workspace/serving-records.css [already expanded in this group]
      - frontend/src/features/workspace/projects.tsx
        - frontend/src/features/workspace/admin-workspace.css
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
      - frontend/src/features/workspace/keys.tsx
        - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
        - frontend/src/components/studio/workspace.tsx [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
      - frontend/src/features/workspace/stats.tsx
        - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
        - frontend/src/features/assistant/actions.tsx [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/features/data/analytical-resource.ts [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
      - frontend/src/features/workspace/settings.tsx
        - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
      - frontend/src/features/workspace/users.tsx
        - frontend/src/features/workspace/admin-workspace.css [already expanded in this group]
        - frontend/src/app/StudioContext.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/features/data/common.tsx [already expanded in this group]
    - frontend/src/app/ComponentCatalogRoute.tsx
      - frontend/src/catalog/index.tsx
        - frontend/src/components/boardui/base/buttons/button.tsx [already expanded in this group]
        - frontend/src/components/boardui/base/input/input.tsx [already expanded in this group]
        - frontend/src/components/boardui/application/agent-thinking/agent-thinking.tsx [already expanded in this group]
        - frontend/src/components/studio/primitives.tsx [already expanded in this group]
        - frontend/src/components/studio/uploads.tsx [already expanded in this group]
        - frontend/src/features/interpretability/plots.tsx [already expanded in this group]
        - frontend/src/features/assistant/display.tsx [already expanded in this group]
        - frontend/src/controllers/conversation.ts [already expanded in this group]
        - frontend/src/features/assistant/messageContent.sample.tsx
          - frontend/src/features/assistant/messageContent.tsx [already expanded in this group]
        - frontend/src/features/assistant/assistant.css [already expanded in this group]
        - frontend/src/controllers/uploads.ts [already expanded in this group]
        - frontend/src/features/interpretability/interp.css [already expanded in this group]
      - frontend/src/app/StudioContext.tsx [already expanded in this group]
      - frontend/src/app/evidence.ts [already expanded in this group]
  - frontend/src/components/boardui/foundations/direction/direction.tsx
    - frontend/src/components/boardui/utils/cx.ts [already expanded in this group]
  - frontend/src/styles/globals.css [already expanded in this group]
  - frontend/src/styles/studio.css
  - frontend/src/styles/workspace.css
  - frontend/src/styles/workbench.css
```

## Resolution validation

All discovered local static import specifiers resolve to existing files. Cycles and repeated dependencies are marked explicitly.

Unique local files traversed across all pages and envelope: 93.

## Independent local structural validation

A second traversal using the installed TypeScript compiler AST, rather than the discovery regex, found 93 reachable local files and 289 local import/re-export/dynamic-import edges from main.tsx. All 93 file paths appear in this discovery document; no local import failed resolution. AST route extraction found 23 Route paths: the 21 feature destinations plus `/` and `*`; every one appears in routes.md. Development catalog and authentication branches were checked separately in the source. All 31 full-source dumps across components/layouts/routes/theme match the current source both byte-content (ignoring only final blank-line trimming) and SHA-256. These are documentation consistency checks, not UI, integration, or independent Agent Team verifier acceptance. The parent owns any required restricted verifier checkpoint.
