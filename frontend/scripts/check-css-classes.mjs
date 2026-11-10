#!/usr/bin/env node
/**
 * check-css-classes.mjs
 *
 * Bidirectional CSS class guard (Phase C-2).
 *
 * FORWARD pass (TSX → CSS): Scans every .tsx/.ts file under frontend/src/ for
 * static className tokens and verifies each token is defined in one of the CSS
 * files or the allowlist. Template-literal stems are now collected so dynamic
 * variants (e.g. `foo--${x}`) count as defined.
 *
 * REVERSE pass (CSS → TSX): Finds CSS classes defined in the CSS files but
 * referenced nowhere in .tsx/.ts sources (dead code). Report-only by default;
 * blocking under STRICT_DEAD=1.
 *
 * RATCHET: Persists dead-count to scripts/.css-debt.json. Fails if the count
 * grows (the debt floor never increases).
 *
 * SCOPE: only tokens from files listed in the SCOPE_FILES env var (colon-
 * separated) are checked for the forward pass, so CI can scope to "files
 * changed in this PR". When SCOPE_FILES is unset the script checks ALL tsx
 * files and exits 0 if no NEW undefined tokens are found (tokens in the
 * static allowlist are skipped).
 *
 * Usage:
 *   node scripts/check-css-classes.mjs                       # all files
 *   SCOPE_FILES=src/foo.tsx:src/bar.tsx node scripts/check-css-classes.mjs
 *   STRICT_DEAD=1 node scripts/check-css-classes.mjs         # block on dead
 *
 * Conventions enforced:
 *   - Never introduce a className without its CSS in the same change.
 *   - ui/ primitives first; a layout pattern used twice gets promoted to a primitive.
 *   - If a class is dynamic, vendor-prefixed, or intentionally global, add it to
 *     ALLOWLIST below with a brief comment explaining why.
 */

import { readFileSync, readdirSync, writeFileSync, existsSync } from 'fs'
import { join, relative } from 'path'
import { fileURLToPath } from 'url'

const ROOT = fileURLToPath(new URL('../../', import.meta.url))
// All CSS files that participate in the guard (forward + reverse).
const CSS_FILES = [
  join(ROOT, 'frontend/src/index.css'),
  join(ROOT, 'frontend/src/chat.css'),
  join(ROOT, 'frontend/src/lucius.css'),
  join(ROOT, 'frontend/src/components/skills/skills.css'),
  // Investments redesign module CSS (mk-*/lx-*/mvr-*/sec-*/rk-* classes)
  join(ROOT, 'frontend/src/components/tabs/investments/design.css'),
  join(ROOT, 'frontend/src/components/tabs/investments/income.css'),
  join(ROOT, 'frontend/src/components/tabs/investments/activity.css'),
  join(ROOT, 'frontend/src/components/tabs/investments/options.css'),
  join(ROOT, 'frontend/src/components/tabs/investments/equity.css'),
  join(ROOT, 'frontend/src/components/intel/following/following.css'),
  // Accounts redesign module CSS (acct-*/lx-*/imp-*/pm-*/pay-*/txn-* classes)
  join(ROOT, 'frontend/src/components/tabs/accounts/accounts.css'),
  // Statement Import Decide step — verbatim port of the handoff prototype (imp3-* classes)
  join(ROOT, 'frontend/src/components/tabs/accounts/statementImport.css'),
  // Plan redesign module CSS (pl-*/cfx-* classes)
  join(ROOT, 'frontend/src/components/tabs/plan/plan.css'),
  // Pluggable budget-engines lens switcher + 50/30/20 pane (bgl-* classes)
  join(ROOT, 'frontend/src/components/tabs/plan/budgetLens/budgetLens.css'),
  // The budgeting-method menu, Compare modal and first-run chooser (Budget Onboarding cards port).
  join(ROOT, 'frontend/src/components/tabs/plan/budgetLens/methods.css'),
  // The 50/30/20 screen + its onboarding (Budget Onboarding cards port, pt-* classes).
  join(ROOT, 'frontend/src/components/tabs/plan/budgetLens/proportional/propTable.css'),
  // Commitments redesign module CSS (c2-*/c3-* classes)
  join(ROOT, 'frontend/src/components/tabs/commitments/commitments.css'),
  // Assets tab module CSS (as-* classes)
  join(ROOT, 'frontend/src/components/tabs/assets/assets.css'),
  join(ROOT, 'frontend/src/components/intro/intro.css'),
  // Local coding-engine Builder workspace (builder-* classes)
  join(ROOT, 'frontend/src/components/tabs/builder.css'),
  // Unified instruction-first Automations surface (a3-* classes)
  join(ROOT, 'frontend/src/components/tabs/automations.css'),
  // Settings overlay redesign module CSS (rd-set-*/rd-* classes, scoped under .onw-settings)
  join(ROOT, 'frontend/src/components/tabs/settings/settings.css'),
  join(ROOT, 'frontend/src/components/tabs/connectors/connectors.css'),
  // Price alerts manage + create modal — verbatim port of the design prototype (pa-* classes)
  join(ROOT, 'frontend/src/components/intel/priceAlerts/priceAlerts.css'),
  // Overview v2 redesign module CSS (ov-* classes)
  join(ROOT, 'frontend/src/components/tabs/overview/overview.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/netWorth.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/overviewCashflowCore.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/cashflowPlanning.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/investing.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/trackingBudgeting.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/reports.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/content.css'),
  join(ROOT, 'frontend/src/components/tabs/overview/widgets/spendingPace.css'),
  // Lucius home tab shell + persistent history sidebar (lucius-home-* classes)
  join(ROOT, 'frontend/src/components/tabs/lucius/lucius-home.css'),
  // Lucius mobile tab shell (lucius-mobile-* classes)
  join(ROOT, 'frontend/src/components/tabs/lucius/lucius-mobile.css'),
  // The Vault panel (vault-* classes) — docked in the Lucius tab, full screen on the phone
  join(ROOT, 'frontend/src/components/vault/vault.css'),
  // Lucius home tab's right-side artifact viewer (artifact-panel-* classes)
  join(ROOT, 'frontend/src/components/tabs/lucius/artifact-side-panel.css'),
  // Shared artifact "open" viewer (header + body) — artifact-viewer-* classes
  join(ROOT, 'frontend/src/components/tabs/lucius/artifact-viewer.css'),
  // True edge-to-edge artifact takeover — artifact-fullscreen class
  join(ROOT, 'frontend/src/components/tabs/lucius/artifact-fullscreen.css'),
  // Docked chat's own artifact side panel — dock-artifact-panel class
  join(ROOT, 'frontend/src/components/tabs/lucius/dock-artifact-panel.css'),
  // Lucius landing screen's Activity panel (Active/Scheduled sections) — lx-activity-* classes
  join(ROOT, 'frontend/src/components/tabs/lucius/activity-panel.css'),
]
const SRC_DIR = join(ROOT, 'frontend/src')
const DEBT_FILE = join(ROOT, 'frontend/scripts/.css-debt.json')

