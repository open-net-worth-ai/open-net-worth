import { Skeleton } from '../../ui'
import { COPY } from './following.copy'
import { Crumb } from './Crumb'

/** The asset page's floating progress banner (`asd-refreshing-*`), docked at the bottom of the
 *  view: what is being fetched and why it takes a moment, over a skeleton of what is coming. */
export function FetchBanner({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="fol-fetch-dock">
      <div className="asd-refreshing-banner" role="status" aria-live="polite">
        <span className="asd-refreshing-spinner" aria-hidden="true" />
        <div className="asd-refreshing-text">
          <span className="fol-fetch-title">{title}</span>
          <span className="asd-refreshing-sub">{sub}</span>
        </div>
        <span className="asd-refreshing-bar" aria-hidden="true" />
      </div>
    </div>
  )
}

const rows = (n: number) =>
  Array.from({ length: n }, (_, i) => (
    <div key={i} className="fol-skel-row">
      <div className="fol-skel-name">
        <Skeleton height={14} width={`${46 - (i % 3) * 8}%`} />
        <Skeleton height={11} width="22%" />
      </div>
      <Skeleton height={13} width={72} />
      <Skeleton height={13} width={64} />
      <Skeleton height={13} width={56} />
    </div>
  ))

/** A portfolio page before its data: this page's own shape (the header with its real way back
 *  and the name when known, the actions, the tabs, the chart, the filter chips, the table). */
export function PageSkeleton({ name, onBack }: { name: string; onBack: () => void }) {
  return (
    <div className="fol-page" aria-busy="true" aria-label={COPY.loadingPage}>
      <Crumb name={name || COPY.loadingPage} onBack={onBack} />
      <header className="fol-head">
        <div className="fol-id">
          <h2 className="fol-title">
            {name ? (
              <span className="fol-title-name">{name}</span>
            ) : (
              <Skeleton height={22} width={220} />
            )}
          </h2>
        </div>
        <div className="fol-icons">
          <Skeleton height={32} width={32} />
          <Skeleton height={32} width={32} />
        </div>
        <div className="fol-cta">
          <Skeleton height={32} width={112} />
        </div>
      </header>
      <div className="fol-skel-tabs">
        <Skeleton height={14} width={104} />
        <Skeleton height={14} width={64} />
        <Skeleton height={14} width={68} />
      </div>
      <div className="fol-skel-chart">
        <div className="fol-skel-chart-head">
          <Skeleton height={12} width={84} />
          <Skeleton height={26} width={260} />
        </div>
        <Skeleton height={28} width={96} />
        <Skeleton height={220} />
      </div>
      <div className="fol-skel-chips">
        {[56, 72, 84, 88, 80].map((w) => (
          <Skeleton key={w} height={28} width={w} />
        ))}
      </div>
      <div className="fol-skel-table">{rows(6)}</div>
    </div>
  )
}
