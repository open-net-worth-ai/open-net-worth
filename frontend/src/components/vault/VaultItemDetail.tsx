import { useEffect, useRef } from 'react'
import type { VaultItem } from '../../api/vault'
import { isStandaloneApp, useEdgeSwipeBack } from '../../hooks/useEdgeSwipeBack'
import type { ArtifactRef } from '../../types/chat'
import { ArrowLeft, Lock, X } from '../../ui/icons'
import { DocumentPreview } from './DocumentPreview'
import { markFades } from './fades'
import { ActionBar, ManageCard, NoteCard, SettingsCard, TagsRow } from './VaultDetailSections'
import { formatDay, formatSize, kindLabel } from './vaultFormat'

function artifactRef(item: VaultItem): ArtifactRef {
  return {
    id: item.source_id,
    title: item.title,
    filename: item.filename,
    artifactType: item.artifact_type ?? 'html',
    sizeBytes: item.size_bytes ?? undefined,
  }
}

function facts(item: VaultItem): [string, string][] {
  const rows: [string, string | null | undefined][] = [
    // The list's kind label for an asset file is its asset's name — here that is its own row.
    ['Kind', item.kind === 'asset' ? 'Asset file' : kindLabel(item)],
    ['Account', item.account_name],
    ['Asset', item.asset_name],
    ['Statement date', formatDay(item.statement_date)],
    ['Transaction date', formatDay(item.transaction_date)],
    ['Added', formatDay(item.added_at)],
    ['Size', formatSize(item.size_bytes)],
    ['File', item.filename],
  ]
  return rows.filter((r): r is [string, string] => !!r[1])
}

export function VaultItemDetail({
  item,
  onBack,
  onClose,
  onOpenArtifact,
  onAsked,
  onTag,
}: {
  item: VaultItem
  onBack: () => void
  onClose: () => void
  onOpenArtifact: (a: ArtifactRef) => void
  onAsked?: () => void
  onTag?: (tag: string) => void
}) {
  const pageRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  // Fade the page at an edge with more to scroll to — re-measured as a preview loads and
  // the page grows (ResizeObserver on its parts), not only on scroll.
  useEffect(() => {
    const body = bodyRef.current
    markFades(body)
    if (!body || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => markFades(body))
    for (const part of Array.from(body.children)) observer.observe(part)
    return () => observer.disconnect()
  }, [item.id])
  // A swipe from the left edge goes back to the list, like any pushed iOS view.
  useEdgeSwipeBack(pageRef, onBack, isStandaloneApp())
  return (
    <div className="vault-detail" ref={pageRef}>
      <div className="vault-head">
        <button
          type="button"
          className="vault-icon-btn"
          onClick={onBack}
          aria-label="Back to Documents"
        >
          <ArrowLeft size={17} strokeWidth={1.7} />
        </button>
        <h2 className="vault-title vault-title--item">{item.title}</h2>
        <button
          type="button"
          className="vault-icon-btn"
          onClick={onClose}
          aria-label="Close Documents"
        >
          <X size={18} strokeWidth={1.6} />
        </button>
      </div>
      <div className="vault-detail-body" ref={bodyRef} onScroll={(e) => markFades(e.currentTarget)}>
        <DocumentPreview item={item} onOpenArtifact={() => onOpenArtifact(artifactRef(item))} />
        <ActionBar item={item} artifact={artifactRef(item)} onAsked={onAsked} />
        {item.private && (
          <p className="vault-private-note">
            <Lock size={13} strokeWidth={1.8} aria-hidden="true" /> Private — Lucius can see this
            file’s name, note and tags, never what is in it.
          </p>
        )}
        {!!item.tags?.length && <TagsRow tags={item.tags} onTag={onTag} />}
        {item.note && <NoteCard note={item.note} />}
        <section className="vault-section">
          <h3 className="vault-section-title">Details</h3>
          <dl className="vault-card">
            {facts(item).map(([k, v]) => (
              <div key={k} className="vault-card-row vault-fact">
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </section>
        {(item.kind === 'file' || item.kind === 'asset') && (
          // Keyed on the stored year: a saved change (or one made by Lucius) resets the input.
          <SettingsCard key={`${item.id}:${item.year ?? ''}`} item={item} />
        )}
        <ManageCard item={item} onRemoved={onBack} />
      </div>
    </div>
  )
}