// ── ALLOWLIST ──────────────────────────────────────────────────────────────
// Each entry must have a comment explaining why it's exempt from the check.
// Categories:
//   [dynamic]  — built at runtime via template literals / conditional joins
//   [vendor]   — third-party component class applied by external library
//   [global]   — browser/Tailwind/reset utility applied everywhere
//   [test]     — appears only in test files, not rendered in production
//   [pre-existing] — existed before the guard was introduced; tracked here
//                    so they surface in future cleanup passes.
const ALLOWLIST = new Set([
  // ── [global] single-word utilities (not project-namespaced) ────────────────
  'sr-only', // screen-reader utility
  'active', // generic interactive state
  'neg', // negative-value color utility
  'muted', // muted text color utility
  'selected', // generic selection state
  'open', // generic open/expanded state
  'ar', // sort-arrow cell span
  'num', // right-aligned numeric cell
  'pos', // positive-value color utility
  'faint', // faint text color utility
  'disabled', // generic disabled state
  'error', // generic error state
  'warn', // generic warning state
  'loading', // generic loading state
  'hidden', // generic visibility
  'focused', // generic focus state
  'checked', // generic checked state
  'expanded', // generic expanded state

  // ── [test] fixture classes used only in test files ────────────────────────
  'my-class', // EntityRow.test.tsx EntityList className passthrough fixture

  // ── [pre-existing] classes used before this guard was introduced ──────────
  // These were present in the codebase before the guard was added. They are
  // valid CSS but their definitions live in inline styles, component-scoped
  // style blocks, or are confirmed to exist at runtime via dynamic classnames.
  // Remove from this list once the class is defined in index.css or removed.
  'acct-sticky-all',
  'agent-detail__memory-fact',
  'agent-improvement__undo-hint',
  'agents-autoapply',
  'agents-perms',
  'artifact-card__action-btn',
  'artifact-card__body',
  'artifact-card__frame',
  'artifact-card__frame--fullscreen',
  'artifact-card__skeleton',
  'artifact-card__skeleton-bar',
  'artifact-inline',
  'artifact-inline__actions',
  'change-confirm-btn',
  'change-confirmed',
  'change-diff-table',
  'change-discard-btn',
  'change-preview',
  'change-preview--readonly',
  'change-preview-actions',
  'change-preview-header',
  'change-preview-label',
  'change-preview-reason',
  'chat-attach-btn',
  'chat-attachment-chip',
  'chat-attachment-name',
  'chat-attachment-thumb',
  'chat-attachments-preview',
  'chat-brand',
  'chat-composer',
  'chat-composer__chips',
  'chat-composer__text',
  'chat-drag-handle',
  'chat-empty',
  'chat-empty-hints',
  'chat-empty-icon',
  'chat-empty-subtitle',
  'chat-empty-title',
  'chat-followup',
  'chat-followups',
  'chat-header',
  'chat-header-actions',
  'chat-header-icon',
  'chat-header-top',
  'chat-hint',
  'chat-hint-skeleton',
  'chat-icon-btn',
  'chat-icon-btn--collapse',
  'chat-input-wrap',
  'chat-messages',
  'chat-model-select',
  'chat-session-cost',
  'chat-textarea',
  'code-run-actions',
  'code-run-source',
  'conn-detail',
  'conn-detail__details',
  'conn-detail__rowlabel',
  'conn-detail__rowvalue',
  'conn-detail__toolperm-list',
  'conn-detail__tools',
  'conv-dropdown',
  'conv-dropdown-header',
  'conv-item-delete',
  'conv-item-meta',
  'conv-item-rename',
  'conv-item-rename-input',
  'conv-item-title',
  'conv-list',
  'conv-new-btn',
  'conv-switcher-arrow',
  'conv-switcher-btn',
  'conv-switcher-title',
  'conv-switcher-wrap',
  'create-menu-item',
  'create-menu-item__label',
  'create-menu-item__subtitle',
  'create-menu-wrap',
  'dedup-decisions-row--header',
  'dedup-decisions-row--match',
  'dedup-decisions-row--pending',
  'dedup-decisions-row--skipped',
  'dedup-decisions-status-badge',
  'dedup-decisions-table',
  'edit-row',
  'emoji-btn',
  'export-modal__section',
  'export-modal__section-title',
  'grant-list',
  'grant-list__empty',
  'grant-list__item',
  'grant-list__item-actions',
  'grant-list__item-meta',
  'grant-list__item-name',
  'grant-list__item-progress',
  'grant-list__item-progress-bar',
  'grant-list__item-progress-fill',
  'grant-list__item-progress-label',
  'grant-list__item-status',
  'grant-list__item-ticker',
  'invoice-table',
  'invoice-table__tax-subtotal',
  'invoice-table__total',
  'mark-vest-form',
  'mention-chip',
  'mention-chip__remove',
  'mention-popup',
  'mention-popup__empty',
  'mention-popup__item',
  'mention-popup__item--active',
  'mention-popup__label',
  'message-bubble',
  'message-bubble--assistant',
  'message-bubble--error',
  'message-bubble--streaming',
  'message-bubble--user',
  'message-bubble__content',
  'message-bubble__header',
  'message-bubble__role',
  'message-bubble__thinking',
  'message-bubble__time',
  'message-bubble__tool-detail',
  'message-bubble__tool-detail-body',
  'message-bubble__tool-detail-toggle',
  'message-bubble__tool-name',
  'message-bubble__tools',
  'message-bubble__tools-count',
  'misc-section',
  'modal-card--wide',
  'modal-overlay--chat',
  'modal-overlay--light',
  'monthly-chart',
  'payee-detail-meta',
  'payee-detail-meta__row',
  'payee-modal__aliases',
  'payee-modal__notes',
  'payee-modal__rules',
  'payee-modal__section-head',
  'payee-modal__txn-list',
  'payee-search',
  'payees-header',
  'payees-list',
  'payees-pane',
  'payees-stats',
  'payees-stats__num',
  'prop-detail',
  'prop-detail__header',
  'prop-detail__payments',
  'prop-detail__title',
  'prop-list',
  'prop-list-item',
  'prop-list-item__meta',
  'prop-list-item__name',
  'prop-list-item__value',
  'recon-result',
  'run-step',
  'run-step__content',
  'run-step__icon',
  'run-step__meta',
  'run-steps',
  'run-steps--expanded',
  'runs-empty',
  'runs-list',
  'runs-list__header',
  'runs-list__item',
  'runs-list__item-actions',
  'runs-list__item-details',
  'runs-list__item-meta',
  'runs-list__item-status',
  'runs-list__item-title',
  'runs-section',
  'runs-section__content',
  'runs-section__header',
  'runs-section__toggle',
  'sell-form',
  'sell-form-cols',
  'sell-form-grid',
  'skill-code-header',
  'skill-code-preview',
  'skill-list-item',
  'skill-list-item__actions',
  'skill-list-item__info',
  'skill-list-item__meta',
  'skill-list-item__name',
  'skill-list-item__status-badge',
  'skills-empty',
  'skills-header',
  'skills-list',
  'skills-pane',
  'sp-chart-row',
  'sp-chart-row__bar',
  'sp-chart-row__bar-wrap',
  'sp-chart-row__label',
  'sp-chart-row__value',
  'sp-summary-row',
  'sp-summary-row__cat',
  'sp-summary-row__pct',
  'sp-summary-row__val',
  'tab-panel-placeholder',
  'table-amounts',
  'table-amounts__footer',
  'table-amounts__row',
  'target-list',
  'target-list-item',
  'target-list-item__detail',
  'target-list-item__heading',
  'target-list-item__meta',
  'target-list-item__progress',
  'target-list-item__progress-bar',
  'target-list-item__progress-fill',
  'target-list-item__row',
  'target-list-item__status',
  'target-modal',
  'target-modal__fields',
  'targets-header',
  'targets-list',
  'targets-pane',
  'targets-section',
  'task-list',
  'task-list-item',
  'task-list-item__meta',
  'task-list-item__name',
  'task-list-item__status',
  'task-next-run',
  'task-section',
  'task-section__actions',
  'task-section__header',
  'tasks-empty',
  'tasks-header',
  'ticker-suggest',
  'ticker-suggest__item',
  'ticker-suggest__item--active',
  'timeline-entry',
  'timeline-entry__amount',
  'timeline-entry__category',
  'timeline-entry__date',
  'timeline-entry__desc',
  'timeline-entry__icon',
  'timeline-entry__meta',
  'timeline-entry__more-link',
  'timeline-entry__type',
  'toast',
  'toast-container',
  'toast--error',
  'toast--info',
  'toast--success',
  'txn-form',
  'txn-form-grid',
  'txn-form__rule-match',
  'txn-modal-overlay',
  'upcoming-item',
  'upcoming-item__amount',
  'upcoming-item__date',
  'upcoming-item__days',
  'upcoming-item__desc',
  'upcoming-item__status',
  'upcoming-items',
  'upcoming-section',
  'upcoming-title',
  'watchlist-empty',
  'watchlist-item',
  'watchlist-item__actions',
  'watchlist-item__details',
  'watchlist-item__meta',
  'watchlist-item__name',
  'watchlist-item__price',
  'watchlist-item__ticker',
  'watchlist-list',
  'watchlist-section',
  'x-chart-wrap',

  // ── [pre-existing] second batch — missed in first pass ───────────────────
  'dedup-pair-row',
  'diff-after',
  'diff-before',
  'dup-cell-acct',
  'dup-cell-amount',
  'dup-cell-count',
  'dup-cell-date',
  'dup-cell-desc',
  'dup-cell-id',
  'dup-cell-null',
  'dup-delete-btn',
  'dup-keep-btn',
  'dup-review',
  'dup-review--done',
  'dup-review-actions',
  'dup-review-header',
  'dup-review-hint',
  'dup-review-partial',
  'dup-review-table',
  'dup-review-table-wrap',
  'dup-review-title',
  'effort-selector',
  'file-node',
  'file-node--dir',
  'file-node__icon',
  'file-node__name',
  'file-tree',
  'form-error',
  'form-row',
  'hold-group',
  'holding-id',
  'inbox-section',
  'lot-disposal-gain',
  'lot-disposal-gain-base',
  'lot-disposal-gain-label',
  'lot-disposal-preview',
  'mcp-srv__tool-row',
  'mcp-srv__trust',
  'mcp-token-row',
  'mention-chip__icon',
  'mention-chip__label',
  'modal-actions',
  'msg-action-caret',
  'msg-action-icon',
  'msg-action-pill',
  'msg-action-pill--fetch',
  'msg-action-row',
  'msg-artifact-wrap',
  'msg-attachment-pill',
  'msg-attachments',
  'msg-copy-btn',
  'msg-cost-chip',
  'msg-cost-chip__model',
  'msg-error',
  'msg-footer',
  'msg-proposed-changes',
  'msg-restore',
  'msg-restore-actions',
  'msg-restore-cancel',
  'msg-restore-done',
  'msg-restore-err',
  'msg-restore-hint',
  'msg-restore-lead',
  'msg-restore-popover',
  'msg-restore-preview',
  'msg-restore-trigger',
  'msg-restore-undo',
  'msg-restore-warn',
  'msg-retry-btn',
  'msg-search-count',
  'msg-search-header',
  'msg-search-icon',
  'msg-search-item',
  'msg-search-loading',
  'msg-search-query',
  'msg-search-result',
  'msg-search-result-favicon',
  'msg-search-result-title',
  'msg-search-results',
  'msg-searches',
  'msg-setup-hint',
  'msg-setup-hint-icon',
  'msg-setup-hint-text',
  'msg-stream-cursor',
  'msg-text',
  'msg-thinking-block',
  'msg-thinking-content',
  'msg-thinking-header',
  'msg-thinking-icon',
  'msg-thinking-label',
  'msg-thinking-toggle',
  'msg-tool-calls',
  'msg-tool-detail',
  'msg-tool-detail-label',
  'msg-tool-detail-pre',
  'msg-tool-detail-section',
  'msg-tool-pill',
  'msg-tool-pill-wrap',
  'payee-section--danger',
  'payees-apply-rules-btn',
  'plan-row--confirm',
  'prompt-diff-block',
  'prompt-diff-gutter',
  'prompt-diff-view',
  'prompt-expand-btn',
  'provider-row__body',
  'provider-row__header',
  'realized-detail',
  'realized-row',
  'realized-table-wrap',
  'rebalance-panel',
  'returns-panel',
  'rfc-canvas-wrap',
  'rfc-legend',
  'rfc-rerun',
  'rfc-retire-line',
  'rfc-wrapper',
  'run-row',
  'run-row--compact',
  'skill-body-preview',
  'skill-code-viewer',
  'skill-code-viewer__line',
  'skill-code-viewer__line-num',
  'skill-code-viewer__row',
  'skill-degraded-banner',
  'skill-description-block',
  'skill-description-block__info',
  'skill-description-block__label',
  'skill-detail-back',
  'skill-detail-divider',
  'skill-detail-footer',
  'skill-detail-header',
  'skill-detail-header__controls',
  'skill-detail-header__title',
  'skill-detail-pane__content',
  'skill-detail-pane__empty',
  'skill-enable-toggle',
  'skill-enable-toggle__label',
  'skill-enable-toggle__thumb',
  'skill-enable-toggle__track',
  'skill-file-viewer',
  'skill-file-viewer__header',
  'skill-file-viewer__view-btns',
  'skill-meta-cell',
  'skill-meta-cell__label',
  'skill-meta-cell__readonly',
  'skill-meta-cell__value',
  'skill-meta-grid',
  'skill-tool-actions',
  'skill-tree-row__badge',
  'skill-tree-row__chevron',
  'skill-tree-row__chevron--placeholder',
  'skill-tree-row__name',
  'skills-tree',
  'slash-popover',
  'slash-popover__desc',
  'slash-popover__list',
  'slash-popover__slug',
  'starter-list',
  'starter-row',
  'starter-row__action',
  'starter-row__info',
  'targets-body',
  'task-runs-section',
  'tax-breakdown-panel',
  'tree-body',
  'tree-search-bar',
  'upload-dropzone__input',
  'upload-installed-banner',

  // ── [pre-existing] mk-* shared investment sub-tab foundation classes ──────
  // These classes come from the investments_2 prototype design.css foundation
  // sheet (mk-subt, mk-panel, mk-panel-head, mk-panel-body, mk-panel-title,
  // mk-panel-spacer, mk-empty*, mk-field-help). The integrator wires the shell's
  // design.css which defines them. They are used by EquityTab.tsx but not yet
  // in index.css — tracked here until the shell integration lands.
  'mk-subt',
  'mk-panel',
  'mk-panel-head',
  'mk-panel-body',
  'mk-panel-title',
  'mk-panel-spacer',
  'mk-empty',
  'mk-empty-icon',
  'mk-empty-title',
  'mk-empty-body',
  'mk-empty-actions',
  'mk-empty-filter',
  'mk-field-help',
  // eq-drawer-row: structural tr container for the expanded drawer — styled
  // only by td's .eq-drawer-cell; no visual rule needed on the tr itself.
  'eq-drawer-row',
])

