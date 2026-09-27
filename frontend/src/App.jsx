import React, { useEffect, useRef, useState } from 'react';
import {
  Activity, ArrowRight, BookOpen, Check, ChevronRight, CircleHelp, Copy,
  Crosshair, ExternalLink, Eye, EyeOff, Gamepad2, Monitor, Plus, Radio,
  RefreshCw, Save, ShieldCheck, SlidersHorizontal, Trash2, Wifi, WifiOff,
} from 'lucide-react';
import demoVideo from '../../docs/media/setup-guide.mp4';
import demoPoster from '../../docs/media/slide-00.png';
import matchPreview from '../../docs/media/status-match.png';
import queuePreview from '../../docs/media/status-queue.png';
import offlinePreview from '../../docs/media/status-offline.png';

const emptyConfig = {
  display_name: '', avatar_url: '', webhook_profiles: [], monitor_index: 1,
  capture_region: '0,0.65,1,1', team_icon_region: '0.960,0.925,0.990,0.965',
  score_region: '0.0169,0.9139,0.1497,0.9514', enrich_from_api: false,
  setup_complete: false,
};
const regions = [
  { key: 'capture_region', label: 'Server panel', hint: 'CURRENT SERVER and SERVER ID', color: '#e7ad5f' },
  { key: 'team_icon_region', label: 'Faction icon', hint: 'Team marker in the HUD', color: '#7bc4c5' },
  { key: 'score_region', label: 'Scoreboard', hint: 'Three score numbers', color: '#c89abe' },
];
const navigation = [
  { id: 'overview', label: 'Overview', icon: Activity },
  { id: 'channels', label: 'Discord channels', icon: Radio },
  { id: 'capture', label: 'Screen capture', icon: Crosshair },
  { id: 'guide', label: 'How it works', icon: BookOpen },
];

function call(method, ...args) {
  const bridge = window.wardogs;
  if (!bridge?.[method]) return Promise.reject(new Error('Desktop bridge unavailable. Open the Electron app to use device controls.'));
  return bridge[method](...args);
}
function parseRegion(value) {
  const n = String(value || '').split(',').map(Number);
  return n.length === 4 && n.every(Number.isFinite) ? n : [0, 0, 0, 0];
}
function maskUrl(value) {
  if (!value) return 'No URL added';
  const parts = value.split('/');
  return `discord.com / ${parts.at(-2) || 'webhook'} / ••••••••`;
}
function newProfile() {
  return { id: crypto.randomUUID(), label: '', url: '', enabled: true };
}

