const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exécuter le vrai bloc réactif sans Vue, navigateur ni réseau.
const source = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const start = source.indexOf('    const podcastGeneratedAt = ref(null);');
const end = source.indexOf('    let chart', start);
assert.ok(start >= 0 && end > start);
function player(payload, now = '2026-11-21T09:00:00Z') {
  class FrozenDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
  }
  const context = vm.createContext({
    Date: FrozenDate,
    tideMs: { value: new Date(now).getTime() },
    ref: value => ({ value }),
    computed: getter => ({ get value() { return getter(); } }),
    fetch: async (url, opts) => {
      assert.equal(url, '/podcast/video_frames/manifest.json');
      assert.equal(opts.cache, 'no-store');
      return { ok: true, json: async () => payload };
    },
  });
  vm.runInContext(source.slice(start, end), context);
  return {
    refresh: () => vm.runInContext('fetchPodcastDate()', context),
    label: () => vm.runInContext('podcastDateLabel.value', context),
    version: () => vm.runInContext('podcastVersion.value', context),
    tick: timestamp => { context.tideMs.value = Date.parse(timestamp); },
  };
}

test('une vidéo du 19 est indiquée comme archive le 21', async () => {
  const p = player({ generated_at: '2026-11-19T18:22:00+01:00' });
  assert.equal(p.label(), 'date non vérifiée');
  await p.refresh();
  assert.equal(p.label(), 'archive du 19 novembre');
  assert.equal(p.version(), '?v=2026-11-19T18%3A22%3A00%2B01%3A00');
});

test('la comparaison utilise la date Paris, même avant minuit UTC', async () => {
  const p = player({ generated_at: '2026-11-20T23:30:00Z' }, '2026-11-20T23:50:00Z');
  await p.refresh();
  assert.equal(p.label(), 'du 21 novembre');
  p.tick('2026-11-21T23:05:00Z');
  assert.equal(p.label(), 'archive du 21 novembre');
});

test('un manifeste invalide ne prétend jamais être du jour', async () => {
  for (const payload of [{}, { generated_at: 'invalide' }]) {
    const p = player(payload);
    await p.refresh();
    assert.equal(p.label(), 'date non vérifiée');
    assert.equal(p.version(), '');
  }
});
