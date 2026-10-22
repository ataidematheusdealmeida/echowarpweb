const streamUrl = "/hls/live.m3u8";

const player = document.getElementById("player");
const playButton = document.getElementById("playButton");
const reloadButton = document.getElementById("reloadButton");
const logoutButton = document.getElementById("logoutButton");
const subtitle = document.getElementById("subtitle");
const state = document.getElementById("state");
const origin = document.getElementById("origin");
const stream = document.getElementById("stream");
const audioLevel = document.getElementById("audioLevel");
const processState = document.getElementById("processState");
const configForm = document.getElementById("configForm");
const serverList = document.getElementById("serverList");
const savedServerList = document.getElementById("savedServerList");
const scanResultsSection = document.getElementById("scanResultsSection");
const scanState = document.getElementById("scanState");
const addServerButton = document.getElementById("addServerButton");
const openServerDialogButton = document.getElementById("openServerDialogButton");
const closeServerDialogButton = document.getElementById("closeServerDialogButton");
const cancelServerDialogButton = document.getElementById("cancelServerDialogButton");
const serverDialog = document.getElementById("serverDialog");
const serverDialogTitle = document.getElementById("serverDialogTitle");
const playerVolume = document.getElementById("playerVolume");
const settingsVolume = document.getElementById("settingsVolume");
const volumeValue = document.getElementById("volumeValue");
const settingsVolumeValue = document.getElementById("settingsVolumeValue");
const muteButton = document.getElementById("muteButton");
let connectionState = "starting";
const devicesState = document.getElementById("devicesState");
const devicesRaw = document.getElementById("devicesRaw");
const doctorState = document.getElementById("doctorState");
const doctorOutput = document.getElementById("doctorOutput");
const logsOutput = document.getElementById("logsOutput");

const fields = {
  serverPresetName: document.getElementById("serverPresetName"),
  address: document.getElementById("address"),
  port: document.getElementById("port"),
  password: document.getElementById("password"),
  nickname: document.getElementById("nickname"),
  discoverTimeout: document.getElementById("discoverTimeout"),
  outputDevice: document.getElementById("outputDevice"),
  deviceMode: document.getElementById("deviceMode"),
  deviceId: document.getElementById("deviceId"),
  deviceName: document.getElementById("deviceName"),
  sampleRate: document.getElementById("sampleRate"),
  channels: document.getElementById("channels"),
  audioBufferFrames: document.getElementById("audioBufferFrames"),
  captureDevices: document.getElementById("captureDevices"),
  playbackDevices: document.getElementById("playbackDevices"),
  virtualMic: document.getElementById("virtualMic"),
  loopback: document.getElementById("loopback"),
  aec: document.getElementById("aec"),
  role: document.getElementById("role"),
  serverMode: document.getElementById("serverMode"),
  serverName: document.getElementById("serverName"),
  logLevel: document.getElementById("logLevel"),
  maxClients: document.getElementById("maxClients"),
  maxReconnect: document.getElementById("maxReconnect"),
  restartDelay: document.getElementById("restartDelay"),
  autoReconnectAttempts: document.getElementById("autoReconnectAttempts"),
  rateLimit: document.getElementById("rateLimit"),
  maxAuthFailures: document.getElementById("maxAuthFailures"),
  record: document.getElementById("record"),
  tlsCert: document.getElementById("tlsCert"),
  tlsKey: document.getElementById("tlsKey"),
  banFile: document.getElementById("banFile"),
  recordDir: document.getElementById("recordDir"),
  enabled: document.getElementById("enabled"),
  discover: document.getElementById("discover"),
  tlsInsecure: document.getElementById("tlsInsecure"),
  serverTlsInsecure: document.getElementById("serverTlsInsecure"),
  autoRestart: document.getElementById("autoRestart"),
  autoReconnect: document.getElementById("autoReconnect"),
  serverDiscovery: document.getElementById("serverDiscovery"),
  hwidRequired: document.getElementById("hwidRequired"),
  serverMuted: document.getElementById("serverMuted"),
  stunServers: document.getElementById("stunServers"),
  extraArgs: document.getElementById("extraArgs"),
};

let hls;
let latestConfig = {};
let savedServers = [];
let scanResults = [];
let editingServerId = "";
let currentAudioLevel = 0;
let meterAnimationId;
let recordingBusy = false;
let replayingHistory = false;
const autoBuffer = document.getElementById("autoBuffer");
const bufferSeconds = document.getElementById("bufferSeconds");
const bufferState = document.getElementById("bufferState");
autoBuffer.checked = window.localStorage.getItem("echowarp_auto_buffer") !== "false";
const savedBuffer = Number(window.localStorage.getItem("echowarp_buffer_seconds") || 2);
bufferSeconds.value = Number.isFinite(savedBuffer) ? Math.max(1, Math.min(10, savedBuffer)) : 2;
let bufferTarget = autoBuffer.checked ? 2 : Number(bufferSeconds.value);
let lastBufferChange = 0;
let stablePlaybackSince = 0;
let hasPlayed = false;

