import { PMSelect } from '../accounts/ds'
import type { CategoryChoices, Preview, PreviewCategory } from './runs'

// The Categories tab of the YNAB review: each YNAB category under its group, then two choices in
// their own columns. Category: one of the book's categories (pre-selected when one reads the same
// or close) or a new one. Envelope: only a NEW category has one to pick, a new envelope or one of
// the book's; a category of yours keeps the envelope it is in, since nothing is moved. Both make
// one choice for the server (``cat:…``, ``env:…`` or ``""``), re-checked by the review like an
// account's.

// The choice shown for a category that matches several of the book's: no answer yet.
export const ASK = 'ask'
const NEW = 'new'

// A list longer than this is searched, not scrolled.
const SEARCH_FROM = 8

export interface CategoryProps {
  catLinks: Record<string, string>
  proposals: Record<string, string>
  checking: boolean
  onChoose: (ynabId: string, ref: string) => void
}

export function Categories({ preview: p, ...pick }: { preview: Preview } & CategoryProps) {
  const groups = p.category_groups
  if (!groups || !p.category_choices)
    return <p className="ynr-empty">Review again to see the categories by name.</p>
  if (groups.length === 0)
    return <p className="ynr-empty">This plan has no categories of its own.</p>
  const choices = p.category_choices
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>YNAB category</th>
          <th>Category here</th>
          <th>Envelope</th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g.id}>
          <tr className="ynr-group">
            <th scope="rowgroup" colSpan={3}>
              {g.name}
              <span className="ynr-count">{g.categories.length}</span>
              <span className="ynr-groupnote">
                {g.group ? `Matches your group ${g.group.name}` : 'A new group here'}
              </span>
            </th>
          </tr>
          {g.categories.map((c) => (
            <CategoryRow key={c.id} category={c} choices={choices} preview={p} {...pick} />
          ))}
        </tbody>
      ))}
    </table>
  )
}

type RowProps = { category: PreviewCategory; choices: CategoryChoices; preview: Preview }

function CategoryRow({ category: c, choices, preview: p, ...pick }: RowProps & CategoryProps) {
  if (c.link?.kind === 'linked')
    return (
      <tr>
        <td data-label="YNAB category" className="ynr-celltext">
          {c.name}
        </td>
        <td data-label="Category here" className="ynr-celltext">
          {c.link.name}
        </td>
        <td data-label="Envelope" className="ynr-celltext ynr-dim">
          {c.note}
        </td>
      </tr>
    )
  // Another row's choice is not offered here again.
  const taken = new Set(
    (p.category_groups ?? [])
      .flatMap((g) => g.categories)
      .filter((x) => x.id !== c.id && x.link && x.link.kind !== 'linked')
      .map((x) => x.link!.ref),
  )
  const proposed = pick.proposals[c.id] === ASK ? '' : (pick.proposals[c.id] ?? '')
  const ref = pick.catLinks[c.id] ?? (c.ask ? ASK : (c.link?.ref ?? ''))
  const ours = ref.startsWith('cat:')
  // Two of the book's ENVELOPES read the same: the pick is asked for in the envelope column.
  const askEnv = ref === ASK && c.options.every((o) => o.ref.startsWith('env:'))
  const free = (r: string) => !taken.has(r) || r === proposed
  const choose = (r: string) => pick.onChoose(c.id, r)
  const toNew = () =>
    choose(proposed.startsWith('env:') && free(proposed) ? proposed : (c.offer ?? ''))
  return (
    <tr>
      <td data-label="YNAB category" className="ynr-celltext">
        {c.name}
      </td>
      <td data-label="Category here">
        <div className="ynr-cat">
          <PMSelect
            className="ynr-dest"
            ariaLabel={`Category here for ${c.name}`}
            value={ref === ASK && !askEnv ? ASK : ours ? ref : NEW}
            disabled={pick.checking}
            searchable={choices.categories.length + 1 > SEARCH_FROM}
            searchPlaceholder="Search your categories…"
            emptyText="No category matches."
            menuWidth={280}
            options={categoryOptions(c, choices, free, proposed, askEnv)}
            onChange={(v) => (v === NEW ? toNew() : v !== ASK && choose(v))}
          />
          {categoryNote(c, askEnv ? '' : ref)}
        </div>
      </td>
      <td data-label="Envelope" className={ours ? 'ynr-celltext ynr-dim' : undefined}>
        {ref === ASK && !askEnv ? null : ours ? (
          c.note
        ) : (
          <div className="ynr-cat">
            <PMSelect
              className="ynr-dest"
              ariaLabel={`Envelope for ${c.name}`}
              value={ref}
              disabled={pick.checking}
              searchable={choices.envelopes.length + 1 > SEARCH_FROM}
              searchPlaceholder="Search your envelopes…"
              emptyText="No envelope matches."
              menuWidth={280}
              options={envelopeOptions(c, choices, free, proposed, askEnv)}
              onChange={(v) => v !== ASK && choose(v)}
            />
            <span className="ynr-dim ynr-catnote">
              {askEnv
                ? 'More than one of yours has this name. Pick one.'
                : envelopeNote(c, ref, choices)}
            </span>
          </div>
        )}
      </td>
    </tr>
  )
}

