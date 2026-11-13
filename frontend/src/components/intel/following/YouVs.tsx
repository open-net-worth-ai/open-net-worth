import { useState } from 'react'
import type { ReactNode } from 'react'
import { Meter, Skeleton, Text } from '../../ui'
import { ChevronDown } from '../../../ui/icons'
import type { LineRisk, PreviewCompare, Spread } from '../../../api/followedPortfolios'
import { sliceColor } from './donutParts'
import { roundToWhole } from './roundToWhole'
import { COPY } from './following.copy'

// One decimal at the edges, where rounding would say something false (0% or 100% exactly).
const share = (w: number) =>
  `${(w * 100).toFixed((w > 0 && w < 0.1) || (w > 0.99 && w < 1) ? 1 : 0)}%`
const SECTORS_SHOWN = 5

interface Props {
  data: PreviewCompare | undefined
  /** The portfolio's name, as the chart labels its line. */
  name: string
  /** Your line's name: "Your portfolio", or the asset type you picked ("Your crypto"). */
  yourLabel: string
  /** Your line includes owned assets, valued only when you record a value. */
  ownedAssets?: boolean
}

/** Under the "You vs." chart: how each line swings, how much of theirs you hold, and how each
 *  book is spread. Built from this page's own parts (its key-figure row, its table) and the
 *  kit's Meter and Text, with no cards; every figure is the server's (compare.py). */
export function YouVs({ data, name, yourLabel, ownedAssets = false }: Props) {
  if (!data) return <YouVsSkeleton />
  return (
    <>
      <RiskTable data={data} name={name} yourLabel={yourLabel} ownedAssets={ownedAssets} />
      <Overlap data={data} name={name} />
      <SpreadAndSectors data={data} name={name} yourLabel={yourLabel} />
    </>
  )
}

function YouVsSkeleton() {
  return (
    <div className="fol-vs-skel" aria-busy="true" aria-label={COPY.vsLoading}>
      <Skeleton height={96} />
      <Skeleton height={72} />
      <Skeleton height={140} />
    </div>
  )
}

/** Figures as the page's key figures show them (Value · Stocks · Report filed): a quiet label
 *  over the figure, no card around it. */
function Figures({ items }: { items: { label: string; value: ReactNode; note?: string }[] }) {
  return (
    <div className="fol-ret3">
      {items.map((f) => (
        <div key={f.label} className="fol-ret3-item">
          <span className="fol-ret3-l">{f.label}</span>
          <span className="fol-ret3-v">{f.value}</span>
          {f.note && <span className="fol-keyfig-chg">{f.note}</span>}
        </div>
      ))}
    </div>
  )
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="fol-vs-block">
      <h3 className="fol-sec-title">{title}</h3>
      {children}
    </div>
  )
}

const RISK_MEASURES: { key: keyof LineRisk; label: string }[] = [
  { key: 'volatility', label: COPY.riskSwings },
  { key: 'max_drawdown', label: COPY.riskWorstFall },
  { key: 'beta', label: COPY.riskBeta },
  { key: 'sharpe', label: COPY.riskSharpe },
]

const riskFig = (key: keyof LineRisk, r: LineRisk | null) => {
  const v = r?.[key]
  if (v == null) return '—'
  return key === 'beta' || key === 'sharpe' ? v.toFixed(2) : share(Math.abs(v))
}

/** The page's table look (SortTable's markup and classes), for a short table that must stay a
 *  table on a phone: a few figure columns fit, and cards made a figure lose its heading. */
