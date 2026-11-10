// @vitest-environment jsdom
//
// Tests for the ONE model chooser (ui/ModelPicker), which replaced six divergent
// controls across the chat panel, the dock, Settings (task models / preferred / dedup)
// and the Automation editor.
//
// The grouping cases came from useModels.test.ts's buildChatModelGroups block — that
// builder is gone; the picker owns grouping now, so its tests live with it.

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ModelOption } from '../../api/models'
import {
  ModelPicker,
  bareModelId,
  groupModels,
  modelRefOf,
  resolveModelSelection,
} from './ModelPicker'

const MODELS: ModelOption[] = [
  {
    id: 'claude-haiku-4-5',
    provider_key: 'anthropic',
    provider_label: 'Claude',
    preferred: true,
  },
  { id: 'claude-sonnet-5', provider_key: 'anthropic', provider_label: 'Claude' },
  { id: 'gpt-4o-mini', provider_key: 'openai', provider_label: 'OpenAI', preferred: true },
  { id: 'grok-4.5', provider_key: 'xai', provider_label: 'xAI', vision: false },
]

const trigger = () => screen.getByRole('button', { name: 'Chat model' })
const openPanel = () => fireEvent.click(trigger())
const optionNames = () =>
  screen.getAllByRole('option').map((o) => o.textContent?.replace(/✓$/, '').trim())

