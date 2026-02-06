<template>
  <div class="calPage">
    <header class="top">
      <div class="title">Calibration</div>
      <a class="link" href="/">← Back</a>
    </header>

    <div class="hud">
      <div class="pill">Samples: {{ sampleCount }}</div>
      <div class="pill" :class="pending ? 'warn' : ''">
        {{ pending ? "Shot detected → click the real hit" : "Waiting for shot…" }}
      </div>
      <button class="btn" @click="startCal">Restart calibration</button>
      <button v-if="fit" class="btn btnApply" @click="showResultsOverlay">Review & Apply</button>
    </div>

    <div class="liveStatsBox">
      <div v-if="fitErr" class="fitErr">Error: {{ fitErr }}</div>
      <div v-else-if="sampleCount < 3" class="statsWaiting">
        <div class="statsTitle">Calibration Progress</div>
        <div class="statsHint">Need {{ 3 - sampleCount }} more sample{{ 3 - sampleCount === 1 ? '' : 's' }} to start computing accuracy</div>
        <div class="progressBar">
          <div class="progressFill" :style="{ width: (sampleCount / 3 * 100) + '%' }"></div>
        </div>
      </div>
      <div v-else-if="fit" class="statsLive">
        <div class="statsHeader">
          <div class="statsTitle">Live Accuracy</div>
          <div class="fitVersionBadge" :class="{ flash: fitJustApplied }">
            FIT v{{ fitVersion }} ACTIVE
          </div>
        </div>
        <div v-if="fitJustApplied" class="fitAppliedBanner">
          NEW FIT APPLIED - Next arrow will use updated calibration
        </div>
        <div class="statsGrid">
          <div class="statItem">
            <span class="statLabel">Mean Error</span>
            <span class="statValue" :class="errorClass(fit.mean_error_cm)">{{ fit.mean_error_cm.toFixed(2) }} cm</span>
          </div>
          <div class="statItem">
            <span class="statLabel">Max Error</span>
            <span class="statValue" :class="errorClass(fit.max_error_cm)">{{ fit.max_error_cm.toFixed(2) }} cm</span>
          </div>
          <div class="statItem">
            <span class="statLabel">Samples</span>
            <span class="statValue">{{ fit.n }}</span>
          </div>
        </div>
        <div class="qualityHint" :class="qualityClass">{{ qualityMessage }}</div>
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
        <TargetView :shots="pendingDot" :rings="rings" :disableAutoZoom="true" :size="1000" />
      </div>
      <div v-else class="loading">Loading target…</div>
      <div v-if="isPendingOutside" class="outsideWarning">
        ⚠ Arrow detected outside target — click where it actually landed
      </div>
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
const fitVersion = ref(0)
const fitJustApplied = ref(false)

// Calculate accuracy percentage from error
const accuracyPct = computed(() => {
  if (!fit.value || !fit.value.mean_error_cm) return 0
  const maxErrorForCalc = 10 // cm - beyond this is 0%
  const pct = Math.max(0, 100 - (fit.value.mean_error_cm / maxErrorForCalc * 100))
  return Math.min(100, pct)
})

// Quality message based on mean error
const qualityMessage = computed(() => {
  if (!fit.value) return ""
  const mean = fit.value.mean_error_cm
  if (mean < 1.5) return "Excellent accuracy - ready to apply!"
  if (mean < 3) return "Good accuracy - can apply or keep improving"
  if (mean < 5) return "Acceptable - more samples may help"
  return "Keep shooting for better accuracy"
})

const qualityClass = computed(() => {
  if (!fit.value) return ""
  const mean = fit.value.mean_error_cm
  if (mean < 1.5) return "excellent"
  if (mean < 3) return "good"
  if (mean < 5) return "acceptable"
  return "poor"
})

function errorClass(cm) {
  if (cm < 2) return "good"
  if (cm < 5) return "warn"
  return "bad"
}

// Check if the pending dot is outside the target (beyond ring 1)
const isPendingOutside = computed(() => {
  if (pendingDot.value.length === 0) return false
  const ring1 = rings.value?.["1"]
  if (typeof ring1 !== "number") return false
  const shot = pendingDot.value[0]
  return typeof shot.r === "number" && shot.r > ring1
})

function showResultsOverlay() {
  if (fit.value) {
    showResults.value = true
  }
}

// show a faint dot for the detected (uncalibrated) location so you can compare
const pendingDot = ref([])

async function loadRings() {
  const cfg = await fetch(`${API}/api/config`).then(r => r.json())
  const out = {}
  for (const [k, v] of Object.entries(cfg.RINGS_CM)) out[String(k)] = v
  rings.value = out
}

function outerRadiusCm() {
  // Get ring 1 radius (outermost ring for standard target)
  // Fixed scale - don't expand for outside shots (matches TargetView with disableAutoZoom)
  const ring1 = rings.value?.["1"]
  return (typeof ring1 === "number") ? ring1 : 25
}

