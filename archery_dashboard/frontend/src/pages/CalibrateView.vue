<template>
  <div class="calPage">
    <header class="top">
      <div class="title">Calibration</div>
      <a class="link" href="/">← Back</a>
    </header>

    <div class="hud">
      <div class="pill">Samples: {{ sampleCount }}/{{ totalTarget }}</div>
      <div class="pill" :class="pending ? 'warn' : ''">
        {{ done ? "Calibration complete ✅" : (pending ? "Shot detected → click the real hit" : "Waiting for shot…") }}
      </div>
      <button class="btn" @click="startCal">Restart calibration</button>
      <button v-if="done" class="btn" @click="computeCal">Compute calibration</button>
    </div>

    <div v-if="fit || fitErr" class="fitBox">
        <div v-if="fitErr" class="fitErr">Compute error: {{ fitErr }}</div>
        <div v-else class="fitOk">
            <div class="fitTitle">Fit results</div>
            <div class="fitRow">Mean error: <b>{{ fit.mean_error_cm.toFixed(2) }} cm</b></div>
            <div class="fitRow">Max error: <b>{{ fit.max_error_cm.toFixed(2) }} cm</b></div>
            <div class="fitRow">Samples used: <b>{{ fit.n }}</b></div>
        </div>
    </div>

    <div v-if="paused" class="pauseOverlay">
      <div class="pauseCard">
        <div class="pauseTitle">Pause</div>
        <div class="pauseText">Go pull your arrows, then come back.</div>
        <button class="btn" @click="resumeSet">Resume</button>
      </div>
    </div>

    <div v-if="showResults && fit" class="pauseOverlay">
      <div class="resultsCard">
        <div class="resultsTitle">Calibration Results</div>

        <div class="resultsGrid">
          <div class="resultMetric">
            <div class="metricLabel">Mean Error</div>
            <div class="metricValue" :class="fit.mean_error_cm < 2 ? 'good' : fit.mean_error_cm < 5 ? 'warn' : 'bad'">
              {{ fit.mean_error_cm.toFixed(2) }} cm
            </div>
          </div>

          <div class="resultMetric">
            <div class="metricLabel">Max Error</div>
            <div class="metricValue" :class="fit.max_error_cm < 3 ? 'good' : fit.max_error_cm < 5 ? 'warn' : 'bad'">
              {{ fit.max_error_cm.toFixed(2) }} cm
            </div>
          </div>

          <div class="resultMetric">
            <div class="metricLabel">Accuracy</div>
            <div class="metricValue" :class="accuracyPct > 90 ? 'good' : accuracyPct > 70 ? 'warn' : 'bad'">
              {{ accuracyPct.toFixed(1) }}%
            </div>
          </div>

          <div class="resultMetric">
            <div class="metricLabel">Samples</div>
            <div class="metricValue">{{ fit.n }}</div>
          </div>
        </div>

        <div class="resultsText">
          <div v-if="fit.mean_error_cm < 2 && fit.max_error_cm < 3">
            ✅ Excellent calibration! Ready for accurate shooting.
          </div>
          <div v-else-if="fit.mean_error_cm < 5 && fit.max_error_cm < 8">
            ⚠️ Good calibration, but could be improved with more samples or more accurate clicks.
          </div>
          <div v-else>
            ❌ Poor calibration. Consider restarting and clicking more accurately.
          </div>
        </div>

        <div class="resultsActions">
          <button class="btn btnPrimary" @click="applyAndGo">Apply & Start Shooting</button>
          <button class="btn btnSecondary" @click="restartFromResults">Restart Calibration</button>
        </div>
      </div>
    </div>

    <div class="targetWrap">
      <div
        v-if="Object.keys(rings).length"
        class="targetClick"
        @click="onTargetClick"
      >
        <TargetView :shots="pendingDot" :rings="rings" />
      </div>
      <div v-else class="loading">Loading target…</div>
    </div>

    <div class="info">
      <div v-if="pending" class="hint">
        Click on the target where the arrow actually hit.
      </div>
      <div v-else class="hint muted">
        Shoot an arrow (or UDP-inject) to create a pending calibration shot.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from "vue"
import TargetView from "../components/TargetView.vue"

const host = location.hostname
const API = `http://${host}:8000`

const rings = ref({})
const pending = ref(null)
const sampleCount = ref(0)
const perSet = 3
const inSet = ref(0)
const paused = ref(false)
const fit = ref(null)
const fitErr = ref(null)
const showResults = ref(false)

const totalTarget = 30
const done = computed(() => sampleCount.value >= totalTarget)

