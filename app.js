const { createApp, ref, computed, onMounted, onUnmounted, nextTick } = Vue;

// ── constantes ────────────────────────────────────────────────────────────────
const LAT = 50.4113, LNG = 1.5676;

const WEBCAMS = [
  { slug: 'baie-d-authie',    label: "Baie d'Authie" },
  { slug: 'entonnoir',        label: 'Entonnoir' },
  { slug: 'maritime',         label: 'Maritime' },
  { slug: 'mer',              label: 'La Mer' },
  { slug: 'poste-de-secours', label: 'Poste de Secours' },
  { slug: 'eole',             label: 'Éole' },
];

const KITE_GUIDE = [
  { min: 0,  max: 12,       go: false,    label: 'Pas de vent',   size: null,     color: '#475569' },
  { min: 12, max: 20,       go: 'light',  label: 'Vent léger',    size: '17–21m', color: '#22c55e' },
  { min: 20, max: 28,       go: true,     label: 'Bon vent',      size: '12–16m', color: '#3b82f6' },
  { min: 28, max: 38,       go: 'best',   label: 'Sweet spot',    size: '9–12m',  color: '#06b6d4' },
  { min: 38, max: 50,       go: 'expert', label: 'Vent fort',     size: '7–9m',   color: '#f59e0b' },
  { min: 50, max: Infinity, go: false,    label: 'Dangereux',     size: null,     color: '#ef4444' },
];

// ── helpers ───────────────────────────────────────────────────────────────────
const DIRS16 = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSO','SO','OSO','O','ONO','NO','NNO'];

function dirLabel(deg) {
  if (deg == null) return '—';
  return DIRS16[Math.round(deg / 22.5) % 16];
}

function speedColor(kmh) {
  if (!kmh) return '#475569';
  const k = KITE_GUIDE.find(z => kmh >= z.min && kmh < z.max);
  return k ? k.color : '#ef4444';
}

function toKt(kmh) { return kmh != null ? Math.round(kmh / 1.852) : 0; }

function wmoIcon(code) {
  if (code === 0) return '☀️';
  if (code <= 2)  return '🌤️';
  if (code <= 3)  return '☁️';
  if (code <= 48) return '🌫️';
  if (code <= 57) return '🌦️';
  if (code <= 67) return '🌧️';
  if (code <= 77) return '❄️';
  if (code <= 82) return '🌦️';
  return '⛈️';
}

function snapUrl(slug, offset = 0) {
  const d = new Date(Date.now() - offset * 3_600_000);
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,'0');
  const day = String(d.getDate()).padStart(2,'0');
  const h = String(d.getHours()).padStart(2,'0');
  return `https://skaping.s3.gra.io.cloud.ovh.net/berck-sur-mer/${slug}/${y}/${m}/${day}/small/${h}-00.jpg`;
}

// ── prédiction marées (modèle harmonique simplifié Boulogne-sur-Mer) ──────────
// Constantes calibrées pour Boulogne (station de référence proche de Berck)
const TIDE_REF_HW_UTC = new Date('2026-04-23T06:00:00Z').getTime(); // PM référence
const M2_MS = 44712_000; // période M2 : 12h25min12s
const MSF_MS = 14.77 * 86_400_000; // cycle vives-eaux/mortes-eaux

function tideCoeff(t) {
  // 1 = vives-eaux (coefficient ~100), 0 = mortes-eaux (~20)
  const phase = ((t - TIDE_REF_HW_UTC) % MSF_MS + MSF_MS) % MSF_MS;
  return 0.5 + 0.5 * Math.cos(2 * Math.PI * phase / MSF_MS);
}

function tideHeight(t) {
  const coeff = tideCoeff(t);
  const hw = 6.5 + coeff * 2.5; // 6.5m mortes-eaux → 9.0m vives-eaux
  const lw = 3.5 - coeff * 2.5; // 3.5m → 1.0m
  const mid = (hw + lw) / 2;
  const range = (hw - lw) / 2;
  const phase = ((t - TIDE_REF_HW_UTC) % M2_MS + M2_MS) % M2_MS;
  return mid + range * Math.cos((phase / M2_MS) * 2 * Math.PI);
}

