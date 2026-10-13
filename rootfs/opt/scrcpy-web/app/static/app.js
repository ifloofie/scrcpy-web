// scrcpy-web front end: noVNC viewer + phone controls.
import RFB from '/novnc/core/rfb.js';

const $ = (sel) => document.querySelector(sel);
const screenEl = $('#screen');
const overlay = $('#overlay');

const state = {
  config: null,
  rfb: null,
  connected: false,
  retry: 0,
  retryTimer: null,
  status: null,
};

// ---------------------------------------------------------------- helpers
function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem('scrcpy-web:' + key);
    localStorage.setItem('scrcpy-web:' + key, value);
  } catch { /* storage unavailable (private mode etc.) */ }
  return null;
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 5000 : 2200);
}

async function api(path, body) {
  const opts = body === undefined
    ? { headers: { 'X-Requested-With': 'scrcpy-web' } }
    : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'scrcpy-web' },
        body: JSON.stringify(body),
      };
  const res = await fetch(path, opts);
  if (res.status === 401) {
    location.href = '/login?next=' + encodeURIComponent(location.pathname);
    throw new Error('signed out');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
  return data;
}

async function run(label, fn) {
  try {
    await fn();
  } catch (e) {
    toast(`${label} failed: ${e.message}`, true);
  }
}

function showOverlay(title, text = '', spinner = true) {
  $('#overlay-title').textContent = title;
  $('#overlay-text').textContent = text;
  $('#overlay-spinner').hidden = !spinner;
  overlay.hidden = false;
}

function hideOverlay() { overlay.hidden = true; }