/** Forces useIsMobile() (jsdom's default matchMedia stub reports desktop — see
 *  vitest.setup.ts). `globalThis.matchMedia` is a getter-only accessor there, so the
 *  property must be REDEFINED rather than assigned — same helper as KebabMenu.test.tsx. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

afterEach(() => cleanup())

// ── pure helpers ─────────────────────────────────────────────────────────────

describe('modelRefOf / bareModelId', () => {
  it('pins the provider when known and round-trips back to the bare id', () => {
    expect(modelRefOf(MODELS[0]!)).toBe('anthropic:claude-haiku-4-5')
    expect(bareModelId('anthropic:claude-haiku-4-5')).toBe('claude-haiku-4-5')
  })

  it('leaves a provider-less model as a bare id', () => {
    expect(modelRefOf({ id: 'llama3.1' } as ModelOption)).toBe('llama3.1')
    expect(bareModelId('llama3.1')).toBe('llama3.1')
  })
})

describe('groupModels', () => {
  it('pins Preferred first, then one group per provider', () => {
    expect(groupModels(MODELS).map((g) => g.label)).toEqual([
      'Preferred',
      'Claude',
      'OpenAI',
      'xAI',
    ])
  })

  it('keeps a preferred model in its provider group too — this narrows nothing', () => {
    const claude = groupModels(MODELS).find((g) => g.label === 'Claude')!
    expect(claude.models.map((m) => m.id)).toContain('claude-haiku-4-5')
  })

  it('omits the Preferred group when nothing is flagged', () => {
    const plain = MODELS.map((m) => ({ ...m, preferred: false }))
    expect(groupModels(plain).map((g) => g.label)).toEqual(['Claude', 'OpenAI', 'xAI'])
  })
})

describe('resolveModelSelection', () => {
  it('splits a provider-pinned ref into both halves', () => {
    const s = resolveModelSelection('xai:grok-4.5', MODELS)
    expect(s).toMatchObject({
      ref: 'xai:grok-4.5',
      modelId: 'grok-4.5',
      providerKey: 'xai',
      providerLabel: 'xAI',
      isDefault: false,
    })
    expect(s.model?.id).toBe('grok-4.5')
  })

  it('flags the instance default', () => {
    expect(resolveModelSelection('', MODELS)).toMatchObject({ isDefault: true, modelId: '' })
  })

  it('still names the provider KEY for a model no longer in the list', () => {
    // A since-disabled/removed provider must not read as "no provider" — naming
    // `openrouter` beats showing nothing next to a model that will still be billed by it.
    const s = resolveModelSelection('openrouter:z-ai/glm-5.2', MODELS)
    expect(s.modelId).toBe('z-ai/glm-5.2')
    expect(s.providerKey).toBe('openrouter')
    expect(s.providerLabel).toBe('openrouter')
    expect(s.model).toBeUndefined()
  })

  // The instance default is stored BARE (`grok-4.5`, not `xai:grok-4.5`), so without this
  // the trigger showed "Default (grok-4.5)" with no provider at all.
  it('resolves a bare id to its provider when that id is unambiguous', () => {
    expect(resolveModelSelection('grok-4.5', MODELS)).toMatchObject({
      modelId: 'grok-4.5',
      providerKey: 'xai',
      providerLabel: 'xAI',
    })
  })

  it('refuses to guess a provider for a bare id that several providers share', () => {
    const ambiguous = [
      { id: 'sonnet', provider_key: 'claude_cli', provider_label: 'Claude Code CLI (local)' },
      { id: 'sonnet', provider_key: 'anthropic', provider_label: 'Anthropic' },
    ] as ModelOption[]
    expect(resolveModelSelection('sonnet', ambiguous)).toMatchObject({
      modelId: 'sonnet',
      providerKey: '',
      providerLabel: '',
    })
  })

  it('reports an empty provider for a model that is in no list at all', () => {
    expect(resolveModelSelection('llama3.1', MODELS)).toMatchObject({
      modelId: 'llama3.1',
      providerKey: '',
      providerLabel: '',
    })
  })
})

// ── single-select ────────────────────────────────────────────────────────────

describe('ModelPicker (single)', () => {
  it('shows the selected model on the trigger, and the default label when unset', () => {
    const { rerender } = render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default (sonnet)"
        onChange={vi.fn()}
      />,
    )
    expect(trigger().textContent).toContain('Default (sonnet)')

    rerender(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value="openai:gpt-4o-mini"
        defaultLabel="Default (sonnet)"
        onChange={vi.fn()}
      />,
    )
    expect(trigger().textContent).toContain('gpt-4o-mini')
  })

  it('still names a model whose provider is no longer in the list', () => {
    // A stored `openrouter:z-ai/glm-5.2` used to render as the FIRST option (i.e. a lie)
    // in the old <select>-based Automation picker.
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value="openrouter:z-ai/glm-5.2"
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    expect(trigger().textContent).toContain('z-ai/glm-5.2')
  })

  it('opens a grouped list with the default row first', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    expect(optionNames()[0]).toBe('Default')
    expect(screen.getByText('Preferred')).toBeTruthy()
    expect(screen.getAllByText('OpenAI').length).toBeGreaterThan(0)
  })

  it('names the provider ONLY in Preferred, the one group that mixes providers', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    const rows = screen.getAllByRole('option', { name: /gpt-4o-mini/ })
    // Two rows for the same model: the Preferred one names its provider, the one under
    // the "OpenAI" header does not — repeating it there is noise on a ~300-row list.
    expect(rows[0]!.textContent).toContain('OpenAI')
    expect(rows[1]!.textContent).not.toContain('OpenAI')
  })

  it('omits the default group entirely when neither default prop is given', () => {
    render(
      <ModelPicker label="Chat model" models={MODELS} value="xai:grok-4.5" onChange={vi.fn()} />,
    )
    openPanel()
    expect(screen.queryByText('Default')).toBeNull()
  })

  // The instance default reads like any other row — a group header, then the model that
  // will actually run and its provider. "Default (sonnet)" as a bare row said it twice and
  // looked nothing like its neighbours.
  it('renders the instance default as its own GROUP naming the real model + provider', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultModelId="grok-4.5"
        onChange={vi.fn()}
      />,
    )
    openPanel()

    expect(screen.getByText('Default')).toBeTruthy()
    const first = screen.getAllByRole('option')[0]!
    expect(first.textContent).toContain('grok-4.5')
    expect(first.textContent).toContain('xAI')
    expect(first.textContent).not.toContain('Default (')
    expect(first.getAttribute('aria-selected')).toBe('true')
  })

  it('the default row still stores the "" ref, not the resolved model', () => {
    const onChange = vi.fn()
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value="xai:grok-4.5"
        defaultModelId="grok-4.5"
        onChange={onChange}
      />,
    )
    openPanel()
    fireEvent.click(screen.getAllByRole('option')[0]!)
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('selects a model, reports the provider-pinned ref, and closes', () => {
    const onChange = vi.fn()
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={onChange}
      />,
    )
    openPanel()
    fireEvent.click(screen.getAllByRole('option', { name: /gpt-4o-mini/ })[0]!)

    expect(onChange).toHaveBeenCalledWith('openai:gpt-4o-mini')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('searches across model id AND provider name', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()

    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'grok' } })
    expect(optionNames()).toEqual(['grok-4.5'])

    // Matching on the PROVIDER name, not the id. Two rows because gpt-4o-mini is
    // preferred: search filters the groups, it does not collapse them, so a preferred
    // model keeps appearing in both places exactly as it does unfiltered.
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'openai' } })
    expect(optionNames()).toEqual(['gpt-4o-miniOpenAI', 'gpt-4o-mini'])
    expect(screen.getByText('Preferred')).toBeTruthy()
  })

  it('reports a search that matches nothing', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'zzz' } })
    expect(screen.getByText(/No model matches/)).toBeTruthy()
  })

  it('annotates rows when the site asks it to (vision)', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        annotate={(m) => (m.vision === false ? '— no vision' : undefined)}
        onChange={vi.fn()}
      />,
    )
    openPanel()
    const row = screen.getAllByRole('option', { name: /grok-4\.5/ })[0]!
    expect(within(row).getByText('— no vision')).toBeTruthy()
  })

  it('never lets an unavailable model be chosen', () => {
    const onChange = vi.fn()
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        isUnavailable={(m) => m.provider_key === 'xai'}
        onChange={onChange}
      />,
    )
    openPanel()
    const row = screen.getAllByRole('option', { name: /grok-4\.5/ })[0]! as HTMLButtonElement
    expect(row.disabled).toBe(true)
    fireEvent.click(row)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('picks with the keyboard and closes on Escape', () => {
    const onChange = vi.fn()
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={onChange}
      />,
    )
    openPanel()
    const search = screen.getByLabelText('Search models')

    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(search, { key: 'Enter' })
    expect(onChange).toHaveBeenCalled()

    openPanel()
    fireEvent.keyDown(screen.getByLabelText('Search models'), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  // The reported bug: every click inside the open popover dismissed it. The old
  // outside-click check was a document `pointerdown` listener that closed unless the
  // target was inside the panel — which cannot tell "clicked outside" from "clicked an
  // overlay stacked above the panel", and the Lucius dock (z-index 710) was exactly that.
  // A scrim owns outside-click now, so interacting inside can never reach it.
  it('does NOT close when the search box or a group header is clicked', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()

    fireEvent.pointerDown(screen.getByLabelText('Search models'))
    fireEvent.click(screen.getByLabelText('Search models'))
    expect(screen.queryByRole('listbox')).not.toBeNull()

    fireEvent.click(screen.getByText('Preferred'))
    expect(screen.queryByRole('listbox')).not.toBeNull()
  })

  // Opened from inside a Radix modal (Settings), the portaled layer sat outside
  // Dialog.Content, where Radix's body-wide `pointer-events: none` made it inert AND the
  // click fell through to the dialog overlay — dismissing Settings along with the picker.
  // ui/Modal's isOutsideExempt() keys off this marker; without it the bug returns silently.
  it('marks the panel as a portal layer, so a host modal exempts it', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    expect(document.querySelector('.mp-pop')!.hasAttribute('data-portal-layer')).toBe(true)
  })

  it('marks the phone sheet too', () => {
    const restore = mockMobile(true)
    try {
      render(
        <ModelPicker
          label="Chat model"
          models={MODELS}
          value=""
          defaultLabel="Default"
          onChange={vi.fn()}
        />,
      )
      openPanel()
      expect(document.querySelector('.mp-sheet')!.hasAttribute('data-portal-layer')).toBe(true)
    } finally {
      restore()
    }
  })

  // No scrim, ANYWHERE: a full-screen layer (even a transparent one) eats the first click
  // on whatever the user was actually aiming at.
  it('renders no scrim over the page', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    expect(document.querySelector('.mp-scrim')).toBeNull()

    const restore = mockMobile(true)
    try {
      cleanup()
      render(
        <ModelPicker
          label="Chat model"
          models={MODELS}
          value=""
          defaultLabel="Default"
          onChange={vi.fn()}
        />,
      )
      openPanel()
      expect(document.querySelector('.mp-scrim')).toBeNull()
    } finally {
      restore()
    }
  })

  it('closes on a pointerdown outside it', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    expect(screen.queryByRole('listbox')).not.toBeNull()

    // Dispatched natively: the listener is a document-level addEventListener, and jsdom
    // has no PointerEvent constructor for fireEvent.pointerDown to build.
    act(() => {
      document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    })
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('names the PROVIDER alongside the model on the trigger', () => {
    // `grok-4.5` alone is ambiguous — the same id can exist on several providers, billed
    // differently. Both halves of the answer, or it is not an answer.
    render(
      <ModelPicker label="Chat model" models={MODELS} value="xai:grok-4.5" onChange={vi.fn()} />,
    )
    expect(trigger().textContent).toContain('grok-4.5')
    expect(trigger().textContent).toContain('xAI')
  })

  it('shows the DEFAULT as a model + provider too, never the word "Default"', () => {
    // useModels().defaultModel is bare — this is the exact shape the sites pass. Following
    // the default still runs a specific model, so the trigger names it like any other.
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        defaultModelId="grok-4.5"
        onChange={vi.fn()}
      />,
    )
    expect(trigger().textContent).toContain('grok-4.5')
    expect(trigger().textContent).toContain('xAI')
    expect(trigger().textContent).not.toContain('Default')
  })

  it('falls back to the plain label when the default model cannot be resolved', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    expect(trigger().textContent).toContain('Default')
  })

  it('honours disabled', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        disabled
        onChange={vi.fn()}
      />,
    )
    expect((trigger() as HTMLButtonElement).disabled).toBe(true)
    openPanel()
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

// ── multi-select ─────────────────────────────────────────────────────────────

describe('ModelPicker (multiple)', () => {
  const multiTrigger = () => screen.getByRole('button', { name: 'Preferred models' })

  it('renders a removable chip per selection', () => {
    const onChange = vi.fn()
    render(
      <ModelPicker
        multiple
        label="Preferred models"
        models={MODELS}
        value={['openai:gpt-4o-mini']}
        onChange={onChange}
      />,
    )
    expect(screen.getByText('gpt-4o-mini')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Remove openai:gpt-4o-mini'))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('shows the empty hint when nothing is picked', () => {
    render(
      <ModelPicker
        multiple
        label="Preferred models"
        models={MODELS}
        value={[]}
        emptyHint={<span>Nothing picked</span>}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByText('Nothing picked')).toBeTruthy()
  })

  it('toggles a model on and off, and STAYS OPEN so a shortlist can be built', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <ModelPicker
        multiple
        label="Preferred models"
        models={MODELS}
        value={[]}
        onChange={onChange}
      />,
    )
    fireEvent.click(multiTrigger())
    fireEvent.click(screen.getAllByRole('option', { name: /gpt-4o-mini/ })[0]!)

    expect(onChange).toHaveBeenLastCalledWith(['openai:gpt-4o-mini'])
    expect(screen.queryByRole('listbox')).not.toBeNull()

    rerender(
      <ModelPicker
        multiple
        label="Preferred models"
        models={MODELS}
        value={['openai:gpt-4o-mini']}
        onChange={onChange}
      />,
    )
    fireEvent.click(screen.getAllByRole('option', { name: /gpt-4o-mini/ })[0]!)
    expect(onChange).toHaveBeenLastCalledWith([])
  })

  it('offers no default row — "the instance default" is not a shortlist entry', () => {
    render(
      <ModelPicker
        multiple
        label="Preferred models"
        models={MODELS}
        value={[]}
        onChange={vi.fn()}
      />,
    )
    fireEvent.click(multiTrigger())
    expect(optionNames()).not.toContain('Default')
  })

  it('marks the picked rows as selected', () => {
    render(
      <ModelPicker
        multiple
        label="Preferred models"
        models={MODELS}
        value={['xai:grok-4.5']}
        onChange={vi.fn()}
      />,
    )
    fireEvent.click(multiTrigger())
    const row = screen.getAllByRole('option', { name: /grok-4\.5/ })[0]!
    expect(row.getAttribute('aria-selected')).toBe('true')
  })
})

// ── responsive ───────────────────────────────────────────────────────────────

describe('ModelPicker on a phone', () => {
  it('renders a bottom sheet instead of an anchored popover', () => {
    const restore = mockMobile(true)
    try {
      render(
        <ModelPicker
          label="Chat model"
          models={MODELS}
          value=""
          defaultLabel="Default"
          onChange={vi.fn()}
        />,
      )
      openPanel()

      expect(document.querySelector('.mp-sheet')).not.toBeNull()
      expect(document.querySelector('.mp-pop')).toBeNull()
      // A sheet covers the page — the body must not scroll behind it.
      expect(document.body.style.overflow).toBe('hidden')
    } finally {
      restore()
    }
  })

  it('stays an anchored popover on desktop', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={MODELS}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    expect(document.querySelector('.mp-pop')).not.toBeNull()
    expect(document.querySelector('.mp-sheet')).toBeNull()
  })
})

// A provider's own "Default model" field: the list is that provider's models, and an id the list
// does not contain is still a valid choice (a new release, an OpenRouter `:free` variant).
describe('ModelPicker (custom model id)', () => {
  const OPENROUTER: ModelOption[] = [
    { id: 'z-ai/glm-5.3-flash', provider_key: 'openrouter', provider_label: 'OpenRouter' },
    { id: 'openai/gpt-5', provider_key: 'openrouter', provider_label: 'OpenRouter' },
  ]
  const field = (onChange = vi.fn(), value = '') => {
    render(
      <ModelPicker
        label="Chat model"
        models={OPENROUTER}
        value={value}
        placeholder="Choose a model…"
        customProviderKey="openrouter"
        onChange={onChange}
      />,
    )
    openPanel()
    return onChange
  }
  const search = (value: string) =>
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value } })

  it('shows the placeholder, not "Default", while nothing is chosen', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={OPENROUTER}
        value=""
        placeholder="Choose a model…"
        onChange={vi.fn()}
      />,
    )
    expect(trigger().textContent).toContain('Choose a model…')
  })

  it('finds a model by searching part of its id', () => {
    field()
    search('glm')
    expect(optionNames().some((n) => n?.includes('z-ai/glm-5.3-flash'))).toBe(true)
    expect(optionNames().some((n) => n?.includes('openai/gpt-5'))).toBe(false)
  })

  it('offers the typed text as a custom id first, pinned to the provider', () => {
    const onChange = field()
    search('meta-llama/llama-3.3-70b-instruct:free')

    expect(optionNames()[0]).toContain('Use “meta-llama/llama-3.3-70b-instruct:free”')
    fireEvent.click(screen.getAllByRole('option')[0]!)

    // The `:free` suffix survives: only the FIRST colon is the provider separator.
    expect(onChange).toHaveBeenCalledWith('openrouter:meta-llama/llama-3.3-70b-instruct:free')
    expect(bareModelId('openrouter:meta-llama/llama-3.3-70b-instruct:free')).toBe(
      'meta-llama/llama-3.3-70b-instruct:free',
    )
  })

  it('does not offer a custom row for an id the list already has', () => {
    field()
    search('Z-AI/GLM-5.3-FLASH')
    expect(optionNames().some((n) => n?.startsWith('Use “'))).toBe(false)
  })

  it('takes the typed id on Enter only when nothing in the list matched it', () => {
    const onChange = field()
    search('glm')
    fireEvent.keyDown(screen.getByLabelText('Search models'), { key: 'Enter' })
    // A model matched: a stray Enter must not commit the half-typed "glm".
    expect(onChange).not.toHaveBeenCalled()

    search('brand-new/model-x')
    fireEvent.keyDown(screen.getByLabelText('Search models'), { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('openrouter:brand-new/model-x')
  })

  it('never offers a custom row unless the site opted in', () => {
    render(
      <ModelPicker
        label="Chat model"
        models={OPENROUTER}
        value=""
        defaultLabel="Default"
        onChange={vi.fn()}
      />,
    )
    openPanel()
    search('zzz')
    expect(screen.getByText(/No model matches/)).toBeTruthy()
  })
})

describe('pulling the phone sheet down', () => {
  async function pull(dy: number) {
    const sheet = document.querySelector('.mp-sheet') as HTMLElement
    const handle = sheet.querySelector('.mp-sheet-handle-wrap') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150))
    })
    fireEvent.pointerMove(handle, { clientY: 200 + dy, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 200 + dy, pointerId: 1 })
  }

  function openSheet() {
    render(<ModelPicker models={[]} value="" label="Model" onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Model' }))
    expect(document.querySelector('.mp-sheet')).toBeTruthy()
  }

  it('closes it, like every other sheet', async () => {
    mockMobile(true)
    openSheet()
    await pull(240)
    expect(document.querySelector('.mp-sheet')).toBeNull()
    mockMobile(false)
  })

  it('leaves it open on a short pull', async () => {
    mockMobile(true)
    openSheet()
    await pull(12)
    expect(document.querySelector('.mp-sheet')).toBeTruthy()
    mockMobile(false)
  })
})