function nextTideExtremes(from = Date.now(), count = 4) {
  const step = 5 * 60_000; // 5 min
  const extremes = [];
  let prev = tideHeight(from - step), curr = tideHeight(from);
  for (let t = from; t < from + 3 * M2_MS; t += step) {
    const next = tideHeight(t + step);
    if (curr > prev && curr > next) {
      extremes.push({ t, h: curr, type: 'HW' });
    } else if (curr < prev && curr < next) {
      extremes.push({ t, h: curr, type: 'BM' });
    }
    if (extremes.length >= count) break;
    prev = curr; curr = next;
  }
  return extremes;
}

function fmtTime(ts, tz = 'Europe/Paris') {
  return new Date(ts).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: tz });
}

// ── vent de terre / mer (plage de Berck face à l'Ouest) ────────────────────────
// Berck : la mer est à l'OUEST → vent d'est = vent de terre = offshore = DANGER
function classifyWindOrigin(deg) {
  const d = ((deg % 360) + 360) % 360;
  if (d >= 315 || d < 45)
    return { label: 'Nord — cross-shore', description: 'Idéal pour kiter à Berck', icon: '✅', cls: 'q-ideal', offshore: false };
  if (d >= 45 && d < 70)
    return { label: 'NE — légèrement offshore', description: 'Prudence — ne pas s\'éloigner du bord', icon: '⚠️', cls: 'q-warn', offshore: 'partial' };
  if (d >= 70 && d < 170)
    return { label: 'Vent de TERRE (E/SE)', description: 'Offshore — session INTERDITE', icon: '🚨', cls: 'q-danger', offshore: true };
  if (d >= 170 && d < 200)
    return { label: 'Sud — cross-shore', description: 'Idéal pour kiter à Berck', icon: '✅', cls: 'q-ideal', offshore: false };
  if (d >= 200 && d < 260)
    return { label: 'SO — cross-onshore', description: 'Acceptable — attention aux vagues', icon: 'ℹ️', cls: 'q-ok', offshore: false };
  if (d >= 260 && d < 315)
    return { label: 'Vent de MER (O/NO)', description: 'Onshore — poussé vers la plage', icon: 'ℹ️', cls: 'q-ok', offshore: false };
  return { label: 'Variable', description: '', icon: '?', cls: '', offshore: false };
}

// ── carte vent Leaflet + leaflet-velocity ─────────────────────────────────────
let berckMap = null;
let berckVelocity = null;

// Grille autour de Berck : la1=nord → la2=sud, lo1=ouest → lo2=est
const MAP_LA1 = 51.5, MAP_LA2 = 49.5;
const MAP_LO1 = -0.5,  MAP_LO2 = 3.5;
const MAP_DLAT = 0.5,  MAP_DLNG = 0.5;

function buildBerckGrid() {
  const lats = [], lngs = [];
  for (let la = MAP_LA1; la >= MAP_LA2 - 0.001; la -= MAP_DLAT)
    lats.push(+la.toFixed(2));
  for (let lo = MAP_LO1; lo <= MAP_LO2 + 0.001; lo += MAP_DLNG)
    lngs.push(+lo.toFixed(2));
  return { lats, lngs };
}

function windToUV(speed, dir) {
  const rad = (dir * Math.PI) / 180;
  return { u: -speed * Math.sin(rad), v: -speed * Math.cos(rad) };
}

