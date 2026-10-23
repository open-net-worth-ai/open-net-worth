/**
 * ds.tsx — Design-system pickers used by the Accounts tab modals.
 * Ported from prototype/accounts/modals.jsx (ColorPicker, InstitutionPicker, CurrencyPicker,
 * AccIcon, PMSelect) and detail.jsx (CcyPicker).
 */

import { useState, useRef, useEffect, useLayoutEffect, useMemo, useId } from 'react'
import { createPortal } from 'react-dom'
import { DRAG_IGNORE_PROPS } from '../../../hooks/useSheetDragToDismiss'
import { Check, Plus } from '../../../ui/icons'

// ── Icon paths (Lucide-style) ─────────────────────────────────────────────

const ICON_PATHS: Record<string, string> = {
  link: `<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>`,
  pencil: `<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>`,
  sparkles: `<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0Z"/>`,
  landmark: `<line x1="3" x2="21" y1="22" y2="22"/><line x1="6" x2="6" y1="18" y2="11"/><line x1="10" x2="10" y1="18" y2="11"/><line x1="14" x2="14" y1="18" y2="11"/><line x1="18" x2="18" y1="18" y2="11"/><polygon points="12 2 20 7 4 7"/>`,
  coins: `<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/>`,
  banknote: `<rect width="20" height="12" x="2" y="6" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>`,
  wallet: `<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>`,
  card: `<rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/>`,
  trending: `<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>`,
  chart: `<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>`,
  bitcoin: `<circle cx="12" cy="12" r="9"/><path d="M9.5 7.5h4a2.5 2.5 0 0 1 0 5h-4h4.5a2.5 2.5 0 0 1 0 5H9.5"/><path d="M10 5.5v13"/>`,
  award: `<circle cx="12" cy="8" r="6"/><path d="M15.48 12.89 17 22l-5-3-5 3 1.52-9.11"/>`,
  umbrella: `<path d="M12 12v8a2 2 0 0 0 4 0"/><path d="M12 2v1"/><path d="M2 12a10 10 0 0 1 20 0Z"/>`,
  building: `<rect width="16" height="20" x="4" y="2" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>`,
  car: `<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>`,
  gem: `<path d="M6 3h12l4 6-10 13L2 9Z"/><path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/>`,
  incoming: `<path d="M12 3v12"/><path d="m8 11 4 4 4-4"/><path d="M4 21h16"/>`,
  box: `<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>`,
  home: `<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>`,
  graduation: `<path d="M21.42 10.92a1 1 0 0 0-.02-1.84L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.83l8.57 3.91a2 2 0 0 0 1.66 0z"/><path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>`,
  scale: `<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>`,
  bag: `<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>`,
  file: `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><polyline points="14 2 14 8 20 8"/>`,
}

export interface AccIconProps {
  name: string
  size?: number
}
export function AccIcon({ name, size = 20 }: AccIconProps) {
  return (
    <svg
      className="acct-ic"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      dangerouslySetInnerHTML={{ __html: ICON_PATHS[name] || '' }}
    />
  )
}

// ── ColorPicker ────────────────────────────────────────────────────────────

const COLORS = [
  '#e2554f',
  '#e0a032',
  '#34b08a',
  '#3b86dd',
  '#9a7fd0',
  '#22b8cf',
  '#d4537e',
  '#bd7a1a',
  '#7c8595',
]

