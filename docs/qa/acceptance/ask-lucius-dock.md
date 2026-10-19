# Acceptance — Ask Lucius: Floating Dock, Mentions & Screen Awareness

Design authority: `frontend/design_handoffs/ask_lucius_handoff/` (prototype `mentions/app.jsx` + `mentions.css`). Verify on the running app via `make qa-e2e` / `make qa-cu`, never `:5174`/`:8080` directly.

## AL1 — Floating dock present everywhere
- **AL1.1** GIVEN any tab (Overview, Cash, Investments, Plan, Tax, Asset Detail) WHEN the page loads THEN a "✦ Ask Lucius" pill is anchored bottom-right (`.lx-dock.lx-collapsed .lx-dock-pill`), fixed position, independent of layout.
- **AL1.2** GIVEN the collapsed pill WHEN hovered or clicked THEN it expands to the composer card (`.lx-dock-inner`: head + composer + suggestions); on click the composer is focused.
- **AL1.3** GIVEN the expanded dock WHEN Escape is pressed or an outside click lands (not on a drag source / picker / detail) THEN it collapses back to the pill.
- **AL1.4** GIVEN the dock WHEN "Open in side panel" is clicked THEN the side panel (`.lx-side`) opens, the dock hides, and the composer contents (text + chips) + caret-at-end transfer to the side panel composer.

## AL2 — Composer, mentions & identity
- **AL2.1** GIVEN the composer WHEN `@` is typed at a word boundary THEN the inline picker opens grouped: Screen context → On this screen → Deeper sources → Talk to → entity type groups → Everywhere (backend).
- **AL2.2** GIVEN the picker WHEN a query naming a TYPE is typed ("stocks", "agents", "subscriptions") THEN items of that type surface even when the word isn't in their name (type-synonym search), with a "why" hint.
- **AL2.3** GIVEN a picked entity THEN an inline chip with the correct identity (glyph/swatch for records, monogram/brand for agents, `/command` for skills) appears in the text flow; typing `@` again immediately after the chip's trailing space re-opens the picker.
- **AL2.4** GIVEN an agent is picked as recipient THEN it renders "To · <agent>"; a second agent pick becomes a dashed context chip (single-recipient rule). Skills (`/`) render as verb chips; multiple allowed.
- **AL2.5** GIVEN a sent message THEN its chips render read-only (no ×), tappable to a detail popover; backend search failures show a retry row, never a crash.

## AL3 — Screen awareness (two-layer + transparency)
- **AL3.1** GIVEN screen-awareness ON THEN the dock/side shows "<recipient> can see this screen" with the screen title + in-view counts + N sources, plus a switch to turn it off (persisted).
- **AL3.2** GIVEN the Peek is opened in either the dock or side panel THEN it floats above its awareness bar, stays inside the visible viewport without clipping, and shows "Visible now — passed to Lucius" facts + "Also on this screen — fetched only if needed" handle list with `+ pin`/`✓ pinned`; WHEN its owning host closes THEN the Peek closes with it.
- **AL3.3** GIVEN a question like "what's the first transaction here?" on Cash THEN the answer reflects the topmost VISIBLE row (positional), counting every row; "summarize all" uses the also-on-screen tier.
- **AL3.4** GIVEN a question needing a handle (a news story, a chart's series, an unopened tab) THEN Lucius shows an animated "Reading <source>…" status and returns a citation chip `[n]`; per-turn fetch cap = 3.
- **AL3.5** GIVEN a handle is pinned in the Peek THEN it is force-fetched on the next send (`handles[].pinned=true`), then pins clear.

## AL4 — Right-click, drag-in, attachments
- **AL4.1** GIVEN any row with `data-entity-id` WHEN right-clicked THEN an "✦ Ask Lucius about this" menu appears and attaches that entity as a chip in the dock.
- **AL4.2** GIVEN any `[data-entity-id][draggable]` row WHEN dragged onto the dock/side dropzone THEN it attaches as a chip (delegated; no per-row JS), with a drag-ghost preview and drop-target highlight.
- **AL4.3** GIVEN the composer WHEN a file/image is added via paperclip, paste, or file-drag THEN an attachment chip row appears with a detail popover; cleared on send.
- **AL4.4** GIVEN a Plan budget row THEN it carries `envelope:<envelope id>` (EVERY row — linked, zero-category, and LOCKED bot-managed rows alike; reorder stays disabled on locked rows while the mention drag works) and the drag ghost/chip reads ENVELOPE, never CATEGORY. Typing `@` and searching an envelope's name (or "envelopes") surfaces it; the resolved mention carries the row's month slice (assigned/activity/available/status, payment fields on a payment envelope).
- **AL4.5** GIVEN the Manage-categories dialog THEN every row (all four kinds) carries `category:<category id>` and is drag-able/right-clickable per AL4.1/AL4.2 (drag pauses while renaming); a dragged category with NO budget row (an inflow like Equity Compensation, an unlinked expense) still resolves — label/kind/`budgeted=false` — never "(not found)".
- **AL4.6** GIVEN an envelope, category, or account with a display color THEN its drag ghost dot, composer chip glyph, sent-message chip glyph, and picker icon all tint with that color (transaction chips keep the green/red direction tint instead); a colorless entity falls back to its type color.

## AL5 — Plumbing intact (regression)
- **AL5.1** Tool-call pills, run-code permission cards, fetch pills, ChangePreview (confirm-before-run incl. money-movement), and ArtifactCards still render inside the conversation, unchanged in behavior.
- **AL5.2** Streaming, conversations (new/switch/rename/delete), model picker, effort levels, suggestions, scroll-pin, and side-panel resize all still work.

## States
- **Empty:** side panel empty hero ("Talk about anything in your money", @ / `/` / drag hint); dock shows screen-aware starter suggestions.
- **Loading:** thinking shimmer label + spinner; "Reading…" pulse for handle fetch.
- **Error:** picker backend failure → retry row; handle hydrate failure → cite the source with an error note, turn still completes.
