const { createApp, ref, computed, onMounted } = Vue;

const S3_BASE = 'https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer';

function pad(n) {
  return String(n).padStart(2, '0');
}

function dtParts(offset = 0) {
  const d = new Date(Date.now() - offset * 3_600_000);
  return {
    y: d.getFullYear(),
    m: pad(d.getMonth() + 1),
    day: pad(d.getDate()),
    h: pad(d.getHours()),
  };
}

function camCandidates(slug, offset = 0) {
  const p = dtParts(offset);
  const base = `${S3_BASE}/${slug}/${p.y}/${p.m}/${p.day}`;
  if (slug === 'eole') {
    return [`${base}/small/${p.h}-00.jpg`, `${base}/large/${p.h}-00.jpg`, `${base}/${p.h}-00.jpg`];
  }
  return [`${base}/small/${p.h}-00.jpg`, `${base}/${p.h}-00.jpg`];
}

function cameraLabel(slug) {
  return {
    eole: 'Éole',
    maritime: 'Maritime',
    mer: 'La Mer',
  }[slug] || slug || 'Webcam';
}

function cameraFromUrl(url = '') {
  const match = String(url).match(/berck-sur-mer\/([^/]+)/);
  return match ? match[1] : '';
}

function dateKey(ts) {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

createApp({
  setup() {
    const loading = ref(false);
    const status = ref(null);
    const lastKites = ref([]);
    const history = ref([]);
    const modalItem = ref(null);
    const liveTick = ref(Date.now());
    const cams = ref([
      { slug: 'eole', label: 'Éole', role: 'Zone kite', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'maritime', label: 'Maritime', role: 'Front de mer', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'mer', label: 'La Mer', role: 'Large plage', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
    ]);

    function resetCam(cam) {
      cam.loaded = false;
      cam.unavailable = false;
      cam.idx = 0;
      cam.tries = [
        ...camCandidates(cam.slug, 0),
        ...camCandidates(cam.slug, 1),
        `cams/${cam.slug}.jpg`,
      ];
      cam.src = cam.tries[0];
    }

    function fallbackCam(cam) {
      cam.idx += 1;
      if (cam.idx < cam.tries.length) {
        cam.src = cam.tries[cam.idx];
      } else {
        cam.unavailable = true;
      }
    }

    function normalizeStatusKites(data) {
      if (Array.isArray(data?.last_kites) && data.last_kites.length) return data.last_kites;
      return data?.last_kite ? [data.last_kite] : [];
    }

    async function fetchJson(url, fallback) {
      try {
        const r = await fetch(`${url}?t=${Date.now()}`);
        if (!r.ok) return fallback;
        return await r.json();
      } catch {
        return fallback;
      }
    }

    function pickLatestStatus(items) {
      return items
        .filter(Boolean)
        .sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')))[0] || null;
    }

    function pickLongestHistory(items) {
      return items
        .filter(Array.isArray)
        .sort((a, b) => b.length - a.length)[0] || [];
    }

    async function refreshAll() {
      loading.value = true;
      cams.value.forEach(resetCam);
      const [rootStatus, publicStatus, rootHistory, publicHistory] = await Promise.all([
        fetchJson('kite_status.json', null),
        fetchJson('berck-kite/kite_status.json', null),
        fetchJson('detection_history.json', []),
        fetchJson('berck-kite/detection_history.json', []),
      ]);
      const statusData = pickLatestStatus([rootStatus, publicStatus]);
      const historyData = pickLongestHistory([rootHistory, publicHistory]);
      status.value = statusData;
      lastKites.value = normalizeStatusKites(statusData);
      history.value = historyData;
      loading.value = false;
      liveTick.value = Date.now();
    }

    const events = computed(() => {
      const fromHistory = history.value
        .filter(e => e && e.timestamp)
        .map(e => ({
          ...e,
          camera_label: e.camera_label || cameraLabel(e.camera),
          kites_detected: Number(e.kites_detected || 0),
        }));
      const known = new Set(fromHistory.map(e => `${e.timestamp}|${e.camera || ''}`));
      const fromStatus = lastKites.value
        .filter(e => e && e.timestamp)
        .filter(e => !known.has(`${e.timestamp}|${e.camera || ''}`))
        .map(e => ({
          ...e,
          camera_label: e.camera_label || cameraLabel(e.camera),
          kites_detected: Number(e.kites_detected || 0),
        }));
      return [...fromHistory, ...fromStatus]
        .filter(e => e.kites_detected > 0)
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    });

    const totalDetections = computed(() => events.value.reduce((sum, e) => sum + e.kites_detected, 0));

    const activeDays = computed(() => new Set(events.value.map(e => dateKey(e.timestamp)).filter(Boolean)).size);

    const dailyBars = computed(() => {
      const byDay = new Map();
      for (const event of events.value) {
        const key = dateKey(event.timestamp);
        if (!key) continue;
        byDay.set(key, (byDay.get(key) || 0) + event.kites_detected);
      }
      const rows = [...byDay.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .slice(-14)
        .map(([key, count]) => ({
          key,
          count,
          label: new Date(`${key}T12:00:00`).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' }),
        }));
      const max = Math.max(1, ...rows.map(r => r.count));
      return rows.map(r => ({ ...r, pct: Math.max(6, Math.round((r.count / max) * 100)) }));
    });

    const hourBars = computed(() => {
      const counts = new Map();
      for (let h = 10; h <= 18; h += 1) counts.set(h, 0);
      for (const event of events.value) {
        const h = new Date(event.timestamp).getHours();
        if (counts.has(h)) counts.set(h, counts.get(h) + event.kites_detected);
      }
      const max = Math.max(1, ...counts.values());
      return [...counts.entries()].map(([hour, count]) => ({
        hour,
        count,
        pct: count ? Math.max(12, Math.round((count / max) * 100)) : 5,
      }));
    });

    const bestHourLabel = computed(() => {
      const best = [...hourBars.value].sort((a, b) => b.count - a.count)[0];
      return best?.count ? `${best.hour}h` : 'à venir';
    });

    const windRows = computed(() => {
      const buckets = [
        { label: '8-12 kt', min: 8, max: 12, count: 0 },
        { label: '12-18 kt', min: 12, max: 18, count: 0 },
        { label: '18-25 kt', min: 18, max: 25, count: 0 },
        { label: '25+ kt', min: 25, max: Infinity, count: 0 },
      ];
      for (const event of events.value) {
        const wind = Number(event.wind_kt);
        if (!wind) continue;
        const bucket = buckets.find(b => wind >= b.min && wind < b.max);
        if (bucket) bucket.count += event.kites_detected;
      }
      const rows = buckets.filter(b => b.count > 0);
      const max = Math.max(1, ...rows.map(r => r.count));
      return rows.map(r => ({ ...r, pct: Math.max(8, Math.round((r.count / max) * 100)) }));
    });

    const bestWindLabel = computed(() => {
      const best = [...windRows.value].sort((a, b) => b.count - a.count)[0];
      return best ? best.label : 'à venir';
    });

    const historyWindowLabel = computed(() => {
      if (!dailyBars.value.length) return 'à venir';
      return `${dailyBars.value.length} jours`;
    });

    const liveTimeLabel = computed(() => {
      return new Date(liveTick.value).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    });

    const lastRunLabel = computed(() => {
      const ts = status.value?.timestamp || lastKites.value[0]?.timestamp;
      return ts ? formatDateTime(ts) : '';
    });

    function kiteCamLabel(kite) {
      return kite?.camera_label || cameraLabel(kite?.camera || cameraFromUrl(kite?.image_url));
    }

    function boxStyle(box) {
      return {
        left: `${box.x1 * 100}%`,
        top: `${box.y1 * 100}%`,
        width: `${(box.x2 - box.x1) * 100}%`,
        height: `${(box.y2 - box.y1) * 100}%`,
      };
    }

    function formatDateTime(ts) {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return '';
      return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
        + ' · '
        + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    }

    function openDetection(kite) {
      modalItem.value = kite;
    }

    function openLive(cam) {
      if (cam.unavailable) return;
      modalItem.value = { ...cam, image_url: cam.src };
    }

    function closeModal() {
      modalItem.value = null;
    }

    onMounted(() => {
      refreshAll();
    });

    return {
      loading,
      cams,
      status,
      lastKites,
      modalItem,
      events,
      totalDetections,
      activeDays,
      dailyBars,
      hourBars,
      windRows,
      bestHourLabel,
      bestWindLabel,
      historyWindowLabel,
      liveTimeLabel,
      lastRunLabel,
      fallbackCam,
      refreshAll,
      kiteCamLabel,
      boxStyle,
      formatDateTime,
      openDetection,
      openLive,
      closeModal,
    };
  },
}).mount('#spotApp');