// ---------------------------------------------------------------- viewer
function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/websockify`;
}

function connect() {
  clearTimeout(state.retryTimer);
  if (state.rfb) {
    try { state.rfb.disconnect(); } catch { /* already closed */ }
  }
  screenEl.textContent = '';
  const rfb = new RFB(screenEl, wsUrl(), {
    wsProtocols: ['binary'],
    credentials: state.config?.vnc_password ? { password: state.config.vnc_password } : undefined,
  });
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.background = 'transparent';
  rfb.qualityLevel = Number(store('quality') ?? 9);
  rfb.compressionLevel = 2;
  rfb.focusOnClick = true;

  rfb.addEventListener('connect', () => {
    state.connected = true;
    state.retry = 0;
    updateOverlay();
  });
  rfb.addEventListener('disconnect', () => {
    state.connected = false;
    if (state.rfb !== rfb) return; // replaced on purpose
    const delay = Math.min(10000, 1000 * 2 ** state.retry++);
    showOverlay('Reconnecting…', 'Lost the connection to the server.');
    state.retryTimer = setTimeout(connect, delay);
  });
  rfb.addEventListener('credentialsrequired', () => {
    if (state.config?.vnc_password) rfb.sendCredentials({ password: state.config.vnc_password });
  });
  rfb.addEventListener('securityfailure', (e) => {
    showOverlay('Could not connect', e.detail?.reason || 'VNC authentication failed.', false);
  });
  rfb.addEventListener('clipboard', (e) => { state.phoneClipboard = e.detail.text; });
  state.rfb = rfb;
}

// Paste any text (including emoji) through the clipboard: the VNC clipboard
// becomes the X clipboard, and scrcpy forwards it to the phone on Ctrl+V.
function pasteText(text) {
  if (!state.rfb || !state.connected) throw new Error('viewer is not connected');
  state.rfb.clipboardPasteFrom(text);
  const XK_Control_L = 0xffe3;
  const XK_v = 0x0076;
  setTimeout(() => {
    state.rfb.sendKey(XK_Control_L, 'ControlLeft', true);
    state.rfb.sendKey(XK_v, 'KeyV');
    state.rfb.sendKey(XK_Control_L, 'ControlLeft', false);
  }, 150);
}

// ---------------------------------------------------------------- status
const STATE_TEXT = {
  disconnected: ['Waiting for the phone', 'Plug the phone into the server and enable USB debugging (Settings › Developer options).'],
  unauthorized: ['Allow USB debugging', 'Unlock the phone and accept the "Allow USB debugging?" prompt. Tick "Always allow from this computer".'],
  offline: ['Phone is offline', 'Try unplugging and replugging the USB cable.'],
  multiple: ['Several phones connected', 'Set ADB_SERIAL in the container settings to choose one.'],
  error: ['ADB problem', ''],
};

function updateOverlay() {
  const dev = state.status?.device;
  if (dev && dev.state !== 'connected') {
    const [title, text] = STATE_TEXT[dev.state] || ['Phone not available', ''];
    showOverlay(title, dev.state === 'multiple'
      ? `${text} Devices: ${dev.devices.map((d) => d.serial).join(', ')}`
      : (text || dev.error || ''), dev.state === 'disconnected');
    return;
  }
  if (dev && state.status && !state.status.mirroring) {
    showOverlay('Starting mirroring…', 'scrcpy is starting on the phone.');
    return;
  }
  if (!state.connected) {
    showOverlay('Connecting…');
    return;
  }
  hideOverlay();
}

function renderStatus() {
  const s = state.status;
  const dev = s?.device || {};
  const dot = $('#status-dot');
  dot.dataset.state = dev.state === 'connected' ? (s.mirroring ? 'ok' : 'warn') : (dev.state === 'disconnected' ? 'off' : 'warn');

  if (dev.state === 'connected') {
    const name = [dev.manufacturer, dev.model].filter(Boolean).join(' ') || 'Phone';
    $('#device-name').textContent = name;
    const parts = [];
    if (dev.android) parts.push(`Android ${dev.android}`);
    if (dev.battery?.level != null) parts.push(`${dev.battery.level}%${dev.battery.charging ? ' ⚡' : ''}`);
    if (dev.screen_on != null) parts.push(dev.screen_on ? 'screen on' : 'screen off');
    if (dev.locked) parts.push('locked');
    $('#device-detail').textContent = parts.join(' · ');
  } else {
    $('#device-name').textContent = 'scrcpy-web';
    $('#device-detail').textContent = (STATE_TEXT[dev.state] || ['Not connected'])[0];
  }
  updateOverlay();
}

async function pollStatus() {
  if (document.visibilityState === 'visible') {
    try {
      const data = await api('/api/status');
      state.status = data;
      renderStatus();
    } catch { /* transient; next poll will retry */ }
  }
  setTimeout(pollStatus, 4000);
}

// ---------------------------------------------------------------- actions
const actions = {
  wake: () => run('Wake', async () => { await api('/api/wake', {}); toast('Screen on'); }),
  sleep: () => run('Screen off', async () => { await api('/api/sleep', {}); toast('Screen off'); }),
  notifications: () => run('Notifications', () => api('/api/key', { key: 'notifications' })),
  unlock: () => {
    if (state.config?.unlock_pin_configured) {
      run('Unlock', async () => {
        const r = await api('/api/unlock', {});
        toast(r.locked ? 'Still locked - check the PIN' : 'Unlocked');
      });
    } else {
      $('#unlock-pin').value = '';
      $('#unlock-dialog').showModal();
    }
  },
  type: () => {
    $('#type-dialog').showModal();
    setTimeout(() => $('#type-text').focus(), 50);
  },
  menu: () => {
    $('#quality').value = String(store('quality') ?? 9);
    $('#menu-dialog').showModal();
  },
  fullscreen: () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen?.().catch(() => {});
  },
  reconnect: () => { $('#menu-dialog').close(); connect(); },
  restart: () => run('Restart', async () => {
    $('#menu-dialog').close();
    await api('/api/restart', {});
    toast('Mirroring restarted');
  }),
  logout: async () => {
    await fetch('/logout', { method: 'POST', headers: { 'X-Requested-With': 'scrcpy-web' } });
    location.href = '/login';
  },
};

document.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-key], button[data-action], button[data-send]');
  if (!btn) return;
  if (btn.dataset.key) {
    const key = btn.dataset.key;
    run('Key', () => api('/api/key', { key }));
  } else if (btn.dataset.action) {
    actions[btn.dataset.action]?.();
  } else if (btn.dataset.send) {
    const text = $('#type-text').value;
    if (!text) return;
    run('Send', async () => {
      if (btn.dataset.send === 'paste') pasteText(text);
      else await api('/api/text', { text });
      $('#type-text').value = '';
      toast('Sent');
    });
  }
});

$('#unlock-dialog').addEventListener('close', () => {
  if ($('#unlock-dialog').returnValue !== 'unlock') return;
  const pin = $('#unlock-pin').value;
  $('#unlock-pin').value = '';
  run('Unlock', async () => {
    const r = await api('/api/unlock', pin ? { pin } : {});
    toast(r.locked ? 'Still locked' : 'Unlocked');
  });
});

$('#quality').addEventListener('change', (e) => {
  store('quality', e.target.value);
  if (state.rfb) state.rfb.qualityLevel = Number(e.target.value);
});

// Ctrl/Cmd+Enter sends from the text box
$('#type-text').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    document.querySelector('button[data-send="paste"]').click();
  }
});

// Dialogs close when tapping the backdrop
for (const dlg of document.querySelectorAll('dialog')) {
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
}

if (!document.fullscreenEnabled) $('#fullscreen-btn').hidden = true;

// ---------------------------------------------------------------- start
(async () => {
  showOverlay('Connecting…');
  try {
    state.config = await api('/api/config');
  } catch (e) {
    showOverlay('Server unavailable', e.message, false);
    return;
  }
  if (state.config.auth === 'none') $('#logout-btn').hidden = true;
  $('#version').textContent = `scrcpy-web ${state.config.version}` +
    (state.config.scrcpy_version ? ` · scrcpy ${state.config.scrcpy_version}` : '');
  connect();
  pollStatus();
})();

document.addEventListener('visibilitychange', () => {
  // Reconnect straight away when coming back to the tab/app
  if (document.visibilityState === 'visible' && !state.connected) connect();
});
