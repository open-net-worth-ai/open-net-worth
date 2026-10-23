import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { SAFE_MARKDOWN_COMPONENTS } from '../ui/safeMarkdown'
import remarkGfm from 'remark-gfm'
import { Badge, Button, EmptyState, Loading, Panel, Text } from '../ui'
import { KebabMenu, type KebabItem } from '../tabs/planning/KebabMenu'
import { authFetch } from '../../api/client'
import { fDate } from '../../utils/format'
import { getBundleFileContent, isCodeOnlyFile } from './fileTreeUtils'
import type { SelectedFile, Skill, SkillBundle } from '../../types/skills'
import { ArrowLeft } from '../../ui/icons'

interface SkillDetailPaneProps {
  skill: Skill | null
  selectedFile: SelectedFile | null
  bundle: SkillBundle | null
  bundleLoading: boolean
  bundleError: boolean
  mobileActive: boolean
  onToggleEnabled: (skill: Skill) => void
  toggling: boolean
  onEdit: (skill: Skill) => void
  onDelete: (skill: Skill) => void
  onRun: (skill: Skill) => void
  onChanged: () => void
  onBack: () => void
}

type ViewMode = 'preview' | 'code'

/**
 * Right detail pane: header (title + enable toggle + kebab), meta grid, description,
 * horizontal divider, file viewer (full-width), footer Run button.
 *
 * The enable toggle controls skill.enabled (can Lucius call this skill).
 * Script execution permissions are instance-level (Settings → Developer), not per-skill.
 *
 * Tool policy and parameter sections do not exist: skills are standard instruction folders.
 */
export function SkillDetailPane({
  skill,
  selectedFile,
  bundle,
  bundleLoading,
  bundleError,
  mobileActive,
  onToggleEnabled,
  toggling,
  onEdit,
  onDelete,
  onRun,
  onBack,
}: SkillDetailPaneProps) {
  const paneClass = ['skill-detail-pane', mobileActive ? 'skill-detail-pane--mobile-active' : '']
    .filter(Boolean)
    .join(' ')

  if (!skill) {
    return (
      <div className={paneClass}>
        <div className="skill-detail-pane__empty">
          <EmptyState
            icon="⚡"
            title="Select a skill"
            body="Choose a skill from the list to view its details."
          />
        </div>
      </div>
    )
  }

  return (
    <div className={paneClass}>
      {/* Mobile back button */}
      <div className="skill-detail-back">
        <Button
          variant="ghost"
          size="sm"
          iconLeft={<ArrowLeft size={14} aria-hidden="true" />}
          onClick={onBack}
        >
          Back
        </Button>
      </div>

      <SkillDetailContent
        skill={skill}
        selectedFile={selectedFile}
        bundle={bundle}
        bundleLoading={bundleLoading}
        bundleError={bundleError}
        onToggleEnabled={onToggleEnabled}
        toggling={toggling}
        onEdit={onEdit}
        onDelete={onDelete}
        onRun={onRun}
      />
    </div>
  )
}

