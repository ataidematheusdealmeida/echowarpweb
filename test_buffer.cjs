const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const controls = Object.fromEntries(['autoBuffer', 'bufferSeconds', 'bufferState'].map(id => [id, { addEventListener() {}, reportValidity: () => true }]));
const callbacks = {};
let now = 20000;
let tick;
const context = vm.createContext({
  document: { getElementById: id => controls[id] },
  window: { localStorage: { getItem: () => null, setItem() {} } },
  performance: { now: () => now },
  player: { paused: false, readyState: 4, addEventListener: (event, fn) => callbacks[event] = fn, canPlayType: () => '' },
  setInterval: fn => tick = fn,
  hls: { config: {} },
  replayingHistory: false,
});
vm.runInContext(source.slice(source.indexOf('const autoBuffer ='), source.indexOf('async function api(')), context);
vm.runInContext('hasPlayed = true; applyBuffer()', context);
assert.equal(context.hls.config.liveSyncDuration, 2);
callbacks.waiting();
assert.equal(context.hls.config.liveSyncDuration, 3);
callbacks.stalled();
assert.equal(context.hls.config.liveSyncDuration, 3);
now = 31000;
callbacks.waiting();
assert.equal(context.hls.config.liveSyncDuration, 4);
vm.runInContext('stablePlaybackSince = performance.now()', context);
now = 152000;
tick();
assert.equal(context.hls.config.liveSyncDuration, 3.5);
controls.autoBuffer.checked = false;
controls.bufferSeconds.value = 7;
vm.runInContext('changeBufferSetting()', context);
assert.equal(context.hls.config.liveSyncDuration, 7);
now += 20000;
callbacks.waiting();
assert.equal(context.hls.config.liveSyncDuration, 7);
console.log('Buffer: adaptation, cooldown, recovery and manual mode OK');
