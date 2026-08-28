---
name: match-mounir-model-form
description: Reproduce the compact Mounir write-form visual system in new or existing React forms. Use when creating, editing, restyling, or reviewing a Mounir creation or modification popup that must match the universal model-form density, fields, choices, guidance, feedback, actions, scrolling, edge fades, interaction states, and responsive behavior.
---

# Match Mounir Model Form

Apply the compact write popup as a reusable visual system without changing the target form's data, validation, or provider behavior.

## Load the exact specification

Read [references/form-style-spec.md](references/form-style-spec.md) completely before editing a form. Treat the live project components and CSS named there as the source of truth if they have evolved since the reference was written.

## Workflow

1. Inspect `AGENTS.md` and preserve the project's universal-configuration and visual-consistency rules.
2. Inspect the target form's JSX, state, validation, submit path, conditional fields, error handling, and responsive behavior.
3. Inspect the current model-form sources listed in the reference. Compare computed styles in a browser when the visual result is ambiguous.
4. Classify the target:
   - For a creation or modification modal or wizard, reproduce the integrated modal shell, scroll frame, heading, density, edge fades, and action row.
   - For a form already hosted in a page, card, or drawer, reproduce the inner field system and density without forcing it into a modal.
5. Reuse the shared `Modal`, `Field`, `Button`, and `Feedback` components. Reuse existing classes and tokens before adding selectors. Keep provider-specific behavior isolated inside the form.
6. Preserve semantic structure: use a real `form`, associated labels, `fieldset`/`legend` for grouped choices, native controls, and submit association through `form={formId}` when actions sit outside the form element.
7. Implement the complete state set: default, hover, focus, selected, disabled, busy, validation error, and success/info feedback where applicable.
8. Verify desktop, short-viewport, and narrow/mobile layouts. Exercise the top and bottom scroll limits when the form overflows.
9. Run the frontend build, focused formatting check, and `git diff --check`. Review the final diff for accidental behavioral changes.

## Composition rules

- Keep the modal surface, border, radius, shadow, and fade color token-based. Do not introduce a second near-match color.
- Keep the title, description, close control, form, and actions in one integrated scroll body for the creation wizard.
- Keep the form compact but readable: labels lead, hints explain, controls follow, and errors sit immediately after their control.
- Use full-width fields by default in this narrow form. Use multiple columns only when the source form intentionally pairs short related values.
- Use the location-card pattern only for a genuine mutually exclusive choice. Do not turn ordinary selects or booleans into decorative cards.
- Keep informational guidance structurally separate from fields and span the full form width.
- Keep single-page actions in a right-aligned action row with a text-only primary Create/Save action. Do not add a Back control to Model, MCP server, or Workflow create/edit forms; Close is the only dismissal control, and staged type selection is restarted by reopening creation.
- For a multi-page edit popup, place one icon-only Save action immediately before Close in the integrated heading so it remains available on every page. Give it an accessible name and busy state; do not repeat Save in page content or footers.
- Let single-page popups size to their content up to the shared maximum height. Keep multi-page wizard/edit popups at the maximum available height so pages do not resize the shell.
- Preserve the 14px surface-colored edge fades across the entire scroll frame, including the scrollbar area. Do not attach them to the outer modal border.
- At the mobile breakpoint, collapse form and option grids to one column. Do not shrink text or touch targets further.

## Change discipline

- Do not copy the entire model form when shared primitives can express the same structure.
- Do not hard-code user-specific providers, model IDs, paths, credentials, languages, or options while restyling.
- Do not change API payloads, field names, defaults, discovery, persistence, or validation unless the user separately requests behavior changes.
- Scope compact overrides beneath a host class so unrelated forms retain their established dimensions.
- Preserve unrelated work in a dirty worktree.

## Completion checklist

- Confirm visual parity against every section of the reference.
- Confirm focus visibility, keyboard operation, native form submission, accessible names for icon-only controls, and readable error announcements.
- Confirm the title and resting action controls do not sit beneath the edge fades.
- Confirm no unshaded strip appears beside the scrollbar.
- Confirm long copy wraps without horizontal overflow and dynamic fields do not break the grid.
- Report the files changed and the verification commands run.
