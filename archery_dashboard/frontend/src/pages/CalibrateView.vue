<template>
  <div class="calPage">
    <header class="top">
      <div class="title">Calibration</div>
      <a class="link" href="/">← Back</a>
    </header>

    <div class="targetWrap">
      <TargetView
        :shots="[]"
        :rings="rings"
        @click.native="onTargetClick"
      />
    </div>

    <div class="info">
      <div>Click on the target where the arrow hit</div>
      <div v-if="lastClick">
        Last click → x: {{ lastClick.x.toFixed(3) }},
        y: {{ lastClick.y.toFixed(3) }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue"
import TargetView from "../components/TargetView.vue"

const rings = ref({})
const lastClick = ref(null)

onMounted(async () => {
  const cfg = await fetch("/api/config").then(r => r.json())
  const out = {}
  for (const [k, v] of Object.entries(cfg.RINGS_M)) out[String(k)] = v
  rings.value = out
})

function onTargetClick(ev) {
  const rect = ev.currentTarget.getBoundingClientRect()
  const cx = rect.left + rect.width / 2
  const cy = rect.top + rect.height / 2

  const px = ev.clientX - cx
  const py = ev.clientY - cy

  const radiusPx = rect.width / 2
  const x = px / radiusPx
  const y = -py / radiusPx   // invert Y to match target coords

  lastClick.value = { x, y }
  console.log("[CAL] click", lastClick.value)
}
</script>

<style>
.calPage{
  min-height:100vh;
  background:#0b0f17;
  color:#e7ecf5;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
  padding: 18px;
}
.top{
  display:flex;
  align-items:center;
  justify-content:space-between;
  margin-bottom:14px;
}
.title{ font-size:20px; font-weight:800; }
.link{ color:#9ad; text-decoration:none; }

.targetWrap{
  display:flex;
  justify-content:center;
  margin: 20px 0;
}

.info{
  text-align:center;
  opacity:0.85;
  font-size:14px;
}
</style>