<template>
  <div class="page">
    <header class="topbar">
      <div class="title">IronSight Dashboard</div>
    </header>

    <main>
      <div class="grid">
        <section class="card">
          <div class="cardhead">
            <h2>Target</h2>
            <button class="btn" @click="clearShots">Clear</button>
          </div>
          <TargetView v-if="Object.keys(rings).length" :shots="shots" :rings="rings" />
        </section>

        <section class="card">
          <h2>Scorecard</h2>
          <ScoreTable :table="table" />
        </section>
      </div>

      <div class="row2">
        <section class="card camera">
          <h2>Camera</h2>
          <div class="placeholder">Camera stream later</div>
        </section>

        <section class="card">
          <h2>Status</h2>
          <pre class="mono">{{ stateText }}</pre>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue"
import TargetView from "./components/TargetView.vue"
import ScoreTable from "./components/ScoreTable.vue"

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

  const ws = new WebSocket(`ws://${location.host}/ws`)
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.type === "shot") {
      shots.value.push(msg.shot)
      table.value = msg.table
      stateText.value = JSON.stringify(table.value, null, 2)
    }
  }
})
</script>

<style>
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
</style>