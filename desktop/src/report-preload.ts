import { contextBridge, ipcRenderer } from 'electron';

if (process.isMainFrame && location.protocol === 'data:') {
  contextBridge.exposeInMainWorld('onwReport', Object.freeze({
    send: (report: unknown) => ipcRenderer.invoke('desktop-report:send', report),
    copy: (report: unknown) => ipcRenderer.invoke('desktop-report:copy', report),
    email: (report: unknown) => ipcRenderer.invoke('desktop-report:email', report),
    close: () => ipcRenderer.invoke('desktop-report:close'),
    community: () => ipcRenderer.invoke('desktop-report:community'),
  }));
}
