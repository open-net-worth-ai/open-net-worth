// Settings → Data. Ported from the DataSection in
// design_handoffs/settings_handoff/prototype/redesign/settings.jsx.
//
// The prototype's backup/upload lists were fixtures; here they are the real ones. Two things
// the handoff explicitly left unwired are wired: the typed RESET confirmation actually gates
// the button, and Restore actually restores (the server writes a safety backup first).
import { useRef, useState } from 'react'
import { authFetch } from '../../../../api/client'
import { desktopBridge } from '../../../../platform/desktop'
import { openReportProblem } from '../../../../state/reportProblem'
import { useCachedFetchState } from '../../../../state/queryCache'
import { fDate } from '../../../../utils/format'
import { Clock } from '../../../../ui/icons'
import { Badge, Btn, Field, Modal, Panel, Toggle, row, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { UploadedFileRow } from './UploadedFileRow'
import { fmtSize } from './uploadFiles'
import type { UploadRow } from './uploadFiles'

interface BackupRow {
  name: string
  created_at: string
  file_count: number
  size_bytes: number
  trigger: 'auto' | 'manual'
}

interface BackupSettings {
  enabled: boolean
  retention_days: number
}

function fmtWhen(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const today = new Date()
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const time = d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  if (sameDay(d, today)) return `Today, ${time}`
  if (sameDay(d, yesterday)) return `Yesterday, ${time}`
  return `${fDate(d)}, ${time}`
}

/** Download a blob response through a header'd fetch — a top-level navigation cannot set the
 *  CSRF header these endpoints require. */
async function downloadFrom(path: string, fallbackName: string): Promise<string | null> {
  let res: Response
  try {
    res = await authFetch(path, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
  } catch {
    // Every other handler here catches; without this a dropped connection was an unhandled
    // rejection AND a silent no-op — the button just did nothing.
    return 'Could not reach the server. Try again.'
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: string } | null
    return body?.detail || `Download failed (HTTP ${res.status})`
  }
  let filename = fallbackName
  const match = (res.headers.get('Content-Disposition') || '').match(/filename="?([^"]+)"?/i)
  if (match && match[1]) filename = match[1]
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return null
}

