<template>
  <div class="page">
    <header class="topbar">
      <div class="header-row">
        <div class="session-info" v-if="currentSession">
          <div class="session-progress">
            <span class="session-label">Session Progress:</span>
            <span class="session-arrows">{{ currentSession.current_arrows }}/{{ currentSession.target_arrows }} arrows</span>
            <span class="session-ends">End {{ currentSession.current_end }}/{{ currentSession.num_ends }}</span>
          </div>
          <div class="session-score">
            Score: {{ currentSession.total_score }}
          </div>
          <button class="btn btn-danger btn-sm" @click="endSession">End Session</button>
        </div>
        <div v-else class="no-session-warning">
          <span>⚠️ No active session - shots will not be saved</span>
          <button class="btn btn-primary btn-sm" @click="showConfigModal = true">Start Session</button>
        </div>
      </div>
      <div class="modebar">
      <div class="modebadge" :class="mode">
        Mode: {{ mode }}
      </div>

      <div class="modebtns">
        <button class="btn" :class="{ active: mode==='shooting' }" @click="setMode('shooting')">
          Shooting
        </button>
        <button class="btn" :class="{ active: mode==='scoring' }" @click="setMode('scoring')">
          Walk / Pull arrows
        </button>
      </div>
    </div>
    </header>

    <main>
        <div class="grid2">
          <!-- LEFT COLUMN (60%) -->
          <div class="col">
            <section class="card camera">
              <h2>Camera</h2>

              <div class="camHeader">
                <div v-if="postureSmooth?.posture" class="posture postureInCam">
                  <div class="pscore" :class="postureClass(postureSmooth.posture.score)">
                    {{ Math.round(postureSmooth.posture.score) }}
                  </div>
                  <div class="ptips">
                    <div class="ptitle">Posture</div>
                    <div v-if="postureSmooth.posture.messages?.length" class="pmsg">
                      <div v-for="(m, i) in postureSmooth.posture.messages" :key="i" class="pmsgLine">
                        {{ m }}
                      </div>
                    </div>
                    <div v-else class="pmsg ok">Looks good</div>
                  </div>
                </div>
              </div>

              <div class="camWrap">
                <img class="camImg" :src="`http://${host}:8081/stream`" alt="camera" />
              </div>
            </section>

            <section class="card">
              <h2>Status</h2>
              <pre class="mono">{{ stateText }}</pre>
            </section>
          </div>

          <!-- RIGHT COLUMN (40%) -->
          <div class="col">
            <section class="card">
              <h2>Scorecard</h2>
              <ScoreTable :table="table" />
            </section>

            <section class="card">
              <div class="cardhead">
                <h2>Target</h2>
                <button class="btn" @click="clearShots">Clear</button>
              </div>
              <TargetView v-if="Object.keys(rings).length" :shots="shots" :rings="rings" />
            </section>
          </div>
        </div>
    </main>

    <!-- Session Config Modal -->
    <SessionConfigModal
      v-if="showConfigModal"
      @close="showConfigModal = false"
      @start="startSession"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue"
import TargetView from "../components/TargetView.vue"
import ScoreTable from "../components/ScoreTable.vue"
import SessionConfigModal from "../components/SessionConfigModal.vue"

const host = window.location.hostname
const API = `http://${host}:8000`

const posture = ref(null)
const shots = ref([])
const rings = ref({})
const table = ref(null)
const stateText = ref("loading...")
const postureSmooth = ref(null)        // smoothed posture object for UI
const _emaScore = ref(null)            // running average
const _lastTipsAt = ref(0)             // rate limit tips text
const _tips = ref([])                  // stable tips array

const mode = ref("shooting")

// Session management
const currentSession = ref(null)
const showConfigModal = ref(false)
const sessionComplete = ref(false)

async function refreshMode() {
  const r = await fetch(`${API}/api/mode`)
  const j = await r.json()
  mode.value = j.mode || "shooting"
}

