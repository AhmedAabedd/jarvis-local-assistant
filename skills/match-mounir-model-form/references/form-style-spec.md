# Mounir compact model-form style specification

This reference records the universal compact creation-and-modification popup system as implemented on 2026-08-27. Before applying it, inspect the live sources below; live values take precedence when the design has intentionally changed.

## Contents

1. Source map
2. Structural anatomy
3. Design tokens and typography
4. Modal shell and scrolling
5. Form layout and fields
6. Location option cards
7. Guidance and feedback
8. Buttons and actions
9. Interaction states
10. Responsive behavior
11. Adaptation rules
12. Verification checklist

## 1. Source map

Inspect these files before making a parity change:

- `frontend/src/styles/index.css`: global tokens and all visual rules.
- `frontend/src/components/ui/Modal.tsx`: portal, dialog semantics, integrated heading, close control, and scroll-frame DOM.
- `frontend/src/components/ui/Field.tsx`: label, hint, control, and error order.
- `frontend/src/components/ui/Button.tsx`: variants, disabled state, icon slot, and busy spinner.
- `frontend/src/components/ui/Feedback.tsx`: error/status semantics.
- `frontend/src/features/resources/ResourcesPage.tsx`: model and subagent creation/edit modal composition and external action rows.
- `frontend/src/features/resources/McpServersPage.tsx`: MCP server creation/edit popup composition.
- `frontend/src/features/resources/ServerForm.tsx`: canonical long-form sections and dynamic credential configuration.
- `frontend/src/features/resources/ModelForm.tsx`: canonical text-model form.
- `frontend/src/features/resources/EmbeddingModelForm.tsx`: compound input/action row and discovery feedback.
- `frontend/src/features/resources/VoiceModelForm.tsx`: conditional fields and dynamic options.
- `frontend/src/features/resources/ModelLocationOptions.tsx`: semantic location fieldset and selectable cards.
- `frontend/src/features/resources/AgentForm.tsx`: canonical multi-stage use of the shared compact write popup.

Search the CSS for `.modal--compact-write-form`, `.modal--model-write-form`, `.modal--subagent-write-form`, `.modal--mcp-write-form`, `.modal__scroll-frame`, `.form-grid`, `.field`, `.guidance`, `.feedback`, `.model-location-options`, `.compact-form-actions`, `.subagent-form-footer`, `.button`, and the `@media (max-width: 720px)` block. Avoid relying on stale line numbers.

## 2. Structural anatomy

Use this hierarchy for a creation or modification popup:

```tsx
<Modal
  open={open}
  wide
  integrated
  className="modal--compact-write-form modal--model-write-form"
  title="Add …"
  description="…"
  onClose={onClose}
>
  <div className="model-write-modal-form">
    <form id={formId} className="form-grid" onSubmit={submit}>
      {/* location fieldset, guidance, fields, feedback */}
    </form>
  </div>
  <div className="compact-form-actions">
    <Button variant="primary" type="submit" form={formId} busy={isSaving}>
      Create model
    </Button>
  </div>
</Modal>
```

`Modal` supplies this internal structure in integrated mode:

```text
section.modal.modal--wide.modal--compact-write-form.modal--model-write-form
└── div.modal__scroll-frame
    └── div.modal__body.modal__body--integrated
        ├── div.modal__body-heading
        │   ├── title and optional description
        │   └── optional heading action(s), then icon-only close button
        ├── div.model-write-modal-form
        │   └── form.form-grid
        └── div.compact-form-actions
```

Keep the action row outside the `form` only when it needs to remain a sibling in this composition. Associate the submit button with the form through a unique `formId` and `form={formId}`.

`Field` supplies this exact order:

```text
label.field.field--full
├── span.field__label
├── span.field__hint (optional)
├── native input, select, or textarea
└── span.field__error (optional)
```

