import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import Module from 'node:module';
import { test } from 'node:test';

type Handler = (event: unknown, ...args: unknown[]) => unknown;

class FakeWindow extends EventEmitter {
  destroyed = false;
  readonly webContents = Object.assign(new EventEmitter(), { setWindowOpenHandler: () => {}, mainFrame: { url: '' } });
  constructor(readonly options: { parent?: FakeWindow } = {}) { super(); }
  isDestroyed(): boolean { return this.destroyed; }
  destroy(): void { if (this.destroyed) return; this.destroyed = true; this.emit('closed'); }
  isVisible(): boolean { return true; }
  show(): void {}
  focus(): void {}
  async loadURL(url: string): Promise<void> { this.webContents.mainFrame.url = url; }
}

test('a switch that replaces the main window takes the Connection window with it, so the failure alert stays clickable', async () => {
  const handlers = new Map<string, Handler>();
  const created: FakeWindow[] = [];
  const alertParents: FakeWindow[] = [];
  const fake = {
    ipcMain: { handle: (channel: string, fn: Handler) => handlers.set(channel, fn) },
    BrowserWindow: class extends FakeWindow { constructor(options: { parent?: FakeWindow }) { super(options); created.push(this); } },
    dialog: { showMessageBox: async (parent: FakeWindow) => { alertParents.push(parent); return { response: 1 }; } },
    nativeTheme: { shouldUseDarkColors: false },
    screen: {},
    session: { fromPartition: () => ({ setPermissionRequestHandler: () => {}, setPermissionCheckHandler: () => {} }) },
  };
  const loader = Module as unknown as { _load: (request: string, ...rest: unknown[]) => unknown };
  const original = loader._load;
  loader._load = function (request: string, ...rest: unknown[]) { return request === 'electron' ? fake : original.call(this, request, ...rest); };
  let ConnectionDialog: typeof import('../src/main/connection-dialog').ConnectionDialog;
  try { ({ ConnectionDialog } = require('../src/main/connection-dialog')); } finally { loader._load = original; }

  let main = new FakeWindow();
  const replacement = new FakeWindow();
  const dialog = new ConnectionDialog({
    parent: () => main, currentServer: () => 'http://localhost:8080', currentFolder: () => null, currentHosted: () => false, localRunning: () => false,
    connectServer: async () => {}, signInHosted: async () => {}, moveBook: async () => {}, moveBack: async () => {}, currentEmail: () => null,
    // The real switch retires (destroys) the main window, builds another, then fails and reopens the server.
    connectLocal: async () => { main.destroy(); main = replacement; throw new Error('This book is already open in another Open Net Worth server on port 8080.'); },
  });
  void dialog.show();
  await new Promise(resolve => setImmediate(resolve));
  const connection = created[0]!;
  assert.equal(connection.options.parent !== undefined, true);

  await handlers.get('desktop-connection:local')!({ sender: connection.webContents, senderFrame: connection.webContents.mainFrame });

  assert.equal(connection.isDestroyed(), true, 'the orphaned Connection window is gone');
  assert.equal(dialog.isOpen, false);
  assert.deepEqual(alertParents, [replacement], 'the failure alert is attached to the live main window');
});