// ── KNOWN_GLOBAL_CSS ────────────────────────────────────────────────────────
// Classes legitimately reference-free in TSX but live at runtime: applied by
// JS libraries, :global hooks, or documented design-language base classes.
const KNOWN_GLOBAL_CSS = new Set([
  // SegmentedControl base classes — used as the primitive base in the design
  // language. Referenced in SegmentedControl.tsx JSDoc and Gallery.tsx.
  'intel-wl-tabs',
  'intel-wl-tab',
  // Phase D-6 utility classes — applied by className="" in TSX once D-7 wires controls.
  // Listed here so the reverse-pass doesn't count them as dead until consumers land.
  'tap-target',
  'hover-reveal',
  // ── Prism.js token classes (ArtifactViewer.tsx) — injected via dangerouslySetInnerHTML
  // from Prism.highlight()'s own output, prefixed pr- to avoid colliding with unrelated
  // classes elsewhere in the app (see the artifact-viewer.css comment beside the color
  // rules). Never a literal JSX className, so the reverse pass can't see them as "used".
  'pr-token',
  'pr-comment',
  'pr-prolog',
  'pr-doctype',
  'pr-tag',
  'pr-punctuation',
  'pr-attr-name',
  'pr-attr-value',
  'pr-string',
  'pr-property',
  'pr-selector',
  'pr-keyword',
  'pr-atrule',
  'pr-number',
  'pr-boolean',
  'pr-function',
  // ── [pre-existing regex artifact] these only appear in a /* comment */ in
  // index.css (L15879), but the CLASS_RE lookahead spans newlines and sees the
  // next rule's '{'. Not real dead CSS — no rule body ever declared.
  'income-table',
  'income-row',
  // ── [lane/foundation] investments redesign contract classes ─────────────────
  // Sub-tab panel shell — consumed by Holdings/Performance/Income/Activity/Options
  // cockpit panels in sibling lanes (not yet landed).
  'mk-panel',
  'mk-panel-head',
  'mk-panel-title',
  'mk-panel-sub',
  'mk-panel-body',
  'mk-panel-spacer',
  // Empty-state shell — consumed by all sibling sub-tab lanes.
  'mk-empty',
  'mk-empty-icon',
  'mk-empty-title',
  'mk-empty-body',
  'mk-empty-actions',
  // Masthead sub-label (used by lean variant in future income masthead).
  'mk-sum-sub',
  // Cockpit two-column grid — consumed by Holdings-cockpit lane (≤1020 breakpoint).
  'mk-cockpit',
  // Wide-table overflow wrappers — consumed by Activity/Holdings lanes.
  'mk-table-wrap',
  'mk-table-wide',
  // Lucius floating dock — CSS contract for LuciusDock component in lane 08.
  'lx-launcher',
  'lx-launcher-badge',
  'lx-dock',
  'lx-dock-chip',
  'lx-dock-mark',
  'lx-dock-add',
  'lx-dock-send',
  'lx-msg-chip',

  // drop-target: JS-applied modifier class on .lx-dock / .lx-side during drag-over.
  // Compound selector only (.lx-dock.drop-target) — never a standalone className.
  'drop-target',
  // id-cmd: span for "/" skill-verb glyph in identity.tsx (W1-ID lane).
  'id-cmd',
  // mx-detail-h: alias used by MentionDetail.tsx for the detail header section.
  'mx-detail-h',

  // ── [lucius-everywhere] lucius.css contracts — consumed by Wave 2 component lanes ──
  // These classes are defined in lucius.css and consumed by LuciusDock, Composer,
  // Autocomplete, Chip, MentionDetail, ScreenContextBar, CitationChips, dragRightClick
  // (components/chat/lucius/*) and ChatPanel. Listed here until those lanes land.
  //
  // Dock
  'lx-dock-pill',
  'lx-dock-inner',
  'lx-dock-head',
  'lx-dock-head-actions',
  'lx-dock-head-t',
  'lx-dock-collapse',
  'lx-dock-sugs',
  'lx-dock-answer',
  'lx-caret',
  'lx-dock-followups',
  'lx-side-followups',
  'lx-label-txt',
  'lx-label-ag',
  'lx-dock-dot',
  'lx-collapsed',
  // Composer
  'lx-compose',
  'lx-dropmask',
  'lx-editrow',
  'lx-tok-spark',
  'lx-editor',
  'lx-actions',
  'lx-tool',
  'lx-send',
  // Chip variants
  'chip',
  'chip-dot',
  'chip-gl',
  'chip-glyph',
  'chip-tt',
  'chip-meta',
  'chip-amt',
  'chip-name',
  'chip-val',
  'chip-x',
  'chip-to',
  'chip-inline',
  'chip-verb',
  'chip-ctx',
  'chip-scope',
  'chip-source',
  'chip-agent',
  'chip-readonly',
  'chip-stale',
  'chip-compact',
  'chip-rich',
  // Mention detail popover
  'mx-detail',
  'mx-detail-head',
  'mx-detail-ic',
  'mx-detail-name',
  'mx-detail-type',
  'mx-detail-rows',
  'mx-detail-row',
  'mx-detail-foot',
  // Screen-awareness bar
  'lx-scope',
  'lx-scope-row',
  'lx-scope-ic',
  'lx-scope-txt',
  'lx-scope-sub',
  'lx-scope-peek',
  'lx-scope-x',
  'lx-switch',
  'lx-switch-knob',
  'lx-scope-peekbox',
  'lx-scope-peek-h',
  'lx-scope-fact',
  'lx-scope-raw',
  'lx-scope-readd',
  'lx-msg-screen',
  'lx-scope-attached',
  'lx-scope-attached-l',
  // Handles
  'lx-handle-list',
  'lx-handle',
  'lx-handle-ic',
  'lx-handle-main',
  'lx-handle-l',
  'lx-handle-d',
  'lx-handle-act',
  'lx-handle-badge',
  'lx-handle-chip',
  // Reading / citations
  'lx-reading',
  'lx-reading-inline',
  'lx-reading-dot',
  'lx-cites',
  'lx-cite',
  'lx-cite-n',
  'lx-cite-src',
  // Skill banner
  'lx-skill',
  'lx-skill-glyph',
  'lx-skill-l',
  'lx-skill-d',
  'lx-skill-x',
  // Autocomplete popover
  'ac',
  'ac-scroll',
  'ac-zone',
  'ac-item',
  'ac-item-ic',
  'ac-item-dot',
  'ac-cmd-name',
  'ac-item-main',
  'ac-item-name',
  'ac-item-sub',
  'ac-item-tt',
  'ac-eye',
  'ac-onscreen',
  'ac-item-v',
  'ac-item-remote',
  'ac-item-add',
  'ac-empty',
  'ac-searching',
  'ac-spin',
  'ac-error',
  'ac-retry',
  'ac-foot',
  'ac-foot-hint',
  'ac-foot-keys',
  // Identity primitives
  'id-brand',
  'id-mono',
  'id-emoji',
  'id-marker',
  'id-glyph',
  'id-swatch',
  // Side panel
  'lx-side',
  'lx-side-resizer',
  'lx-atts',
  'lx-att',
  'lx-att-thumb',
  'lx-att-ic',
  'lx-att-nm',
  'lx-att-dim',
  'lx-att-x',
  'lx-att-detail',
  'lx-att-card',
  'lx-att-preview',
  'lx-att-meta',
  'lx-att-meta-nm',
  'lx-att-meta-sub',
  'lx-att-remove',
  'lx-msg-att',
  'lx-ctxmenu',
  'lx-ctxmenu-h',
  'lx-ctxmenu-dot',
  'lx-ctxmenu-ic',
  'lx-side-head',
  'lx-side-t',
  'lx-side-t-inner',
  'lx-side-head-actions',
  'lx-side-clear',
  'lx-side-x',
  'lx-ctx',
  'lx-ctx-k',
  'lx-side-scroll',
  'lx-side-empty',
  'lx-side-empty-t',
  'lx-side-empty-d',
  'lx-msg-wrap',
  'lx-msg',
  'lx-msg-mentions',
  'lx-agent-tag',
  'lx-think',
  'lx-side-foot',
  // Agent selector
  'lx-agentsel',
  'lx-agentsel-btn',
  'lx-agentsel-menu',
  'lx-agentsel-item',
  // Drag ghost + keyboard hint
  'mx-drag-ghost',
  'mx-drag-chip',
  'mx-kbd',

  // ── [investments-redesign Phase 2] design.css contracts ────────────────────
  // These classes are defined in investments/design.css and will be consumed by
  // Phase 2 lanes (equity drawer, income panel, Lucius panel, modal forms, etc.).
  // Listed here so the dead-class ratchet doesn't fail before those lanes land.

  // Keyboard nav / drill-through
  'mk-k',
  'mk-caret',
  'mk-drill',
  'mk-drill-inner',
  // Tax lots panel
  'mk-lots-tabs',
  'mk-lots-tab',
  'mk-lots',
  'mk-term',
  // Flag cards
  'mk-flag-mark',
  'mk-flag-body',
  'mk-flag-actions',
  'mk-flag-hint',
  // Brief footer
  'intel-brief-foot',
  'intel-brief-foot-txt',
  'mk-foot-btn',
  // Equity table + drawer
  'eq-table',
  'eq-row--click',
  'eq-caret',
  'eq-drawer-cell',
  'eq-drawer',
  'eq-drawer-since',
  'eq-drawer-h',
  'eq-sub',
  'eq-link',
  'eq-link--danger',
  'eq-drawer-actions',
  'eq-dr-top',
  'eq-dr-prog',
  'eq-dr-prog-head',
  'eq-dr-prog-pct',
  'eq-dr-prog-sub',
  'eq-dr-bar',
  'eq-dr-seg',
  'eq-dr-since',
  'eq-dr-meta',
  'eq-dr-meta-k',
  'eq-dr-meta-v',
  'eq-dr-meta-sub',
  // Equity timeline + upcoming
  'eq-tl',
  'eq-tl-row',
  'is-vested',
  'eq-tl-node',
  'eq-tl-dot',
  'is-next',
  'eq-tl-date',
  'is-upcoming',
  'eq-tl-sub',
  'eq-tl-act',
  'eq-up',
  'eq-up-row',
  'eq-up-l',
  'eq-up-date',
  'eq-up-meta',
  // Equity schedule + extras
  'eq-sched',
  'eq-sched-grid',
  'eq-sched-prev',
  'eq-sched-prev-row',
  'eq-sched-row2',
  'eq-sched-sum',
  'eq-seg',
  'eq-yrs',
  'eq-or',
  'eq-adv',
  'eq-adv-toggle',
  // Equity grant row
  'mk-grant',
  'mk-grant-top',
  'mk-grant-sym',
  'mk-grant-nm',
  'mk-grant-meta',
  'mk-grant-bar',
  'mk-grant-pct',
  'mk-grant-vested',
  'mk-grant-unvested',
  'mk-grant-next',
  'mk-grant-ask',
  'mk-grant-ask',
  'mk-ask-link',
  'mk-card-ask',
  // Equity tranche edit / preview
  'eq-tranche-edit',
  'eq-tranche-row',
  'eq-tranche-x',
  'eq-preview',
  'eq-preview-h',
  'eq-preview-grid',
  'eq-preview-l',
  'eq-preview-v',
  // Equity AI box
  'eq-ai',
  'eq-ai-head',
  'eq-ai-box',
  'eq-ai-foot',
  // Income panel
  'inc-grid',
  'inc-bar',
  'inc-bar-fill',
  'inc-cap',
  'inc-chart-cap',
  'inc-legend',
  'inc-legend-h',
  'inc-legend-d',
  'inc-legend-item',
  'inc-legend-k',
  'inc-cmp',
  'inc-cmp-top',
  'inc-cmp-row',
  'inc-cmp-k',
  'inc-cmp-v',
  'inc-src',
  'inc-src-row',
  'inc-src-row--agg',
  'inc-src-nm',
  'inc-src-sym',
  'inc-src-amt',
  'inc-src-id',
  'inc-src-bar',
  'inc-src-track',
  'inc-src-fill',
  'inc-pay',
  'inc-pay-head',
  'inc-pay-row',
  'inc-pay-date',
  'inc-pay-sym',
  'inc-pay-nm',
  'inc-pay-amt',
  'inc-next',
  'inc-next-main',
  'inc-next-l',
  'inc-next-r',
  'inc-next-tag',
  'inc-next-amt',
  'inc-next-pay',
  'inc-next-sub',
  'inc-soon',
  'inc-dot',
  'inc-info',
  'inc-info-pop',
  'inc-showall',
  'inc-titlerow',
  // Lucius panel (slide-out)
  'lx',
  'lx-scrim',
  'lx-panel',
  'lx-panel-head',
  'lx-panel-mark',
  'lx-panel-title',
  'lx-panel-close',
  'lx-body',
  'lx-intro',
  'lx-msgs',
  'lx-msg',
  'lx-msg-mentions',
  'lx-think',
  'lx-chips',
  'lx-chip',
  'lx-chip-mark',
  'lx-dock-chips',
  'lx-dock-pdot',
  'lx-dock-pgroup',
  'lx-dock-picker',
  'lx-dock-pitem',
  'lx-dock-plist',
  'lx-dock-psearch',
  'lx-composer',
  'lx-input',
  'lx-ctx',
  'lx-ctx-mention',
  'lx-send',
  'lx-btn',
  'lx-more-label',
  // Lucius note / suggest strip
  'mk-lx-note',
  'mk-lx-note-mark',
  'mk-lx-suggest',
  // Modal shell
  'mk-modal-scrim',
  'mk-modal',
  'mk-modal-head',
  'mk-modal-title',
  'mk-modal-x',
  'mk-modal-body',
  'mk-modal-foot',
  // Form fields (modal)
  'mk-field',
  'mk-field-lbl',
  'mk-field-help',
  'mk-input',
  'mk-select',
  // Chart / perf overlay
  'mk-chart',
  'mk-chart-wrap',
  'mk-chart-axis',
  'mk-chart-dot',
  'mk-chart-end',
  'mk-chart-tip',
  'mk-chart-tip-d',
  'mk-chart-tip-r',
  // Income-overlay bars
  'mk-imo',
  'mk-imo-row',
  'mk-imo-lbl',
  'mk-imo-amt',
  'mk-imo-track',
  'mk-imo-fill',
  // Rows / sub-table
  'mk-rows',
  'mk-rows-head',
  'mk-rows-row',
  // Returns ticker list
  'mk-rt-sym',
  'mk-rt-nm',
  'mk-rt-r',
  // Misc sub-labels
  'mk-subt',
  'mk-set-head',
  'mk-legend',
  // Risk grid
  'rk-grid',
  'rk-cell',
  'rk-k',
  'rk-v',
  // Activity row / tax row
  'sk-row',
  'mut',
  'act-tax',
  'act-tax-l1',
  'act-tax-main',
  'act-tax-sub',
  // Rebalance action buttons row (Phase 2)
  'mk-rb-actions',
  // Standalone movers bar widget (Phase 2 intel panel variant)
  'mvr',
])