// Calculate accuracy percentage from error
const accuracyPct = computed(() => {
  if (!fit.value || !fit.value.mean_error_cm) return 0
  // Assume ring 1 radius is ~20cm (200mm), so perfect accuracy at center
  // Mean error of 2cm = 90% accuracy, 1cm = 95%, 0.5cm = 97.5%
  const maxErrorForCalc = 10 // cm - beyond this is 0%
  const pct = Math.max(0, 100 - (fit.value.mean_error_cm / maxErrorForCalc * 100))
  return Math.min(100, pct)
})

// show a faint dot for the detected (uncalibrated) location so you can compare
const pendingDot = ref([])

async function loadRings() {
  const cfg = await fetch(`${API}/api/config`).then(r => r.json())
  const out = {}
  for (const [k, v] of Object.entries(cfg.RINGS_M)) out[String(k)] = v
  rings.value = out
}

function outerRadiusM() {
  // Get ring 1 radius (outermost ring for standard target)
  // This matches TargetView's maxR calculation for consistent SCALE
  const ring1 = rings.value?.["1"]
  const base = (typeof ring1 === "number") ? ring1 : 0.25

  // Also consider any pending dot position so scale matches display
  let maxShot = 0
  for (const s of pendingDot.value) {
    if (typeof s.r === "number" && s.r > maxShot) maxShot = s.r
  }

  return Math.max(base, maxShot)
}

async function startCal() {
  await fetch(`${API}/api/calibration/start`, { method: "POST" })
  pending.value = null
  pendingDot.value = []
  sampleCount.value = 0
  inSet.value = 0
  paused.value = false
}

async function resumeSet() {
    await fetch(`${API}/api/calibration/resume`, { method: "POST" })
    paused.value = false
    inSet.value = 0
    pending.value = null
    pendingDot.value = []
}

async function computeCal() {
  fitErr.value = null
  fit.value = null

  try {
    // 1) compute only (don't apply yet)
    const r1 = await fetch(`${API}/api/calibration/compute`, { method: "POST" })
    const j1 = await r1.json()
    if (!j1.ok) {
      fitErr.value = j1.error || "compute failed"
      return
    }
    fit.value = j1

    // 2) show results overlay
    showResults.value = true
  } catch (e) {
    fitErr.value = String(e)
  }
}

async function applyAndGo() {
  try {
    // Apply calibration (save + stop calibration + set mode shooting)
    const r2 = await fetch(`${API}/api/calibration/apply`, { method: "POST" })
    const j2 = await r2.json()
    if (!j2.ok) {
      fitErr.value = j2.error || "apply failed"
      return
    }

    // Redirect to dashboard
    window.location.href = "/"
  } catch (e) {
    fitErr.value = String(e)
  }
}

async function restartFromResults() {
  showResults.value = false
  await startCal()
}

function onTargetClick(ev) {
  if (done.value) return
  if (paused.value) return
  if (!pending.value) return

  // IMPORTANT: use the canvas rect, not the wrapper div rect
  const canvasEl = ev.currentTarget.querySelector("canvas")
  if (!canvasEl) return

  const rect = canvasEl.getBoundingClientRect()

  // Map CSS pixels -> canvas pixels (because canvas internal units are fixed at 520)
  const SIZE = 520
  const CX = SIZE / 2
  const CY = SIZE / 2
  const PAD = 14
  const scaleX = SIZE / rect.width
  const scaleY = SIZE / rect.height

  const xCanvas = (ev.clientX - rect.left) * scaleX
  const yCanvas = (ev.clientY - rect.top) * scaleY

  const px = xCanvas - CX
  const py = yCanvas - CY

  // This must match TargetView's effective drawing radius
  const radiusCanvas = (CX - PAD)

  // Calculate SCALE to match TargetView's coordinate system
  // TargetView uses: SCALE = (CX - PAD) / maxR
  // This ensures calibration and display use identical transformations
  const Rm = outerRadiusM()
  const SCALE = radiusCanvas / Rm  // pixels per meter

  // Convert canvas pixels directly to meters
  const x_gt = px / SCALE
  const y_gt = -py / SCALE  // Invert Y (canvas Y down, target Y up)

  // Enhanced debug logging
  console.log("CAL_CLICK", {
    rect: { w: rect.width, h: rect.height },
    canvas: { x: xCanvas, y: yCanvas },
    relative: { px, py },
    radiusCanvas,
    Rm,
    SCALE,
    x_gt,
    y_gt
  })

  fetch(`${API}/api/calibration/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x_gt, y_gt }),
  }).then(async (r) => {
    const j = await r.json()
    if (j.ok) {
      sampleCount.value = j.count
      pending.value = null
      pendingDot.value = []

      inSet.value += 1
      if (inSet.value >= perSet) {
        paused.value = true
        fetch(`${API}/api/calibration/pause`, { method: "POST" })
      }
    }
  })
}

onMounted(async () => {
  await loadRings()
  await startCal()

  // start calibration automatically if you want:
  // await startCal()

  const ws = new WebSocket(`ws://${host}:8000/ws`)
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)

    if (msg.type === "cal_pending") {
      if (done.value) return
      if (paused.value) return
      pending.value = msg.pending
      sampleCount.value = msg.count ?? sampleCount.value

      // show detected dot (uncalibrated) in the target as reference
      if (msg.pending && typeof msg.pending.x === "number" && typeof msg.pending.y === "number") {
        pendingDot.value = [{
          ts: msg.pending.ts || Date.now()/1000,
          x: msg.pending.x,
          y: msg.pending.y,
          r: msg.pending.r || 0,
          score: 0
        }]
      }
    }
  }
})
</script>

