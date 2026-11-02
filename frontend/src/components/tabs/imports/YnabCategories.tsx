import { PMSelect } from '../accounts/ds'
import type { CategoryChoices, Preview, PreviewCategory } from './runs'

// The Categories tab of the YNAB review: each YNAB category under its group, with where it goes
// in this book and one line on what its budget will do. A category goes with one of the book's
// categories (pre-selected when one reads the same), into one of its envelopes, or becomes a new
// category. The choice and the line under it are the server's answer for the choice made, so a
// change is re-checked by the review like an account's.

// The choice shown for a category that matches several of the book's: no answer yet.
export const ASK = 'ask'

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
          <th>Category</th>
          <th>In this book</th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g.id}>
          <tr className="ynr-group">
            <th scope="rowgroup" colSpan={2}>
              {g.name}
              <span className="ynr-dim">
                {' '}
                · {g.categories.length}
                {g.group ? ` · matches your group ${g.group.name}` : ' · a new group here'}
              </span>
            </th>
          </tr>
          {g.categories.map((c) => (
            <tr key={c.id}>
              <td data-label="Category">{c.name}</td>
              <td data-label="In this book">
                <CategoryDestination category={c} choices={choices} preview={p} {...pick} />
              </td>
            </tr>
          ))}
        </tbody>
      ))}
    </table>
  )
}

// A category made new from one hidden in YNAB comes in hidden, and the line under it says so. One
// matched with a category of yours is left as it is.
const noteOf = (c: PreviewCategory) =>
  c.hidden && (c.link === null || c.link.kind === 'envelope')
    ? `${c.note.replace(/\.$/, '')}. Hidden in YNAB, so hidden here too`
    : c.note

// The options for one category: what it matches (when the plan proposed it), a new category, the
// other categories and envelopes of the book that no other YNAB category has taken.
function optionsFor(
  c: PreviewCategory,
  choices: CategoryChoices,
  taken: Set<string>,
  proposed: string,
) {
  const name = (ref: string) =>
    choices.categories.find((x) => x.ref === ref)?.name ??
    choices.envelopes.find((x) => x.ref === ref)?.name ??
    ''
  const free = (ref: string) => !taken.has(ref) || ref === proposed
  const out: { value: string; label: string }[] = []
  if (c.ask) out.push({ value: ASK, label: 'Choose one…' })
  if (proposed && free(proposed))
    out.push({
      value: proposed,
      label: proposed.startsWith('env:')
        ? `Into your envelope ${name(proposed)}`
        : `Matches your ${name(proposed)}`,
    })
  out.push({ value: '', label: 'New category' })
  const ambiguous = new Set(c.options.map((o) => o.ref))
  const rest = [
    ...choices.categories.map((x) => ({ ref: x.ref, label: `Use your ${x.name}` })),
    ...choices.envelopes.map((x) => ({ ref: x.ref, label: `Into your envelope ${x.name}` })),
  ]
  const shown = rest.filter((x) => x.ref !== proposed && free(x.ref))
  shown.sort((a, b) => Number(ambiguous.has(b.ref)) - Number(ambiguous.has(a.ref)))
  for (const x of shown) out.push({ value: x.ref, label: x.label })
  return out
}

function CategoryDestination({
  category: c,
  choices,
  preview: p,
  catLinks,
  proposals,
  checking,
  onChoose,
}: { category: PreviewCategory; choices: CategoryChoices; preview: Preview } & CategoryProps) {
  if (c.link?.kind === 'linked')
    return (
      <div className="ynr-cat">
        <span className="ynr-dim">{c.link.name} (linked)</span>
        <span className="ynr-dim ynr-catnote">{noteOf(c)}</span>
      </div>
    )
  // Another category's explicit choice is not offered here again.
  const taken = new Set(
    (p.category_groups ?? [])
      .flatMap((g) => g.categories)
      .filter((x) => x.id !== c.id && x.link && x.link.kind !== 'linked')
      .map((x) => x.link!.ref),
  )
  const value = c.ask ? ASK : (c.link?.ref ?? '')
  const options = optionsFor(
    c,
    choices,
    taken,
    proposals[c.id] === ASK ? '' : (proposals[c.id] ?? ''),
  )
  return (
    <div className="ynr-cat">
      <PMSelect
        className="ynr-dest"
        ariaLabel={`Where ${c.name} goes`}
        value={catLinks[c.id] ?? value}
        disabled={checking}
        searchable={options.length > SEARCH_FROM}
        searchPlaceholder="Search categories…"
        emptyText="No category matches."
        options={options}
        onChange={(v) => v !== ASK && onChoose(c.id, v)}
      />
      <span className="ynr-dim ynr-catnote">{noteOf(c)}</span>
    </div>
  )
}
