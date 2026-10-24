const { createApp, ref, computed, onMounted } = Vue;

const S3_BASE = 'https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer';
const MIN_KITE_CONF = 0.5;
const DIRS16 = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'];

function dirLabel(deg) {
  const value = Number(deg);
  if (deg == null || Number.isNaN(value)) return '';
  return DIRS16[Math.round(value / 22.5) % 16];
}

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

function track(name, data = {}) {
  if (window.kiteAnalytics?.track) {
    window.kiteAnalytics.track(name, data);
  }
}

createApp({
  setup() {
    const loading = ref(false);
    const detections = ref([]);
    const modalItem = ref(null);
    const liveTick = ref(Date.now());
    const cams = ref([
      { slug: 'eole', label: 'Éole', role: 'Zone kite', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'baie-d-authie', label: "Baie d'Authie", role: 'Sud baie', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'entonnoir', label: 'Entonnoir', role: 'Centre plage', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'maritime', label: 'Maritime', role: 'Front de mer', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'mer', label: 'La Mer', role: 'Large plage', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
      { slug: 'poste-de-secours', label: 'Poste de Secours', role: 'Zone surveillee', src: '', tries: [], idx: 0, loaded: false, unavailable: false },
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

    const orderedCams = computed(() => {
      const order = ['baie-d-authie', 'entonnoir', 'maritime', 'mer', 'poste-de-secours', 'eole'];
      return [...cams.value].sort((a, b) => order.indexOf(a.slug) - order.indexOf(b.slug));
    });

    function confidentBoxes(kite) {
      return Array.isArray(kite?.boxes)
        ? kite.boxes.filter(box => Number(box?.conf || 0) >= MIN_KITE_CONF)
        : [];
    }

    // Liste complète des détections confiantes, de la plus récente à la plus ancienne.
    function normalizeDetections(statusData, historyItems = []) {
      const entries = [];
      if (Array.isArray(statusData?.last_kites)) entries.push(...statusData.last_kites);
      if (statusData?.last_kite) entries.push(statusData.last_kite);
      if (Array.isArray(historyItems)) entries.push(...historyItems);

      // Une entrée sans boîtes hérite de celles de la même image déjà rencontrée.
      const boxesByImage = new Map();
      entries.forEach(kite => {
        const boxes = confidentBoxes(kite);
        if (kite?.image_url && boxes.length) boxesByImage.set(kite.image_url, boxes);
      });

      const seen = new Set();
      return entries
        .filter(kite => kite?.timestamp && kite?.image_url)
        .map(kite => {
          const ownBoxes = confidentBoxes(kite);
          const boxes = ownBoxes.length ? ownBoxes : (boxesByImage.get(kite.image_url) || []);
          const camera = kite.camera || cameraFromUrl(kite.image_url);
          return {
            ...kite,
            camera,
            camera_label: kite.camera_label || cameraLabel(camera),
            boxes,
            kites_detected: boxes.length,
          };
        })
        .filter(kite => kite.boxes.length > 0)
        .sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
        .filter(kite => {
          const key = kite.image_url;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
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
      detections.value = normalizeDetections(statusData, historyData);
      loading.value = false;
      liveTick.value = Date.now();
    }

    const liveTimeLabel = computed(() => {
      return new Date(liveTick.value).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    });

    function kiteCamLabel(kite) {
      return kite?.camera_label || cameraLabel(kite?.camera || cameraFromUrl(kite?.image_url));
    }

    function boxesOverlap(a, b, margin = 0) {
      return !(a.x2 + margin < b.x1 || b.x2 + margin < a.x1 || a.y2 + margin < b.y1 || b.y2 + margin < a.y1);
    }

    function isBoxCrowded(box, boxes = []) {
      return boxes.some(other => other !== box && boxesOverlap(box, other, 0.025));
    }

    function boxStyle(box, boxes = [], index = 0) {
      const width = Math.max(0.001, box.x2 - box.x1);
      const height = Math.max(0.001, box.y2 - box.y1);
      const crowded = isBoxCrowded(box, boxes);
      const minPadX = crowded ? 0.01 : 0.018;
      const minPadY = crowded ? 0.018 : 0.032;
      const ratio = crowded ? 0.22 : 0.55;
      const padX = Math.max(minPadX, width * ratio);
      const padY = Math.max(minPadY, height * ratio);
      const x1 = Math.max(0, box.x1 - padX);
      const y1 = Math.max(0, box.y1 - padY);
      const x2 = Math.min(1, box.x2 + padX);
      const y2 = Math.min(1, box.y2 + padY);
      return {
        left: `${x1 * 100}%`,
        top: `${y1 * 100}%`,
        width: `${(x2 - x1) * 100}%`,
        height: `${(y2 - y1) * 100}%`,
        '--label-offset': `${8 + (index % 3) * 14}px`,
        '--label-side': index % 2 ? 'calc(100% + var(--label-offset))' : 'auto',
        '--label-bottom': index % 2 ? 'auto' : 'calc(100% + var(--label-offset))',
        '--highlight-opacity': crowded ? 0.72 : 1,
        '--corner-size': crowded ? '12px' : '18px',
      };
    }

    function formatDateTime(ts) {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return '';
      return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
        + ' · '
        + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    }

    function formatDayLong(ts) {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return '';
      return d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
    }

    // Petites infos affichées sous l'image quand on ouvre une détection.
    const modalFacts = computed(() => {
      const kite = modalItem.value;
      if (!kite || kite.type === 'live') return [];

      const date = new Date(kite.timestamp);
      const facts = [
        { label: 'Jour', value: formatDayLong(kite.timestamp) },
        { label: 'Heure', value: Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) },
        { label: 'Caméra', value: kiteCamLabel(kite) },
        { label: 'Kites', value: String(kite.kites_detected || 0) },
      ];

      if (kite.wind_kt != null) {
        const dir = dirLabel(kite.wind_dir);
        facts.push({ label: 'Vent', value: `${Math.round(kite.wind_kt)} kt${dir ? ' ' + dir : ''}` });
      }
      if (kite.temp_c != null) facts.push({ label: 'Température', value: `${Math.round(kite.temp_c)} °C` });
      if (Number(kite.rain_mm) > 0) facts.push({ label: 'Pluie', value: `${Number(kite.rain_mm).toFixed(1)} mm` });

      const maxConf = Math.max(0, ...(kite.boxes || []).map(box => Number(box.conf) || 0));
      if (maxConf) facts.push({ label: 'Confiance', value: `${Math.round(maxConf * 100)} %` });

      return facts.filter(fact => fact.value);
    });

    function openDetection(kite) {
      modalItem.value = kite;
      track('Open kite detection', {
        camera: kite?.camera || cameraFromUrl(kite?.image_url),
        kites: kite?.kites_detected || kite?.boxes?.length || 0,
        timestamp: kite?.timestamp || '',
      });
    }

    // Les archives Skaping purgent les caméras maritime/mer au bout de ~40 jours.
    // Une détection dont l'image a disparu est retirée de la liste.
    function dropDetection(kite) {
      if (modalItem.value === kite) modalItem.value = null;
      detections.value = detections.value.filter(item => item !== kite);
      track('Hide unavailable detection', {
        camera: kite?.camera || '',
        timestamp: kite?.timestamp || '',
      });
    }

    function openLive(cam) {
      modalItem.value = {
        type: 'live',
        slug: cam.slug,
        label: cam.label,
        live_url: `https://www.skaping.com/berck-sur-mer/${cam.slug}`,
      };
      track('Open live webcam', {
        camera: cam.slug,
        label: cam.label,
      });
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
      orderedCams,
      detections,
      modalItem,
      modalFacts,
      liveTimeLabel,
      fallbackCam,
      refreshAll,
      kiteCamLabel,
      boxStyle,
      formatDateTime,
      openDetection,
      dropDetection,
      openLive,
      closeModal,
    };
  },
}).mount('#spotApp');
