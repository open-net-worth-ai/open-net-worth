import { Check, LuciusMark, X } from '../../ui/icons'

// Scan progress indicator — distinct from the chat "Ask Lucius" flow. A centered
// bottom pill: a spinner while the scan runs, then a teal-check summary that
// auto-dismisses. State is owned by Intel.tsx (see runScan).
export type ScanState =
  | { phase: 'scanning' }
  | { phase: 'done' | 'error' | 'pending'; msg: string }
  | null

export function ScanToast({ scan, onClose }: { scan: ScanState; onClose: () => void }) {
  if (!scan) return null
  const scanning = scan.phase === 'scanning'
  const error = scan.phase === 'error'
  return (
    <div
      className="intel-scan-toast mb-anchored"
      role={error ? 'alert' : 'status'}
      aria-live={error ? 'assertive' : 'polite'}
    >
      <div
        className={`intel-scan-pill${error ? ' intel-scan-pill--error' : scan.phase === 'done' ? ' intel-scan-pill--done' : ''}`}
      >
        {scanning ? (
          <span className="intel-scan-spinner" aria-hidden />
        ) : (
          <span className="intel-scan-check" aria-hidden>
            {error ? <X size={13} /> : <Check size={13} />}
          </span>
        )}
        <div className="intel-scan-text">
          <div className="intel-scan-title">
            {scanning ? (
              <>
                Analysing key developments{' '}
                <span className="intel-scan-spark">
                  <LuciusMark size="1em" aria-hidden="true" />
                </span>
              </>
            ) : error ? (
              'Scan failed'
            ) : scan.phase === 'pending' ? (
              'Scan processing'
            ) : (
              'Up to date'
            )}
          </div>
          <div className="intel-scan-sub">
            {scanning ? 'Re-scanning your holdings and watchlists…' : scan.msg}
          </div>
        </div>
        {!scanning && (
          <button type="button" className="intel-scan-close" onClick={onClose} aria-label="Dismiss">
            <X size={14} />
          </button>
        )}
      </div>
    </div>
  )
}
