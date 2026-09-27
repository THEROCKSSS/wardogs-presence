// Run against a locally launched EXE with --remote-debugging-port=9222.
// Reads only public UI text and aggregate bridge state; never prints webhook URLs.
async function main() {
  const targets = await (await fetch('http://127.0.0.1:9222/json')).json();
  const page = targets.find(target => target.type === 'page' && target.title === 'WARDOGS Presence');
  if (!page) throw new Error('Electron page not found');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  const result = await new Promise((resolve, reject) => {
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.id !== 1) return;
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result.result.value);
    };
    socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: {
      expression: `Promise.all([window.wardogs.GetConfig(), window.wardogs.ListMonitors(), window.wardogs.EngineStatus()]).then(async ([config, monitors, status]) => { const [capture, ocr] = await Promise.all([window.wardogs.Capture(1, 0), window.wardogs.CheckOCR(config)]); const navigation = document.querySelectorAll('nav .nav-item').length; document.querySelectorAll('nav .nav-item')[3]?.click(); await new Promise(resolve => setTimeout(resolve, 200)); const video = document.querySelector('.guide-media video'); if (video && video.readyState < 1) await new Promise(resolve => { video.addEventListener('loadedmetadata', resolve, {once:true}); setTimeout(resolve, 5000); }); return {brand: document.querySelector('.brand')?.textContent, navigation, heading: document.querySelector('h1')?.textContent, channels: JSON.parse(config).webhook_profiles.length, displays: JSON.parse(monitors).length, watcher: JSON.parse(status).running, captured: JSON.parse(capture).image?.startsWith('data:image/jpeg;base64,') || false, ocr: JSON.parse(ocr).ok, url: location.protocol, guideVideoDuration: video?.duration || 0, guideVideoLocal: video?.currentSrc.startsWith('file:'), guidePreviews: document.querySelectorAll('.guide-preview-grid img').length, guideCopy: document.querySelector('.guide-media-heading p')?.textContent}; })`,
      awaitPromise: true, returnByValue: true,
    }}));
  });
  socket.close();
  if (!result?.brand?.includes('WARDOGS') || result.navigation !== 4 || result.displays < 1 || !result.captured || !result.ocr || result.url !== 'file:' || result.guideVideoDuration < 70 || !result.guideVideoLocal || result.guidePreviews !== 3 || !result.guideCopy?.includes('short walkthrough')) {
    throw new Error(`Portable smoke failed: ${JSON.stringify(result)}`);
  }
  console.log(JSON.stringify(result));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
