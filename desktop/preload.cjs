const { contextBridge, ipcRenderer } = require('electron');

const methods = [
  'GetConfig', 'ListMonitors', 'EngineStatus', 'SaveConfig',
  'StartEngine', 'StopEngine', 'TestWebhook', 'Capture', 'CheckOCR',
];
const api = Object.fromEntries(methods.map(method => [
  method, (...args) => ipcRenderer.invoke(`wardogs:${method}`, ...args),
]));
contextBridge.exposeInMainWorld('wardogs', Object.freeze(api));