export default function App() {
  const [tab, setTab] = useState(() => {
    const route = window.location.hash.slice(1);
    return navigation.some(item => item.id === route) ? route : 'overview';
  });
  const [config, setConfig] = useState(emptyConfig);
  const [monitors, setMonitors] = useState([]);
  const [shot, setShot] = useState('');
  const [activeRegion, setActiveRegion] = useState('capture_region');
  const [drag, setDrag] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState('');
  const [reveal, setReveal] = useState({});
  const [ocr, setOcr] = useState(null);
  const [motionPaused, setMotionPaused] = useState(false);
  const [engine, setEngine] = useState({ running: false, detail: 'Checking status…' });
  const imageRef = useRef(null);
  const noticeTimer = useRef(null);
  const desktop = !!window.wardogs;

  useEffect(() => { window.history.replaceState(null, '', `#${tab}`); }, [tab]);
  useEffect(() => {
    const followHash = () => {
      const route = window.location.hash.slice(1);
      if (navigation.some(item => item.id === route)) setTab(route);
    };
    window.addEventListener('hashchange', followHash);
    return () => window.removeEventListener('hashchange', followHash);
  }, []);

  function showNotice(message, kind = 'ok') {
    setNotice({ message, kind });
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), 6000);
  }
  function update(patch) { setConfig(current => ({ ...current, ...patch })); }
  function updateProfile(id, patch) {
    setConfig(current => ({
      ...current,
      webhook_profiles: current.webhook_profiles.map(item => item.id === id ? { ...item, ...patch } : item),
    }));
  }
  function removeProfile(id) {
    if (!window.confirm('Remove this saved channel?')) return;
    setConfig(current => ({ ...current,
      webhook_profiles: current.webhook_profiles.filter(item => item.id !== id),
    }));
  }
  function invoke(method, args = [], label = method) {
    setBusy(label);
    return call(method, ...args).finally(() => setBusy(''));
  }

  useEffect(() => {
    if (!desktop) {
      setEngine({ running: false, detail: 'Browser design preview' });
      return;
    }
    Promise.all([call('GetConfig'), call('ListMonitors'), call('EngineStatus')])
      .then(([saved, displays, status]) => {
        setConfig({ ...emptyConfig, ...JSON.parse(saved) });
        setMonitors(JSON.parse(displays));
        setEngine(JSON.parse(status));
      })
      .catch(error => showNotice(error.message, 'error'));
  }, []);

  async function save() {
    try {
      const response = JSON.parse(await invoke('SaveConfig', [JSON.stringify(config)], 'save'));
      if (!response.ok) throw new Error(response.error);
      update({ setup_complete: true });
      showNotice('Settings saved. The watcher will use them on its next start.');
    } catch (error) { showNotice(error.message, 'error'); }
  }
  async function toggleEngine() {
    try {
      const method = engine.running ? 'StopEngine' : 'StartEngine';
      const response = JSON.parse(await invoke(method, [], 'engine'));
      if (!response.ok) throw new Error(response.error);
      setEngine(JSON.parse(await call('EngineStatus')));
      showNotice(engine.running ? 'Watcher stopped.' : 'Watcher started. Open Wardogs to publish your status.');
    } catch (error) { showNotice(error.message, 'error'); }
  }
  async function testProfile(profile) {
    try {
      const response = JSON.parse(await invoke('TestWebhook', [profile.url], profile.id));
      if (!response.ok) throw new Error(response.message);
      showNotice(`${profile.label || 'Webhook'}: ${response.message}`);
    } catch (error) { showNotice(error.message, 'error'); }
  }
  async function capture(delay = 0) {
    try {
      showNotice(delay ? 'Switch to Wardogs. Capturing in 5 seconds…' : 'Capturing your selected display…');
      const response = JSON.parse(await invoke('Capture', [config.monitor_index, delay], 'capture'));
      if (!response.ok) throw new Error(response.error);
      setShot(response.image);
      showNotice('Capture ready. Pick a region and drag its box.');
    } catch (error) { showNotice(error.message, 'error'); }
  }
  async function checkOcr() {
    try {
      const response = JSON.parse(await invoke('CheckOCR', [JSON.stringify(config)], 'ocr'));
      if (!response.ok) throw new Error(response.error);
      setOcr(response);
      showNotice('OCR check complete. Review the exact text below.');
    } catch (error) { showNotice(error.message, 'error'); }
  }
  function pointerPosition(event) {
    const bounds = imageRef.current?.getBoundingClientRect();
    if (!bounds) return null;
    return [Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
      Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height))];
  }
  function onPointerDown(event) {
    if (!shot) return;
    imageRef.current.setPointerCapture(event.pointerId);
    const point = pointerPosition(event);
    setDrag({ start: point, end: point });
  }
  function onPointerMove(event) {
    if (drag) setDrag(current => ({ ...current, end: pointerPosition(event) }));
  }
  function onPointerUp(event) {
    if (!drag) return;
    const end = pointerPosition(event);
    const [x0, y0] = drag.start;
    const [x1, y1] = end;
    setDrag(null);
    if (Math.abs(x1 - x0) < .01 || Math.abs(y1 - y0) < .01) return;
    const box = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    update({ [activeRegion]: box.map(n => n.toFixed(4)).join(',') });
  }

  const profiles = config.webhook_profiles || [];
  const enabled = profiles.filter(profile => profile.enabled && profile.url.trim());
  const ready = !!config.display_name.trim() && enabled.length > 0;
  const step = !config.display_name ? 'Set your player name' : !enabled.length ? 'Connect a Discord channel' : 'Calibrate the capture';

  return <div className={`shell ${motionPaused ? 'motion-paused' : ''}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Crosshair size={23} strokeWidth={1.6} /></span><span><strong>WARDOGS</strong><small>PRESENCE / CONTROL</small></span></div>
      <div className="sidebar-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{navigation.map(item => <a href={`#${item.id}`} key={item.id} className={`nav-item ${tab === item.id ? 'active' : ''}`} aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}><item.icon size={17} strokeWidth={1.8} /><span>{item.label}</span>{tab === item.id && <ChevronRight size={15} />}</a>)}</nav>
      <div className="sidebar-foot"><div className="watcher-status"><span className={`pulse-dot ${engine.running ? 'online' : ''}`} /><span><b>{engine.running ? 'Watcher active' : 'Watcher idle'}</b><small>{engine.detail}</small></span></div><div className="side-version">WINDOWS DESKTOP <span>v1.1</span></div></div>
    </aside>
    <main className="main" id="main-content">
      <header className="topbar"><div className="breadcrumb">WARDOGS PRESENCE <ChevronRight size={13} /> <b>{navigation.find(item => item.id === tab)?.label}</b></div><div className="top-actions"><span className="local-badge"><ShieldCheck size={14} /> LOCAL CONFIGURATION</span><button className="motion-button" onClick={() => setMotionPaused(value => !value)} aria-pressed={motionPaused}>{motionPaused ? 'Resume motion' : 'Pause motion'}</button><button className="save-button" onClick={save} disabled={!!busy || !desktop}><Save size={15} /> Save changes</button></div></header>
      <div className="content" key={tab}>
        {tab === 'overview' && <>
          <div className="hero"><div className="hero-copy"><span className="eyebrow"><span className="eyebrow-line" /> LIVE GAME → DISCORD</span><h1>Your squad knows<br /><em>where you are.</em></h1><p>Read your War Dogs screen and keep one status message current in each Discord channel you choose.</p><div className="hero-actions"><button className="primary-button" onClick={toggleEngine} disabled={!ready || !!busy || !desktop}>{engine.running ? 'Stop watcher' : 'Start watcher'} <ArrowRight size={17} /></button><button className="text-button" onClick={() => setTab('guide')}>Read the setup guide <ArrowRight size={16} /></button></div></div><div className="radar" aria-hidden="true"><div className="radar-ring outer" /><div className="radar-ring middle" /><div className="radar-ring inner" /><div className="radar-cross horizontal" /><div className="radar-cross vertical" /><div className="radar-sweep" /><span className="radar-center" /><span className="radar-label">STATUS SIGNAL / {engine.running ? 'ACTIVE' : 'STANDBY'}</span></div></div>
          <div className="overview-grid"><section className="panel identity-panel"><div className="panel-header"><span className="panel-kicker">01 / IDENTITY</span><Gamepad2 size={18} /></div><h2>Player identity</h2><p>This name appears beside your Discord status.</p><label className="field-label" htmlFor="display-name">Display name</label><input id="display-name" placeholder="Your player name" value={config.display_name} maxLength={80} onChange={event => update({ display_name: event.target.value })} /><label className="field-label" htmlFor="avatar-url">Avatar image URL <span>optional</span></label><input id="avatar-url" placeholder="https://…" value={config.avatar_url} onChange={event => update({ avatar_url: event.target.value })} /></section><section className="panel setup-panel"><div className="panel-header"><span className="panel-kicker">02 / CONNECTIONS</span><Wifi size={18} /></div><h2>Channel routing</h2><p>Each enabled destination gets its own message, edited in place.</p><div className="metric"><strong>{enabled.length.toString().padStart(2, '0')}</strong><span>ACTIVE {enabled.length === 1 ? 'CHANNEL' : 'CHANNELS'}</span></div><button className="outline-button" onClick={() => setTab('channels')}>Manage Discord channels <ArrowRight size={16} /></button></section><section className="panel next-panel"><div className="panel-header"><span className="panel-kicker">03 / NEXT ACTION</span><SlidersHorizontal size={18} /></div><h2>{ready ? 'Check your screen' : step}</h2><p>{ready ? 'Capture a frame from Wardogs, place three reading boxes, and confirm the text before going live.' : 'Complete your identity and at least one active webhook before starting the watcher.'}</p><button className="outline-button" onClick={() => setTab(ready ? 'capture' : config.display_name ? 'channels' : 'overview')}>{ready ? 'Open calibration' : 'Continue setup'} <ArrowRight size={16} /></button></section></div>
          <div className="status-strip"><span><span className="small-light" /> OCR runs only while War Dogs is open</span><span>One message per destination · edited when status changes</span><span>Settings stay on this PC</span></div>
        </>}
        {tab === 'channels' && <><PageIntro kicker="DISCORD ROUTING" title="Choose who gets the signal." description="Save several webhooks, then enable just the channels you want active. Multiple enabled channels receive the same status with independent message IDs." /><div className="section-toolbar"><div><b>{profiles.length} saved</b><span> · {enabled.length} enabled</span></div><button className="primary-button compact" onClick={() => update({ webhook_profiles: [...profiles, newProfile()] })}><Plus size={16} /> Add channel</button></div>{profiles.length === 0 ? <div className="empty-state"><Radio size={28} /><h2>No channels yet</h2><p>Create a Discord webhook in Channel settings → Integrations → Webhooks, then add it here. Your URL stays on your PC.</p><button className="outline-button" onClick={() => update({ webhook_profiles: [newProfile()] })}>Add your first channel <ArrowRight size={16} /></button></div> : <div className="profile-list">{profiles.map((profile, index) => <section className="profile-card" key={profile.id}><div className="profile-heading"><span className="profile-index">{String(index + 1).padStart(2, '0')}</span><div><h2>{profile.label || 'New channel'}</h2><span>{profile.enabled ? 'Enabled for publishing' : 'Saved, currently paused'}</span></div><label className="switch"><input type="checkbox" checked={!!profile.enabled} onChange={event => updateProfile(profile.id, { enabled: event.target.checked })} aria-label={`Enable ${profile.label || 'channel'}`} /><span /></label></div><div className="profile-fields"><div><label className="field-label" htmlFor={`label-${profile.id}`}>Channel label</label><input id={`label-${profile.id}`} placeholder="Squad channel" value={profile.label} onChange={event => updateProfile(profile.id, { label: event.target.value })} /></div><div><label className="field-label" htmlFor={`url-${profile.id}`}>Discord webhook URL</label><div className="secret-input"><input id={`url-${profile.id}`} type={reveal[profile.id] ? 'text' : 'password'} placeholder="https://discord.com/api/webhooks/…" value={profile.url} onChange={event => updateProfile(profile.id, { url: event.target.value })} /><button onClick={() => setReveal(value => ({ ...value, [profile.id]: !value[profile.id] }))} aria-label={reveal[profile.id] ? 'Hide URL' : 'Show URL'}>{reveal[profile.id] ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></div></div><div className="profile-footer"><span>{maskUrl(profile.url)}</span><div><button className="small-button" onClick={() => testProfile(profile)} disabled={!profile.url || !!busy || !desktop}>Test connection</button><button className="icon-button danger" title="Remove saved channel" aria-label={`Remove ${profile.label || 'channel'}`} onClick={() => removeProfile(profile.id)}><Trash2 size={16} /></button></div></div></section>)}</div>}<div className="tip"><CircleHelp size={18} /><p><b>How updates stay bounded</b><br />Status changes are debounced. Score changes are throttled, unchanged status uses a five minute heartbeat, and Discord’s retry delay is honored after a rate limit.</p></div></>}
        {tab === 'capture' && <><PageIntro kicker="SCREEN CALIBRATION" title="Show the app where to look." description="Take a frame from the monitor running War Dogs, then drag a rectangle for the server panel, faction icon, and scoreboard." /><div className="capture-controls"><div><label className="field-label" htmlFor="monitor">Game monitor</label><select id="monitor" value={config.monitor_index} onChange={event => update({ monitor_index: Number(event.target.value) })}>{monitors.length ? monitors.map(m => <option key={m.index} value={m.index}>Display {m.index} · {m.width} × {m.height}{m.primary ? ' · primary' : ''}</option>) : <option value={config.monitor_index}>Display {config.monitor_index} · connect desktop to detect</option>}</select></div><button className="outline-button" onClick={() => capture(0)} disabled={!!busy || !desktop}><Monitor size={16} /> Capture now</button><button className="primary-button compact" onClick={() => capture(5)} disabled={!!busy || !desktop}><Crosshair size={16} /> Capture in 5 sec</button></div><div className="capture-layout"><div className="preview-panel"><div className="preview-top"><span>MONITOR PREVIEW</span><span>{shot ? 'DRAG TO POSITION A BOX' : 'WAITING FOR CAPTURE'}</span></div><div className={`preview-area ${shot ? 'has-shot' : ''}`} ref={imageRef} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}>{shot ? <><img src={shot} alt="Captured game monitor" draggable="false" />{regions.map(region => { const [left, top, right, bottom] = parseRegion(config[region.key]); return <div className={`region-box ${activeRegion === region.key ? 'selected' : ''}`} key={region.key} style={{ left: `${left * 100}%`, top: `${top * 100}%`, width: `${(right - left) * 100}%`, height: `${(bottom - top) * 100}%`, borderColor: region.color, color: region.color }}><span>{region.label}</span></div>; })}{drag && <div className="drag-box" style={{ left: `${Math.min(drag.start[0], drag.end[0]) * 100}%`, top: `${Math.min(drag.start[1], drag.end[1]) * 100}%`, width: `${Math.abs(drag.end[0] - drag.start[0]) * 100}%`, height: `${Math.abs(drag.end[1] - drag.start[1]) * 100}%` }} />}</> : <div className="preview-empty"><Crosshair size={44} strokeWidth={1} /><b>No frame captured</b><span>Open the pause menu in War Dogs, then capture a frame.</span></div>}</div></div><div className="region-panel"><span className="panel-kicker">READING ZONES</span><h2>Three precise areas</h2><p>Select a zone, then drag on the captured frame to place it.</p><div className="region-options">{regions.map((region, i) => <button key={region.key} className={`region-option ${activeRegion === region.key ? 'active' : ''}`} onClick={() => setActiveRegion(region.key)}><span className="region-swatch" style={{ background: region.color }} /><span><b>{String(i + 1).padStart(2, '0')} / {region.label}</b><small>{region.hint}</small></span><ChevronRight size={15} /></button>)}</div><div className="region-footer"><button className="text-button" onClick={() => update({ [activeRegion]: emptyConfig[activeRegion] })}><RefreshCw size={15} /> Reset selected area</button></div></div></div><div className="ocr-check"><div><span className="panel-kicker">READBACK</span><h2>Verify what the app sees</h2><p>Set Wardogs → Settings → Interface → Faction to <b>Always On</b> so the faction icon remains visible.</p></div><button className="outline-button" onClick={checkOcr} disabled={!!busy || !desktop}><Eye size={16} /> Run OCR check</button></div>{ocr && <div className="ocr-result"><div><span>SERVER</span><b>{ocr.status || 'No server detected. Adjust the server panel box.'}</b></div><div><span>FACTION</span><b>{ocr.team || 'Not detected'}</b></div><div><span>SCORES</span><b>{ocr.scores?.length ? ocr.scores.join(' / ') : 'Not detected'}</b></div><div className="raw-ocr"><span>RAW OCR TEXT</span><pre>{ocr.text || '(nothing read)'}</pre></div></div>}</>}
        {tab === 'guide' && <><PageIntro kicker="FIELD MANUAL" title="From download to Discord." description="Everything needed to start using WARDOGS Presence is here. No command line, Python, or Tesseract install is needed." /><div className="guide-grid"><div className="guide-steps"><GuideStep number="01" title="Create a webhook" icon={Radio}>In your Discord channel, open <b>Edit Channel → Integrations → Webhooks → New Webhook</b>, then copy its URL. Share the URL only with people you trust.</GuideStep><GuideStep number="02" title="Set your identity" icon={Gamepad2}>On Overview, enter the name friends should see. In Discord channels, add the webhook and turn it on. You can save several destinations and pause any of them.</GuideStep><GuideStep number="03" title="Line up the capture" icon={Crosshair}>Open War Dogs on the chosen monitor. Press Esc in a match, use <b>Capture in 5 sec</b>, then draw each of the three reading areas. Run the OCR check before going live.</GuideStep><GuideStep number="04" title="Start the watcher" icon={Activity}>Save settings and select <b>Start watcher</b>. The app checks the game process before taking screen captures and edits one message per enabled destination.</GuideStep></div><aside className="guide-aside"><div className="guide-aside-heading"><ShieldCheck size={23} /><h2>What leaves your PC?</h2></div><p>Only the status text assembled from the selected screen areas is sent to your enabled Discord webhooks. Screenshots and webhook URLs stay local.</p><div className="guide-rule" /><h3>If nothing appears</h3><ul><li>Test the webhook connection.</li><li>Check that the watcher is running.</li><li>Open War Dogs and verify the game monitor.</li><li>Run the OCR check and adjust the server panel box.</li></ul><div className="guide-rule" /><h3>Before sharing the EXE</h3><p>Downloads belong in the project’s GitHub Releases. Compare the SHA-256 hash listed there with the file you downloaded.</p></aside></div><GuideMedia /></>}
      </div>
    </main>
    {notice && <div role="status" className={`toast ${notice.kind}`}>{notice.kind === 'ok' ? <Check size={17} /> : <WifiOff size={17} />}{notice.message}<button onClick={() => setNotice(null)} aria-label="Dismiss notification">×</button></div>}
  </div>;
}