<style>
.calPage{
  min-height:100vh;
  background:#0b0f17;
  color:#e7ecf5;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
  padding: 18px;
}
.top{ display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
.title{ font-size:20px; font-weight:800; }
.link{ color:#9ad; text-decoration:none; }

.hud{
  display:flex;
  gap:10px;
  align-items:center;
  flex-wrap:wrap;
  margin-bottom: 14px;
}
.pill{
  padding: 8px 10px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.06);
  font-weight: 700;
  font-size: 12px;
}
.pill.warn{
  border-color: rgba(255,200,80,0.22);
  background: rgba(255,200,80,0.10);
}

.btn{
  padding: 8px 10px;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.14);
  background: rgba(255,255,255,0.08);
  color: #e7ecf5;
  font-weight: 800;
  cursor: pointer;
}

.targetWrap{ display:flex; justify-content:center; margin: 10px 0 14px; }
.targetClick{ width: min(520px, 92vw); }

.loading{ opacity:0.75; padding: 20px; }

.info{ text-align:center; }
.hint{ opacity:0.9; font-weight:700; }
.hint.muted{ opacity:0.65; font-weight:600; }

.pauseOverlay{
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.pauseCard{
  width: min(420px, 92vw);
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 18px;
  padding: 16px;
  text-align: center;
}
.pauseTitle{ font-size: 18px; font-weight: 900; margin-bottom: 6px; }
.pauseText{ opacity: 0.85; margin-bottom: 12px; }

.fitBox{
  margin: 10px 0 14px;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.05);
}
.fitTitle{ font-weight: 900; margin-bottom: 6px; }
.fitRow{ font-size: 13px; opacity: 0.9; margin: 2px 0; }
.fitErr{ color: rgba(255,120,120,0.95); font-weight: 800; }

/* Results overlay */
.resultsCard {
  width: min(580px, 92vw);
  background: rgba(20,25,35,0.98);
  border: 1px solid rgba(255,255,255,0.2);
  border-radius: 20px;
  padding: 24px;
  text-align: center;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
}

.resultsTitle {
  font-size: 24px;
  font-weight: 900;
  margin-bottom: 20px;
  color: #fff;
}

.resultsGrid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.resultMetric {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 12px;
  padding: 16px;
}

.metricLabel {
  font-size: 12px;
  opacity: 0.7;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.metricValue {
  font-size: 28px;
  font-weight: 900;
  color: #e7ecf5;
}

.metricValue.good { color: rgba(60, 220, 120, 0.95); }
.metricValue.warn { color: rgba(255, 200, 60, 0.95); }
.metricValue.bad { color: rgba(255, 90, 90, 0.95); }

.resultsText {
  background: rgba(255,255,255,0.06);
  border-radius: 12px;
  padding: 14px;
  margin-bottom: 20px;
  font-size: 14px;
  line-height: 1.5;
  font-weight: 600;
}

.resultsActions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

.btnPrimary {
  background: rgba(60, 220, 120, 0.2);
  border: 2px solid rgba(60, 220, 120, 0.5);
  color: rgba(60, 220, 120, 1);
  font-weight: 900;
  padding: 12px 24px;
  font-size: 14px;
}

.btnPrimary:hover {
  background: rgba(60, 220, 120, 0.3);
  border-color: rgba(60, 220, 120, 0.7);
}

.btnSecondary {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.2);
  color: #e7ecf5;
  padding: 12px 24px;
  font-size: 14px;
}

.btnSecondary:hover {
  background: rgba(255,255,255,0.12);
}

@media (max-width: 600px) {
  .resultsGrid {
    grid-template-columns: 1fr;
  }
}
</style>