# Approval cards — the design system

The card Lucius shows before a write is the one place a user reads money facts and decides,
so it has to read the same for every tool. It used to be different for each card type. The
card CSS used eight text sizes (10, 11.5, 12, 13px and .76–.88em), and one card put a 17px
figure beside 11px lines.

Every card type is now built from the primitives in
`frontend/src/components/chat/approval/ApprovalCard.tsx`. Two tests hold the line:

- `approval/approvalCardCss.test.ts`: every `font-size` on the card (`.ac-*`, `.change-*`,
  `.grant-split*`) is one of the two tokens.
- `approval/anatomy.test.tsx`: renders every fixture in `approval/fixtures/*.ts` and fails on
  any legacy per-card class or inline style.

## Where things live

| Piece | File |
|---|---|
| Primitives | `approval/ApprovalCard.tsx` |
| Header (title + subject), shared by the live and read-only card | `approval/CardHeader.tsx` |
| Per-type body dispatch, shared likewise | `approval/CardBody.tsx` |
| Bodies by area | `approval/bodies/{Position,Disposal,SkillAgent,Code,Txn,Budget,Generic}*.tsx` |
| Classification / formatting helpers | `approval/cardTypes.ts` |
| One realistic payload per card variant | `approval/fixtures/*.ts` |
| Card state, buttons, resolution | `ChangePreview.tsx` |

**See every card at once:** run the frontend dev server and open `/?gallery=cards`. It renders
every fixture at chat-panel width. It is dev-only and lazy-loaded, so the fixtures never ship
in the app bundle. A new card type gets a fixture, and the gallery and the anatomy test pick it
up automatically.

## Type: two sizes, no others

| Token | Size | For |
|---|---|---|
| `--ac-text` | 12px | every line: header, body, buttons |
| `--ac-caps` | 10px | table column headings, the subject pill, badges |

Emphasis is a **tone**, never a size: `strong` (600), `good` / `bad` (600 + P&L colour), `warn`,
`muted`.

## Anatomy: always this order

```
Record sale  VOO • Meridian Wealth           ← header: the action + its subject (CardHeader)
Sell 3 @ $615.00                 $1,845.00    ← ApprovalLead: what the header can't say + the figure
Oct 11, 2026 · FIFO                           ← ApprovalMeta: muted, " · "-joined
Realized gain                    +$424.92     ← ApprovalFact rows: muted label, value right
Cash                           not booked     ←   (ApprovalChange for an edit: old → new)
▸ 3 lots · all long-term                      ← ApprovalDetails: tables / raw args, folded
Reason: Duplicate of the October 9 buy        ← a card's own reason, last, one muted line
[Allow once] [Always allow]    Deny         ← actions (GrantActions): grouped [Allow once ▾] when narrow
```

- **Header.** The action ("Record sale", "Update automations") and its subject pill. A generic
  gate card is titled by its tool, never a bare "Tool permission". A destructive write
  (delete, remove) tones the header red, once.
- **Lead.** Never repeats the header's action or subject. It adds only what the header can't
  say: a quantity, an amount, a direction, a purpose. If there is nothing to add, there is no
  lead. A skill create is just its header.
- **Meta.** Facts the user typed or that rarely matter (date, method, fees), on one muted line.
- **Facts.** Only what the user is deciding on. A fact the lead already states is not repeated.
- **Details.** Anything tabular or long goes behind a disclosure. Its summary still carries the
  decision-relevant fact: "3 lots · all long-term", not "Lots".
- **Actions** (`approval/GrantActions.tsx`). The allow scopes sit on the left and **Deny** at
  the right end. With room (a row of at least 300px), **Allow once** and **Always allow** are
  separate buttons. On a narrow card they group into one `[Allow once ▾]` split button, with
  **Always allow** behind the caret. The row measures itself, so one card adapts across the
  side panel, the dock and the Lucius tab. **Allow once** is the filled primary button.
  **Always allow** is a quiet button with primary-coloured text, never the warning tint;
  what it commits you to is in its title.

The other primitives are `ApprovalTable` (compact table: one rule under the headings and none between rows; cells wrap
at spaces, never mid-word; `numeric` columns align right and never wrap; a table still too
wide for a phone scrolls sideways with an edge shadow on the side that has more),
`ApprovalCode` (source / JSON), `ApprovalDiff` + `ApprovalDiffLine` (line diffs; `ApprovalDiffWord` marks the changed words in a paired removed/added line),
`ApprovalSection` (a titled group on a card that changes several things), `ApprovalToggle`
("Show 3 unchanged lines") and `ApprovalNote` (below).

## Copy

- **No paragraphs in the body.** A caveat is a fact's value (`not booked`). The explanation
  goes in that fact's `title` (hover) and the product docs.
- **ApprovalNote** is one short line that must be seen before approving: a warning or a
  consequence. It is never an explanation.
- **Say a thing once.** That applies across the header, lead, facts and notes.
- **A warning must be true in every case it shows.** If it depends, say the condition ("net
  worth drops unless the cash is already in an account") or don't claim it.
- Labels are sentence case words ("Cost basis"), never snake_case.
- **Few separators.** No line between table rows and no `·` inside a table cell. A secondary
  detail (where a category came from, which account) goes on a muted line under its value. A
  value that is the same for every row (one account) is said once, as a fact, not repeated in
  a column.
- **A phone is 320px wide.** A table's amount column must never be the one cut off. Fold extra
  columns into muted lines under the main one, as the import card does.

## Using it

```tsx
<ApprovalLead figure={fCurrency(total, ccy)}>Sell {qty} @ {fCurrency(price, ccy)}</ApprovalLead>
<ApprovalMeta items={[fDate(date, 'short', 'always'), 'FIFO', fees > 0 && `${fees} fees`]} />
<ApprovalFacts>
  <ApprovalFact label="Realized gain" tone="good">+$424.92</ApprovalFact>
  <ApprovalFact label="Cash" tone="warn" title="Why it matters…">not booked</ApprovalFact>
  <ApprovalChange label="Category" before="Dining" after="Transport · as specified" />
</ApprovalFacts>
<ApprovalDetails summary="3 lots · all long-term"><table>…</table></ApprovalDetails>
```

`bodies/DisposalPreview.tsx` (the sale card) is the reference implementation.