function applyBuffer() {
  bufferSeconds.disabled = autoBuffer.checked;
  bufferState.textContent = `${autoBuffer.checked ? "Automatico" : "Manual"}: ${bufferTarget.toFixed(1)} s${!hls && player.canPlayType("application/vnd.apple.mpegurl") ? " (controle limitado pelo navegador)" : ""}`;
  if (hls) {
    hls.config.liveSyncDuration = bufferTarget;
    hls.config.liveMaxLatencyDuration = replayingHistory ? Infinity : bufferTarget + 3;
    hls.config.maxLiveSyncPlaybackRate = replayingHistory ? 1 : 1.1;
    hls.config.maxBufferLength = bufferTarget + 4;
  }
}

function changeBufferSetting() {
  if (!bufferSeconds.reportValidity()) return;
  window.localStorage.setItem("echowarp_auto_buffer", String(autoBuffer.checked));
  window.localStorage.setItem("echowarp_buffer_seconds", bufferSeconds.value);
  bufferTarget = autoBuffer.checked ? 2 : Number(bufferSeconds.value);
  lastBufferChange = performance.now();
  stablePlaybackSince = lastBufferChange;
  applyBuffer();
}
autoBuffer.addEventListener("change", changeBufferSetting);
bufferSeconds.addEventListener("change", changeBufferSetting);

function handleBufferStall() {
  stablePlaybackSince = 0;
  const now = performance.now();
  if (replayingHistory || !autoBuffer.checked || !hasPlayed || player.paused || now - lastBufferChange < 10000) return;
  bufferTarget = Math.min(10, bufferTarget + 1);
  lastBufferChange = now;
  applyBuffer();
}
player.addEventListener("waiting", handleBufferStall);
player.addEventListener("stalled", handleBufferStall);
setInterval(() => {
  const now = performance.now();
  if (replayingHistory || !autoBuffer.checked || player.paused || player.readyState < 3 || !stablePlaybackSince) return;
  if (now - stablePlaybackSince >= 120000 && now - lastBufferChange >= 120000 && bufferTarget > 2) {
    bufferTarget = Math.max(2, bufferTarget - 0.5);
    lastBufferChange = now;
    stablePlaybackSince = now;
    applyBuffer();
  }
}, 5000);

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.error === "auth_required") {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    return data;
  }
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function setPlayerSource() {
  replayingHistory = false;
  hasPlayed = false;
  stablePlaybackSince = 0;
  if (hls) {
    hls.destroy();
    hls = undefined;
  }

  if (window.Hls && window.Hls.isSupported()) {
    hls = new window.Hls({
      liveSyncDuration: bufferTarget,
      liveMaxLatencyDuration: bufferTarget + 3,
      liveSyncOnStallIncrease: 0,
      maxLiveSyncPlaybackRate: 1.1,
      maxBufferLength: bufferTarget + 4,
      backBufferLength: 0,
    });
    hls.loadSource(streamUrl);
    hls.attachMedia(player);
    applyBuffer();
    return;
  }

  if (player.canPlayType("application/vnd.apple.mpegurl")) {
    player.src = streamUrl;
    applyBuffer();
    return;
  }

  subtitle.textContent = "HLS nao suportado neste navegador";
}

function formatAudioLevel(level) {
  if (!level || !level.ok) return "sem sinal";
  const dbfs = Number(level.dbfs ?? -120);
  const peakValue = Number(level.peak || 0);
  const peak = Math.round(peakValue * 100);
  if (dbfs <= -95 && peakValue < 0.0005) return "sem sinal";
  if (dbfs < -70 && peak < 1) return `sinal baixo (${dbfs.toFixed(1)} dBFS)`;
  if (dbfs < -55 && peak < 2) return `sinal baixo (${dbfs.toFixed(1)} dBFS / pico ${peak}%)`;
  return `${dbfs.toFixed(1)} dBFS / pico ${peak}%`;
}

function updateMeterLevel(level) {
  currentAudioLevel = Math.max(0, Math.min(1, Number(level?.peak || level?.rms || 0)));
  if (currentAudioLevel > 0.01) {
    document.body.classList.add("has-audio-signal");
  } else {
    document.body.classList.remove("has-audio-signal");
  }
}

function setVolume(percent, persist = true) {
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  player.volume = value / 100;
  if (value > 0) player.muted = false;
  playerVolume.value = String(value);
  settingsVolume.value = String(value);
  volumeValue.textContent = `${Math.round(value)}%`;
  settingsVolumeValue.textContent = `${Math.round(value)}%`;
  updateMuteControl();
  if (persist) {
    window.localStorage.setItem("echowarp_player_volume", String(value));
  }
}