Do not place an interactive button inside a wrapping `label`. For compound controls, reproduce the field spans in a `div.field.field--full`, then place the input and button inside their own row.

## 3. Design tokens and typography

The system uses the global root tokens rather than isolated colors:

| Role                          | Exact value                              |
| ----------------------------- | ---------------------------------------- |
| App background                | `--bg: #0b100e`                          |
| Popup surface and fade color  | `--surface: #111815`                     |
| Raised control/button surface | `--surface-2: #17201c`                   |
| Hovered raised surface        | `--surface-3: #1d2823`                   |
| Standard border               | `--line: #29362f`                        |
| Soft border                   | `--line-soft: #202b26`                   |
| Main text                     | `--text: #edf4f0`                        |
| Supporting text               | `--muted: #8fa099`                       |
| Tertiary/icon text            | `--muted-2: #66776f`                     |
| Accent                        | `--accent: #89e6b4`                      |
| Strong accent                 | `--accent-strong: #59c98e`               |
| Text on primary accent        | `--accent-ink: #07150e`                  |
| Error                         | `--danger: #ff7d79`                      |
| Popup shadow                  | `--shadow: 0 22px 60px rgb(0 0 0 / 30%)` |

Use the root font stack: `Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. The app uses `color-scheme: dark`, `font-synthesis: none`, and optimized text rendering.

Typography inside the compact form:

- Modal title: `17px`, default bold heading weight, zero margin.
- Modal description: `12px`, `--muted`, `6px` top margin, zero other margins.
- Field label and location legend: `12px`/`11px` respectively, weight `650`.
- Field hint: `11px`, `--muted`, `line-height: 1.45`, minimum height `15px` to stabilize vertical rhythm.
- Field error: `11px`, `--danger`.
- Native control and button text: `12px`; buttons use weight `650`.
- Location card title: `11px`; description: `9px`, `line-height: 1.35`.
- Guidance and feedback: `12px`; guidance line-height `1.55`, feedback line-height `1.45`.

Do not add a separate placeholder color unless the overall application introduces one; the dark `color-scheme` currently supplies native placeholder treatment.

## 4. Modal shell and scrolling

Backdrop:

- Fix to the viewport with `inset: 0` and `z-index: 100`.
- Center with grid `place-items: center`.
- Use `20px` padding so the modal never touches the viewport edge.
- Use `rgb(3 7 5 / 74%)` plus `backdrop-filter: blur(6px)`.

Base modal:

- Use a vertical flex container.
- Use `1px solid #33433b`, `18px` radius, `--surface` background, and `--shadow`.
- The form-specific width is `min(580px, 100%)`.
- The form-specific maximum height is `min(700px, calc(100vh - 40px))`.
- Single-page write popups use content-responsive height up to that maximum.
- Multi-page subagent creation/edit popups use the fixed height `min(700px, calc(100vh - 40px))` so the shell never resizes between pages.
- Set `overflow: hidden` so border-radius clipping is stable.
- Base modal maximum height remains `min(800px, calc(100vh - 40px))`.

Integrated scroll structure:

- `.modal__scroll-frame`: `min-height: 0`, `flex: 1`, flex column.
- Under `.modal--compact-write-form`, also set `position: relative`, `overflow: hidden`, and `isolation: isolate`.
- `.modal__body`: `overflow: auto`; under this form use `min-height: 0`, `flex: 1`, and `padding: 18px 18px 0`.
- `.modal__body--integrated`: `min-height: 0`, `flex: 1`, flex column.
- `.modal__body-heading`: flex row, align start, space-between, `18px` gap; add `15px` bottom margin in this form.
- `.modal__body-heading-actions`: non-shrinking flex row with `4px` gap. Put a multi-page edit Save action here immediately before Close.
- Integrated close control: `28px × 28px`, fixed basis, no border, `7px` radius, transparent background and no shadow; on hover use `rgb(255 255 255 / 5%)`. Its X icon is `14px`.

Edge fades:

- Attach `::before` and `::after` to `.modal__scroll-frame`, never the outer modal. This aligns them with the actual inner viewport rather than spending the strongest stop on the 1px modal border.
- Position absolutely with `left: 0`, `right: 0`, `height: 14px`, `z-index: 4`, and `pointer-events: none`.
- Place the top fade at `top: 0`; use a `180deg` gradient.
- Place the bottom fade at `bottom: 0`; use a `0deg` gradient.
- Use identical stops: `var(--surface) 0%`, `color-mix(in srgb, var(--surface) 72%, transparent) 22%`, `color-mix(in srgb, var(--surface) 34%, transparent) 56%`, `transparent 100%`.
- Span the full frame, including the native scrollbar region. Do not add pseudo-element corner radii; the modal's overflow clipping owns the outer shape.
- Keep `18px` top content padding and `24px` action-row bottom padding so resting headings/actions are clear of the 14px fades.

## 5. Form layout and fields

Base form grid:

- Display grid with `repeat(2, minmax(0, 1fr))` columns.
- Use `18px` global gap, overridden to `13px` inside `.modal--compact-write-form`.
- Give all grid children `min-width: 0` where overflow is possible.
- Use `.field--full { grid-column: 1 / -1; }` for normal model fields, the location fieldset, guidance, and feedback.
- Use `.model-write-modal-form { min-width: 0; }` around the dynamic form.

Field stack:

- `.field`: flex column, `min-width: 0`, `--text`; global gap `7px`, compact-modal gap `4px`.
- Render label first, then a concise hint, then the control, then a field-specific error.
- Keep hint text informative and implementation-neutral. Mention exact URL shapes or compatibility limitations when relevant.
- Preserve the field's native `name`, `type`, `required`, value/default value, and form serialization behavior during visual migrations.

Native controls:

- Base: width `100%`, `--text`, `1px solid --line`, `9px` radius, no outline, `#0d1411` background, `10px 11px` padding, `12px` font.
- Base `input` and `select` minimum height: `40px`.
- Compact modal `input:not([type='radio']):not([type='checkbox'])`, `select`, and `textarea`: minimum height `32px`, padding `6px 9px`, radius `7px`.
- Textareas retain vertical resize and `line-height: 1.55`; if adapting a textarea to the compact form, match its horizontal treatment while retaining enough height for its content.
- Focus: `--accent-strong` border plus `0 0 0 3px rgb(89 201 142 / 10%)` ring.
- Disabled: opacity `0.65`, `not-allowed` cursor.
- Transitions: border color and box shadow over `0.15s`.

Compound embedding model picker:

- Use grid columns `minmax(0, 1fr) auto` with `8px` gap.
- Keep the Discover button at least `92px` wide inside the compact modal (`112px` globally).
- At `max-width: 700px`, collapse the compound picker to one column.
- Put discovery errors immediately after the compound row as `.field__error`.

## 6. Location option cards

Semantic structure:

- Use `fieldset.model-location-options.field--full` with a real `legend`.
- Use one wrapping `label.model-location-option` per radio choice so the entire card activates the input.
- Place the radio, decorative icon wrapper, and copy wrapper inside the label. CSS orders the radio last.
- Keep icons `aria-hidden`; expose the choice through the radio and visible label text.

Fieldset and grid:

- Reset fieldset with `min-width: 0`, zero margin/padding, and no border.
- Legend: `--text`, `11px`, weight `650`; global bottom margin `8px`, compact margin `6px`.
- Grid: two equal `minmax(0, 1fr)` columns; global gap `10px`, compact gap `8px`.

Cards:

- Base card: flex row, vertically centered, `min-width: 0`, `--muted`, `1px solid --line`, `#0d1411`, pointer cursor.
- Global geometry: minimum height `62px`, gap `10px`, padding `11px 12px`, radius `10px`.
- Compact geometry: minimum height `48px`, gap `8px`, padding `7px 9px`, radius `8px`.
- Transition color, border color, and background over `150ms ease`.
- Hover and selected: `--text`, border `rgb(89 201 142 / 52%)`, background `rgb(89 201 142 / 7%)`.
- Focus-within: no outline; use `0 0 0 3px rgb(89 201 142 / 10%)`.

Radio:

- Order last and push right with `margin: 0 0 0 auto`.
- Use `accent-color: --accent`, pointer cursor, zero padding.
- Global size/basis `15px`; compact size/min-height/basis `13px`.

Icon and copy:

- Icon wrapper: grid centered, `--muted-2`, `1px solid --line-soft`, `rgb(255 255 255 / 2%)`.
- Global icon wrapper: `30px` square/flex basis, `8px` radius.
- Compact icon wrapper: `24px` square/flex basis, `6px` radius.
- Use a `16px` Lucide icon inside the wrapper unless the target needs a semantically clearer icon at the same visual weight.
- On hover/selected, use `--accent` icon color and `rgb(89 201 142 / 32%)` border.
- Copy wrapper: `min-width: 0`, grid, `3px` gap. Title inherits state color; description remains `--muted`.

## 7. Guidance and feedback

Guidance block:

- Span the whole grid.
- Use `1px solid #294839`, `#12221a` background, `#bad4c6` text, `12px`, and `line-height: 1.55`.
- Global geometry is `13px 15px` padding and `10px` radius; compact modal geometry is `9px 11px` and `8px` radius.
- Use a bold leading phrase only when it improves scanning, such as `Cloud LLM:`. Keep all remaining copy regular weight.

Feedback block:

- Return no DOM when the message is empty.
- Use `role="alert"` for errors and `role="status"` for success/info.
- Base geometry: `11px 13px` padding, `9px` radius, `12px`, `line-height: 1.45`.
- Error: `#ffc0bd` text, `rgb(255 125 121 / 10%)` background, `1px solid rgb(255 125 121 / 20%)`.
- Success: `#bff3d5` text, `rgb(89 201 142 / 10%)` background, `1px solid rgb(89 201 142 / 20%)`.
- Info: `#bfdcff` text, `rgb(121 184 255 / 10%)` background, `1px solid rgb(121 184 255 / 20%)`.
- Place form-level feedback in a `.field--full` wrapper at the end of the form.

## 8. Buttons and actions

Shared base button:

- Inline flex, centered content, global minimum height `38px`, `8px` gap, `1px solid --line`, `9px` radius, `8px 14px` padding.
- Use `--surface-2`, `--text`, `12px`, weight `650`, pointer cursor.
- Compact form override: minimum height `32px`, `6px` gap, `5px 10px` padding, `7px` radius.
- Hover: border `#44574e`, background `--surface-3`.
- Active: translate down `1px`.
- Disabled/busy: opacity `0.48`, `not-allowed` cursor.
- Spinner: `15px` square, `2px solid currentColor`, transparent right border, circular, `0.7s linear infinite` rotation.

Primary button:

- Use `--accent-strong` background/border and `--accent-ink` text.
- Hover with `--accent` background/border.
- Keep action-row primary writes text-only, such as `Create model` or `Save changes`. Do not add a plus, save, or edit icon there; the icon-only multi-page heading Save described below is the deliberate exception.

Action row:

- Flex, right aligned, `9px` gap, `14px` top margin, `24px` bottom padding.
- Do not add a Back control to Model, MCP server, or Workflow creation/edit forms. Close dismisses the popup; reopening creation restarts staged type selection.
- Multi-page creation wizards may keep explicit Previous navigation when moving backward is part of the form flow.
- Keep the primary submit linked to the form by `form={formId}` and expose busy state through the shared `Button`.

Multi-page edit heading action:

