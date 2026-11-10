// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ReportProblemHost } from './ReportProblemDialog'
import { ErrorBoundary } from './ErrorBoundary'
import { closeReportProblem, openReportProblem } from '../state/reportProblem'
import { emailHref, reportText, SUPPORT_EMAIL, type ProblemReport } from '../utils/problemReport'

const DRAFT: ProblemReport = {
  description: '',
  contact_email: null,
  app_version: '0.1.2',
  platform: 'Linux x86_64 6.1; Chrome 130',
  mode: 'web',
  screen: '/accounts',
  error: 'Failed to import [email]',
  logs: '2026-10-04T10:00:00 INFO     backend.x created [amount] at [payee]',
}

interface Call {
  url: string
  body: unknown
}

function stubServer(options: { draft?: ProblemReport | null; send?: () => Response } = {}) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null })
      if (url.endsWith('/draft')) {
        if (options.draft === null) throw new TypeError('network down')
        return new Response(JSON.stringify(options.draft ?? DRAFT), { status: 200 })
      }
      return (
        options.send?.() ??
        new Response(JSON.stringify({ sent: true, id: 'rep_1' }), { status: 200 })
      )
    }),
  )
  return calls
}

async function openDialog(error?: string) {
  render(<ReportProblemHost />)
  act(() => openReportProblem({ error }))
  await screen.findByLabelText(/What happened/)
}

beforeEach(() => {
  closeReportProblem()
})

afterEach(() => {
  cleanup()
  closeReportProblem()
  delete window.onwDesktop
  delete window.onwDesktopClient
  vi.unstubAllGlobals()
})