function hexToHsv(hex: string) {
  let h = (hex || '#3b86dd').replace('#', '')
  if (h.length === 3)
    h = h
      .split('')
      .map((c) => c + c)
      .join('')
  const r = parseInt(h.slice(0, 2), 16) / 255,
    g = parseInt(h.slice(2, 4), 16) / 255,
    b = parseInt(h.slice(4, 6), 16) / 255
  const mx = Math.max(r, g, b),
    mn = Math.min(r, g, b),
    d = mx - mn
  let hue = 0
  if (d) {
    if (mx === r) hue = ((g - b) / d) % 6
    else if (mx === g) hue = (b - r) / d + 2
    else hue = (r - g) / d + 4
    hue *= 60
    if (hue < 0) hue += 360
  }
  return { h: hue, s: mx ? d / mx : 0, v: mx }
}
function hsvToHex(h: number, s: number, v: number) {
  const c = v * s,
    x = c * (1 - Math.abs(((h / 60) % 2) - 1)),
    m = v - c
  let r = 0,
    g = 0,
    b = 0
  if (h < 60) [r, g, b] = [c, x, 0]
  else if (h < 120) [r, g, b] = [x, c, 0]
  else if (h < 180) [r, g, b] = [0, c, x]
  else if (h < 240) [r, g, b] = [0, x, c]
  else if (h < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  const to = (n: number) =>
    Math.round((n + m) * 255)
      .toString(16)
      .padStart(2, '0')
  return '#' + to(r) + to(g) + to(b)
}

export interface ColorPickerProps {
  value: string
  onChange: (c: string) => void
}
export function ColorPicker({ value, onChange }: ColorPickerProps) {
  const [open, setOpen] = useState(false)
  const popRef = useRef<HTMLDivElement>(null)
  const [shiftX, setShiftX] = useState(0)
  const ref = useRef<HTMLDivElement>(null),
    svRef = useRef<HTMLDivElement>(null)
  const [h, setH] = useState(hexToHsv(value).h)
  useEffect(() => {
    if (!open) return
    setH(hexToHsv(value).h)
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  // The popover opens anchored at the swatch's left edge, then slides sideways just enough to
  // stay inside whatever clips it (or the window): the dashboard's settings rail puts this
  // swatch near its right edge, where a fixed anchor on either side was cut off.
  useLayoutEffect(() => {
    if (!open || !popRef.current || !ref.current) return
    const anchor = ref.current.getBoundingClientRect()
    const width = popRef.current.getBoundingClientRect().width
    let lo = 0
    let hi = window.innerWidth
    for (let el = ref.current.parentElement; el; el = el.parentElement) {
      const cs = getComputedStyle(el)
      if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
        const box = el.getBoundingClientRect()
        lo = Math.max(lo, box.left)
        hi = Math.min(hi, box.right)
        break
      }
    }
    const inset = 8
    const overRight = anchor.left + width - (hi - inset)
    const shift = overRight > 0 ? -overRight : 0
    setShiftX(Math.max(shift, lo + inset - anchor.left))
  }, [open])
  const cur = hexToHsv(value)
  const dragSV = (e: React.PointerEvent<HTMLDivElement>) => {
    const box = svRef.current
    if (!box) return
    const move = (ev: PointerEvent) => {
      const r = box.getBoundingClientRect()
      onChange(
        hsvToHex(
          h,
          Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
          1 - Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)),
        ),
      )
    }
    move(e.nativeEvent)
    const up = () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
  }
  const onHue = (e: React.ChangeEvent<HTMLInputElement>) => {
    const nh = +e.target.value
    setH(nh)
    onChange(hsvToHex(nh, cur.s || 1, cur.v || 1))
  }
  const onHex = (e: React.ChangeEvent<HTMLInputElement>) => {
    let v = e.target.value.trim()
    if (!v.startsWith('#')) v = '#' + v
    if (/^#[0-9a-fA-F]{6}$/.test(v)) onChange(v)
  }
  return (
    <div className="acct-cp" ref={ref}>
      <button
        type="button"
        className={'acct-swatch acct-swatch-custom' + (COLORS.includes(value) ? '' : ' on')}
        style={COLORS.includes(value) ? undefined : { background: value }}
        title="Custom colour"
        onClick={() => {
          setShiftX(0)
          setOpen((o) => !o)
        }}
      >
        {COLORS.includes(value) && (
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
          </svg>
        )}
      </button>
      {open && (
        <div
          className="acct-cp-pop"
          ref={popRef}
          style={shiftX ? { left: shiftX } : undefined}
          // Inside a phone bottom sheet, dragging the colour field must not drag the sheet.
          {...DRAG_IGNORE_PROPS}
        >
          <div
            className="acct-cp-sv"
            ref={svRef}
            onPointerDown={dragSV}
            style={{
              background: `linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,${hsvToHex(h, 1, 1)})`,
            }}
          >
            <span
              className="acct-cp-thumb"
              style={{ left: cur.s * 100 + '%', top: (1 - cur.v) * 100 + '%', background: value }}
            />
          </div>
          <input
            type="range"
            className="acct-cp-hue"
            min="0"
            max="359"
            value={Math.round(h)}
            onChange={onHue}
          />
          <div className="acct-cp-foot">
            <span className="acct-cp-prev" style={{ background: value }} />
            <input
              className="acct-cp-hex"
              value={value.toUpperCase()}
              onChange={onHex}
              spellCheck={false}
              maxLength={7}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Shared caret icon (CurrencyPicker, CcyPicker) ──────────────────────────

const Caret = () => (
  <svg
    className="acct-ccyp-caret"
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.4"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M6 9l6 6 6-6" />
  </svg>
)

// ── InstitutionPicker ─────────────────────────────────────────────────────

export interface InstitutionPickerProps {
  id?: string
  value: string
  onChange: (v: string) => void
  options: InstitutionOption[]
  onManage?: () => void
}
export interface InstitutionOption {
  id: string
  name: string
  accountCount: number
}
export function InstitutionPicker({
  id,
  value,
  onChange,
  options,
  onManage,
}: InstitutionPickerProps) {
  const generatedId = useId()
  const listId = `${id || generatedId}-options`
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1)
  const ref = useRef<HTMLDivElement>(null)
  const allOptions = useMemo(() => {
    const seen = new Set<string>(),
      out: InstitutionOption[] = []
    ;(options || []).forEach((o) => {
      const k = o.name.toLowerCase()
      if (!seen.has(k)) {
        seen.add(k)
        out.push(o)
      }
    })
    return out
  }, [options])
  const ql = (value || '').trim().toLowerCase()
  const matches = allOptions.filter((o) => o.name.toLowerCase().includes(ql))
  const isKnown = !!ql && allOptions.some((o) => o.name.toLowerCase() === ql)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  const choose = (option: InstitutionOption) => {
    onChange(option.name)
    setOpen(false)
  }
  const acceptInst = (v: string) => {
    const name = v.trim()
    if (!name) return
    onChange(name)
    setOpen(false)
  }
  const showCreate = !!ql && !allOptions.some((o) => o.name.toLowerCase() === ql)
  function onKey(e: React.KeyboardEvent) {
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, matches.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter' && hi >= 0 && matches[hi]) {
      e.preventDefault()
      choose(matches[hi])
    } else if (e.key === 'Enter' && hi < 0 && showCreate) {
      e.preventDefault()
      acceptInst(value)
    } else if (e.key === 'Escape') setOpen(false)
  }
  return (
    <div className="acct-ccyp" ref={ref}>
      <div className="acct-instp-wrap">
        <input
          id={id}
          className="ui-input acct-instp-input"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listId}
          placeholder="e.g. DBS, IBKR"
          value={value}
          onFocus={() => {
            setOpen(true)
            setHi(-1)
          }}
          onChange={(e) => {
            onChange(e.target.value)
            setOpen(true)
            setHi(-1)
          }}
          onKeyDown={onKey}
        />
        <span className="acct-instp-caret" aria-hidden="true">
          <Caret />
        </span>
      </div>
      {!!ql && !isKnown && !open && (
        <div className="acct-instp-note">Will be saved with this account</div>
      )}
      {open && (
        <div className="acct-ccyp-pop">
          <div className="acct-ccyp-list" id={listId} role="listbox">
            {matches.length === 0 && !showCreate && (
              <div className="acct-instp-empty">No saved institutions yet</div>
            )}
            {matches.map((o, i) => (
              <button
                type="button"
                role="option"
                aria-label={o.name}
                aria-selected={o.name.toLowerCase() === ql}
                key={o.id}
                className={
                  'acct-ccyp-item' + (o.name === value ? ' sel' : '') + (i === hi ? ' hi' : '')
                }
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(o)
                }}
              >
                <span className="acct-ccyp-name" style={{ color: 'var(--ink)' }}>
                  {o.name}
                </span>
                {o.name === value && (
                  <Check className="acct-ccyp-check" size={12} aria-hidden="true" />
                )}
              </button>
            ))}
            {showCreate && (
              <button
                type="button"
                role="option"
                aria-label={`Use “${value.trim()}”`}
                aria-selected="false"
                className="acct-ccyp-item create"
                onMouseDown={(e) => {
                  e.preventDefault()
                  acceptInst(value)
                }}
              >
                <Plus className="acct-payf-plus" size={12} aria-hidden="true" />
                <span>
                  Use “<b>{value.trim()}</b>”
                </span>
              </button>
            )}
          </div>
          {onManage && (
            <button
              type="button"
              className="acct-instp-manage"
              onMouseDown={(e) => {
                e.preventDefault()
                setOpen(false)
                onManage()
              }}
            >
              Manage saved institutions
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── ISO 4217 currency list (common pinned first) ───────────────────────────

const CURRENCIES: [string, string][] = [
  ['SGD', 'Singapore Dollar'],
  ['USD', 'US Dollar'],
  ['EUR', 'Euro'],
  ['GBP', 'British Pound'],
  ['JPY', 'Japanese Yen'],
  ['CNY', 'Chinese Yuan'],
  ['HKD', 'Hong Kong Dollar'],
  ['AUD', 'Australian Dollar'],
  ['CAD', 'Canadian Dollar'],
  ['CHF', 'Swiss Franc'],
  ['INR', 'Indian Rupee'],
  ['MYR', 'Malaysian Ringgit'],
  ['—', '—'],
  ['AED', 'UAE Dirham'],
  ['AFN', 'Afghan Afghani'],
  ['ALL', 'Albanian Lek'],
  ['AMD', 'Armenian Dram'],
  ['ANG', 'Netherlands Antillean Guilder'],
  ['AOA', 'Angolan Kwanza'],
  ['ARS', 'Argentine Peso'],
  ['AWG', 'Aruban Florin'],
  ['AZN', 'Azerbaijani Manat'],
  ['BAM', 'Bosnia-Herzegovina Mark'],
  ['BBD', 'Barbadian Dollar'],
  ['BDT', 'Bangladeshi Taka'],
  ['BGN', 'Bulgarian Lev'],
  ['BHD', 'Bahraini Dinar'],
  ['BIF', 'Burundian Franc'],
  ['BMD', 'Bermudan Dollar'],
  ['BND', 'Brunei Dollar'],
  ['BOB', 'Bolivian Boliviano'],
  ['BRL', 'Brazilian Real'],
  ['BSD', 'Bahamian Dollar'],
  ['BTN', 'Bhutanese Ngultrum'],
  ['BWP', 'Botswanan Pula'],
  ['BYN', 'Belarusian Ruble'],
  ['BZD', 'Belize Dollar'],
  ['CDF', 'Congolese Franc'],
  ['CLP', 'Chilean Peso'],
  ['COP', 'Colombian Peso'],
  ['CRC', 'Costa Rican Colón'],
  ['CUP', 'Cuban Peso'],
  ['CVE', 'Cape Verdean Escudo'],
  ['CZK', 'Czech Koruna'],
  ['DJF', 'Djiboutian Franc'],
  ['DKK', 'Danish Krone'],
  ['DOP', 'Dominican Peso'],
  ['DZD', 'Algerian Dinar'],
  ['EGP', 'Egyptian Pound'],
  ['ERN', 'Eritrean Nakfa'],
  ['ETB', 'Ethiopian Birr'],
  ['FJD', 'Fijian Dollar'],
  ['FKP', 'Falkland Islands Pound'],
  ['GEL', 'Georgian Lari'],
  ['GHS', 'Ghanaian Cedi'],
  ['GIP', 'Gibraltar Pound'],
  ['GMD', 'Gambian Dalasi'],
  ['GNF', 'Guinean Franc'],
  ['GTQ', 'Guatemalan Quetzal'],
  ['GYD', 'Guyanaese Dollar'],
  ['HNL', 'Honduran Lempira'],
  ['HRK', 'Croatian Kuna'],
  ['HTG', 'Haitian Gourde'],
  ['HUF', 'Hungarian Forint'],
  ['IDR', 'Indonesian Rupiah'],
  ['ILS', 'Israeli New Shekel'],
  ['IQD', 'Iraqi Dinar'],
  ['IRR', 'Iranian Rial'],
  ['ISK', 'Icelandic Króna'],
  ['JMD', 'Jamaican Dollar'],
  ['JOD', 'Jordanian Dinar'],
  ['KES', 'Kenyan Shilling'],
  ['KGS', 'Kyrgystani Som'],
  ['KHR', 'Cambodian Riel'],
  ['KMF', 'Comorian Franc'],
  ['KRW', 'South Korean Won'],
  ['KWD', 'Kuwaiti Dinar'],
  ['KYD', 'Cayman Islands Dollar'],
  ['KZT', 'Kazakhstani Tenge'],
  ['LAK', 'Laotian Kip'],
  ['LBP', 'Lebanese Pound'],
  ['LKR', 'Sri Lankan Rupee'],
  ['LRD', 'Liberian Dollar'],
  ['LSL', 'Lesotho Loti'],
  ['LYD', 'Libyan Dinar'],
  ['MAD', 'Moroccan Dirham'],
  ['MDL', 'Moldovan Leu'],
  ['MGA', 'Malagasy Ariary'],
  ['MKD', 'Macedonian Denar'],
  ['MMK', 'Myanmar Kyat'],
  ['MNT', 'Mongolian Tugrik'],
  ['MOP', 'Macanese Pataca'],
  ['MRU', 'Mauritanian Ouguiya'],
  ['MUR', 'Mauritian Rupee'],
  ['MVR', 'Maldivian Rufiyaa'],
  ['MWK', 'Malawian Kwacha'],
  ['MXN', 'Mexican Peso'],
  ['MZN', 'Mozambican Metical'],
  ['NAD', 'Namibian Dollar'],
  ['NGN', 'Nigerian Naira'],
  ['NIO', 'Nicaraguan Córdoba'],
  ['NOK', 'Norwegian Krone'],
  ['NPR', 'Nepalese Rupee'],
  ['NZD', 'New Zealand Dollar'],
  ['OMR', 'Omani Rial'],
  ['PAB', 'Panamanian Balboa'],
  ['PEN', 'Peruvian Sol'],
  ['PGK', 'Papua New Guinean Kina'],
  ['PHP', 'Philippine Peso'],
  ['PKR', 'Pakistani Rupee'],
  ['PLN', 'Polish Złoty'],
  ['PYG', 'Paraguayan Guarani'],
  ['QAR', 'Qatari Rial'],
  ['RON', 'Romanian Leu'],
  ['RSD', 'Serbian Dinar'],
  ['RUB', 'Russian Ruble'],
  ['RWF', 'Rwandan Franc'],
  ['SAR', 'Saudi Riyal'],
  ['SBD', 'Solomon Islands Dollar'],
  ['SCR', 'Seychellois Rupee'],
  ['SDG', 'Sudanese Pound'],
  ['SEK', 'Swedish Krona'],
  ['SHP', 'Saint Helena Pound'],
  ['SLE', 'Sierra Leonean Leone'],
  ['SOS', 'Somali Shilling'],
  ['SRD', 'Surinamese Dollar'],
  ['SSP', 'South Sudanese Pound'],
  ['STN', 'São Tomé Dobra'],
  ['SYP', 'Syrian Pound'],
  ['SZL', 'Swazi Lilangeni'],
  ['THB', 'Thai Baht'],
  ['TJS', 'Tajikistani Somoni'],
  ['TMT', 'Turkmenistani Manat'],
  ['TND', 'Tunisian Dinar'],
  ['TOP', 'Tongan Paʻanga'],
  ['TRY', 'Turkish Lira'],
  ['TTD', 'Trinidad & Tobago Dollar'],
  ['TWD', 'New Taiwan Dollar'],
  ['TZS', 'Tanzanian Shilling'],
  ['UAH', 'Ukrainian Hryvnia'],
  ['UGX', 'Ugandan Shilling'],
  ['UYU', 'Uruguayan Peso'],
  ['UZS', 'Uzbekistani Som'],
  ['VES', 'Venezuelan Bolívar'],
  ['VND', 'Vietnamese Dong'],
  ['VUV', 'Vanuatu Vatu'],
  ['WST', 'Samoan Tala'],
  ['XAF', 'Central African CFA Franc'],
  ['XCD', 'East Caribbean Dollar'],
  ['XOF', 'West African CFA Franc'],
  ['XPF', 'CFP Franc'],
  ['YER', 'Yemeni Rial'],
  ['ZAR', 'South African Rand'],
  ['ZMW', 'Zambian Kwacha'],
  ['ZWL', 'Zimbabwean Dollar'],
]

// ── CurrencyPicker ─────────────────────────────────────────────────────────

// Popup geometry. CCY_POP_H is the preferred height; CCY_POP_MIN_H is the floor below which
// flipping stops helping and a short scrolling list is simply accepted.
const CCY_POP_H = 300
const CCY_POP_MIN_H = 160
const CCY_POP_GAP = 5

export interface CurrencyPickerProps {
  value: string
  onChange: (c: string) => void
  /** Open the list on mount. For a caller that only renders this once the user has already
      said "change this" — a second click to reveal the list answers a question they answered. */
  defaultOpen?: boolean
}
export function CurrencyPicker({ value, onChange, defaultOpen }: CurrencyPickerProps) {
  const [open, setOpen] = useState(false),
    [q, setQ] = useState(''),
    [hi, setHi] = useState(0)
  // The picker lives inside a modal body with `overflow: auto`, and an absolutely-positioned
  // popup is CLIPPED by that scroll container — you'd get two rows and a cut-off list, and
  // flipping it upward only moves which edge clips. So the popup is PORTALLED to <body> and
  // positioned `fixed` off the button's rect, which no ancestor overflow can crop.
  const [box, setBox] = useState<{
    left: number
    top: number
    width: number
    up: boolean
    maxH: number
  } | null>(null)
  const ref = useRef<HTMLDivElement>(null),
    popRef = useRef<HTMLDivElement>(null),
    inputRef = useRef<HTMLInputElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null),
    popBoxRef = useRef<HTMLDivElement>(null)
  // Did `hi` last move by ARROW KEY (scroll it into view) or by hover (leave the list alone)?
  const hiFromKey = useRef(false)
  // Where the popup is portalled. It must land INSIDE the Radix dialog's content subtree:
  // portalled to <body> it sat outside, so react-remove-scroll (which Dialog installs) ate its
  // wheel events — the list would not scroll — and Radix's outside-press handler treated a
  // click on a currency as a click outside the dialog and closed the whole modal. The card has
  // no transform, so a `position: fixed` child of it is still viewport-positioned and is NOT
  // clipped by the card's `overflow: hidden`.
  const hostRef = useRef<Element | null>(null)
  const place = () => {
    const r = btnRef.current?.getBoundingClientRect()
    if (!r) return
    // Escaping the overflow clip must not mean escaping the DIALOG — a `fixed` popup that
    // simply drops 300px would hang off the bottom edge onto the page behind. Bound it by the
    // modal card when there is one (viewport otherwise), pick the roomier side, and cap the
    // height to what that side actually has; the list scrolls inside whatever it gets.
    const host = btnRef.current?.closest('.ui-modal-card')
    hostRef.current = host ?? document.body
    const b = host ? host.getBoundingClientRect() : { top: 0, bottom: window.innerHeight }
    const below = b.bottom - r.bottom - CCY_POP_GAP * 2,
      above = r.top - b.top - CCY_POP_GAP * 2
    const up = below < Math.min(CCY_POP_H, above)
    setBox({
      left: r.left,
      width: r.width,
      up,
      top: up ? r.top - CCY_POP_GAP : r.bottom + CCY_POP_GAP,
      maxH: Math.max(CCY_POP_MIN_H, Math.min(CCY_POP_H, up ? above : below)),
    })
  }
  const real = CURRENCIES.filter(([c]) => c !== '—')
  const ql = q.trim().toLowerCase()
  const matches = ql
    ? real.filter(([c, n]) => c.toLowerCase().includes(ql) || n.toLowerCase().includes(ql))
    : real
  const cur = real.find(([c]) => c === value)
  useEffect(() => {
    if (!open) return
    // The popup is portalled out of `ref`, so an outside-click test must accept it too.
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || popBoxRef.current?.contains(t)) return
      setOpen(false)
    }
    // Any ANCESTOR scrolling (the modal body) moves the button out from under a `fixed`
    // popup — follow it. `capture` so the modal's own scroll container is heard, but ignore
    // the popup's OWN list scrolling, which doesn't move the anchor and only churns state.
    const onMove = (e: Event) => {
      if (e.target instanceof Node && popBoxRef.current?.contains(e.target)) return
      place()
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    requestAnimationFrame(() => inputRef.current?.focus())
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open])
  // Keep the highlighted row in view for KEYBOARD navigation only. Doing it for hover made
  // the list unscrollable: scrolling slid a new row under a stationary cursor, whose
  // mouseenter re-highlighted it, and this effect yanked the list straight back.
  useEffect(() => {
    if (!open || !popRef.current || !hiFromKey.current) return
    const el = popRef.current.querySelector<HTMLElement>('.acct-ccyp-item.hi')
    if (!el) return
    const er = el.getBoundingClientRect(),
      pr = popRef.current.getBoundingClientRect()
    if (er.top < pr.top) popRef.current.scrollTop -= pr.top - er.top + 6
    else if (er.bottom > pr.bottom) popRef.current.scrollTop += er.bottom - pr.bottom + 6
  }, [hi, open])
  const choose = (c: string) => {
    onChange(c)
    setOpen(false)
    setQ('')
  }
  function onKey(e: React.KeyboardEvent) {
    hiFromKey.current = true
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, matches.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (matches[hi]) choose(matches[hi][0])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }
  // Runs once, after layout: place() reads the button's rect, which does not exist until the
  // button is mounted.
  useEffect(() => {
    if (!defaultOpen) return
    place()
    setOpen(true)
    setHi(
      Math.max(
        0,
        matches.findIndex(([c]) => c === value),
      ),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="acct-ccyp" ref={ref}>
      <button
        type="button"
        ref={btnRef}
        className={'ui-select acct-ccyp-btn' + (open ? ' open' : '')}
        onClick={() => {
          place()
          setOpen((o) => !o)
          setQ('')
          setHi(
            Math.max(
              0,
              matches.findIndex(([c]) => c === value),
            ),
          )
        }}
      >
        <span className="acct-ccyp-val">{cur ? cur[0] + ' — ' + cur[1] : value}</span>
        <Caret />
      </button>
      {open &&
        box &&
        createPortal(
          <div
            ref={popBoxRef}
            className={'acct-ccyp-pop portal' + (box.up ? ' up' : '')}
            style={{
              left: box.left,
              width: box.width,
              maxHeight: box.maxH,
              ...(box.up ? { bottom: window.innerHeight - box.top } : { top: box.top }),
            }}
          >
            <div className="acct-ccyp-search">
              <input
                ref={inputRef}
                className="acct-ccyp-input"
                placeholder="Search currency…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value)
                  setHi(0)
                }}
                onKeyDown={onKey}
              />
            </div>
            <div className="acct-ccyp-list" ref={popRef}>
              {matches.length === 0 && <div className="acct-ccyp-empty">No match</div>}
              {matches.map(([c, n], i) => (
                <button
                  type="button"
                  key={c}
                  className={
                    'acct-ccyp-item' + (c === value ? ' sel' : '') + (i === hi ? ' hi' : '')
                  }
                  onMouseEnter={() => {
                    hiFromKey.current = false
                    setHi(i)
                  }}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    choose(c)
                  }}
                >
                  <span className="acct-ccyp-code">{c}</span>
                  <span className="acct-ccyp-name">{n}</span>
                  {c === value && (
                    <Check className="acct-ccyp-check" size={12} aria-hidden="true" />
                  )}
                </button>
              ))}
            </div>
          </div>,
          hostRef.current ?? document.body,
        )}
    </div>
  )
}

// ── CcyPicker (from detail.jsx — "" = Same as account) ────────────────────

const CCY_NAMES: Record<string, string> = {
  SGD: 'Singapore Dollar',
  USD: 'US Dollar',
  EUR: 'Euro',
  GBP: 'British Pound',
  HKD: 'Hong Kong Dollar',
  JPY: 'Japanese Yen',
  AUD: 'Australian Dollar',
  VND: 'Vietnamese Dong',
  THB: 'Thai Baht',
  MYR: 'Malaysian Ringgit',
  IDR: 'Indonesian Rupiah',
  CNY: 'Chinese Yuan',
  INR: 'Indian Rupee',
  KRW: 'South Korean Won',
  PHP: 'Philippine Peso',
  TWD: 'Taiwan Dollar',
  CHF: 'Swiss Franc',
  CAD: 'Canadian Dollar',
  NZD: 'New Zealand Dollar',
  SEK: 'Swedish Krona',
  NOK: 'Norwegian Krone',
  DKK: 'Danish Krone',
  AED: 'UAE Dirham',
  SAR: 'Saudi Riyal',
  QAR: 'Qatari Riyal',
  ZAR: 'South African Rand',
  BRL: 'Brazilian Real',
  MXN: 'Mexican Peso',
  RUB: 'Russian Ruble',
  TRY: 'Turkish Lira',
  PLN: 'Polish Zloty',
  CZK: 'Czech Koruna',
  HUF: 'Hungarian Forint',
  ILS: 'Israeli Shekel',
}
const ALL_CCY = Object.keys(CCY_NAMES).sort()

export interface CcyPickerProps {
  value: string
  onChange: (c: string) => void
}
export function CcyPicker({ value, onChange }: CcyPickerProps) {
  const [open, setOpen] = useState(false),
    [q, setQ] = useState(''),
    [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement>(null),
    inRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!open) return
    setQ('')
    setHi(0)
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    const t = setTimeout(() => inRef.current?.focus(), 0)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      clearTimeout(t)
    }
  }, [open])
  const ql = q.trim().toLowerCase()
  const opts = [{ code: '', name: 'Same as account' }].concat(
    ALL_CCY.map((c) => ({ code: c, name: CCY_NAMES[c] || c })),
  )
  const matches = ql
    ? opts.filter(
        (o) =>
          o.code.toLowerCase().includes(ql) ||
          o.name.toLowerCase().includes(ql) ||
          (o.code === '' && 'same'.includes(ql)),
      )
    : opts
  const choose = (code: string) => {
    onChange(code)
    setOpen(false)
  }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, matches.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (matches[hi]) choose(matches[hi].code)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }
  return (
    <div className="acct-ccyp" ref={ref}>
      <button
        type="button"
        className={'ui-select acct-edit-in acct-ccyp-btn' + (open ? ' open' : '')}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="acct-ccyp-val">{value || 'Same'}</span>
        <Caret />
      </button>
      {open && (
        <div className="acct-ccyp-pop">
          <div className="acct-ccyp-search">
            <input
              ref={inRef}
              className="acct-ccyp-input"
              placeholder="Search currency"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setHi(0)
              }}
              onKeyDown={onKey}
            />
          </div>
          <div className="acct-ccyp-list">
            {matches.length === 0 && <div className="acct-ccyp-empty">No currency found</div>}
            {matches.map((o, i) => (
              <button
                type="button"
                key={o.code || '_same'}
                className={
                  'acct-ccyp-item' + (o.code === value ? ' sel' : '') + (i === hi ? ' hi' : '')
                }
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(o.code)
                }}
              >
                <span className="acct-ccyp-code">{o.code || '—'}</span>
                <span className="acct-ccyp-name">{o.name}</span>
                {o.code === value && (
                  <Check className="acct-ccyp-check" size={12} aria-hidden="true" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── PMSelect ───────────────────────────────────────────────────────────────

export interface PMSelectProps {
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (v: string) => void
  placeholder?: string
  className?: string
  searchable?: boolean
  compact?: boolean
}

export function PMSelect({
  value,
  options,
  onChange,
  placeholder,
  className,
  searchable,
  compact,
}: PMSelectProps) {
  const [open, setOpen] = useState(false),
    [pos, setPos] = useState<React.CSSProperties | null>(null)
  const [q, setQ] = useState(''),
    [hi, setHi] = useState(0)
  const ref = useRef<HTMLDivElement>(null),
    btnRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null),
    popRef = useRef<HTMLDivElement>(null)
  function place() {
    if (!btnRef.current) return
    const r = btnRef.current.getBoundingClientRect(),
      below = window.innerHeight - r.bottom
    const width = Math.round(Math.max(r.width, 168))
    let left = Math.round(r.left)
    if (left + width > window.innerWidth - 8) left = window.innerWidth - 8 - width
    const base: React.CSSProperties = { position: 'fixed', left, width, zIndex: 600 }
    if (below < 260)
      setPos({
        ...base,
        bottom: Math.round(window.innerHeight - r.top) + 4,
        maxHeight: Math.round(r.top - 12),
      })
    else setPos({ ...base, top: Math.round(r.bottom) + 4, maxHeight: Math.round(below - 12) })
  }
  function openMenu() {
    setQ('')
    setHi(0)
    place()
    setOpen(true)
  }
  useEffect(() => {
    if (!open) return
    if (searchable && searchRef.current) searchRef.current.focus()
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onScroll = (e: Event) => {
      if (ref.current && ref.current.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('scroll', onScroll, true)
    }
  })
  const cur = options.find((o) => o.value === value)
  const shown =
    searchable && q.trim()
      ? options.filter(
          (o) => o.value !== '' && o.label.toLowerCase().includes(q.toLowerCase().trim()),
        )
      : options
  useEffect(() => {
    if (!open || !popRef.current) return
    const el = popRef.current.querySelector<HTMLElement>('.acct-acctsel-item.hi')
    if (!el) return
    const pop = popRef.current,
      top = el.offsetTop,
      bottom = top + el.offsetHeight
    if (top < pop.scrollTop) pop.scrollTop = top
    else if (bottom > pop.scrollTop + pop.clientHeight) pop.scrollTop = bottom - pop.clientHeight
  }, [hi, open, q])
  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        openMenu()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHi((h) => Math.min(h + 1, shown.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const o = shown[hi]
      if (o) {
        onChange(o.value)
        setOpen(false)
        btnRef.current?.focus()
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      btnRef.current?.focus()
    }
  }
  return (
    <div className={'acct-acctsel pm-select' + (className ? ' ' + className : '')} ref={ref}>
      <button
        ref={btnRef}
        type="button"
        className={'acct-acctsel-btn' + (open ? ' open' : '')}
        title={compact ? 'Sort' : undefined}
        onClick={() => {
          if (open) setOpen(false)
          else openMenu()
        }}
        onKeyDown={searchable ? undefined : onKey}
      >
        {compact ? (
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M7 4v16M7 4 4 7M7 4l3 3M17 20V4M17 20l-3-3M17 20l3-3" />
          </svg>
        ) : (
          <>
            <span className="acct-acctsel-nm">{cur ? cur.label : placeholder || ''}</span>
            <svg
              className="acct-acctsel-caret"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </>
        )}
      </button>
      {open && (
        <div className="acct-acctsel-pop" role="listbox" style={pos ?? undefined} ref={popRef}>
          {searchable && (
            <input
              ref={searchRef}
              className="pm-select-search"
              placeholder="Search payees…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setHi(0)
              }}
              onKeyDown={onKey}
              onMouseDown={(e) => e.stopPropagation()}
            />
          )}
          {shown.map((o, i) => (
            <button
              type="button"
              key={o.value}
              className={
                'acct-acctsel-item' + (o.value === value ? ' sel' : '') + (i === hi ? ' hi' : '')
              }
              onMouseEnter={() => setHi(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                onChange(o.value)
                setOpen(false)
              }}
            >
              <span className="acct-acctsel-check">
                {o.value === value ? <Check size={12} aria-hidden="true" /> : null}
              </span>
              <span className="acct-acctsel-nm">{o.label}</span>
            </button>
          ))}
          {searchable && shown.filter((o) => o.value !== '').length === 0 && (
            <div className="pm-select-noresult">No payees match.</div>
          )}
        </div>
      )}
    </div>
  )
}