async function initWindMap() {
  const el = document.getElementById('berck-map');
  if (!el) return;

  // Créer la carte une seule fois
  if (!berckMap) {
    berckMap = L.map('berck-map', {
      center: [LAT, LNG],
      zoom: 8,
      zoomControl: false,
      attributionControl: false,
      dragging: true,
    });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png', {
      maxZoom: 14,
    }).addTo(berckMap);
    // Labels au-dessus des vecteurs
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png', {
      maxZoom: 14, pane: 'shadowPane',
    }).addTo(berckMap);
    // Marqueur Berck
    L.circleMarker([LAT, LNG], {
      radius: 7, color: '#06b6d4', fillColor: '#06b6d4',
      fillOpacity: 0.95, weight: 2,
    }).bindTooltip('Berck', {
      permanent: true, direction: 'right', className: 'berck-label',
    }).addTo(berckMap);
  }

  // Construire la grille
  const { lats, lngs } = buildBerckGrid();
  const ny = lats.length, nx = lngs.length;
  const latPairs = [], lngPairs = [];
  for (const la of lats) for (const lo of lngs) { latPairs.push(la); lngPairs.push(lo); }

  try {
    const url = `https://api.open-meteo.com/v1/forecast`
      + `?latitude=${latPairs.join(',')}`
      + `&longitude=${lngPairs.join(',')}`
      + `&hourly=wind_speed_10m,wind_direction_10m`
      + `&wind_speed_unit=ms&forecast_days=1&models=meteofrance_seamless&timezone=UTC`;

    const res = await fetch(url);
    if (!res.ok) throw new Error(`Grid ${res.status}`);
    const raw = await res.json();
    const pts = Array.isArray(raw) ? raw : [raw];

    // Index de l'heure courante
    const now = Date.now();
    const times = pts[0].hourly.time;
    let hIdx = times.findIndex(t => new Date(t).getTime() >= now);
    if (hIdx < 0) hIdx = 0;

    // Construire les tableaux U/V (ordre ligne-par-ligne, nord→sud)
    const uData = new Array(nx * ny).fill(0);
    const vData = new Array(nx * ny).fill(0);
    for (let i = 0; i < pts.length; i++) {
      const speed = pts[i].hourly.wind_speed_10m[hIdx] ?? 0;
      const dir   = pts[i].hourly.wind_direction_10m[hIdx] ?? 0;
      const { u, v } = windToUV(speed, dir);
      uData[i] = u; vData[i] = v;
    }

    // Retirer l'ancien calque
    if (berckVelocity) { berckMap.removeLayer(berckVelocity); berckVelocity = null; }

    const makeHdr = (pNum) => ({
      parameterUnit: 'm.s-1', parameterCategory: 2, parameterNumber: pNum,
      dx: MAP_DLNG, dy: MAP_DLAT,
      la1: MAP_LA1, lo1: MAP_LO1, la2: MAP_LA2, lo2: MAP_LO2,
      nx, ny, refTime: new Date(now).toISOString(),
    });

    berckVelocity = L.velocityLayer({
      displayValues: true,
      displayOptions: {
        velocityType: 'Vent',
        displayPosition: 'bottomleft',
        displayEmptyString: 'Pas de données',
        speedUnit: 'kt',
      },
      data: [
        { header: makeHdr(2), data: uData },
        { header: makeHdr(3), data: vData },
      ],
      maxVelocity: 15,
      velocityScale: 0.01,
      lineWidth: 1.8,
      particleAge: 64,
      colorScale: ['#4d7a96', '#06b6d4', '#22c55e', '#f59e0b', '#ef4444'],
    }).addTo(berckMap);

    // Force Leaflet à recalculer la taille une fois le layout stabilisé
    setTimeout(() => {
      berckMap.invalidateSize();
      berckMap.setView([LAT, LNG], 8, { animate: false });
    }, 100);
  } catch (e) {
    console.warn('Wind map error:', e);
  }
}