describe('Report a problem dialog (browser)', () => {
  it('asks the server for a draft carrying the error and the screen, and sends nothing yet', async () => {
    const calls = stubServer()
    await openDialog('Boom at render')
    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('/api/problem-reports/draft')
    expect(calls[0]!.body).toMatchObject({ error: 'Boom at render', include_logs: true })
    expect((screen.getByLabelText('Error') as HTMLTextAreaElement).value).toBe(DRAFT.error)
    expect(screen.getByText('0.1.2')).toBeTruthy()
  })

  it('says why it asks for an email, and links to the Discord community', async () => {
    stubServer()
    await openDialog()
    expect(screen.getByText(/tell you when the fix is out/)).toBeTruthy()
    const link = screen.getByRole('link', { name: 'Join us on Discord' })
    expect(link.getAttribute('href')).toBe('https://discord.gg/gRRsQRgwE8')
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    fireEvent.click(link)
    expect(open).toHaveBeenCalledWith(
      'https://discord.gg/gRRsQRgwE8',
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('shows the Error field only when the report was opened from an error', async () => {
    stubServer({ draft: { ...DRAFT, error: null } })
    await openDialog()
    expect(screen.queryByLabelText('Error')).toBeNull()
  })

  it('offers one action, Send, which needs a description first', async () => {
    stubServer()
    await openDialog()
    const send = () => screen.getByRole('button', { name: 'Send report' }) as HTMLButtonElement
    expect(send().disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Copy report' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Email instead' })).toBeNull()
    fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: 'It froze' } })
    expect(send().disabled).toBe(false)
  })

  it('sends exactly what the exact-data box shows, edits included', async () => {
    const calls = stubServer()
    await openDialog()
    fireEvent.change(screen.getByLabelText(/What happened/), {
      target: { value: '  It froze  ' },
    })
    fireEvent.change(screen.getByLabelText('Email (optional)'), {
      target: { value: 'me@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Recent logs'), { target: { value: 'edited logs' } })
    const shown = JSON.parse(document.querySelector('.report-problem__exact pre')!.textContent!)
    fireEvent.click(screen.getByRole('button', { name: 'Send report' }))
    await screen.findByText('Thank you. Your report was sent.')
    const sent = calls.find((call) => call.url.endsWith('/send'))!
    expect(sent.body).toEqual(shown)
    expect(sent.body).toEqual({
      ...DRAFT,
      description: 'It froze',
      contact_email: 'me@example.com',
      logs: 'edited logs',
    })
    expect(screen.getByText('rep_1')).toBeTruthy()
    await waitFor(() => expect(document.activeElement?.textContent).toBe('Close'))
  })

  it('leaves the logs out when the box is unticked', async () => {
    const calls = stubServer()
    await openDialog()
    fireEvent.click(screen.getByLabelText(/Include recent logs/))
    fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: 'It froze' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send report' }))
    await screen.findByText('Thank you. Your report was sent.')
    expect(calls.find((call) => call.url.endsWith('/send'))!.body).toMatchObject({ logs: null })
  })

  it('when sending fails, keeps the report and offers copy and the support address', async () => {
    stubServer({
      send: () =>
        new Response(JSON.stringify({ detail: "Couldn't reach the report server." }), {
          status: 502,
        }),
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...window.navigator, clipboard: { writeText } })
    await openDialog()
    fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: 'It froze' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send report' }))
    await waitFor(() => expect(screen.getByText(/Couldn't reach the report server/)).toBeTruthy())
    expect(
      document.querySelector('.report-problem__status .report-problem__address')!.textContent,
    ).toBe(SUPPORT_EMAIL)
    fireEvent.click(screen.getByRole('button', { name: 'Copy report' }))
    const expected = reportText({
      ...DRAFT,
      description: 'It froze',
    })
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expected))
  })

  it('with the server unreachable, still lets the user copy or email, and says nothing was masked', async () => {
    stubServer({ draft: null })
    await openDialog('Cannot reach')
    expect(screen.getByText(/nothing was masked/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: 'No server' } })
    expect(screen.queryByRole('button', { name: 'Send report' })).toBeNull()
    for (const name of ['Copy report', 'Email instead']) {
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(false)
    }
    expect((screen.getByLabelText(/Include recent logs/) as HTMLInputElement).disabled).toBe(true)
  })

  it('formats the email draft with the support address and the top of the report', () => {
    const href = emailHref({ ...DRAFT, description: 'It froze' })
    expect(href.startsWith(`mailto:${SUPPORT_EMAIL}?subject=`)).toBe(true)
    expect(decodeURIComponent(href)).toContain('It froze')
    expect(href.length).toBeLessThan(4000)
  })
})

describe('entry points', () => {
  it('the error boundary offers Report this problem, prefilled with its error', async () => {
    stubServer()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    function Broken(): never {
      throw new Error('render exploded')
    }
    render(
      <>
        <ErrorBoundary label="Plan">
          <Broken />
        </ErrorBoundary>
        <ReportProblemHost />
      </>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Report this problem' }))
    const field = (await screen.findByLabelText('Error')) as HTMLTextAreaElement
    expect(field.value).toBe(DRAFT.error)
    expect(JSON.stringify(vi.mocked(fetch).mock.calls[0]![1]!.body)).toContain('render exploded')
  })

  it('inside the desktop app, the native dialog opens instead of the page one', () => {
    const reportProblem = vi.fn().mockResolvedValue(undefined)
    window.onwDesktop = { version: 1, reportProblem } as unknown as typeof window.onwDesktop
    render(<ReportProblemHost />)
    act(() => openReportProblem({ error: 'Bad key' }))
    expect(reportProblem).toHaveBeenCalledWith('Bad key')
    expect(screen.queryByLabelText(/What happened/)).toBeNull()
  })

  it('a desktop shell that refuses still leaves the page dialog', async () => {
    stubServer()
    const reportProblem = vi.fn().mockRejectedValue(new Error('busy'))
    window.onwDesktopClient = {
      version: 1,
      reportProblem,
    } as unknown as typeof window.onwDesktopClient
    render(<ReportProblemHost />)
    act(() => openReportProblem())
    await screen.findByLabelText(/What happened/)
  })
})