function updateMuteControl() {
  const muted = player.muted || player.volume === 0;
  muteButton.dataset.muted = String(muted);
  muteButton.setAttribute("aria-pressed", String(muted));
  muteButton.setAttribute("aria-label", muted ? "Ativar som" : "Silenciar");
  muteButton.title = muted ? "Ativar som (M)" : "Silenciar (M)";
}
muteButton.addEventListener("click", () => {
  if (player.volume === 0) setVolume(50);
  else player.muted = !player.muted;
  updateMuteControl();
});
player.addEventListener("volumechange", updateMuteControl);

function loadVolume() {
  const saved = window.localStorage.getItem("echowarp_player_volume");
  setVolume(saved === null ? 100 : saved, false);
}

function animateMeter() {
  const bars = document.querySelectorAll(".meter span");
  const base = currentAudioLevel;
  bars.forEach((bar, index) => {
    const wave = 0.72 + (Math.sin(Date.now() / 170 + index * 0.9) + 1) * 0.14;
    const floor = 0.08 + (index % 3) * 0.025;
    const scale = Math.max(floor, Math.min(1, base * wave * (1 + index * 0.035)));
    bar.style.transform = `scaleY(${scale})`;
  });
  meterAnimationId = window.requestAnimationFrame(animateMeter);
}

function fillConfig(config) {
  latestConfig = config;
  fields.address.value = config.address || "";
  fields.port.value = config.port || 4415;
  fields.password.value = "";
  fields.password.placeholder = config.password_set ? "senha salva" : "";
  fields.nickname.value = config.nickname || "";
  fields.discoverTimeout.value = config.discover_timeout || "3s";
  fields.deviceMode.value = config.device_mode || "name";
  fields.deviceId.value = config.device_id || "";
  fields.deviceName.value = config.device_name || "EchoWarpWeb";
  fields.sampleRate.value = config.sample_rate || 48000;
  fields.channels.value = config.channels || 2;
  fields.audioBufferFrames.value = config.audio_buffer_frames || 5;
  fields.captureDevices.value = (config.capture_devices || []).join("\n");
  fields.playbackDevices.value = (config.playback_devices || []).join("\n");
  fields.virtualMic.checked = Boolean(config.virtual_mic);
  fields.loopback.checked = Boolean(config.loopback);
  fields.aec.checked = Boolean(config.aec);
  fields.role.value = config.role || "client";
  fields.serverMode.value = config.server_mode || "normal";
  fields.serverName.value = config.server_name || "EchoWarpWeb";
  fields.logLevel.value = config.log_level || "info";
  fields.maxClients.value = config.max_clients || 1;
  fields.maxReconnect.value = config.max_reconnect ?? 5;
  fields.restartDelay.value = config.restart_delay || 3;
  fields.autoReconnectAttempts.value = config.auto_reconnect_attempts || 0;
  fields.rateLimit.value = config.rate_limit ?? 5;
  fields.maxAuthFailures.value = config.max_auth_failures ?? 5;
  fields.record.value = config.record || "";
  fields.tlsCert.value = config.tls_cert || "";
  fields.tlsKey.value = config.tls_key || "";
  fields.banFile.value = config.ban_file || "";
  fields.recordDir.value = config.record_dir || "";
  fields.enabled.checked = config.enabled !== false;
  fields.discover.checked = config.discover !== false;
  fields.tlsInsecure.checked = Boolean(config.tls_insecure);
  fields.autoRestart.checked = config.auto_restart !== false;
  fields.autoReconnect.checked = Boolean(config.auto_reconnect);
  fields.serverDiscovery.checked = config.server_discovery !== false;
  fields.hwidRequired.checked = Boolean(config.hwid_required);
  fields.serverMuted.checked = Boolean(config.server_muted);
  fields.stunServers.value = (config.stun_servers || []).join("\n");
  fields.extraArgs.value = config.extra_args || "";
}

function splitItems(value) {
  return value.split(/[\r\n,]+/).map((item) => item.trim()).filter(Boolean);
}

