const { app, BrowserWindow, ipcMain, session } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

let window;
let watcher;
let watcherDetail = 'Stopped';
const uiPath = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');
const engineDir = () => app.isPackaged
  ? path.join(process.resourcesPath, 'engine')
  : path.join(__dirname, '..', '.tools', 'engine-dist', 'WardogsPresence');
const engineExe = (debug = false) => path.join(engineDir(), debug ? 'WardogsPresence-debug.exe' : 'WardogsPresence.exe');

function trusted(event) {
  if (!window || event.sender !== window.webContents) throw new Error('Untrusted request');
  const url = event.sender.getURL();
  if (!url.startsWith('file://') || !decodeURIComponent(new URL(url).pathname).toLowerCase().endsWith('/frontend/dist/index.html')) {
    throw new Error('Untrusted page');
  }
}

function bridge(action, request = {}) {
  return new Promise((resolve, reject) => {
    const exe = engineExe(true);
    if (!fs.existsSync(exe)) return reject(new Error('Bundled engine missing. Rebuild the desktop app.'));
    const child = spawn(exe, ['--bridge', action], { cwd: engineDir(), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Engine request timed out.')); }, action === 'capture' || action === 'ocr' ? 45000 : 15000);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { stdout += chunk; if (stdout.length > 20_000_000) child.kill(); });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', () => {
      clearTimeout(timer);
      const line = stdout.trim().split(/\r?\n/).at(-1);
      if (!line) return reject(new Error(stderr.trim() || 'Engine returned no response.'));
      try { resolve(JSON.parse(line)); } catch { reject(new Error('Engine returned an invalid response.')); }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

function encode(result) { return JSON.stringify(result); }
function register(method, handler) {
  ipcMain.handle(`wardogs:${method}`, async (event, ...args) => {
    trusted(event);
    try { return encode(await handler(...args)); }
    catch (error) { return encode({ ok: false, error: error.message }); }
  });
}

register('GetConfig', async () => {
  const result = await bridge('config');
  if (!result.ok) throw new Error(result.error || 'Cannot load settings.');
  return result.config;
});
register('ListMonitors', async () => {
  const result = await bridge('monitors');
  if (!result.ok) throw new Error(result.error || 'Cannot list displays.');
  return result.monitors;
});
register('EngineStatus', async () => ({ running: !!watcher && watcher.exitCode === null, detail: watcherDetail }));
register('SaveConfig', async raw => bridge('save-config', { config: JSON.parse(raw) }));
register('TestWebhook', async url => bridge('webhook', { url }));
register('Capture', async (monitor_index, delay) => bridge('capture', { monitor_index, delay }));
register('CheckOCR', async raw => bridge('ocr', { config: JSON.parse(raw) }));
register('StartEngine', async () => {
  if (watcher && watcher.exitCode === null) return { ok: true };
  const config = await bridge('config');
  if (!config.ok || !config.config?.setup_complete) return { ok: false, error: 'Save your settings before starting the watcher.' };
  const exe = engineExe();
  if (!fs.existsSync(exe)) return { ok: false, error: 'Bundled watcher missing.' };
  watcher = spawn(exe, ['--watch'], { cwd: engineDir(), windowsHide: true, stdio: 'ignore' });
  watcherDetail = 'Waiting for War Dogs';
  watcher.on('error', error => { watcherDetail = error.message; watcher = undefined; });
  watcher.on('exit', code => { watcherDetail = `Stopped${code ? ` (exit ${code})` : ''}`; watcher = undefined; });
  await new Promise(resolve => setTimeout(resolve, 450));
  return watcher ? { ok: true } : { ok: false, error: watcherDetail };
});
register('StopEngine', async () => {
  if (watcher) { watcher.kill(); watcher = undefined; }
  watcherDetail = 'Stopped';
  return { ok: true };
});

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window = new BrowserWindow({
    width: 1320, height: 870, minWidth: 850, minHeight: 640,
    backgroundColor: '#11171c', title: 'WARDOGS Presence',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (url !== `file://${uiPath.replace(/\\/g, '/')}`) event.preventDefault(); });
  window.loadFile(uiPath);
});

app.on('before-quit', () => { if (watcher) watcher.kill(); });
app.on('window-all-closed', () => app.quit());
