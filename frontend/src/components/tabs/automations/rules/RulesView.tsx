// The Rules view of Automations: transaction rules that run by fixed logic — no AI, instant on
// every new transaction. Owns its list actions and the editor; the rows live in the page's
// `automations` state so a Lucius-made Rule (or any write) shows here without a second fetch.
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, Plus, Search, Wrench } from 'lucide-react'
import {
  createRule,
  deleteAutomation,
  reorderRules,
  updateAutomation,
  updateRule,
  type Automation,
} from '../../../../api/automations'
import { authFetch } from '../../../../api/client'
import { useQueryCache } from '../../../../state/queryCache'
import { Button, EmptyState, Modal } from '../../../ui'
import { LuciusMark, Workflow } from '../../../../ui/icons'
import { AutomationMenu } from '../AutomationMenu'
import type { AutomationOptions } from '../AutomationTriggerSummary'
import { RuleEditorModal } from './RuleEditorModal'
import { RuleList } from './RuleList'
import { RuleTemplates } from './RuleTemplates'
import { nextPosition, rulesInOrder, type RuleForm } from './ruleModel'
import { peekRuleSeed, takeRuleSeed } from './ruleSeed'
import './rules.css'

const CATEGORIES_KEY = '/api/categories'

const NEW_RULE_PROMPT =
  'Draft a new transaction Rule for me. Ask which transactions it should match and what it should ' +
  'do to them, then show me the completed rule before creating it.'

async function fetchCategories(): Promise<Array<{ id: string; label: string }>> {
  const response = await authFetch(CATEGORIES_KEY)
  if (!response.ok) throw new Error(`Categories could not be loaded (${response.status})`)
  return (await response.json()) as Array<{ id: string; label: string }>
}

const messageOf = (error: unknown, fallback: string) =>
  error instanceof Error && error.message ? error.message : fallback