async function startCal() {
  await fetch(`${API}/api/calibration/start`, { method: "POST" })
  pending.value = null
  pendingDot.value = []
  sampleCount.value = 0
  inSet.value = 0
  paused.value = false
  fit.value = null
  fitErr.value = null
  showResults.value = false
  fitVersion.value = 0
  fitJustApplied.value = false
}

async function resumeSet() {
    await fetch(`${API}/api/calibration/resume`, { method: "POST" })
    paused.value = false
    inSet.value = 0
    pending.value = null
    pendingDot.value = []
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
  if (paused.value) return
  if (!pending.value) return

  // IMPORTANT: use the canvas rect, not the wrapper div rect
  const canvasEl = ev.currentTarget.querySelector("canvas")
  if (!canvasEl) return

  const rect = canvasEl.getBoundingClientRect()

  // Map CSS pixels -> canvas pixels (must match TargetView's size prop)
  const SIZE = 1000
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
  const Rm = outerRadiusCm()
  const SCALE = radiusCanvas / Rm  // pixels per cm

  // Convert canvas pixels to cm
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

      // Update live fit stats if available (auto-computed when >= 6 samples)
      if (j.fit) {
        fit.value = j.fit
        if (j.fit_version) {
          fitVersion.value = j.fit_version
        }
        if (j.fit_applied) {
          // Flash the indicator to show new fit was applied
          fitJustApplied.value = true
          setTimeout(() => { fitJustApplied.value = false }, 1500)
        }
      }

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

.targetWrap{ display:flex; flex-direction:column; align-items:center; margin: 10px 0 14px; }
.targetClick{ width: min(1000px, 92vw); }

.outsideWarning {
  margin-top: 10px;
  padding: 10px 16px;
  border-radius: 10px;
  background: rgba(255, 90, 90, 0.15);
  border: 1px solid rgba(255, 90, 90, 0.4);
  color: rgba(255, 120, 120, 0.95);
  font-weight: 700;
  font-size: 13px;
  text-align: center;
}

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

/* Live stats panel */
.liveStatsBox {
  margin: 10px 0 14px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.05);
}

.statsTitle {
  font-weight: 900;
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  opacity: 0.85;
}

.statsWaiting .statsTitle {
  margin-bottom: 10px;
}

.statsWaiting .statsHint {
  font-size: 13px;
  opacity: 0.75;
  margin-bottom: 10px;
}

.progressBar {
  height: 6px;
  background: rgba(255,255,255,0.1);
  border-radius: 3px;
  overflow: hidden;
}

.progressFill {
  height: 100%;
  background: rgba(100, 180, 255, 0.7);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.statsLive .statsGrid {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.statItem {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.statLabel {
  font-size: 11px;
  opacity: 0.6;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.statValue {
  font-size: 18px;
  font-weight: 900;
}

.statValue.good { color: rgba(60, 220, 120, 0.95); }
.statValue.warn { color: rgba(255, 200, 60, 0.95); }
.statValue.bad { color: rgba(255, 90, 90, 0.95); }

.qualityHint {
  font-size: 13px;
  font-weight: 700;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(255,255,255,0.06);
}

.qualityHint.excellent { color: rgba(60, 220, 120, 0.95); background: rgba(60, 220, 120, 0.1); }
.qualityHint.good { color: rgba(100, 200, 255, 0.95); background: rgba(100, 200, 255, 0.1); }
.qualityHint.acceptable { color: rgba(255, 200, 60, 0.95); background: rgba(255, 200, 60, 0.1); }
.qualityHint.poor { color: rgba(255, 90, 90, 0.95); background: rgba(255, 90, 90, 0.1); }

.fitErr { color: rgba(255,120,120,0.95); font-weight: 800; }

.statsHeader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.fitVersionBadge {
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 800;
  background: rgba(60, 220, 120, 0.15);
  border: 1px solid rgba(60, 220, 120, 0.4);
  color: rgba(60, 220, 120, 1);
  transition: all 0.3s ease;
}

.fitVersionBadge.flash {
  background: rgba(60, 220, 120, 0.4);
  border-color: rgba(60, 220, 120, 0.8);
  box-shadow: 0 0 12px rgba(60, 220, 120, 0.5);
  animation: pulse 0.5s ease-in-out 3;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.fitAppliedBanner {
  background: rgba(60, 220, 120, 0.2);
  border: 1px solid rgba(60, 220, 120, 0.5);
  color: rgba(60, 220, 120, 1);
  padding: 10px 14px;
  border-radius: 8px;
  font-weight: 800;
  font-size: 13px;
  margin-bottom: 12px;
  text-align: center;
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-5px); }
  to { opacity: 1; transform: translateY(0); }
}

.btnApply {
  background: rgba(60, 220, 120, 0.15);
  border: 1px solid rgba(60, 220, 120, 0.4);
  color: rgba(60, 220, 120, 1);
}

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