function collectConfig() {
  return {
    enabled: fields.enabled.checked,
    role: fields.role.value,
    address: latestConfig.address || "",
    port: Number(latestConfig.port || 4415),
    password: "__KEEP__",
    nickname: latestConfig.nickname || "",
    discover_timeout: latestConfig.discover_timeout || "3s",
    device_mode: fields.deviceMode.value,
    device_id: fields.deviceId.value.trim(),
    device_name: fields.deviceName.value.trim(),
    sample_rate: Number(fields.sampleRate.value || 48000),
    channels: Number(fields.channels.value || 2),
    audio_buffer_frames: Number(fields.audioBufferFrames.value || 5),
    capture_devices: splitItems(fields.captureDevices.value),
    playback_devices: splitItems(fields.playbackDevices.value),
    virtual_mic: fields.virtualMic.checked,
    loopback: fields.loopback.checked,
    aec: fields.aec.checked,
    server_mode: fields.serverMode.value,
    server_name: fields.serverName.value.trim(),
    log_level: fields.logLevel.value,
    max_clients: Number(fields.maxClients.value || 1),
    max_reconnect: Number(fields.maxReconnect.value || 0),
    restart_delay: Number(fields.restartDelay.value || 3),
    auto_reconnect_attempts: Number(fields.autoReconnectAttempts.value || 0),
    rate_limit: Number(fields.rateLimit.value || 0),
    max_auth_failures: Number(fields.maxAuthFailures.value || 0),
    record: fields.record.value,
    tls_cert: fields.tlsCert.value.trim(),
    tls_key: fields.tlsKey.value.trim(),
    ban_file: fields.banFile.value.trim(),
    record_dir: fields.recordDir.value.trim(),
    discover: fields.discover.checked,
    tls_insecure: fields.tlsInsecure.checked,
    auto_restart: fields.autoRestart.checked,
    auto_reconnect: fields.autoReconnect.checked,
    server_discovery: fields.serverDiscovery.checked,
    hwid_required: fields.hwidRequired.checked,
    server_muted: fields.serverMuted.checked,
    stun_servers: splitItems(fields.stunServers.value),
    extra_args: fields.extraArgs.value.trim(),
  };
}

async function loadConfig() {
  const config = await api("/api/config");
  fillConfig(config);
}

async function saveConfig() {
  const button = document.getElementById("saveButton");
  button.disabled = true;
  try {
    const data = await api("/api/config", {
      method: "POST",
      body: JSON.stringify(collectConfig()),
    });
    fillConfig(data.config);
    loadServers().catch(() => {});
    subtitle.textContent = "Configuracao aplicada";
  } finally {
    button.disabled = false;
  }
}