// ── Read CSS definitions ────────────────────────────────────────────────────

// CLASS_RE: matches a CSS class selector that opens a rule block.
// Only capture selectors followed (somewhere in the selector list) by `{`.
// This filters out classes that appear inside @import url() or comments.
const CLASS_RE = /\.([a-zA-Z][a-zA-Z0-9_-]+)(?=[^{]*\{)/g

const defined = new Map() // class name → source file path
for (const cssFile of CSS_FILES) {
  let cssContent
  try {
    cssContent = readFileSync(cssFile, 'utf8')
  } catch {
    // File might not exist in all configurations — skip gracefully.
    continue
  }
  let cm
  CLASS_RE.lastIndex = 0
  while ((cm = CLASS_RE.exec(cssContent)) !== null) {
    const cls = cm[1]
    // Filter regex artifacts: bare-stem captures ending in `-` or `__`,
    // the font @import URL fragment `googleapis`, and `w3` — every inline SVG
    // `background-image: url("data:image/svg+xml,...xmlns='http://www.w3.org/...")`
    // false-matches CLASS_RE the same way (a literal ".w3" substring inside
    // "www.w3.org", eventually followed by some later rule's `{`). Exposed by
    // the Overview redesign deleting the one file whose OWN unrelated "w3"
    // substring (an xmlns URL in a leftover empty-state icon) had been
    // masking this as "used" — .w3 was never a real class on either side.
    if (cls.endsWith('-') || cls.endsWith('__') || cls === 'googleapis' || cls === 'w3') continue
    if (!defined.has(cls)) defined.set(cls, cssFile)
  }
}

// ── Collect TSX/TS files to scan ────────────────────────────────────────────

function findSources(dir) {
  const files = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...findSources(p))
    else if (/\.(tsx|ts)$/.test(entry.name) && !entry.name.endsWith('.d.ts')) files.push(p)
  }
  return files
}