function PlainTable({
  label,
  head,
  rows,
  className,
}: {
  /** A modifier for one table's layout (the top-5 table reads left to right). */
  className?: string
  label: string
  head: ReactNode[]
  /** `total`: the table's closing sum, set apart in weight and by a rule above it. */
  rows: { key: string; cells: ReactNode[]; total?: boolean }[]
}) {
  return (
    <div className="fol-tbl-box">
      <div className="mk-tbl-wrap">
        <div className="mk-tbl-scroll">
          <table
            className={`mk-tbl fol-tbl fol-vs-tbl${className ? ` ${className}` : ''}`}
            aria-label={label}
          >
            <thead>
              <tr>
                {head.map((h, i) => (
                  <th key={i} className={i === 0 ? 'left nostick' : 'nostick'}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className={r.total ? 'data fol-vs-total' : 'data'}>
                  {r.cells.map((c, i) => (
                    <td key={i} className={i === 0 ? 'left' : undefined}>
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

/** A column's heading: the line's swatch from the chart above, then whose it is. */
function Who({ line, label }: { line: string; label: string }) {
  return (
    <span className="fol-vs-who">
      <i className={`fol-line fol-line--${line}`} aria-hidden="true" />
      {label}
    </span>
  )
}

function RiskTable({
  data,
  name,
  yourLabel,
  ownedAssets,
}: {
  data: PreviewCompare
  name: string
  yourLabel: string
  ownedAssets: boolean
}) {
  const { risk } = data
  // One row per measure, a column each for them, you and the benchmark: three figure columns
  // fit a phone, so it stays a table there (as cards, a figure lost its heading).
  const lines = [risk.theirs, risk.yours, risk.spy]
  return (
    <Block title={COPY.riskTitle}>
      {!risk.theirs && !risk.yours ? (
        <p className="fol-foot">{COPY.riskTooShort}</p>
      ) : (
        <>
          {risk.correlation != null && (
            <Text as="p">{COPY.riskCorrelation(name, yourLabel, risk.correlation)}</Text>
          )}
          <PlainTable
            label={COPY.riskTitle}
            head={[
              '',
              // Short: the chart right above names each line in full, in the same colours.
              <Who key="t" line="main" label={COPY.riskThem} />,
              <Who key="y" line="mine" label={COPY.riskYou} />,
              <Who key="b" line="bench" label={risk.benchmark ?? 'SPY'} />,
            ]}
            rows={RISK_MEASURES.map((m) => ({
              key: m.key,
              cells: [m.label, ...lines.map((r) => riskFig(m.key, r))],
            }))}
          />
          {ownedAssets && <p className="fol-foot">{COPY.riskOwnedAssetsCaveat}</p>}
          <p className="fol-foot">{COPY.riskExplain}</p>
        </>
      )}
    </Block>
  )
}

function Overlap({ data, name }: { data: PreviewCompare; name: string }) {
  const o = data.overlap
  return (
    <Block title={COPY.overlapTitle}>
      {o.shared_count === 0 ? (
        <Text as="p" tone="muted">
          {COPY.overlapNone(name)}
        </Text>
      ) : (
        <>
          <Figures
            items={[
              // Said as what you have in common (owner, 2026-11-13): "93% different" had to be
              // turned around to read, and seemed to contradict the table's 21% / 24% below.
              // Overlap counts each shared stock only up to the smaller of its two weights.
              { label: COPY.overlapSame, value: share(1 - o.active_share) },
              // A plain count: the table below gives what those stocks weigh on each side.
              { label: COPY.overlapShared, value: String(o.shared_count) },
            ]}
          />
          {/* Every shared stock, then what they add up to as the table's last row, under the
              columns it totals (owner, 2026-11-13). Nothing is cut. */}
          <PlainTable
            label={COPY.overlapBoth}
            head={[COPY.overlapBoth, COPY.overlapTheirs, COPY.overlapYours]}
            rows={[
              ...o.shared.map((r) => ({
                key: r.symbol,
                cells: [r.symbol, share(r.theirs), share(r.yours)],
              })),
              {
                key: '__total',
                total: true,
                cells: [COPY.overlapTotal, share(o.their_weight_held), share(o.your_weight_shared)],
              },
            ]}
          />
        </>
      )}
      {/* Positions with no ticker yet (a fund of thousands is matched over time) cannot be
          compared with yours: said, so the figures above are read as partial. */}
      {o.unmatched_count > 0 && (
        <p className="fol-foot">
          {COPY.overlapUnmatched(o.unmatched_count, share(o.unmatched_weight))}
        </p>
      )}
    </Block>
  )
}

function SpreadAndSectors({
  data,
  name,
  yourLabel,
}: {
  data: PreviewCompare
  name: string
  yourLabel: string
}) {
  // One colour per sector across both columns: theirs in the order their Overview ring colours
  // them (largest first), then any sector only you hold, continuing the same palette.
  const order = [...data.sectors.theirs, ...data.sectors.yours].map((s) => s.sector)
  const colorOf = new Map([...new Set(order)].map((sector, i) => [sector, sliceColor(i)]))
  return (
    <Block title={COPY.spreadTitle}>
      <div className="fol-vs-pair">
        <SpreadColumn
          label={name}
          spread={data.spread.theirs}
          sectors={data.sectors.theirs}
          colorOf={colorOf}
        />
        <SpreadColumn
          label={yourLabel}
          spread={data.spread.yours}
          sectors={data.sectors.yours}
          colorOf={colorOf}
        />
      </div>
    </Block>
  )
}

function SpreadColumn({
  label,
  spread,
  sectors,
  colorOf,
}: {
  label: string
  spread: Spread
  sectors: { sector: string; weight: number }[]
  colorOf: Map<string, string>
}) {
  // The largest five, then "Rest": tapping it lists the sectors it holds (owner, 2026-11-13).
  const [open, setOpen] = useState(false)
  const restOf = sectors.slice(SECTORS_SHOWN)
  const rest = restOf.reduce((sum, s) => sum + s.weight, 0)
  // Folding one sector behind "Rest" saves nothing: it folds only two or more.
  const folded = restOf.length > 1 && rest > 0.0005 && !open
  const listed = folded ? sectors.slice(0, SECTORS_SHOWN) : sectors
  // The rows on screen, Rest included, always add up to exactly 100.0% (one decimal each,
  // balanced rounding): rounded one by one, a column read 100.2% (owner, 2026-11-13).
  const rounded = roundToWhole([...listed.map((x) => x.weight), ...(folded ? [rest] : [])])
  const pctOf = (i: number) => `${(rounded[i] ?? 0).toFixed(1)}%`
  // Only when there is a Rest row: with none, its slot is past the list's end (that crashed).
  const restPct = folded ? pctOf(listed.length) : ''
  return (
    <div className="fol-vs-col">
      {/* The column's heading: larger and bolder than every label under it, so it reads as
          whose figures these are. No line swatch: there is no chart here for it to key. */}
      <h4 className="fol-vs-col-head">
        <Text size="lg" weight={600} tone="ink">
          {label}
        </Text>
      </h4>
      <Figures
        items={[
          { label: COPY.spreadCount, value: String(spread.count) },
          { label: COPY.spreadTop5, value: share(spread.top5) },
          ...(spread.largest
            ? [
                {
                  label: COPY.spreadLargest,
                  // The stock and its share on one line, the stock at the note's small size.
                  value: (
                    <>
                      <span className="fol-vs-sym">{spread.largest.symbol}</span>
                      {share(spread.largest.weight)}
                    </>
                  ),
                },
              ]
            : []),
        ]}
      />
      {/* Two lists, each under its own heading, so sectors and holdings never run together
          (owner, 2026-11-13). */}
      <div className="fol-vs-subhead">{COPY.sectorsTitle}</div>
      <ul className="fol-vs-sectors" aria-label={COPY.sectorsOf(label)}>
        {listed.map((s, i) => (
          <li key={s.sector}>
            <div className="fol-vs-sector-head">
              <Text size="sm" tone="muted" truncate>
                {s.sector}
              </Text>
              <Text size="sm">{pctOf(i)}</Text>
            </div>
            <Meter
              size="sm"
              value={s.weight * 100}
              color={colorOf.get(s.sector)}
              tone={colorOf.has(s.sector) ? undefined : 'neutral'}
              ariaLabel={`${s.sector} ${pctOf(i)}`}
            />
          </li>
        ))}
        {folded && (
          <li>
            <button
              type="button"
              className="fol-vs-rest"
              aria-expanded={false}
              onClick={() => setOpen(true)}
            >
              <div className="fol-vs-sector-head">
                {/* "Rest" like any sector; "5 more" in the accent with its chevron: a link. */}
                <span className="fol-vs-rest-l">
                  <Text size="sm" tone="muted">
                    {COPY.sectorsRest}
                  </Text>
                  <span className="fol-vs-more">
                    {COPY.sectorsMore(restOf.length)}
                    <ChevronDown size={14} aria-hidden="true" />
                  </span>
                </span>
                <Text size="sm">{restPct}</Text>
              </div>
              <Meter
                size="sm"
                value={rest * 100}
                tone="neutral"
                ariaLabel={`${COPY.sectorsRest} ${restPct}`}
              />
            </button>
          </li>
        )}
      </ul>
      {open && restOf.length > 0 && (
        <button type="button" className="fol-spot-link" onClick={() => setOpen(false)}>
          {COPY.sectorsLess}
        </button>
      )}
      {/* Then its five largest holdings, in the sectors' own style (owner, 2026-11-13): what
          each side is really betting on, in shares, comparable at any size. */}
      {spread.top.length > 0 && (
        <>
          <div className="fol-vs-subhead fol-vs-subhead--next">{COPY.topFiveTitle}</div>
          <ul className="fol-vs-sectors" aria-label={COPY.topFiveOf(label)}>
            {spread.top.map((t) => (
              <li key={t.symbol}>
                <div className="fol-vs-sector-head">
                  <span className="fol-vs-top-sym">{t.symbol}</span>
                  <Text size="sm">{share(t.weight)}</Text>
                </div>
                <Meter
                  size="sm"
                  value={t.weight * 100}
                  // Its sector's colour, as in the sector list above: no stock singled out.
                  color={colorOf.get(t.sector)}
                  tone={colorOf.has(t.sector) ? undefined : 'neutral'}
                  ariaLabel={`${t.symbol} ${share(t.weight)}`}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
