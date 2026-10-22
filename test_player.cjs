const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map([...fs.readFileSync('web/index.html', 'utf8').matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
  value: '2', checked: false, disabled: false, textContent: '', dataset: {},
  style: { setProperty() {} }, classList: { add() {}, remove() {} }, events: {}, attrs: {},
  setAttribute(key, value) { this.attrs[key] = value; },
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); },
  querySelectorAll: () => [], reportValidity: () => true,
  async emit(name) { for (const fn of this.events[name] || []) await fn({ target: this, currentTarget: this }); },
  click() { return this.emit('click'); },
} ]));
const player = elements.get('player');
Object.assign(player, {
  paused: true, ended: false, muted: false, volume: 1, currentTime: 0, currentSrc: 'stream', readyState: 4,
  seekable: { length: 1, start: () => 100, end: () => 1900 },
  canPlayType: () => '',
  async play() { this.paused = false; await this.emit('playing'); },
  pause() { this.paused = true; return this.emit('pause'); },
});
class Hls {
  static isSupported() { return true; }
  constructor(config) { this.config = config; }
  loadSource() {} attachMedia() {} destroy() {}
}
const context = vm.createContext({
  document: { getElementById(id) { assert(elements.has(id), `Missing ${id}`); return elements.get(id); }, querySelectorAll: () => [], body: { classList: { add() {}, remove() {} } } },
  window: { Hls, localStorage: { getItem: () => null, setItem() {} }, requestAnimationFrame: () => 1, addEventListener() {} },
  performance: { now: () => 20000 }, setInterval() {}, setTimeout() {},
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ state: 'connected', config: {}, files: [], servers: [], output: [] }) }),
});
(async () => {
  vm.runInContext(fs.readFileSync('web/app.js', 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  await elements.get('playButton').click();
  assert(!player.paused);
  assert.equal(elements.get('playButton').attrs['aria-label'], 'Pausar');
  const timeline = elements.get('historySeek');
  timeline.value = '900';
  await timeline.emit('change');
  assert.equal(player.currentTime, 1000);
  assert.equal(vm.runInContext('hls.config.liveMaxLatencyDuration', context), Infinity);
  assert.equal(elements.get('liveLabel').textContent, 'VOLTAR AO VIVO');
  const speed = elements.get('playbackSpeed');
  for (const rate of ['1.5', '2', '3']) {
    speed.value = rate;
    await speed.emit('change');
    assert.equal(player.playbackRate, Number(rate));
    await timeline.emit('change');
    assert.equal(player.playbackRate, Number(rate));
  }
  player.currentTime = 1898;
  await player.emit('timeupdate');
  assert.equal(player.playbackRate, 1);
  assert.equal(speed.value, '1');
  timeline.value = '900';
  await timeline.emit('change');
  speed.value = '2';
  await speed.emit('change');
  await elements.get('goLiveButton').click();
  assert.equal(player.playbackRate, 1);
  assert.equal(speed.value, '1');
  assert.equal(player.currentTime, 1898);
  assert.equal(elements.get('liveLabel').textContent, 'AO VIVO');
  await elements.get('playButton').click();
  assert(player.paused);
  assert.equal(vm.runInContext('hls.config.liveMaxLatencyDuration', context), Infinity);
  assert.notEqual(elements.get('liveLabel').textContent, 'AO VIVO');
  await elements.get('muteButton').click();
  assert(player.muted);
  elements.get('playerVolume').value = '35';
  await elements.get('playerVolume').emit('input');
  assert(!player.muted);
  assert.equal(player.volume, 0.35);
  vm.runInContext('connectionState = "auth_error"', context);
  await elements.get('goLiveButton').click();
  assert.notEqual(elements.get('liveLabel').textContent, 'AO VIVO');
  console.log('Player: initialization, play/pause, history, go-live, volume/mute and offline state OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