async function clearPassword() {
  const button = document.getElementById("clearPasswordButton");
  button.disabled = true;
  try {
    const payload = collectConfig();
    payload.password = "";
    const data = await api("/api/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    fillConfig(data.config);
    subtitle.textContent = "Senha removida";
  } finally {
    button.disabled = false;
  }
}

async function refreshStatus() {
  try {
    const data = await api(`/api/status?ts=${Date.now()}`);
    const config = data.config || {};
    connectionState = data.state || "starting";
    state.textContent = data.state || "-";
    origin.textContent = config.role === "server"
      ? `server:${data.port || 4415}`
      : (data.server ? `${data.server}:${data.port || 4415}` : "sem servidor");
    processState.textContent = [
      data.echowarp_pid ? `ew ${data.echowarp_pid}` : "ew -",
      data.ffmpeg_pid ? `ffmpeg ${data.ffmpeg_pid}` : "ffmpeg -",
    ].join(" | ");
    subtitle.textContent = data.detail || "Stream ativo";
    stream.textContent = data.hls_ready ? streamUrl : "preparando stream";
    audioLevel.textContent = formatAudioLevel(data.audio_level);
    updateMeterLevel(data.audio_level);
    const recording = data.recording || {};
    document.getElementById("recordingState").textContent = recording.error || (recording.active ? "Gravando no servidor" : "Sem gravacao ativa");
    document.getElementById("recordButton").disabled = recordingBusy || recording.active || data.state !== "connected";
    document.getElementById("stopRecordButton").disabled = recordingBusy || !recording.active;
    document.getElementById("disconnectButton").disabled = !config.enabled;
  } catch {
    state.textContent = "sem status";
    subtitle.textContent = "Aguardando container";
    stream.textContent = "preparando stream";
    audioLevel.textContent = "sem status";
    updateMeterLevel(null);
  }
}

function serverPasswordValue(id) {
  if (fields.password.value) return fields.password.value;
  if (id) return "__KEEP__";
  return "";
}

function collectServerPayload(overrides = {}) {
  const id = overrides.id ?? editingServerId;
  return {
    id,
    name: String(overrides.name ?? fields.serverPresetName.value).trim(),
    address: String(overrides.address ?? fields.address.value).trim(),
    port: Number(overrides.port ?? fields.port.value ?? 4415),
    password: overrides.password ?? serverPasswordValue(id),
    nickname: String(overrides.nickname ?? fields.nickname.value).trim(),
    discover_timeout: String(overrides.discover_timeout ?? fields.discoverTimeout.value).trim() || "3s",
    tls_insecure: Boolean(overrides.tls_insecure ?? fields.serverTlsInsecure.checked),
  };
}

function resetServerEditor() {
  editingServerId = "";
  fields.serverPresetName.value = "";
  fields.address.value = "";
  fields.port.value = 4415;
  fields.password.value = "";
  fields.password.placeholder = "";
  fields.nickname.value = "";
  fields.discoverTimeout.value = "3s";
  fields.serverTlsInsecure.checked = false;
  addServerButton.textContent = "Adicionar servidor";
  serverDialogTitle.textContent = "Novo servidor";
}

function openServerDialog(server = null) {
  if (server?.id) {
    editSavedServer(server);
  } else if (server) {
    resetServerEditor();
    fields.serverPresetName.value = server.name || "";
    fields.address.value = server.address || "";
    fields.port.value = server.port || 4415;
    fields.nickname.value = server.nickname || "";
    fields.discoverTimeout.value = server.discover_timeout || "3s";
    fields.serverTlsInsecure.checked = Boolean(server.tls_insecure);
  } else {
    resetServerEditor();
  }
  if (typeof serverDialog.showModal === "function") {
    serverDialog.showModal();
  } else {
    serverDialog.setAttribute("open", "");
  }
}

function closeServerDialog() {
  serverDialog.close();
}

function editSavedServer(server) {
  editingServerId = server.id;
  fields.serverPresetName.value = server.name || "";
  fields.address.value = server.address || "";
  fields.port.value = server.port || 4415;
  fields.password.value = "";
  fields.password.placeholder = server.password_set ? "senha salva" : "";
  fields.nickname.value = server.nickname || "";
  fields.discoverTimeout.value = server.discover_timeout || "3s";
  fields.serverTlsInsecure.checked = Boolean(server.tls_insecure);
  addServerButton.textContent = "Salvar servidor";
  serverDialogTitle.textContent = "Editar servidor";
  subtitle.textContent = "Servidor carregado para edicao";
}

function renderSavedServers() {
  if (!savedServers.length) {
    savedServerList.innerHTML = '<div class="server-row"><div><strong>Nenhum servidor salvo</strong><span>Adicione manualmente ou pelo scan</span></div></div>';
    return;
  }

  savedServerList.innerHTML = savedServers.map((server) => `
    <div class="server-row ${server.active ? "is-active" : ""}">
      <div>
        <strong>${escapeHtml(server.name || `${server.address}:${server.port || 4415}`)} <span class="server-availability" data-server-status="${escapeHtml(server.id)}" data-online="${server.online == null ? "unknown" : server.online}" title="Disponibilidade pela API de status do EchoWarp (porta de audio + 1)">${server.online == null ? "Verificando" : server.online ? "Online" : "Indisponivel"}</span></strong>
        <span>${escapeHtml(server.address || "")}:${server.port || 4415}${server.active ? " / conectado" : ""}${server.password_set ? " / senha salva" : ""}</span>
      </div>
      <div class="server-actions">
        <button class="primary" type="button" data-connect-server="${escapeHtml(server.id)}">Conectar</button>
        <button type="button" data-edit-server="${escapeHtml(server.id)}">Editar</button>
        <button class="danger" type="button" data-delete-server="${escapeHtml(server.id)}">Excluir</button>
      </div>
    </div>
  `).join("");

  savedServerList.querySelectorAll("[data-connect-server]").forEach((button) => {
    button.addEventListener("click", () => connectSavedServer(button.dataset.connectServer));
  });
  savedServerList.querySelectorAll("[data-edit-server]").forEach((button) => {
    button.addEventListener("click", () => {
      const server = savedServers.find((item) => item.id === button.dataset.editServer);
      if (server) openServerDialog(server);
    });
  });
  savedServerList.querySelectorAll("[data-delete-server]").forEach((button) => {
    button.addEventListener("click", () => deleteSavedServer(button.dataset.deleteServer));
  });
}

function renderServers(servers) {
  scanResults = servers;
  scanResultsSection.hidden = false;
  if (!servers.length) {
    serverList.innerHTML = '<div class="server-row"><div><strong>Nenhum servidor encontrado</strong><span>-</span></div></div>';
    return;
  }

  serverList.innerHTML = servers.map((server, index) => {
    const address = server.address || server.host || "";
    const port = server.port || 4415;
    const name = server.name || `${address}:${port}`;
    return `
      <div class="server-row scan-result">
        <div>
          <strong>${escapeHtml(name)}</strong>
          <span>${escapeHtml(address)}:${port} / ${escapeHtml(server.source || "scan")}</span>
        </div>
        <button type="button" data-add-scan-index="${index}">Adicionar</button>
      </div>
    `;
  }).join("");

  serverList.querySelectorAll("[data-add-scan-index]").forEach((button) => {
    button.addEventListener("click", async () => {
      const index = Number(button.dataset.addScanIndex);
      const server = scanResults[index];
      openServerDialog({
        name: server.name || `${server.address || server.host || ""}:${server.port || 4415}`,
        address: server.address || server.host || "",
        port: server.port || 4415,
        nickname: server.name || "",
        discover_timeout: fields.discoverTimeout.value || "3s",
        tls_insecure: false,
      });
    });
  });
}

async function loadServers() {
  const data = await api("/api/servers");
  savedServers = data.servers || [];
  renderSavedServers();
}

async function saveServer(payload = collectServerPayload()) {
  addServerButton.disabled = true;
  try {
    const data = await api("/api/servers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    savedServers = data.servers || [];
    renderSavedServers();
    resetServerEditor();
    closeServerDialog();
    scanState.textContent = "servidor salvo";
  } catch (error) {
    scanState.textContent = error.message;
  } finally {
    addServerButton.disabled = false;
  }
}

async function connectSavedServer(id) {
  scanState.textContent = "conectando";
  try {
    const data = await api("/api/servers/connect", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    fillConfig(data.config);
    savedServers = data.servers || [];
    renderSavedServers();
    refreshStatus();
    scanState.textContent = "conectado";
  } catch (error) {
    scanState.textContent = error.message;
  }
}

async function deleteSavedServer(id) {
  if (!window.confirm("Excluir este servidor?")) return;
  try {
    const data = await api("/api/servers/delete", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    savedServers = data.servers || [];
    renderSavedServers();
    if (editingServerId === id) resetServerEditor();
    scanState.textContent = "servidor excluido";
  } catch (error) {
    scanState.textContent = error.message;
  }
}

async function scanServers() {
  scanState.textContent = "scan";
  document.getElementById("scanButton").disabled = true;
  try {
    const port = Number(fields.port.value || 4415);
    const data = await api(`/api/discover?timeout=3&port=${port}&portscan=1`);
    renderServers(data.servers || []);
    scanState.textContent = `${(data.servers || []).length} encontrados`;
  } catch (error) {
    scanState.textContent = error.message;
  } finally {
    document.getElementById("scanButton").disabled = false;
  }
}

async function loadDevices() {
  devicesState.textContent = "carregando";
  try {
    const data = await api("/api/devices");
    const outputs = data.output || [];
    fields.outputDevice.innerHTML = outputs.map((device) => (
      `<option value="${escapeHtml(device.id)}" data-name="${escapeHtml(device.name)}">${escapeHtml(device.id)} - ${escapeHtml(device.name)}</option>`
    )).join("");
    devicesRaw.textContent = data.raw || "";
    devicesState.textContent = `${outputs.length} saidas`;
  } catch (error) {
    devicesState.textContent = error.message;
  }
}

async function runDoctor() {
  doctorState.textContent = "rodando";
  try {
    const data = await api("/api/doctor", {
      method: "POST",
      body: JSON.stringify(collectConfig()),
    });
    doctorOutput.textContent = data.raw || JSON.stringify(data.report, null, 2);
    doctorState.textContent = data.ok ? "ok" : `codigo ${data.code}`;
  } catch (error) {
    doctorState.textContent = error.message;
  }
}

async function loadLogs() {
  try {
    const data = await api("/api/logs");
    const runtime = (data.runtime || []).join("\n");
    const client = (data.client || []).join("\n");
    logsOutput.textContent = [runtime, client].filter(Boolean).join("\n\n--- client.log ---\n");
  } catch {
    logsOutput.textContent = "";
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function loadRecordings() {
  try {
    const data = await api("/api/recordings");
    document.getElementById("recordingsList").innerHTML = data.files.length
      ? data.files.map((file) => `<a href="/api/recordings/download/${encodeURIComponent(file.name)}">${escapeHtml(new Date(file.created_at * 1000).toLocaleString())} · ${(file.bytes / 1048576).toFixed(1)} MB · Baixar</a>`).join("")
      : "Nenhuma gravacao salva";
  } catch (error) {
    document.getElementById("recordingsList").textContent = error.message;
  }
}

async function recordingAction(action, payload = {}) {
  recordingBusy = true;
  document.getElementById("recordButton").disabled = true;
  document.getElementById("stopRecordButton").disabled = true;
  try {
    await api(`/api/recordings/${action}`, { method: "POST", body: JSON.stringify(payload) });
    await refreshStatus();
    await loadRecordings();
  } catch (error) {
    document.getElementById("recordingState").textContent = error.message;
  } finally {
    recordingBusy = false;
    refreshStatus();
  }
}

document.getElementById("recordButton").addEventListener("click", () => recordingAction("start"));
document.getElementById("stopRecordButton").addEventListener("click", () => recordingAction("stop"));
document.getElementById("saveRetentionButton").addEventListener("click", () => {
  const input = document.getElementById("recordingRetention");
  if (input.reportValidity()) recordingAction("settings", { retention_hours: Number(input.value) });
});
document.getElementById("disconnectButton").addEventListener("click", async (event) => {
  event.currentTarget.disabled = true;
  try {
    await api("/api/stop", { method: "POST", body: "{}" });
    player.pause();
    await loadConfig();
    await loadServers();
    await loadRecordings();
  } catch (error) {
    subtitle.textContent = error.message;
  } finally {
    refreshStatus();
  }
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("is-active"));
    document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("is-active"));
    button.classList.add("is-active");
    document.querySelector(`[data-panel="${button.dataset.tab}"]`).classList.add("is-active");
    configForm.dataset.activeTab = button.dataset.tab;
    if (button.dataset.tab === "logs") loadLogs();
  });
});

playButton.addEventListener("click", async () => {
  if (!player.paused) { player.pause(); return; }
  if (!player.currentSrc && !hls) setPlayerSource();
  try {
    await player.play();
  } catch {
    subtitle.textContent = "Toque novamente quando o stream estiver pronto";
  }
});

reloadButton.addEventListener("click", () => {
  setPlayerSource();
  refreshStatus();
});

logoutButton.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  window.location.href = "/login";
});

configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveConfig();
});

document.getElementById("scanButton").addEventListener("click", scanServers);
addServerButton.addEventListener("click", () => saveServer());
openServerDialogButton.addEventListener("click", () => openServerDialog());
closeServerDialogButton.addEventListener("click", closeServerDialog);
cancelServerDialogButton.addEventListener("click", closeServerDialog);
serverDialog.addEventListener("click", (event) => {
  if (event.target === serverDialog) closeServerDialog();
});
document.getElementById("clearPasswordButton").addEventListener("click", clearPassword);
document.getElementById("devicesButton").addEventListener("click", loadDevices);
document.getElementById("doctorButton").addEventListener("click", runDoctor);

document.getElementById("restartButton").addEventListener("click", async () => {
  await api("/api/restart", { method: "POST", body: "{}" });
  refreshStatus();
});

document.getElementById("stopButton").addEventListener("click", async () => {
  await api("/api/stop", { method: "POST", body: "{}" });
  refreshStatus();
});

fields.outputDevice.addEventListener("change", () => {
  const option = fields.outputDevice.selectedOptions[0];
  if (!option) return;
  fields.deviceId.value = option.value;
  fields.deviceName.value = option.dataset.name || "";
});

playerVolume.addEventListener("input", () => setVolume(playerVolume.value));
settingsVolume.addEventListener("input", () => setVolume(settingsVolume.value));