function PageIntro({ kicker, title, description }) { return <div className="page-intro"><span className="eyebrow"><span className="eyebrow-line" /> {kicker}</span><h1>{title}</h1><p>{description}</p></div>; }
function GuideStep({ number, title, icon: Icon, children }) { return <section className="guide-step"><div className="guide-step-number">{number}</div><div className="guide-step-body"><Icon size={21} strokeWidth={1.6} /><h2>{title}</h2><p>{children}</p></div></section>; }
function GuideMedia() { return <section className="guide-media" aria-label="Setup demo and Discord message previews"><div className="guide-media-heading"><span className="panel-kicker">05 / SEE IT WORK</span><h2>Watch the setup. Preview the signal.</h2><p>This short walkthrough and its example status messages are bundled with the EXE, so they work offline. Names and server details are fictional.</p></div><video controls playsInline preload="metadata" poster={demoPoster} aria-label="Narrated setup walkthrough"><source src={demoVideo} type="video/mp4" />Your device cannot play this video.</video><div className="guide-preview-grid">{[[matchPreview, 'In a match', 'Example match server and scores'], [queuePreview, 'In queue', 'Example queue position'], [offlinePreview, 'Out of game', 'Example out-of-game status']].map(([src, label, alt]) => <figure key={label}><img src={src} alt={alt} /><figcaption>{label}</figcaption></figure>)}</div></section>; }
