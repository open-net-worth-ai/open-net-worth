// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { CliSubscriptionHelp, type CliHostPlatform } from './CliSubscriptionHelp'

const writeText = vi.fn<(value: string) => Promise<void>>()
const onCheck = vi.fn<() => Promise<unknown>>()

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined)
  onCheck.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const cases = [
  [
    'claude_cli',
    'macos',
    'Claude Code',
    'curl -fsSL https://claude.ai/install.sh | bash',
    'claude auth login',
  ],
  [
    'claude_cli',
    'linux',
    'Claude Code',
    'curl -fsSL https://claude.ai/install.sh | bash',
    'claude auth login',
  ],
  [
    'claude_cli',
    'windows',
    'Claude Code',
    'irm https://claude.ai/install.ps1 | iex',
    'claude auth login',
  ],
  [
    'codex_cli',
    'macos',
    'Codex',
    'curl -fsSL https://chatgpt.com/codex/install.sh | sh',
    'codex login',
  ],
  [
    'codex_cli',
    'linux',
    'Codex',
    'curl -fsSL https://chatgpt.com/codex/install.sh | sh',
    'codex login',
  ],
  [
    'codex_cli',
    'windows',
    'Codex',
    'powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"',
    'codex login',
  ],
] as const

it.each(cases)(
  'copies exact %s commands for %s',
  async (provider, platform, label, install, login) => {
    render(
      <CliSubscriptionHelp
        provider={provider}
        platform={platform}
        detected={false}
        onCheck={onCheck}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Install & sign in/ }))
    fireEvent.click(screen.getByRole('button', { name: `Copy ${label} install command` }))
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(install))
    fireEvent.click(screen.getByRole('button', { name: `Copy ${label} sign-in command` }))
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(login))
  },
)

it.each([undefined, 'other'] as (CliHostPlatform | undefined)[])(
  'does not guess installation commands for platform %s',
  (platform) => {
    render(
      <CliSubscriptionHelp
        provider="claude_cli"
        platform={platform}
        detected={false}
        onCheck={onCheck}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Install & sign in/ }))
    expect(
      screen
        .getByRole('link', { name: /Official Claude Code installation instructions/ })
        .getAttribute('href'),
    ).toBe('https://code.claude.com/docs/en/setup')
    expect(screen.queryByRole('button', { name: /Copy .* command/ })).toBeNull()
    expect(screen.queryByText(/curl -fsSL|irm https:/)).toBeNull()
  },
)

it('keeps help open when an installation is detected without claiming authentication', () => {
  const { rerender } = render(
    <CliSubscriptionHelp
      provider="codex_cli"
      platform="linux"
      detected={false}
      onCheck={onCheck}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /Install & sign in/ }))
  expect(screen.getByText('Not installed')).toBeTruthy()
  rerender(<CliSubscriptionHelp provider="codex_cli" platform="linux" detected onCheck={onCheck} />)
  expect(
    screen.getByRole('button', { name: /Install & sign in/ }).getAttribute('aria-expanded'),
  ).toBe('true')
  expect(screen.getByRole('button', { name: 'Copy Codex sign-in command' })).toBeTruthy()
  expect(screen.getByText('Detected')).toBeTruthy()
  expect(screen.queryByText(/Authenticated|Ready/)).toBeNull()
})

it('leaves commands available for manual copying when the clipboard fails', async () => {
  writeText.mockRejectedValue(new Error('clipboard denied'))
  render(
    <CliSubscriptionHelp
      provider="codex_cli"
      platform="macos"
      detected={false}
      onCheck={onCheck}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /Install & sign in/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Copy Codex install command' }))
  expect(await screen.findByText(/Select the command and copy it manually/)).toBeTruthy()
  expect(screen.getByText('curl -fsSL https://chatgpt.com/codex/install.sh | sh')).toBeTruthy()
  expect(screen.queryByText('Copied')).toBeNull()
})

it('resets brief copy confirmation and re-checks on its own, one check at a time, while open', async () => {
  vi.useFakeTimers()
  let rejectCheck!: (reason: Error) => void
  onCheck.mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectCheck = reject
      }),
  )
  render(
    <CliSubscriptionHelp
      provider="claude_cli"
      platform="macos"
      detected={false}
      onCheck={onCheck}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /Install & sign in/ }))
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Copy Claude Code install command' })),
  )
  expect(screen.getByText('Copied')).toBeTruthy()
  act(() => vi.advanceTimersByTime(2500))
  expect(screen.queryByText('Copied')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull()
  fireEvent.focus(window)
  fireEvent.focus(window)
  expect(onCheck).toHaveBeenCalledTimes(1)
  await act(async () => rejectCheck(new Error('offline')))
  expect(screen.queryByRole('alert')).toBeNull()
  fireEvent.focus(window)
  expect(onCheck).toHaveBeenCalledTimes(2)
})

it('shows no install help for a tool that is already found', () => {
  render(<CliSubscriptionHelp provider="claude_cli" platform="macos" detected onCheck={onCheck} />)
  expect(screen.queryByText('Install & sign in')).toBeNull()
  cleanup()
  render(
    <CliSubscriptionHelp
      provider="claude_cli"
      platform="macos"
      detected={false}
      onCheck={onCheck}
    />,
  )
  expect(screen.getByText('Install & sign in')).toBeTruthy()
})

it('offers sign-in for a found CLI with no login, which used to get no row at all', () => {
  render(
    <CliSubscriptionHelp
      provider="codex_cli"
      platform="linux"
      detected
      signIn="not_signed_in"
      onCheck={onCheck}
    />,
  )
  expect(screen.getByText('Not signed in')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /^Sign in/ }))
  expect(screen.getByText('codex login')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Copy Codex install command' })).toBeNull()
})

it.each([
  ['codex_cli', 'Codex', 'docker exec -it open-net-worth codex login --device-auth'],
  ['claude_cli', 'Claude Code', 'docker exec -it open-net-worth claude auth login'],
] as const)('gives the docker exec command in a container: %s', (provider, label, command) => {
  render(
    <CliSubscriptionHelp
      provider={provider}
      platform="linux"
      detected
      signIn="not_signed_in"
      inContainer
      onCheck={onCheck}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /^Sign in/ }))
  expect(screen.getByText(command)).toBeTruthy()
  expect(screen.getByText(new RegExp(`${label} runs inside the container`))).toBeTruthy()
})

it.each(['signed_in', 'unknown', undefined] as const)(
  'shows no row for a found CLI with no word against its login: %s',
  (signIn) => {
    const { container } = render(
      <CliSubscriptionHelp
        provider="codex_cli"
        platform="linux"
        detected
        signIn={signIn}
        onCheck={onCheck}
      />,
    )
    expect(container.textContent).toBe('')
  },
)

it('says so when the sign-in lands while the help is open', () => {
  const props = { provider: 'codex_cli', platform: 'linux', detected: true, onCheck } as const
  const { rerender } = render(<CliSubscriptionHelp {...props} signIn="not_signed_in" />)
  fireEvent.click(screen.getByRole('button', { name: /^Sign in/ }))
  rerender(<CliSubscriptionHelp {...props} signIn="signed_in" />)
  expect(screen.getByText('Signed in. Lucius can use it now.')).toBeTruthy()
})