player.addEventListener("playing", () => {
  hasPlayed = true;
  stablePlaybackSince = performance.now();
  document.body.classList.add("is-playing");
  updateMediaControls();
  if (!replayingHistory && !hls && player.seekable.length) {
    const end = player.seekable.end(player.seekable.length - 1);
    if (end - player.currentTime > bufferTarget + 3) {
      player.currentTime = Math.max(player.seekable.start(player.seekable.length - 1), end - bufferTarget);
    }
  }
});
player.addEventListener("pause", () => {
  document.body.classList.remove("is-playing");
  if (hasPlayed) { replayingHistory = true; applyBuffer(); }
  updateMediaControls();
});
player.addEventListener("ended", () => document.body.classList.remove("is-playing"));
player.addEventListener("error", () => {
  subtitle.textContent = "Reconectando ao stream";
  setTimeout(setPlayerSource, 1200);
});
window.addEventListener("beforeunload", () => {
  if (meterAnimationId) window.cancelAnimationFrame(meterAnimationId);
});

const historySeek = document.getElementById("historySeek");
const playbackSpeed = document.getElementById("playbackSpeed");
playbackSpeed.value = "1";
function resetPlaybackSpeed() {
  playbackSpeed.value = "1";
  player.playbackRate = 1;
}
playbackSpeed.addEventListener("change", () => {
  replayingHistory = true;
  applyBuffer();
  player.playbackRate = Number(playbackSpeed.value);
});
let seekingHistory = false;
historySeek.addEventListener("input", () => { seekingHistory = true; });
historySeek.addEventListener("blur", () => { seekingHistory = false; });
function historyTime(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
function historyRange() {
  if (!player.seekable.length) return null;
  const end = player.seekable.end(player.seekable.length - 1);
  return { start: Math.max(player.seekable.start(0), end - 1800), end };
}
function updateMediaControls() {
  const paused = player.paused || player.ended;
  playButton.dataset.paused = String(paused);
  playButton.setAttribute("aria-label", paused ? "Reproduzir" : "Pausar");
  playButton.title = paused ? "Reproduzir (K)" : "Pausar (K)";
  const range = historyRange();
  historySeek.disabled = !range;
  const live = document.getElementById("goLiveButton");
  const label = document.getElementById("liveLabel");
  live.disabled = !range;
  if (!range) {
    live.dataset.state = "waiting";
    label.textContent = "AGUARDANDO";
    document.getElementById("historyState").textContent = "Aguardando áudio e histórico";
    return;
  }
  const delay = Math.max(0, range.end - player.currentTime);
  const nearLive = delay <= bufferTarget + 1.5;
  if (nearLive && !paused && player.playbackRate > 1) {
    resetPlaybackSpeed();
    replayingHistory = false;
    applyBuffer();
  }
  const atLive = nearLive && !paused && player.readyState >= 3 && connectionState === "connected";
  live.dataset.state = atLive ? "live" : "history";
  label.textContent = atLive ? "AO VIVO" : "VOLTAR AO VIVO";
  live.setAttribute("aria-label", atLive ? "Ao vivo" : "Voltar ao vivo");
  document.getElementById("playbackPosition").textContent = nearLive ? (paused ? "Pausado" : player.readyState < 3 ? "Carregando" : "0:00") : `−${historyTime(delay)}`;
  historySeek.max = Math.floor(range.end - range.start);
  if (!seekingHistory) historySeek.value = Math.max(0, player.currentTime - range.start);
  historySeek.style.setProperty("--played", `${Math.min(100, Number(historySeek.value) / Math.max(1, Number(historySeek.max)) * 100)}%`);
  historySeek.setAttribute("aria-valuetext", `${historyTime(range.end - range.start - Number(historySeek.value))} atras`);
  document.getElementById("historyState").textContent = `${historyTime(range.end - range.start)} de histórico disponível${connectionState !== "connected" ? " · Origem desconectada" : ""}`;
}
setInterval(updateMediaControls, 500);
for (const event of ["timeupdate", "playing", "pause", "waiting", "ended", "loadedmetadata"]) {
  player.addEventListener(event, updateMediaControls);
}
historySeek.addEventListener("change", async () => {
  seekingHistory = false;
  const range = historyRange();
  if (!range) return;
  const destination = Math.min(range.end - bufferTarget, range.start + Number(historySeek.value));
  replayingHistory = destination < range.end - bufferTarget - 1;
  applyBuffer();
  player.playbackRate = Number(playbackSpeed.value);
  player.currentTime = Math.max(range.start, destination);
  try { await player.play(); } catch { subtitle.textContent = "Toque em Tocar para ouvir"; }
});
document.getElementById("goLiveButton").addEventListener("click", async () => {
  resetPlaybackSpeed();
  replayingHistory = false;
  applyBuffer();
  const range = historyRange();
  if (range) player.currentTime = Math.max(range.start, range.end - bufferTarget);
  try { await player.play(); } catch { subtitle.textContent = "Toque em reproduzir para ouvir"; }
});

document.getElementById("mediaControls").addEventListener("keydown", (event) => {
  if (["INPUT", "SELECT"].includes(event.target.tagName)) return;
  if (event.key.toLowerCase() === "k" || (event.key === " " && event.target.tagName !== "BUTTON")) {
    event.preventDefault();
    playButton.click();
  } else if (event.key.toLowerCase() === "m") {
    event.preventDefault();
    muteButton.click();
  } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    const range = historyRange();
    if (!range) return;
    event.preventDefault();
    replayingHistory = true;
    applyBuffer();
    player.playbackRate = Number(playbackSpeed.value);
    player.currentTime = Math.max(range.start, Math.min(range.end - 0.1, player.currentTime + (event.key === "ArrowLeft" ? -5 : 5)));
  }
});
document.getElementById("saveHistoryButton").addEventListener("click", async (event) => {
  const minutes = document.getElementById("historyMinutes");
  if (!minutes.reportValidity()) return;
  const button = event.currentTarget;
  button.disabled = true;
  const message = document.getElementById("historySaveState");
  message.textContent = "Salvando historico no servidor...";
  try {
    const data = await api("/api/history/save", { method: "POST", body: JSON.stringify({ minutes: Number(minutes.value) }) });
    message.textContent = `Salvos ${(data.seconds / 60).toFixed(1)} minutos. Disponivel nas gravacoes abaixo.`;
    await loadRecordings();
  } catch (error) { message.textContent = error.message; }
  finally { button.disabled = false; }
});

loadVolume();
setPlayerSource();
animateMeter();
loadConfig().then(() => {
  document.getElementById("recordingRetention").value = latestConfig.recording_retention_hours ?? 168;
  return loadServers();
}).catch(() => {});
loadRecordings();
setInterval(loadRecordings, 10000);
loadDevices().catch(() => {});
refreshStatus();
loadLogs();
setInterval(refreshStatus, 2500);
setInterval(loadLogs, 5000);

let checkingServers = false;
setInterval(async () => {
  if (checkingServers || document.hidden) return;
  checkingServers = true;
  try {
    const data = await api('/api/servers');
    for (const server of data.servers || []) {
      const badge = Array.from(savedServerList.querySelectorAll('[data-server-status]')).find(item => item.dataset.serverStatus === server.id);
      if (!badge) continue;
      badge.dataset.online = server.online == null ? 'unknown' : String(server.online);
      badge.textContent = server.online == null ? 'Verificando' : server.online ? 'Online' : 'Indisponivel';
    }
  } catch {
    savedServerList.querySelectorAll('[data-server-status]').forEach(badge => {
      badge.dataset.online = 'unknown';
      badge.textContent = 'Sem status';
    });
  } finally { checkingServers = false; }
}, 15000);
