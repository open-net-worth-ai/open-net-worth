import { Modal } from './ui/Modal'
import { desktopBridge, desktopClientBridge } from '../platform/desktop'
import { isMacPlatform, shortcutKeys } from '../utils/shortcuts'

interface Shortcut {
  label: string
  keys: readonly string[]
}

const EVERYWHERE: Shortcut[] = [
  { label: 'Command palette', keys: ['Mod', 'K'] },
  { label: 'Show or hide the sidebar', keys: ['Mod', 'B'] },
  { label: 'Show or hide the Lucius panel', keys: ['Mod', '/'] },
  { label: 'Hide amounts', keys: ['H'] },
  { label: 'Keyboard shortcuts', keys: ['Mod', 'Shift', '/'] },
]

function desktopShortcuts(mac: boolean): Shortcut[] {
  return [
    { label: 'Open Lucius', keys: ['Mod', 'L'] },
    { label: 'New Lucius conversation', keys: ['Mod', 'N'] },
    { label: 'New transaction', keys: ['Mod', 'Shift', 'N'] },
    { label: 'Import statement or receipt', keys: ['Mod', 'O'] },
    { label: 'Export book', keys: ['Mod', 'Shift', 'E'] },
    { label: 'Settings', keys: ['Mod', ','] },
    { label: 'Back', keys: mac ? ['Mod', '['] : ['Alt', '←'] },
    { label: 'Forward', keys: mac ? ['Mod', ']'] : ['Alt', '→'] },
    { label: 'Go to a main screen, in sidebar order', keys: ['Mod', '1–8'] },
    { label: 'Reload', keys: ['Mod', 'R'] },
  ]
}

function Group({ title, rows }: { title: string; rows: Shortcut[] }) {
  return (
    <section className="shortcuts-group" aria-label={title}>
      <h3 className="shortcuts-group__title">{title}</h3>
      <ul className="shortcuts-list">
        {rows.map((row) => (
          <li key={row.label} className="shortcuts-row">
            <span>{row.label}</span>
            <span className="shortcuts-keys">
              {shortcutKeys(row.keys).map((key) => (
                <kbd key={key}>{key}</kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The real shortcuts, listed from one place. The desktop group only appears inside the desktop
 *  app, whose native menu owns those keys. */
export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const desktop = Boolean(desktopBridge() ?? desktopClientBridge())
  return (
    <Modal open={open} onClose={onClose} title="Keyboard shortcuts" size="md">
      <div className="shortcuts">
        <Group title="Anywhere" rows={EVERYWHERE} />
        {desktop && <Group title="Desktop app" rows={desktopShortcuts(isMacPlatform())} />}
      </div>
    </Modal>
  )
}