export function DataSection({ toast }: SectionProps) {
  const backups = useCachedFetchState<{ backups: BackupRow[]; last_backup_at: string | null }>(
    '/api/backup',
    async () => {
      const r = await authFetch('/api/backup')
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    },
    { staleTime: 0 },
  )
  const settings = useCachedFetchState<BackupSettings>(
    '/api/backup/settings',
    async () => {
      const r = await authFetch('/api/backup/settings')
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    },
    { staleTime: 0 },
  )
  const uploads = useCachedFetchState<UploadRow[]>(
    '/api/uploads',
    async () => {
      const r = await authFetch('/api/uploads')
      if (!r.ok) throw new Error(String(r.status))
      const data = (await r.json()) as { uploads?: UploadRow[] }
      return data.uploads ?? []
    },
    { staleTime: 0 },
  )
  useSectionBusy(
    (backups.initialLoading && !backups.data) ||
      (settings.initialLoading && !settings.data) ||
      (uploads.initialLoading && !uploads.data),
  )

  const [confirmReset, setConfirmReset] = useState(false)
  const [resetTyped, setResetTyped] = useState('')
  const [confirmRestore, setConfirmRestore] = useState<BackupRow | null>(null)
  const [busy, setBusy] = useState(false)
  // A toast is gone in two seconds; a failed restore or import stays up, beside a way to report it.
  const [dataFailure, setDataFailure] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const rows = backups.data?.backups ?? []
  const uploadRows = uploads.data ?? []
  const lastBackup = backups.data?.last_backup_at

  async function patchSettings(patch: Partial<BackupSettings>, message: string) {
    try {
      const r = await authFetch('/api/backup/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        void settings.refresh()
        return
      }
      void settings.refresh()
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function exportBackup() {
    const bridge = desktopBridge()
    if (bridge) {
      try {
        const result = await bridge.saveExport()
        if (!result.cancelled) toast('Backup exported')
      } catch {
        toast('Could not export your book. Try again.')
      }
      return
    }
    const err = await downloadFrom('/api/data/export', 'open-net-worth-export.zip')
    toast(err || 'Backup exported')
  }

  async function backupNow() {
    setBusy(true)
    try {
      const r = await authFetch('/api/backup/run-now', { method: 'POST' })
      if (!r.ok) {
        toast(`Backup failed (HTTP ${r.status})`)
        return
      }
      const data = (await r.json()) as { size_bytes?: number }
      void backups.refresh()
      toast('Backing up… done' + (data.size_bytes ? ` • ${fmtSize(data.size_bytes)}` : ''))
    } catch {
      toast('Could not reach the server. Try again.')
    } finally {
      setBusy(false)
    }
  }

  function failed(message: string) {
    setDataFailure(message)
    toast(message)
  }

  async function restore(b: BackupRow) {
    setBusy(true)
    setDataFailure(null)
    try {
      const r = await authFetch(`/api/backup/${encodeURIComponent(b.name)}/restore`, {
        method: 'POST',
      })
      if (!r.ok) {
        const body = (await r.json().catch(() => null)) as { detail?: string } | null
        failed(body?.detail || `Restore failed (HTTP ${r.status})`)
        return
      }
      toast('Restored — reloading')
      setTimeout(() => window.location.reload(), 1200)
    } catch {
      failed('Could not reach the server. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function importFile(file: File) {
    setBusy(true)
    setDataFailure(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('replace', 'true')
      const r = await authFetch('/api/data/import', { method: 'POST', body: fd })
      if (!r.ok) {
        const body = (await r.json().catch(() => null)) as { detail?: string } | null
        failed(body?.detail || `Import failed (HTTP ${r.status})`)
        return
      }
      toast('Imported — reloading')
      setTimeout(() => window.location.reload(), 1200)
    } catch {
      failed('Import failed.')
    } finally {
      setBusy(false)
    }
  }

  async function deleteUpload(u: UploadRow) {
    try {
      const r = await authFetch(`/api/uploads/${encodeURIComponent(u.id)}`, { method: 'DELETE' })
      if (!r.ok) {
        toast(`Could not delete (HTTP ${r.status})`)
        return
      }
      void uploads.refresh()
      toast('Deleted — future chat turns can no longer read this file')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function resetInstance() {
    setBusy(true)
    try {
      await authFetch('/api/reset', { method: 'POST' })
      localStorage.clear()
      window.location.reload()
    } catch {
      toast('Reset failed.')
      setBusy(false)
    }
  }

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept=".zip"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void importFile(f)
        }}
      />

      {dataFailure && (
        <Panel title="That didn’t work">
          {row(
            'The restore or import did not finish',
            dataFailure,
            <Btn onClick={() => openReportProblem({ error: dataFailure })}>
              Report this problem
            </Btn>,
          )}
        </Panel>
      )}

      <Panel
        title="Backup & restore"
        sub="Your data is yours. Export a full bundle — a single .zip of every store: accounts, transactions, holdings, history, memory, and chat sessions."
      >
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Btn variant="primary" onClick={() => void exportBackup()}>
            Export backup (.zip)
          </Btn>
          <Btn disabled={busy} onClick={() => fileRef.current?.click()}>
            Choose import file…
          </Btn>
        </div>
        <span className="rd-help">
          Import <b>replaces all current data</b>; a checkpoint is saved first so you can undo it. A
          downloaded backup carries your data, never your password, API token or connector
          credentials.
        </span>
      </Panel>

      <Panel
        title="Automatic backups"
        sub="A full copy of your data is saved automatically once a day, independent of any chat session — so a mistake (yours, Lucius's, or a bug) is always recoverable to within a day, not just for the last ~50 AI turns."
      >
        {row(
          'Back up daily',
          'Runs at 03:00 to the local backups folder.',
          <Toggle
            on={settings.data?.enabled ?? true}
            disabled={!settings.data}
            onChange={(v) =>
              void patchSettings({ enabled: v }, v ? 'Daily backups on' : 'Daily backups off')
            }
            label="Daily backup"
          />,
        )}
        <div className="rd-formgrid">
          <Field label="Keep for (days)" help="Older backups are deleted automatically.">
            <input
              className="rd-input"
              type="number"
              min={1}
              max={365}
              key={settings.data?.retention_days ?? 'na'}
              defaultValue={settings.data?.retention_days ?? 30}
              onBlur={(e) => {
                const next = Number(e.target.value)
                if (!Number.isFinite(next) || next < 1 || next === settings.data?.retention_days)
                  return
                void patchSettings({ retention_days: next }, 'Retention updated')
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
          </Field>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <Btn size="xs" disabled={busy} onClick={() => void backupNow()}>
            Back up now
          </Btn>
          <span className="rd-help">
            {lastBackup ? `Last backup: ${fmtWhen(lastBackup)}` : 'No backup yet'} • {rows.length}{' '}
            kept, listed below
          </span>
        </div>
      </Panel>

      <Panel
        title="Restore from a backup"
        sub="Restoring replaces all current data — a checkpoint is saved first, so it can be undone"
        flush
      >
        {rows.length ? (
          rows.map((b) => (
            <div className="rd-row" key={b.name}>
              <span className="rd-row-ic">
                <Clock size={13} strokeWidth={1.7} aria-hidden="true" />
              </span>
              <div className="rd-row-main">
                <div className="rd-row-title">
                  {fmtWhen(b.created_at)}{' '}
                  {b.trigger === 'manual' ? (
                    <Badge tone="accent">Manual</Badge>
                  ) : (
                    <Badge>Automatic</Badge>
                  )}
                </div>
                <div className="rd-row-meta">
                  <span>{b.file_count} files</span>
                  <span>{fmtSize(b.size_bytes)}</span>
                </div>
              </div>
              <div className="rd-row-actions">
                <Btn
                  size="xs"
                  variant="ghost"
                  onClick={() =>
                    void downloadFrom(
                      `/api/backup/${encodeURIComponent(b.name)}/download`,
                      `open-net-worth-backup-${b.name}.zip`,
                    )
                      .then((err) => toast(err || 'Downloading ' + fmtWhen(b.created_at)))
                      .catch(() => toast('Could not reach the server. Try again.'))
                  }
                >
                  Download
                </Btn>
                <Btn size="xs" onClick={() => setConfirmRestore(b)}>
                  Restore…
                </Btn>
              </div>
            </div>
          ))
        ) : (
          <div style={{ padding: '14px 18px' }}>
            <span className="rd-help">
              No backups yet — run one above and it will show up here.
            </span>
          </div>
        )}
        <div style={{ padding: '10px 18px' }}>
          <Btn size="xs" variant="ghost" disabled={busy} onClick={() => fileRef.current?.click()}>
            Restore from a file instead…
          </Btn>
        </div>
      </Panel>

      <Panel
        title="Uploaded files"
        sub="Files you attach in chat are stored here (so retries and old conversations keep working) until you delete them"
        flush
      >
        {uploadRows.length ? (
          uploadRows.map((u) => (
            <UploadedFileRow key={u.id} upload={u} onDelete={(row) => void deleteUpload(row)} />
          ))
        ) : (
          <div style={{ padding: '14px 18px' }}>
            <span className="rd-help">
              Nothing stored yet — attach a file in chat and it will show up here.
            </span>
          </div>
        )}
      </Panel>

      <section className="rd-panel rd-danger-panel">
        <div className="rd-panel-head">
          <div
            className="rd-panel-title"
            style={{
              color: 'var(--bad)',
              fontSize: 11,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            Danger zone
          </div>
        </div>
        <div className="rd-panel-body">
          {row(
            'Reset instance / clear all data',
            'Permanently deletes all data on this instance — accounts, transactions, positions, conversations, memory, and profile. Your password and sign-in settings are kept. This cannot be undone (export a backup first).',
            <Btn
              variant="danger"
              onClick={() => {
                setResetTyped('')
                setConfirmReset(true)
              }}
            >
              Reset instance…
            </Btn>,
          )}
        </div>
      </section>

      {confirmRestore && (
        <Modal
          title={'Restore the backup from ' + fmtWhen(confirmRestore.created_at) + '?'}
          onClose={() => setConfirmRestore(null)}
          footer={
            <>
              <Btn onClick={() => setConfirmRestore(null)}>Cancel</Btn>
              <Btn
                variant="danger-solid"
                disabled={busy}
                onClick={() => {
                  const b = confirmRestore
                  setConfirmRestore(null)
                  void restore(b)
                }}
              >
                Restore this backup
              </Btn>
            </>
          }
        >
          <p style={{ margin: 0 }}>
            Everything currently on this instance is replaced by the contents of this backup —
            accounts, transactions, holdings, memory, and chat history. Anything added since{' '}
            {fmtWhen(confirmRestore.created_at)} is lost.
          </p>
          <span className="rd-help">
            A fresh backup is written first, so the restore can itself be undone. Your password and
            API token are left alone.
          </span>
        </Modal>
      )}

      <p className="rd-setfoot">
        Price charts are rendered with{' '}
        <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">
          TradingView Lightweight Charts™
        </a>
        . Copyright © 2025 TradingView, Inc.
      </p>

      {confirmReset && (
        <Modal
          title="Reset this instance?"
          onClose={() => setConfirmReset(false)}
          footer={
            <>
              <Btn onClick={() => setConfirmReset(false)}>Cancel</Btn>
              <Btn
                variant="danger-solid"
                disabled={busy || resetTyped.trim() !== 'RESET'}
                onClick={() => void resetInstance()}
              >
                I understand — reset
              </Btn>
            </>
          }
        >
          <p style={{ margin: 0 }}>
            Everything is deleted: accounts, transactions, rules, tasks, skills, connector config,
            and app settings. Your password and sign-in settings are kept. An automatic backup is
            written first, but restoring it is on you. Type <b className="rd-mono">RESET</b> to
            confirm.
          </p>
          <input
            className="rd-input"
            placeholder="RESET"
            aria-label="Type RESET to confirm"
            value={resetTyped}
            onChange={(e) => setResetTyped(e.target.value)}
          />
        </Modal>
      )}
    </>
  )
}