const scopeEnv = process.env.SCOPE_FILES
let tsxFiles
if (scopeEnv) {
  tsxFiles = scopeEnv
    .split(':')
    .filter(Boolean)
    .map((f) => (f.startsWith('/') ? f : join(ROOT, 'frontend', f)))
    .filter((f) => /\.(tsx|ts)$/.test(f))
} else {
  tsxFiles = findSources(SRC_DIR)
}

// Read all source text for stem collection and literal matching (reverse pass
// always uses ALL sources, not just the scoped set).
const allSourceFiles = findSources(SRC_DIR)
const allSourceTexts = allSourceFiles.map((f) => readFileSync(f, 'utf8'))
const allSourceText = allSourceTexts.join('\n')

// ── Dynamic-stem collection (C-2a) ─────────────────────────────────────────
// Capture the literal run before each `${…}` in template literals and before
// string concatenation. A CSS class is "dynamically reachable" if some stem
// is a strict prefix of it and the remainder is all class-safe chars.
//
// Template-literal backticks are matched PER FILE, not on one joined blob: a
// single file with an odd count of raw backtick characters (e.g. a regex
// literal that matches markdown code fences, like /`([^`]+)`/) desyncs a
// corpus-wide `` /`([^`]*)`/g `` scan for every file after it in traversal
// order — hundreds of unrelated files' live classes then read as dead.
// Per-file scanning contains that damage to the one odd-count file.
function collectStems(srcTexts) {
  const stems = new Set()
  // Only keep stems that LOOK like a CSS class prefix — they must contain a
  // hyphen or end at a BEM `_` separator. This excludes English prose captured
  // from template literals like `${n} signal${…}` (stem "signal"), which would
  // otherwise mask every signal-* class as dynamically reachable (a false
  // negative that hides real dead code).
  const add = (s) => {
    if (s.includes('-') || s.endsWith('_')) stems.add(s)
  }
  for (const srcText of srcTexts) {
    for (const [, body] of srcText.matchAll(/`([^`]*)`/g))
      for (const [, s] of body.matchAll(/([A-Za-z0-9_-]+)\$\{/g)) add(s)
  }
  for (const [, s] of srcTexts.join('\n').matchAll(/['"]([A-Za-z0-9_-]+)['"]\s*\+/g)) add(s)
  return [...stems]
}

const stems = collectStems(allSourceTexts)

function dynRef(cls) {
  return stems.some(
    (st) =>
      cls.length > st.length && cls.startsWith(st) && /^[A-Za-z0-9_-]+$/.test(cls.slice(st.length)),
  )
}

// ── FORWARD PASS: TSX → CSS ─────────────────────────────────────────────────
// Match static string literals AND template literals with stems.
const CLASSNAME_RE = /className=["']([^"']+)["']/g

const violations = [] // { file, token }

for (const file of tsxFiles) {
  const src = readFileSync(file, 'utf8')
  let match
  while ((match = CLASSNAME_RE.exec(src)) !== null) {
    const tokens = match[1].split(/\s+/)
    for (const token of tokens) {
      if (!token) continue
      // Skip tokens that look like JS expressions (leftover from ternaries etc.)
      if (token.includes('{') || token.includes('$') || token.includes('(')) continue
      // Skip single-character tokens and pure numeric/symbol tokens
      if (!token.match(/^[a-zA-Z][a-zA-Z0-9_-]{2,}$/)) continue
      if (defined.has(token)) continue
      if (ALLOWLIST.has(token)) continue
      // Dynamic-stem check: if some stem from templates produces this token, it's live.
      if (dynRef(token)) continue
      violations.push({ file: relative(ROOT, file), token })
    }
  }
}

// ── REVERSE PASS: CSS → TSX (dead-class detection) (C-2b) ──────────────────
// A defined class is dead if: no literal token match in any .tsx/.ts source
// AND no dynamic-stem match. Exempt: KNOWN_GLOBAL_CSS.

function litRef(cls) {
  // Word-boundary match so `foo` doesn't match `foo-bar`
  return new RegExp(
    `(^|[^a-zA-Z0-9_-])${cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^a-zA-Z0-9_-]|$)`,
  ).test(allSourceText)
}

const deadClasses = [...defined.keys()].filter(
  (cls) => !KNOWN_GLOBAL_CSS.has(cls) && !litRef(cls) && !dynRef(cls),
)

// ── RATCHET: persist and enforce a non-growth floor (C-2c) ─────────────────
let debtData = { deadCount: null, allowlistSize: null }
if (existsSync(DEBT_FILE)) {
  try {
    debtData = JSON.parse(readFileSync(DEBT_FILE, 'utf8'))
  } catch {
    // Corrupt file — treat as empty
  }
}

const currentDead = deadClasses.length
const currentAllowlist = ALLOWLIST.size
let ratchetFailed = false

if (debtData.deadCount !== null && currentDead > debtData.deadCount) {
  console.error(
    `✗ check-css-classes: dead-class count grew from ${debtData.deadCount} → ${currentDead}. ` +
      `New dead classes were added. Remove unused CSS or move it to @layer app.`,
  )
  ratchetFailed = true
}
if (debtData.allowlistSize !== null && currentAllowlist > debtData.allowlistSize) {
  console.error(
    `✗ check-css-classes: ALLOWLIST grew from ${debtData.allowlistSize} → ${currentAllowlist}. ` +
      `Define the class in CSS or document why it's exempt.`,
  )
  ratchetFailed = true
}