function SkillDetailContent({
  skill,
  selectedFile,
  bundle,
  bundleLoading,
  bundleError,
  onToggleEnabled,
  toggling,
  onEdit,
  onDelete,
  onRun,
}: Omit<SkillDetailPaneProps, 'mobileActive' | 'onBack' | 'skill' | 'onChanged'> & {
  skill: Skill
}) {
  const userInvocable = skill.claude?.user_invocable !== false
  const modelInvocable =
    skill.claude?.disable_model_invocation !== true &&
    skill.openai?.policy?.allow_implicit_invocation !== false
  const runnable = skill.enabled && userInvocable

  // Determine what content to show in the file viewer.
  const fileSelected = selectedFile !== null && selectedFile.skillId === skill.id

  let bodyPath: string
  let bodyContent: string
  if (fileSelected && selectedFile) {
    // An explicit file pick → show that file's RAW content from the bundle: SKILL.md shows the
    // full file (frontmatter + body), scripts/* and other files from bundle.files.
    bodyPath = selectedFile.relativePath
    bodyContent = bundle ? (getBundleFileContent(bundle, bodyPath) ?? '') : (skill.body ?? '')
  } else {
    bodyPath = 'SKILL.md'
    bodyContent = skill.body ?? ''
  }

  const awaitingBundle = fileSelected
  const codeOnly = isCodeOnlyFile(bodyPath)

  // Author label
  const authorLabel =
    skill.author === 'builtin' ? 'Open Net Worth' : skill.author === 'lucius' ? 'Lucius' : 'You'

  const triggerLabel = userInvocable
    ? modelInvocable
      ? 'Slash + automatic'
      : 'Slash only'
    : modelInvocable
      ? 'Automatic only'
      : 'No invocation path'

  async function exportSkill() {
    try {
      const res = await authFetch(`/api/skills/${encodeURIComponent(skill.id)}/export`)
      if (!res.ok) return
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${skill.id}.zip`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      /* best-effort download */
    }
  }

  const kebabItems: KebabItem[] = [{ label: 'Export', onClick: () => void exportSkill() }]
  if (skill.author !== 'builtin') {
    kebabItems.push({ label: 'Edit', onClick: () => onEdit(skill) })
    kebabItems.push({ label: 'Delete', onClick: () => onDelete(skill), danger: true })
  }

  return (
    <>
      <div className="skill-detail-pane__content">
        {/* ── Header: title + enable toggle + kebab ── */}
        <div className="skill-detail-header">
          <div className="skill-detail-header__title">
            <Text as="h2" size="lg" weight={700} truncate title={skill.name || '(unnamed)'}>
              {skill.name || '(unnamed)'}
            </Text>
          </div>
          <div className="skill-detail-header__controls">
            <EnableToggle
              enabled={skill.enabled}
              disabled={toggling}
              onChange={() => onToggleEnabled(skill)}
            />
            {kebabItems.length > 0 && (
              <KebabMenu items={kebabItems} label={`${skill.name} actions`} />
            )}
          </div>
        </div>

        {/* ── Meta grid: Added by / Last updated / Trigger ── */}
        <div className="skill-meta-grid">
          <div className="skill-meta-cell">
            <span className="skill-meta-cell__label">Added by</span>
            <span className="skill-meta-cell__value">
              {authorLabel}
              {skill.author === 'builtin' && (
                <span className="skill-meta-cell__readonly"> • read-only</span>
              )}
            </span>
          </div>
          {(Object.keys(skill.claude ?? {}).length > 0 ||
            Object.keys(skill.openai ?? {}).length > 0) && (
            <div className="skill-meta-cell">
              <span className="skill-meta-cell__label">Compatibility</span>
              <span className="skill-meta-cell__value skill-compat-badges">
                {Object.keys(skill.claude ?? {}).length > 0 && (
                  <Badge tone="neutral">Claude Code</Badge>
                )}
                {Object.keys(skill.openai ?? {}).length > 0 && <Badge tone="neutral">OpenAI</Badge>}
              </span>
            </div>
          )}
          {skill.updated_at && (
            <div className="skill-meta-cell">
              <span className="skill-meta-cell__label">Last updated</span>
              <span className="skill-meta-cell__value">{fDate(skill.updated_at)}</span>
            </div>
          )}
          <div className="skill-meta-cell">
            <span className="skill-meta-cell__label">Trigger</span>
            <span className="skill-meta-cell__value">{triggerLabel}</span>
          </div>
        </div>

        {/* ── Description ── */}
        {skill.description && (
          <div className="skill-description-block">
            <span className="skill-description-block__label">
              Description{' '}
              <span
                className="skill-description-block__info"
                title="The description is how Lucius decides when to use this skill"
                aria-label="The description is how Lucius decides when to use this skill"
              >
                ⓘ
              </span>
            </span>
            <Text tone="muted" size="sm">
              {skill.description}
            </Text>
          </div>
        )}

        {skill.validation_errors && skill.validation_errors.length > 0 && (
          <div className="skill-description-block" role="alert">
            <span className="skill-description-block__label">Invalid skill files</span>
            <Text tone="bad" size="sm">
              Invocation is disabled until these files are repaired:{' '}
              {skill.validation_errors.join('; ')}
            </Text>
          </div>
        )}

        {/* ── File viewer (full-width, primary surface) ── */}
        <FileViewer
          key={`${skill.id}:${bodyPath}`}
          path={bodyPath}
          content={bodyContent}
          codeOnly={codeOnly}
          awaitingBundle={awaitingBundle}
          bundleLoading={bundleLoading}
          bundleError={bundleError}
          hasBundle={!!bundle}
        />
      </div>

      {/* ── Footer: Run button ── */}
      <div className="skill-detail-footer">
        <Button
          variant="primary"
          disabled={!runnable}
          title={
            !skill.enabled
              ? 'Enable the skill to run it'
              : !userInvocable
                ? 'This skill can only be selected automatically'
                : 'Runs the skill in chat — Lucius executes it and results appear there.'
          }
          onClick={() => onRun(skill)}
        >
          Run
        </Button>
        <Text tone="faint" size="xs">
          Runs the skill in chat — Lucius executes it and results appear there.
        </Text>
      </div>
    </>
  )
}

// ── Enable/Disable toggle ─────────────────────────────────────────────────────
// Visual style carried over from the pre-redesign ScriptSkillsPanel (same CSS vars, sizing).

function EnableToggle({
  enabled,
  disabled,
  onChange,
}: {
  enabled: boolean
  disabled: boolean
  onChange: () => void
}) {
  return (
    <div className="skill-enable-toggle">
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={enabled ? 'Disable skill' : 'Enable skill'}
        disabled={disabled}
        className="skill-enable-toggle__track"
        style={{ background: enabled ? 'var(--accent)' : 'var(--surface2)' }}
        onClick={onChange}
      >
        <span
          className="skill-enable-toggle__thumb"
          style={{
            left: enabled ? 23 : 3,
            background: enabled ? '#fff' : 'var(--faint)',
          }}
        />
      </button>
      <span className="skill-enable-toggle__label">{enabled ? 'Enabled' : 'Disabled'}</span>
    </div>
  )
}

// ── Small icon button (view-mode toggle) ─────────────────────────────────────

function IconButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean
  onClick: () => void
  title: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      onClick={onClick}
      className={`skill-viewer-icon-btn${active ? ' skill-viewer-icon-btn--active' : ''}`}
    >
      {children}
    </button>
  )
}

// ── File viewer ───────────────────────────────────────────────────────────────

/**
 * Strip YAML frontmatter (--- ... ---) from a markdown string for the preview view.
 * The frontmatter is still visible in code view.
 */
function stripFrontmatter(content: string): string {
  if (!content.startsWith('---')) return content
  const end = content.indexOf('\n---', 3)
  if (end === -1) return content
  return content.slice(end + 4).trimStart()
}

function FileViewer({
  path,
  content,
  codeOnly,
  awaitingBundle,
  bundleLoading,
  bundleError,
  hasBundle,
}: {
  path: string
  content: string
  codeOnly: boolean
  awaitingBundle: boolean
  bundleLoading: boolean
  bundleError: boolean
  hasBundle: boolean
}) {
  const defaultMode: ViewMode = codeOnly ? 'code' : 'preview'
  const [mode, setMode] = useState<ViewMode>(defaultMode)

  const filename = path === 'SKILL.md' ? 'SKILL.md' : (path.split('/').pop() ?? path)

  return (
    <Panel className="skill-file-viewer">
      {/* Header: filename + two icon buttons */}
      <div className="skill-file-viewer__header">
        <Text size="sm" weight={600} tone="muted">
          {filename}
        </Text>
        {!codeOnly && (
          <div className="skill-file-viewer__view-btns">
            <IconButton
              active={mode === 'preview'}
              onClick={() => setMode('preview')}
              title="Preview"
            >
              {/* Eye icon */}
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M8 3C4.5 3 1.5 8 1.5 8s3 5 6.5 5 6.5-5 6.5-5-3-5-6.5-5z"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinejoin="round"
                />
                <circle cx="8" cy="8" r="1.8" stroke="currentColor" strokeWidth="1.4" />
              </svg>
            </IconButton>
            <IconButton active={mode === 'code'} onClick={() => setMode('code')} title="Code">
              {/* Code brackets icon */}
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </IconButton>
          </div>
        )}
      </div>

      {/* Loading / error / empty states */}
      {awaitingBundle && bundleLoading && <Loading label="Loading file…" />}
      {awaitingBundle && bundleError && (
        <Text tone="faint" size="xs">
          Could not load the bundle source.
        </Text>
      )}
      {awaitingBundle && !bundleLoading && !bundleError && !hasBundle && (
        <Text tone="faint" size="xs">
          No bundle loaded.
        </Text>
      )}

      {/* Content */}
      {(!awaitingBundle || hasBundle) &&
        (mode === 'preview' && !codeOnly ? (
          <div className="skill-body-preview">
            {content ? (
              <ReactMarkdown
                remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
                components={SAFE_MARKDOWN_COMPONENTS}
              >
                {stripFrontmatter(content)}
              </ReactMarkdown>
            ) : (
              <Text tone="faint" size="sm">
                (no instructions)
              </Text>
            )}
          </div>
        ) : (
          <CodeWithLines content={content || '(empty)'} />
        ))}
    </Panel>
  )
}

// ── Code view with line-number gutter (CSS counter, no new deps) ──────────────

function CodeWithLines({ content }: { content: string }) {
  const lines = content.split('\n')
  return (
    <div className="skill-code-viewer">
      {lines.map((line, i) => (
        <div key={i} className="skill-code-viewer__row">
          <span className="skill-code-viewer__line-num" aria-hidden="true">
            {i + 1}
          </span>
          <span className="skill-code-viewer__line">{line || ' '}</span>
        </div>
      ))}
    </div>
  )
}