async function setMode(newMode) {
  await fetch(`${API}/api/mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: newMode }),
  })
  await refreshMode()
}

async function clearShots() {
  await fetch(`${API}/api/reset`, { method: "POST" })
  shots.value = []
  const stRes = await fetch(`${API}/api/state`)
  table.value = await stRes.json()
}

function postureClass(score) {
  if (score >= 85) return "good"
  if (score >= 70) return "warn"
  return "bad"
}

async function checkActiveSession() {
  try {
    const response = await fetch(`${API}/api/session/current`)
    const data = await response.json()
    if (data.ok && data.session_id) {
      currentSession.value = data
    } else {
      currentSession.value = null
    }
  } catch (error) {
    console.error('Failed to check active session:', error)
  }
}

async function startSession(config) {
  try {
    const response = await fetch(`${API}/api/session/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    const data = await response.json()

    if (data.ok) {
      showConfigModal.value = false
      await checkActiveSession()
    }
  } catch (error) {
    console.error('Failed to start session:', error)
  }
}

async function endSession() {
  if (!confirm('Are you sure you want to end this session? This will save the current progress.')) {
    return
  }

  try {
    const response = await fetch(`${API}/api/session/end`, {
      method: 'POST'
    })
    const data = await response.json()

    if (data.ok) {
      currentSession.value = null
      if (confirm('Session ended and saved. View session details?')) {
        window.location.href = '/sessions'
      }
    }
  } catch (error) {
    console.error('Failed to end session:', error)
  }
}

onMounted(async () => {
  // Check for active session
  await checkActiveSession()

  const shotsRes = await fetch(`${API}/api/shots`)
  const stRes = await fetch(`${API}/api/state`)
  table.value = await stRes.json()
  stateText.value = JSON.stringify(table.value, null, 2)
  const shotsData = await shotsRes.json()
  shots.value = shotsData.shots

  const cfgRes = await fetch(`${API}/api/config`)
  const cfg = await cfgRes.json()
  const out = {}
  for (const [k, v] of Object.entries(cfg.RINGS_M)) out[String(k)] = v
  rings.value = out

  const ws = new WebSocket(`ws://${location.hostname}:8000/ws`)
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.type === "shot") {
      shots.value.push(msg.shot)
      table.value = msg.table
      stateText.value = JSON.stringify(table.value, null, 2)

      // Update session info after shot
      checkActiveSession()

      // Check if session is complete
      if (table.value.is_complete) {
        sessionComplete.value = true
        setTimeout(() => {
          if (confirm('Session complete! View session details?')) {
            window.location.href = '/sessions'
          }
        }, 1000)
      }
    }
  }
  const wsPose = new WebSocket(`ws://${location.hostname}:8000/ws_pose`)
  wsPose.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg?.type !== "pose" || !msg.posture) return

    const now = Date.now()
    const raw = msg.posture
    const s = Number(raw.score ?? 0)

    // EMA smoothing (0.15–0.25 feels good)
    const alpha = 0.20
    _emaScore.value = (_emaScore.value == null) ? s : (_emaScore.value * (1 - alpha) + s * alpha)

    // Update tips at most 2x/sec to avoid flicker
    if (now - _lastTipsAt.value > 500) {
      _tips.value = Array.isArray(raw.messages) ? raw.messages : []
      _lastTipsAt.value = now
    }

    postureSmooth.value = {
      ...msg,
      posture: {
        ...raw,
        score: _emaScore.value,
        messages: _tips.value,
      }
    }
  }
  await refreshMode()
})
</script>

<style>
*, *::before, *::after {
  box-sizing: border-box;
}

  .grid2 {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 14px;
  align-items: start;
  padding: 14px;
}