export function RulesView({
  automations,
  setAutomations,
  options,
  onError,
  onAskLucius,
}: {
  automations: Automation[]
  setAutomations: React.Dispatch<React.SetStateAction<Automation[]>>
  options: AutomationOptions
  onError: (message: string | null) => void
  /** Opens Lucius with this message; omitted where there is no chat. */
  onAskLucius?: (text: string) => void
}) {
  const [query, setQuery] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [editor, setEditor] = useState<{ rule?: Automation; seed?: RuleForm } | null>(() => {
    const seed = peekRuleSeed()
    return seed ? { seed } : null
  })
  const [deleteTarget, setDeleteTarget] = useState<Automation | null>(null)
  useEffect(() => {
    takeRuleSeed()
  }, [])
  const categories = useQueryCache(CATEGORIES_KEY, fetchCategories).data ?? []

  const rules = useMemo(() => rulesInOrder(automations), [automations])
  const needle = query.trim().toLocaleLowerCase()
  const visible = needle
    ? rules.filter((r) => `${r.name} ${r.instructions}`.toLocaleLowerCase().includes(needle))
    : rules

  const replaceRows = (changed: Automation[]) =>
    setAutomations((rows) => rows.map((row) => changed.find((c) => c.id === row.id) ?? row))

  const move = async (id: string, toIndex: number) => {
    const from = rules.findIndex((rule) => rule.id === id)
    if (from < 0 || from === toIndex) return
    const order = [...rules]
    const [moved] = order.splice(from, 1)
    order.splice(toIndex, 0, moved!)
    const previous = rules
    replaceRows(order.map((r, i) => ({ ...r, execution: { ...r.execution, position: i } })))
    onError(null)
    try {
      replaceRows(await reorderRules(order.map((rule) => rule.id)))
    } catch (error) {
      replaceRows(previous)
      onError(messageOf(error, 'The order could not be saved.'))
    }
  }

  const toggle = async (rule: Automation) => {
    const status = rule.status === 'paused' ? 'active' : 'paused'
    onError(null)
    setBusyId(rule.id)
    replaceRows([{ ...rule, status }])
    try {
      replaceRows([await updateAutomation(rule.id, { status })])
    } catch (error) {
      replaceRows([rule])
      onError(messageOf(error, 'The rule could not be updated.'))
    } finally {
      setBusyId(null)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setBusyId(deleteTarget.id)
    onError(null)
    try {
      await deleteAutomation(deleteTarget.id)
      setAutomations((rows) => rows.filter((row) => row.id !== deleteTarget.id))
      setDeleteTarget(null)
    } catch (error) {
      onError(messageOf(error, 'The rule could not be deleted.'))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="a3-wrap" data-od-id="rules-list">
      <div className="a3-toolbar" data-od-id="rules-toolbar">
        <label className="a3-search">
          <Search size={15} strokeWidth={1.6} aria-hidden="true" />
          <span className="ui-visually-hidden">Search rules</span>
          <input
            type="search"
            value={query}
            placeholder="Search rules…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <span className="grow" />
        {onAskLucius ? (
          <AutomationMenu
            triggerClassName="a3-new-button"
            popoverClassName="a3-new-pop"
            label="New rule"
            trigger={
              <>
                <Plus className="a3-toolbar-ic" size={14} aria-hidden="true" />
                <span className="a3-toolbar-label">New rule</span>
                <ChevronDown
                  className="a3-toolbar-chevron"
                  size={12}
                  strokeWidth={1.7}
                  aria-hidden="true"
                />
              </>
            }
            items={[
              {
                label: 'Create with Lucius',
                icon: (
                  <span className="a3-menu-ic lucius">
                    <LuciusMark size={13} aria-hidden="true" />
                  </span>
                ),
                onSelect: () => onAskLucius(NEW_RULE_PROMPT),
              },
              {
                label: 'Set up manually',
                icon: (
                  <span className="a3-menu-ic">
                    <Wrench size={14} aria-hidden="true" />
                  </span>
                ),
                onSelect: () => setEditor({}),
              },
            ]}
          />
        ) : (
          <Button
            size="sm"
            variant="primary"
            iconLeft={<Plus size={14} aria-hidden="true" />}
            onClick={() => setEditor({})}
          >
            New rule
          </Button>
        )}
      </div>
      {rules.length === 0 ? (
        <EmptyState
          icon={<Workflow size={28} strokeWidth={1.5} aria-hidden="true" />}
          title="No rules yet"
          body="A rule sets the category, payee or tags of each new transaction, or splits it — instantly, with no AI. Start from a template below."
        />
      ) : (
        <>
          {visible.length === 0 ? (
            <div className="a3-empty">
              <span>Nothing matches “{query}”</span>.{' '}
              <button type="button" className="a3-linkbtn" onClick={() => setQuery('')}>
                Clear the search
              </button>
            </div>
          ) : (
            <RuleList
              rules={visible}
              reorderable={!needle}
              busyId={busyId}
              onMove={(id, to) => void move(id, to)}
              onToggle={(rule) => void toggle(rule)}
              onEdit={(rule) => setEditor({ rule })}
              onDelete={setDeleteTarget}
            />
          )}
          <p className="a3-muted rl-note">
            Rules run from top to bottom. When two change the same thing, the lower one wins. Drag a
            rule by its handle to change the order.
          </p>
        </>
      )}
      {!needle && (
        <RuleTemplates categories={categories} onTemplate={(seed) => setEditor({ seed })} />
      )}
      {editor && (
        <RuleEditorModal
          rule={editor.rule}
          seed={editor.seed}
          options={options}
          categories={categories}
          position={nextPosition(rules)}
          onClose={() => setEditor(null)}
          onSubmit={async (draft) => {
            if (editor.rule) replaceRows([await updateRule(editor.rule.id, draft)])
            else {
              const saved = await createRule(draft)
              setAutomations((rows) => [...rows, saved])
            }
            setEditor(null)
          }}
        />
      )}
      <Modal
        open={deleteTarget !== null}
        title="Delete this rule?"
        onClose={() => setDeleteTarget(null)}
        footer={
          <>
            <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button
              variant="danger"
              loading={busyId === deleteTarget?.id}
              onClick={() => void confirmDelete()}
            >
              Delete rule
            </Button>
          </>
        }
      >
        <p>
          “{deleteTarget?.name}” stops applying to new transactions. Transactions it already changed
          stay as they are.
        </p>
      </Modal>
    </div>
  )
}