// ── composant principal ───────────────────────────────────────────────────────
createApp({
  setup() {
    const loading = ref(false);
    const error   = ref(null);
    const current = ref(null);
    const hourly  = ref(null);
    const daily   = ref(null);
    const waveData = ref(null);
    const lastUpdate = ref(null);
    const activeCam  = ref(null);
    const tides   = ref([]);
    const tideMs  = ref(Date.now());
    const heroBg  = ref(null);
    const kiteDetection = ref(null);
    const lastKite = ref(null);
    let chart = null, refreshTimer = null, tideTimer = null;

    // ── Détection kites YOLO (mise à jour quotidienne via GitHub Actions) ────
    async function fetchKiteStatus() {
      try {
        const r = await fetch('/kite_status.json?t=' + Math.floor(Date.now() / 3_600_000));
        if (!r.ok) return;
        const data = await r.json();
        if (data.last_kite) lastKite.value = data.last_kite;
        if (!data.timestamp) return;
        const ts = new Date(data.timestamp);
        const today = new Date();
        if (ts.toDateString() === today.toDateString()) {
          kiteDetection.value = data;
        }
      } catch {}
    }

    // ── Fond hero = dernière image webcam Éole disponible ────────────────────
    function loadHeroBg() {
      const tryOffset = (offset) => new Promise((resolve, reject) => {
        const img = new Image();
        img.onload  = () => resolve(snapUrl('eole', offset));
        img.onerror = () => reject();
        img.src = snapUrl('eole', offset);
      });
      // Essaie l'heure actuelle, puis -1h, -2h
      tryOffset(0)
        .catch(() => tryOffset(1))
        .catch(() => tryOffset(2))
        .then(url => { heroBg.value = url; })
        .catch(() => {});
    }

    // ── marée en temps réel ───────────────────────────────────────────────
    const tideNow = computed(() => {
      const h = tideHeight(tideMs.value);
      const MIN_H = 0.5, MAX_H = 9.5;
      const pct = Math.max(0, Math.min(1, (h - MIN_H) / (MAX_H - MIN_H)));
      const raw = tideCoeff(tideMs.value); // 0=mortes → 1=vives
      const coeff = Math.round(20 + raw * 100);
      const coeffLabel = coeff >= 80 ? 'Vives-eaux' : coeff >= 55 ? 'Moyennes eaux' : 'Mortes-eaux';
      const coeffCls   = coeff >= 80 ? 'coeff-high' : coeff >= 55 ? 'coeff-mid' : 'coeff-low';
      return { h: +h.toFixed(1), pct, coeff, coeffLabel, coeffCls };
    });

    // ── computed ──────────────────────────────────────────────────────────────

    // Direction du vent — en premier car utilisé par heroClass/goStatus
    const windOrigin = computed(() => classifyWindOrigin(current.value?.wind_direction_10m ?? 0));

    // ── Température de l'eau ─────────────────────────────────────────────────
    const seaTemp = computed(() => {
      const t = waveData.value?.current?.sea_surface_temperature;
      return t != null ? Math.round(t) : null;
    });

    // ── Hauteur des vagues (Marine API) ──────────────────────────────────────
    const waveInfo = computed(() => {
      const empty = (h = '—') => ({
        h, period: '—', label: 'Indisponible', cls: '', bars: [25,40,55,40,30,45,35],
      });
      if (!waveData.value?.hourly) return empty();
      const now = Date.now();
      const times = waveData.value.hourly.time;
      const idx = times.findIndex(t => new Date(t).getTime() >= now);
      const i = idx < 0 ? 0 : idx;
      const h = waveData.value.hourly.wave_height[i];
      const period = waveData.value.hourly.wave_period?.[i];
      if (h == null) return empty();
      let label, cls;
      if      (h < 0.3) { label = 'Mer plate';         cls = 'q-ideal'; }
      else if (h < 0.7) { label = 'Légèrement agitée'; cls = 'q-go';    }
      else if (h < 1.2) { label = 'Vagues modérées';   cls = 'q-ok';    }
      else if (h < 2.0) { label = 'Mer agitée';         cls = 'q-warn';  }
      else              { label = 'Grosse mer';          cls = 'q-danger';}
      // Profil visuel : 7 barres dont la hauteur suit le ratio réel
      const pct = Math.min(1, h / 2.5);
      const bars = [0.45, 0.70, 0.95, 0.80, 0.55, 0.85, 0.60]
        .map(f => Math.max(8, Math.round(f * pct * 100)));
      return { h: h.toFixed(1), period: period != null ? Math.round(period) : '—', label, cls, bars };
    });

    const kiteInfo = computed(() => {
      const s = current.value?.wind_speed_10m ?? 0;
      return KITE_GUIDE.find(z => s >= z.min && s < z.max) || KITE_GUIDE[0];
    });

    const heroClass = computed(() => {
      if (windOrigin.value.offshore === true) return 'hero-nogo';
      const go = kiteInfo.value.go;
      if (go === 'best') return 'hero-best';
      if (go === true || go === 'light') return 'hero-go';
      if (go === 'expert') return 'hero-expert';
      return 'hero-nogo';
    });

    const goStatus = computed(() => {
      if (windOrigin.value.offshore === true)
        return { emoji: '🚨', label: 'Vent de terre — NO GO !', cls: 'badge-danger' };
      const go = kiteInfo.value.go;
      if (go === 'best')   return { emoji: '🟢', label: 'Sweet spot — GO !', cls: 'badge-best' };
      if (go === true)     return { emoji: '🟢', label: 'GO — Bon vent',      cls: 'badge-go' };
      if (go === 'light')  return { emoji: '🟡', label: 'Vent léger — light kite', cls: 'badge-light' };
      if (go === 'expert') return { emoji: '🟠', label: 'Experts seulement',  cls: 'badge-expert' };
      const s = current.value?.wind_speed_10m ?? 0;
      return s < 5
        ? { emoji: '⚫', label: 'Pas de vent — NO GO', cls: 'badge-nogo' }
        : { emoji: '🔴', label: 'Dangereux — NO GO',   cls: 'badge-danger' };
    });

    const goBadgeClass = computed(() => goStatus.value.cls);

    // Helper : date locale YYYY-MM-DD sans bug UTC
    function localDateStr(d) {
      return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    }

    // Prochains jours (résumé quotidien)
    const nextDays = computed(() => {
      if (!daily.value) return [];
      const todayStr = localDateStr(new Date());
      return daily.value.time.slice(0, 8).map((t, i) => {
        const dA = new Date(todayStr + 'T00:00:00');
        const dB = new Date(t + 'T00:00:00');
        const diff = Math.round((dB - dA) / 86400000);
        if (diff < 0) return null;
        const speed = daily.value.wind_speed_10m_max[i];
        if (speed == null) return null;
        const dir = daily.value.wind_direction_10m_dominant[i];
        const wo  = dir != null ? classifyWindOrigin(dir) : { offshore: false };
        const d = new Date(t + 'T00:00:00');
        const label = diff === 0 ? 'Auj.' : diff === 1 ? 'Dem.'
          : d.toLocaleDateString('fr-FR', { weekday: 'short' });
        return {
          label,
          kt: toKt(speed),
          dir: dir != null ? dirLabel(dir) : '—',
          offshore: wo.offshore,
          color: speedColor(speed),
        };
      }).filter(Boolean);
    });

    // Météo à venir (remplace rainInfo)
    const weatherForecast = computed(() => {
      if (!hourly.value?.temperature_2m) return null;
      const now = Date.now();
      const times = hourly.value.time;
      let idx = times.findIndex(t => new Date(t + ':00').getTime() >= now);
      if (idx < 0) idx = 0;
      const slots = [];
      for (let i = idx; i < Math.min(idx + 6, times.length); i++) {
        const code = hourly.value.weather_code?.[i] ?? 0;
        const temp = hourly.value.temperature_2m?.[i];
        const prob = hourly.value.precipitation_probability?.[i] ?? 0;
        slots.push({
          hour: times[i].slice(11, 16),
          temp: temp != null ? Math.round(temp) : '—',
          prob,
          icon: wmoIcon(code),
        });
      }
      const currTemp = slots[0]?.temp ?? '—';
      return { slots, currTemp };
    });

    // Sélecteur de jour pour le détail horaire
    const selectedDayIdx = ref(0);

    const allDaysHourly = computed(() => {
      if (!hourly.value) return [];
      const now = Date.now();
      const todayStr = localDateStr(new Date());

      // Grouper les créneaux par date API (déjà en heure Paris, ex: "2026-04-25T14:00")
      const groups = {};
      hourly.value.time.forEach((t, i) => {
        const ds = t.slice(0, 10);
        if (!groups[ds]) groups[ds] = [];
        const speed = hourly.value.wind_speed_10m[i];
        if (speed == null) return; // pas de données AROME pour ce créneau
        groups[ds].push({
          hour:   t.slice(11, 16),
          speed,
          kt:     toKt(speed),
          gkt:    toKt(hourly.value.wind_gusts_10m[i]),
          gust:   hourly.value.wind_gusts_10m[i],
          dir:    hourly.value.wind_direction_10m[i],
          isPast: new Date(t + ':00').getTime() < now - 3_600_000,
        });
      });

      const todayDate = new Date(todayStr + 'T00:00:00');
      return Object.keys(groups).sort().slice(0, 7).map(ds => {
        const d   = new Date(ds + 'T00:00:00');
        const diff = Math.round((d - todayDate) / 86400000);
        const tabLabel = diff === 0 ? "Auj." : diff === 1 ? "Dem."
          : d.toLocaleDateString('fr-FR', { weekday: 'short' });
        return { tabLabel, dateStr: ds, slots: groups[ds] };
      });
    });

    const selectedDayHourly = computed(() => allDaysHourly.value[selectedDayIdx.value] ?? null);

    const timeAgo = computed(() => {
      if (!lastUpdate.value) return '';
      const diff = Math.round((Date.now() - lastUpdate.value) / 60000);
      return diff < 1 ? "à l'instant" : `${diff} min`;
    });

    // ── chart ─────────────────────────────────────────────────────────────────
    function drawChart() {
      const ctx = document.getElementById('windChart');
      if (!ctx || !hourly.value) return;

      const now = new Date();
      const startIdx = hourly.value.time.findIndex(t => new Date(t) >= now);
      const slice = arr => arr.slice(startIdx, startIdx + 25);

      const labels = slice(hourly.value.time).map(t =>
        new Date(t).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
      );
      const speeds = slice(hourly.value.wind_speed_10m).map(toKt);
      const gusts  = slice(hourly.value.wind_gusts_10m).map(toKt);

      if (chart) chart.destroy();

      const grad = ctx.getContext('2d').createLinearGradient(0, 0, 0, 220);
      grad.addColorStop(0,   '#06b6d466');
      grad.addColorStop(0.7, '#06b6d411');
      grad.addColorStop(1,   '#06b6d400');

      chart = new Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: 'Vent', data: speeds, borderColor: '#06b6d4', backgroundColor: grad, borderWidth: 2.5, pointRadius: 0, fill: true, tension: 0.4 },
            { label: 'Rafales', data: gusts, borderColor: '#f59e0b', backgroundColor: 'transparent', borderWidth: 1.5, borderDash: [5,4], pointRadius: 0, fill: false, tension: 0.4 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { intersect: false, mode: 'index' },
          plugins: {
            legend: { labels: { color: '#94a3b8', font: { size: 11 }, usePointStyle: true, boxWidth: 10 } },
            tooltip: { backgroundColor: '#0d2033', borderColor: '#1a3a52', borderWidth: 1, titleColor: '#e2f0ff', bodyColor: '#94a3b8', padding: 8 },
          },
          scales: {
            x: { ticks: { color: '#475569', maxRotation: 0, autoSkip: true, maxTicksLimit: 6, font: { size: 11 } }, grid: { color: '#1a3a5220' } },
            y: { min: 0, ticks: { color: '#475569', callback: v => v + ' kt', font: { size: 11 } }, grid: { color: '#1a3a5220' } },
          },
        },
        plugins: [{
          id: 'kiteZone',
          beforeDraw({ ctx, chartArea, scales }) {
            if (!chartArea) return;
            ctx.save();
            ctx.fillStyle = '#22c55e0a';
            const y1 = scales.y.getPixelForValue(toKt(15));
            const y2 = scales.y.getPixelForValue(toKt(40));
            ctx.fillRect(chartArea.left, y2, chartArea.right - chartArea.left, y1 - y2);
            ctx.restore();
          },
        }],
      });
    }

    // ── fetch data ────────────────────────────────────────────────────────────
    async function fetchAll() {
      loading.value = true;
      error.value = null;
      try {
        const windUrl = `https://api.open-meteo.com/v1/forecast`
          + `?latitude=${LAT}&longitude=${LNG}`
          + `&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m`
          + `&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,temperature_2m,weather_code,precipitation_probability`
          + `&daily=wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,precipitation_sum`
          + `&wind_speed_unit=kmh&forecast_days=8&models=meteofrance_seamless&timezone=Europe/Paris`;

        const marineUrl = `https://marine-api.open-meteo.com/v1/marine`
          + `?latitude=${LAT}&longitude=${LNG}`
          + `&current=sea_surface_temperature`
          + `&hourly=wave_height,wave_period`
          + `&timezone=Europe/Paris&forecast_days=3`;

        // Fetch vent + vagues en parallèle
        const [res, marineRes] = await Promise.all([
          fetch(windUrl),
          fetch(marineUrl).catch(() => null),
        ]);
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();

        current.value    = data.current;
        hourly.value     = data.hourly;
        daily.value      = data.daily;
        lastUpdate.value = new Date();

        if (marineRes?.ok) {
          waveData.value = await marineRes.json();
        }

        // Compute tides
        const extremes = nextTideExtremes(Date.now(), 4);
        tides.value = extremes.map(e => ({
          ...e,
          timeStr: fmtTime(e.t),
          hStr: e.h.toFixed(1),
        }));

        await nextTick();
        drawChart();
        setTimeout(() => initWindMap(), 150);
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    }

    function openCam(cam) { activeCam.value = cam; }

    // ── Viewer kite — zoom auto statique + modal plein écran ────────────────
    const lastKiteImg    = ref(null);
    const lastKiteCanvas = ref(null);
    const kiteModalOpen  = ref(false);
    const kiteModalCanvas = ref(null);

    function drawKiteBoxes() {
      const img    = lastKiteImg.value;
      const canvas = lastKiteCanvas.value;
      if (!img || !canvas || !lastKite.value?.boxes?.length) return;
      const rect = canvas.getBoundingClientRect();
      canvas.width  = rect.width  || 400;
      canvas.height = rect.height || 225;
      const ctx   = canvas.getContext('2d');
      const boxes = lastKite.value.boxes;
      const cw = canvas.width, ch = canvas.height;

      const minX  = Math.min(...boxes.map(b => b.x1)) * cw;
      const minY  = Math.min(...boxes.map(b => b.y1)) * ch;
      const maxX  = Math.max(...boxes.map(b => b.x2)) * cw;
      const maxY  = Math.max(...boxes.map(b => b.y2)) * ch;
      const cx    = (minX + maxX) / 2;
      const cy    = (minY + maxY) / 2;
      const areaW = Math.max(maxX - minX, 1);
      const areaH = Math.max(maxY - minY, 1);
      const scale = Math.min(cw * 0.35 / areaW, ch * 0.35 / areaH, 10);
      const tx = cw / 2 - cx * scale;
      const ty = ch / 2 - cy * scale;

      ctx.save();
      ctx.translate(tx, ty);
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, cw, ch);
      ctx.strokeStyle = '#22c55e';
      ctx.lineWidth   = 1.5 / scale;
      ctx.shadowColor = 'rgba(0,0,0,0.55)';
      ctx.shadowBlur  = 3 / scale;
      for (const b of boxes) {
        ctx.strokeRect(b.x1 * cw, b.y1 * ch, (b.x2 - b.x1) * cw, (b.y2 - b.y1) * ch);
      }
      ctx.restore();
    }

    async function openKiteModal() {
      kiteModalOpen.value = true;
      await nextTick();
      const canvas = kiteModalCanvas.value;
      const img    = lastKiteImg.value;
      if (!canvas || !img) return;
      canvas.width  = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
      const cw = canvas.width, ch = canvas.height;
      const iw = img.naturalWidth, ih = img.naturalHeight;
      const scale = Math.min(cw / iw, ch / ih);
      const sw = iw * scale, sh = ih * scale;
      const ox = (cw - sw) / 2, oy = (ch - sh) / 2;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, cw, ch);
      ctx.drawImage(img, ox, oy, sw, sh);
      if (lastKite.value?.boxes?.length) {
        ctx.strokeStyle = '#22c55e';
        ctx.lineWidth   = 2;
        ctx.shadowColor = 'rgba(0,0,0,0.6)';
        ctx.shadowBlur  = 4;
        ctx.fillStyle   = '#22c55e';
        ctx.font        = '600 13px DM Sans, sans-serif';
        for (const b of lastKite.value.boxes) {
          const x = ox + b.x1 * sw, y = oy + b.y1 * sh;
          const w = (b.x2 - b.x1) * sw, h = (b.y2 - b.y1) * sh;
          ctx.strokeRect(x, y, w, h);
          ctx.shadowBlur = 0;
          ctx.fillText(`${Math.round(b.conf * 100)}%`, x + 3, y - 5);
          ctx.shadowBlur = 4;
        }
      }
    }

    function closeKiteModal() { kiteModalOpen.value = false; }

    // ── Tableau prévisions Windguru ───────────────────────────────────────────
    const wgForecast = computed(() => {
      if (!hourly.value) return null;
      const now      = Date.now();
      const todayStr = localDateStr(new Date());
      const KEEP     = new Set([0, 3, 6, 9, 12, 15, 18, 21]);
      const byDate   = {};

      hourly.value.time.forEach((t, i) => {
        const h = parseInt(t.slice(11, 13));
        if (!KEEP.has(h)) return;
        const spd = hourly.value.wind_speed_10m[i];
        if (spd == null) return;
        const ds = t.slice(0, 10);
        if (!byDate[ds]) byDate[ds] = [];
        byDate[ds].push({
          hh:     t.slice(11, 13),
          kt:     toKt(spd),
          spd,
          gkt:    toKt(hourly.value.wind_gusts_10m[i] ?? 0),
          dir:    Math.round(hourly.value.wind_direction_10m[i] ?? 0),
          temp:   Math.round(hourly.value.temperature_2m[i] ?? 0),
          isPast: new Date(t + ':00').getTime() < now,
        });
      });

      const today = new Date(todayStr + 'T00:00:00');
      return Object.keys(byDate).sort().slice(0, 7).map(ds => {
        const d    = new Date(ds + 'T00:00:00');
        const diff = Math.round((d - today) / 86400000);
        return {
          ds,
          label:   diff === 0 ? 'Auj.' : diff === 1 ? 'Dem.'
                 : d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' }),
          isToday: diff === 0,
          slots:   byDate[ds],
        };
      });
    });

    function wgWindBg(kt) {
      if (kt <  5) return 'transparent';
      if (kt < 10) return '#bfdbfe';
      if (kt < 15) return '#3b82f6';
      if (kt < 20) return '#22c55e';
      if (kt < 25) return '#06b6d4';
      if (kt < 30) return '#f59e0b';
      if (kt < 35) return '#f97316';
      return '#ef4444';
    }
    function wgWindFg(kt) { return kt < 10 ? 'var(--text)' : '#fff'; }
    function wgIsOffshore(dir) {
      const d = ((dir % 360) + 360) % 360;
      return d > 45 && d < 170;
    }

    onMounted(() => {
      fetchAll();
      loadHeroBg();
      fetchKiteStatus();
      refreshTimer = setInterval(fetchAll, 10 * 60_000);
      tideTimer    = setInterval(() => { tideMs.value = Date.now(); }, 60_000);
    });
    onUnmounted(() => { clearInterval(refreshTimer); clearInterval(tideTimer); });

    // ── Partage conditions ────────────────────────────────────────────────────
    const shareToast = ref(false);
    function shareConditions() {
      if (!current.value) return;
      const c = current.value;
      const kt  = toKt(c.wind_speed_10m);
      const gkt = toKt(c.wind_gusts_10m);
      const dir = dirLabel(c.wind_direction_10m);
      const now = new Date();
      const jours = ['dimanche','lundi','mardi','mercredi','jeudi','vendredi','samedi'];
      const mois  = ['janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre'];
      const date  = `${jours[now.getDay()]} ${now.getDate()} ${mois[now.getMonth()]}`;
      const wh = waveInfo.value.h !== '—' ? `\n🌊 Vagues ${waveInfo.value.h}m` : '';
      const tide = tides.value.length ? (() => {
        const pm = tides.value.find(t => t.type === 'HW');
        const bm = tides.value.find(t => t.type === 'LW');
        const parts = [];
        if (pm) parts.push(`PM ${pm.timeStr} · ${pm.hStr}m`);
        if (bm) parts.push(`BM ${bm.timeStr} · ${bm.hStr}m`);
        return parts.length ? `\n🕐 ${parts.join(' | ')}` : '';
      })() : '';
      const temp = `\n🌡️ ${Math.round(c.temperature_2m)}°C`;
      const text = `Berck-sur-Mer — ${date}\n💨 ${dir} · ${kt} nœuds · rafales ${gkt}${wh}${temp}${tide}\n\nhttp://berck.info`;

      if (navigator.share) {
        navigator.share({ text }).catch(() => {});
      } else {
        navigator.clipboard.writeText(text).then(() => {
          shareToast.value = true;
          setTimeout(() => { shareToast.value = false; }, 2500);
        });
      }
    }

    return {
      loading, error, current, hourly, daily, lastUpdate, activeCam, tides,
      WEBCAMS, heroClass, heroBg, kiteDetection, lastKite, lastKiteImg, lastKiteCanvas,
      kiteModalOpen, kiteModalCanvas, drawKiteBoxes, openKiteModal, closeKiteModal,
      nextDays, weatherForecast, windOrigin, tideNow, waveInfo, seaTemp,
      wgForecast, wgWindBg, wgWindFg, wgIsOffshore,
      timeAgo, shareToast,
      fetchAll, dirLabel, speedColor, snapUrl, openCam, toKt, shareConditions,
    };
  },
}).mount('#app');
