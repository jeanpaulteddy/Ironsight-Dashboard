<template>
  <div class="page">
    <header class="topbar">
      <div class="title">IronSight Dashboard</div>
    </header>

    <main>
        <div class="grid2">
          <!-- LEFT COLUMN -->
          <div class="col">
            <section class="card">
              <div class="cardhead">
                <h2>Target</h2>
                <button class="btn" @click="clearShots">Clear</button>
              </div>
              <TargetView v-if="Object.keys(rings).length" :shots="shots" :rings="rings" />
            </section>

            <section class="card camera">
              <h2>Camera</h2>

              <div v-if="posture?.posture" class="posture postureInCam">
                <div class="pscore" :class="postureClass(posture.posture.score)">
                  {{ Math.round(posture.posture.score) }}
                </div>
                <div class="ptips">
                  <div class="ptitle">Posture</div>
                  <div v-if="posture.posture.messages?.length" class="pmsg">
                    {{ posture.posture.messages.join(" • ") }}
                  </div>
                  <div v-else class="pmsg ok">Looks good</div>
                </div>
              </div>

              <div class="camWrap">
                <img class="camImg" :src="`http://${host}:8081/stream`" alt="camera" />
              </div>
            </section>

            
          </div>

          <!-- RIGHT COLUMN -->
          <div class="col">
            <section class="card">
              <h2>Scorecard</h2>
              <ScoreTable :table="table" />
            </section>

            <section class="card">
              <h2>Status</h2>
              <pre class="mono">{{ stateText }}</pre>
            </section>
          </div>
        </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue"
import TargetView from "./components/TargetView.vue"
import ScoreTable from "./components/ScoreTable.vue"

const host = window.location.hostname

const posture = ref(null)
const shots = ref([])
const rings = ref({})
const table = ref(null)
const stateText = ref("loading...")

async function clearShots() {
  await fetch("/api/reset", { method: "POST" })
  shots.value = []
  const stRes = await fetch("/api/state")
  table.value = await stRes.json()
}

function postureClass(score) {
  if (score >= 85) return "good"
  if (score >= 70) return "warn"
  return "bad"
}

onMounted(async () => {
  const shotsRes = await fetch("/api/shots")
  const stRes = await fetch("/api/state")
  table.value = await stRes.json()
  stateText.value = JSON.stringify(table.value, null, 2)
  const shotsData = await shotsRes.json()
  shots.value = shotsData.shots

  const cfgRes = await fetch("/api/config")
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
    }
  }
  const wsPose = new WebSocket(`ws://${location.hostname}:8000/ws_pose`)
  wsPose.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg?.type === "pose") posture.value = msg
  }
})
</script>

<style>
  .grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
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

@media (max-width: 1100px) {
  .grid2 {
    grid-template-columns: 1fr;
  }
}

.page { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#0b0f17; color:#e7ecf5; min-height:100vh; }
.topbar { padding:16px 18px; border-bottom:1px solid rgba(255,255,255,0.08); }
.title { font-size:20px; font-weight:700; }
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

.pscore {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 900;
  font-size: 26px;
  color: #0b0f14;
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
  object-fit: cover;
  display: block;
}
.postureInCam {
  margin-bottom: 10px;
}
</style>