- Render one icon-only primary Save button immediately left of the integrated Close button.
- Use a `15px` Save icon in a `28px` square button to match the Close control.
- Link dynamic form submission with `type="submit"` and `form={formId}`; use the form's established imperative save handler only when no native form exists.
- Give it an explicit `aria-label` and `title`, and show the shared busy spinner while saving.
- Do not render another Save or Back action in individual edit pages or at the bottom of the multi-page form.

## 9. Interaction states

Implement and inspect every applicable state:

- Default: subdued borders and dark control surfaces.
- Hover: visibly stronger surface/border without layout change.
- Focus: accent ring visible with keyboard navigation; do not rely on color alone.
- Selected radio card: same visual family as hover, persistent while checked.
- Disabled: visually reduced and non-interactive, with native `disabled` attributes.
- Busy: disable the submit and render the shared spinner; keep layout stable.
- Error: show nearest field error when field-specific, or the full-width alert for submission/discovery failures.
- Scroll: content passes beneath the surface-colored top/bottom fades; close button and scrollbar stay operable because fades ignore pointer events.
- Escape/backdrop close: preserve `Modal` behavior. Preserve any unsaved-change confirmation already owned by the calling page.

## 10. Responsive behavior

- Width remains `min(580px, 100%)`; backdrop padding supplies 20px side gutters.
- Maximum height remains `min(700px, calc(100vh - 40px))`; multi-page popups take that full height, while single-page popups stop at their natural content height.
- At `max-width: 720px`, collapse `.form-grid` to one column, reset `.field--full` to the automatic grid column, and collapse `.model-location-options__grid` to one column.
- At `max-width: 700px`, collapse `.embedding-model-picker` to one column.
- Preserve `min-width: 0` on wrappers and `minmax(0, 1fr)` tracks to prevent long URLs, paths, provider names, or translated copy from forcing horizontal overflow.
- Preserve the same type sizes and 32px compact controls on mobile; responsiveness comes from reflow, not further miniaturization.
- Verify at approximately `1280×800` and `390×700`, plus any target-specific breakpoint.

## 11. Adaptation rules

When matching a page, drawer, or card form rather than a modal:

- Copy the inner rhythm—13px form gap, 4px field gap, compact controls, guidance, feedback, and choice cards.
- Keep the host container's established width, padding, scrolling, heading, and action placement unless the user explicitly requests modal parity.
- Add one scope class to the host and place compact overrides beneath it. Avoid changing global `.field`, `input`, `.button`, or `.form-grid` values solely for one migration.

When the target has different content:

- Keep labels short and concrete.
- Keep hints directly useful; wrap naturally and never truncate configuration limitations.
- Preserve conditional rendering and dynamically discovered options.
- Use the two-column choice-card layout only for peer choices that benefit from description and icon treatment.
- Use native selects for ordinary enumerations and native inputs for free-form/configuration values.
- Retain full paths and exact endpoint examples when they help users configure local or compatible services.

## 12. Verification checklist

Structure and behavior:

- The form submits through its original handler and payload.
- Every visible label is associated with its control.
- Every icon-only button has an `aria-label`; decorative icons are hidden from assistive technology.
- Required, password, URL, radio, select, datalist, and disabled semantics remain intact.
- Conditional fields, discovery, saved-secret behavior, feedback, and unsaved-change handling still work.

Visual inspection:

- Modal is 580px maximum width and 700px maximum content height with 20px viewport clearance.
- Heading and resting actions stay outside the 14px fade depth.
- Fades begin on the actual scroll frame, use `--surface`, span under the scrollbar, and match at top/bottom.
- Field, guidance, card, button, border, type, radius, and gap values match this specification.
- Long content wraps with no horizontal scroll.
- Mobile grids collapse cleanly and actions remain usable.

Tooling:

- Run `npm run build` from `frontend/`.
- Run Prettier in check mode on every touched frontend source.
- Run `git diff --check` from the repository root.
- Review the focused diff and avoid formatting or rewriting unrelated dirty files.