function categoryNote(c: PreviewCategory, ref: string) {
  const text =
    ref === ASK
      ? 'More than one of yours has this name. Pick one.'
      : ref.startsWith('cat:')
        ? c.close && c.link?.ref === ref && !c.chosen
          ? 'Similar name'
          : null
        : c.hidden
          ? 'Hidden in YNAB, so hidden here too'
          : null
  return text && <span className="ynr-dim ynr-catnote">{text}</span>
}

function envelopeNote(c: PreviewCategory, ref: string, choices: CategoryChoices) {
  const env = choices.envelopes.find((e) => e.ref === ref)
  if (env && env.holds > 0) return "Holds other categories too, so its Assigned won't sync"
  if (!env && c.offer) {
    const offered = choices.envelopes.find((e) => e.ref === c.offer)
    if (offered) return `Your ${offered.name} has this name but holds other categories`
  }
  return 'Assigned syncs with YNAB'
}

// The categories a row can go with: the suggested one and those it reads the same as first.
function categoryOptions(
  c: PreviewCategory,
  choices: CategoryChoices,
  free: (ref: string) => boolean,
  proposed: string,
  askEnv: boolean,
) {
  const first = new Set([proposed, ...c.options.map((o) => o.ref)])
  const mine = choices.categories
    .filter((x) => free(x.ref))
    .sort((a, b) => Number(first.has(b.ref)) - Number(first.has(a.ref)))
  return [
    ...(c.ask && !askEnv ? [{ value: ASK, label: 'Choose one…' }] : []),
    { value: NEW, label: 'New category' },
    ...mine.map((x) => ({ value: x.ref, label: x.name })),
  ]
}

// The envelopes a new category can go in: the one of the same name first, then empty ones.
function envelopeOptions(
  c: PreviewCategory,
  choices: CategoryChoices,
  free: (ref: string) => boolean,
  proposed: string,
  askEnv: boolean,
) {
  const asked = new Set(c.options.map((o) => o.ref))
  const rank = (e: CategoryChoices['envelopes'][number]) =>
    e.ref === proposed || e.ref === c.offer || asked.has(e.ref) ? 0 : e.holds === 0 ? 1 : 2
  const mine = choices.envelopes.filter((e) => free(e.ref)).sort((a, b) => rank(a) - rank(b))
  return [
    ...(askEnv ? [{ value: ASK, label: 'Choose one…' }] : []),
    { value: '', label: 'New envelope' },
    ...mine.map((e) => ({ value: e.ref, label: e.name })),
  ]
}