// Update the ratchet file with a floor that only ever RATCHETS DOWN: take the
// min of current vs stored. A cleanup pass lowers the floor; a regression run
// (which set ratchetFailed above and exits 1 below) can NEVER auto-raise the
// floor and silently clear itself on the next run.
const newDebt = {
  deadCount: debtData.deadCount === null ? currentDead : Math.min(currentDead, debtData.deadCount),
  allowlistSize:
    debtData.allowlistSize === null
      ? currentAllowlist
      : Math.min(currentAllowlist, debtData.allowlistSize),
}
writeFileSync(DEBT_FILE, JSON.stringify(newDebt, null, 2) + '\n')

// ── REVERSE PASS OUTPUT ─────────────────────────────────────────────────────
const strictDead = process.env.STRICT_DEAD === '1'

if (deadClasses.length > 0) {
  if (strictDead) {
    console.error(
      `✗ check-css-classes: ${deadClasses.length} dead CSS class(es) found (STRICT_DEAD=1):\n`,
    )
    deadClasses.forEach((c) => console.error(`  .${c}  (${relative(ROOT, defined.get(c))})`))
    console.error('\nRemove these rules or add to KNOWN_GLOBAL_CSS with a comment.')
  } else {
    console.log(
      `ℹ check-css-classes: ${deadClasses.length} potentially dead CSS class(es) (run STRICT_DEAD=1 to block):`,
    )
    deadClasses.forEach((c) => console.log(`  .${c}  (${relative(ROOT, defined.get(c))})`))
  }
}

// ── FORWARD PASS REPORT ─────────────────────────────────────────────────────

if (violations.length === 0 && !ratchetFailed && (!strictDead || deadClasses.length === 0)) {
  console.log('✓ check-css-classes: all static className tokens are defined')
  process.exit(0)
}

if (violations.length > 0) {
  console.error('✗ check-css-classes: undefined CSS class tokens found\n')
  const byToken = new Map()
  for (const { file, token } of violations) {
    if (!byToken.has(token)) byToken.set(token, new Set())
    byToken.get(token).add(file)
  }
  for (const [token, files] of [...byToken.entries()].sort()) {
    console.error(`  .${token}`)
    for (const f of files) console.error(`    ${f}`)
  }
  console.error(
    `\n${byToken.size} undefined token(s). Either define the class in frontend/src/index.css ` +
      'or add it to the ALLOWLIST in frontend/scripts/check-css-classes.mjs with a comment.',
  )
}

process.exit(1)