.col {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.postureInCam {
  margin-bottom: 10px;
}

@media (max-width: 900px) {
  .grid2 {
    grid-template-columns: 1fr;
  }
}

.page { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#0b0f17; color:#e7ecf5; min-height:100vh; }
.topbar { padding:16px 18px; border-bottom:1px solid rgba(255,255,255,0.08); }
.title { font-size: 20px; font-weight: 700; }
.subtitle { opacity:0.7; font-size:12px; margin-top:4px; }
.card {
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px;
  padding: 12px;
  overflow:auto;
}
.card h2 { margin:0 0 10px 0; font-size:14px; opacity:0.9; }
.placeholder {
  min-height: 160px;
  display:flex;
  align-items:center;
  justify-content:center;
  border-radius: 12px;
  border: 1px dashed rgba(255,255,255,0.25);
  opacity:0.7;
}
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12px; white-space: pre-wrap; }

.cardhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.cardhead h2 { margin: 0; }

.posture {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 14px;
  background: rgba(255,255,255,0.04);
  margin-bottom: 12px;
}

.pscore{
  width: 64px;
  height: 64px;
  border-radius: 14px;
  display:flex;
  align-items:center;
  justify-content:center;
  font-weight: 900;
  font-size: 28px;
  color:#0b0f14;
  flex: 0 0 64px;
}

.pscore.good { background: rgba(60, 220, 120, 0.95); }
.pscore.warn { background: rgba(255, 200, 60, 0.95); }
.pscore.bad  { background: rgba(255, 90, 90, 0.95); }

.ptitle {
  font-weight: 800;
  opacity: 0.9;
  margin-bottom: 2px;
}

.pmsg {
  opacity: 0.85;
  line-height: 1.2;
}
.pmsg.ok {
  opacity: 0.75;
}

.camWrap {
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(0,0,0,0.25);
}

.camImg {
  width: 100%;
  height: 100%;
  max-width: 100%;
  object-fit: cover;
  display: block;
}

.camHeader{
  min-height: 110px;
  margin-bottom: 10px;
}

.pmsgLine{
  margin-top: 6px;
}

.card.camera {
  overflow: hidden;
}

.modebar{ display:flex; align-items:center; gap:12px; margin-top:10px; flex-wrap:wrap; }
.modebadge{
  padding:8px 12px; border-radius:12px; font-weight:800;
  border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.06);
}
.modebadge.shooting{
  background: rgba(80,200,255,0.14);
  border-color: rgba(80,200,255,0.25);
}
.modebadge.scoring{
  background: rgba(255,200,80,0.14);
  border-color: rgba(255,200,80,0.25);
}

.modebtns{ display:flex; gap:8px; }
.btn.active{ outline:2px solid rgba(255,255,255,0.25); }

/* Session UI */
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  gap: 1rem;
  flex-wrap: wrap;
}

.session-info {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: rgba(31, 111, 235, 0.1);
  border: 1px solid rgba(31, 111, 235, 0.3);
  border-radius: 8px;
}

.session-progress {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.875rem;
}

.session-label {
  font-weight: 600;
  opacity: 0.8;
}

.session-arrows {
  font-weight: 700;
  color: #1f6feb;
}

.session-ends {
  opacity: 0.7;
}

.session-score {
  font-weight: 700;
  font-size: 1.125rem;
  color: #1f6feb;
}

.no-session-warning {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 1rem;
  background: rgba(255, 184, 0, 0.1);
  border: 1px solid rgba(255, 184, 0, 0.3);
  border-radius: 8px;
  font-size: 0.875rem;
}

.btn-primary {
  background: #1f6feb;
  color: white;
}

.btn-primary:hover {
  background: #1a5cd7;
}

.btn-danger {
  background: #da3633;
  color: white;
}

.btn-danger:hover {
  background: #c93229;
}

.btn-sm {
  padding: 0.375rem 0.75rem;
  font-size: 0.875rem;
}

@media (max-width: 768px) {
  .header-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .session-info, .no-session-warning {
    width: 100%;
  }
}
</style>