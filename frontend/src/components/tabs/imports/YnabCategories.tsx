import { Mail, Tag, TriangleAlert } from 'lucide-react'
import { InfoPopover } from '../../ui'
import { PMSelect } from '../accounts/ds'
import { YNAB_APP, type BudgetAppInfo } from '../automations/datasync/budgetApps'
import type { CategoryChoices, Preview, PreviewCategory } from './runs'

// The Categories tab of the YNAB review: each YNAB category under its group, then two choices in
// their own columns. Category: one of the book's categories (pre-selected when one reads the same
// or close) or a new one. Envelope: only a NEW category has one to pick, a new envelope or one of
// the book's; a category of yours keeps the envelope it is in, since nothing is moved. Both make
// one choice for the server (``cat:…``, ``env:…`` or ``""``), re-checked by the review like an
// account's. Every row has the same shape — a chip or a dropdown in each column, in the app's own
// tag and envelope colours — and no line under it: anything worth checking is a warning beside
// the choice, read by hovering or tapping it.

// The choice shown for a category that matches several of the book's: no answer yet.
export const ASK = 'ask'
const NEW = 'new'

// A list longer than this is searched, not scrolled.
const SEARCH_FROM = 8

export interface CategoryProps {
  app?: BudgetAppInfo
  catLinks: Record<string, string>
  proposals: Record<string, string>
  checking: boolean
  onChoose: (ynabId: string, ref: string) => void
}

export function Categories({
  app = YNAB_APP,
  preview: p,
  ...pick
}: { preview: Preview } & CategoryProps) {
  const groups = p.category_groups
  if (!groups || !p.category_choices)
    return <p className="ynr-empty">Review again to see the categories by name.</p>
  if (groups.length === 0)
    return <p className="ynr-empty">{`This ${app.planWord} has no categories of its own.`}</p>
  const choices = p.category_choices
  return (
    <table className="imp-table ynr-table ynr-cats">
      <thead>
        <tr>
          <th>{app.categoryColumn}</th>
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
            <CategoryRow
              key={c.id}
              app={app}
              category={c}
              choices={choices}
              preview={p}
              {...pick}
            />
          ))}
        </tbody>
      ))}
    </table>
  )
}

type RowProps = {
  app: BudgetAppInfo
  category: PreviewCategory
  choices: CategoryChoices
  preview: Preview
}

function CategoryRow({ app, category: c, choices, preview: p, ...pick }: RowProps & CategoryProps) {
  if (c.link?.kind === 'linked')
    return (
      <tr>
        <td data-label={app.categoryColumn}>{c.name}</td>
        <td data-label="Category here">
          <Chip kind="tag" color={colorOf(choices, c.link.ref)} name={c.link.name} />
        </td>
        <td data-label="Envelope">
          <EnvelopeOfYours app={app} category={c} choices={choices} ref_={c.link.ref} />
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
  const chosenEnv = choices.envelopes.find((e) => e.ref === ref)
  const why = categoryWarning(app, c, ref, askEnv)
  return (
    <tr>
      <td data-label={app.categoryColumn}>{c.name}</td>
      <td data-label="Category here">
        <span className="ynr-cell">
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
          {why && <Warn text={why} name={c.name} />}
        </span>
      </td>
      <td data-label="Envelope">
        {ref === ASK && !askEnv ? null : ours ? (
          <EnvelopeOfYours app={app} category={c} choices={choices} ref_={ref} />
        ) : (
          <span className="ynr-cell">
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
            {askEnv ? (
              <Warn text={sameNames(app, c)} name={c.name} />
            ) : (
              chosenEnv &&
              chosenEnv.holds > 0 && <Warn text={shared(app, chosenEnv.name)} name={c.name} />
            )}
          </span>
        )}
      </td>
    </tr>
  )
}

/** The envelope a category of yours is already in — it stays there, so it is shown, not asked. */
function EnvelopeOfYours({
  app,
  category: c,
  choices,
  ref_,
}: {
  app: BudgetAppInfo
  category: PreviewCategory
  choices: CategoryChoices
  ref_: string
}) {
  const mine = choices.categories.find((x) => x.ref === ref_)
  const env = mine?.envelope
  if (!env)
    return c.assigned ? (
      <Chip kind="envelope" name={`${mine?.new_envelope ?? mine?.name ?? c.name} (new)`} />
    ) : (
      <Chip kind="envelope" name="Not budgeted" faint />
    )
  return (
    <span className="ynr-cell">
      <Chip kind="envelope" color={env.color} name={env.name} />
      {env.shared && <Warn text={shared(app, env.name)} name={c.name} />}
    </span>
  )
}

/** A new category or envelope has no colour yet, so its glyph is the faint one. */
function Glyph({ kind, color }: { kind: 'tag' | 'envelope'; color?: string }) {
  const Icon = kind === 'tag' ? Tag : Mail
  return (
    <Icon
      className="ynr-glyph"
      size={13}
      strokeWidth={2.2}
      color={color || 'var(--faint)'}
      aria-hidden="true"
    />
  )
}

function Chip(props: { kind: 'tag' | 'envelope'; name: string; color?: string; faint?: boolean }) {
  return (
    <span className={'ynr-chip' + (props.faint ? ' ynr-chip--faint' : '')}>
      <Glyph kind={props.kind} color={props.faint ? '' : props.color} />
      <span className="ynr-chip-nm">{props.name}</span>
    </span>
  )
}

/** Something worth checking about a choice, read by hovering or tapping the mark beside it. */
function Warn({ text, name }: { text: string; name: string }) {
  return (
    <InfoPopover text={text} triggerClassName="ynr-warn" label={`Check ${name}`}>
      <TriangleAlert size={14} strokeWidth={2.2} aria-hidden="true" />
    </InfoPopover>
  )
}

const shared = (app: BudgetAppInfo, envelope: string) =>
  `${envelope} holds other categories too, so its Assigned won't sync with ${app.name}. Their labels still do.`

const sameNames = (app: BudgetAppInfo, c: PreviewCategory) =>
  `${listed(c.options.map((o) => o.name))} all read the same as ${app.name}'s ${c.name}. Pick the one it is.`

function listed(names: string[]) {
  return names.length < 2
    ? (names[0] ?? '')
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** Why the category picked for a row is worth a look — only when the review guessed it. */
function categoryWarning(app: BudgetAppInfo, c: PreviewCategory, ref: string, askEnv: boolean) {
  if (ref === ASK) return askEnv ? null : sameNames(app, c)
  if (c.chosen || c.link?.ref !== ref) return null
  if (c.among)
    return `${listed(c.options.map((o) => o.name))} all read the same as ${app.name}'s ${c.name}. This one is spelled most like it; change it if it's the wrong one.`
  if (c.close)
    return `Picked because its name is close to ${app.name}'s ${c.name}. Change it if it's the wrong one.`
  return null
}

function colorOf(choices: CategoryChoices, ref: string) {
  return choices.categories.find((x) => x.ref === ref)?.color ?? ''
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
    { value: NEW, label: 'New category', icon: <Glyph kind="tag" /> },
    ...mine.map((x) => ({
      value: x.ref,
      label: x.name,
      icon: <Glyph kind="tag" color={x.color} />,
    })),
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
    { value: '', label: `${c.new_name ?? c.name} (new)`, icon: <Glyph kind="envelope" /> },
    ...mine.map((e) => ({
      value: e.ref,
      label: e.name,
      icon: <Glyph kind="envelope" color={e.color} />,
    })),
  ]
}
