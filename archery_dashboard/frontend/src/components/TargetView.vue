<template>
  <div class="target-wrap">
    <canvas ref="canvas" :width="SIZE" :height="SIZE"></canvas>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, computed } from "vue"

const props = defineProps({
  shots: { type: Array, default: () => [] },
  rings: { type: Object, default: () => ({}) } // expects { "X":0.01, "1":0.2, ... "10":0.02 } (meters)
})

const canvas = ref(null)

const SIZE = 520
const CX = SIZE / 2
const CY = SIZE / 2
const PAD = 14 // padding so outer ring fits nicely

const maxR = computed(() => {
  const ring1 = props.rings?.["1"]
  const base = (typeof ring1 === "number") ? ring1 : 0.25

  // also consider farthest shot so dots don't go off-canvas
  let maxShot = 0
  for (const s of props.shots) {
    if (typeof s.r === "number" && s.r > maxShot) maxShot = s.r
  }

  return Math.max(base, maxShot)
})

const SCALE = computed(() => {
  return (CX - PAD) / maxR.value
})

function scoreColor(s) {
  if (s <= 2) return "#ffffff"   // white
  if (s <= 4) return "#000000"   // black
  if (s <= 6) return "#1e73be"   // blue
  if (s <= 8) return "#c4161c"   // red
  return "#ffd200"               // yellow
}

function draw() {
  if (!canvas.value) return
  const ctx = canvas.value.getContext("2d")
  ctx.clearRect(0, 0, SIZE, SIZE)

  // Subtle dark backdrop (not a target fill). If you want true transparency, remove these 2 lines.
  ctx.fillStyle = "rgba(0,0,0,0.0)"
  ctx.fillRect(0, 0, SIZE, SIZE)

  // ---------- Rings only ----------
  ctx.lineWidth = 2
  ctx.strokeStyle = "rgba(255,255,255,0.35)"

  // Draw rings 1..10 (outer -> inner)
  for (let s = 1; s <= 10; s++) {
    const r_m = props.rings?.[String(s)]
    if (typeof r_m !== "number") continue
    const r_px = r_m * SCALE.value

    ctx.beginPath()
    ctx.arc(CX, CY, r_px, 0, Math.PI * 2)
    ctx.stroke()
  }
  
    // ---------- Ring labels (right side, subtle) ----------
  ctx.save()
  ctx.fillStyle = "rgba(255,255,255,0.55)"
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace"
  ctx.textAlign = "left"
  ctx.textBaseline = "middle"

  // labels for 1..10 at 3 o'clock
  for (let s = 1; s <= 10; s++) {
    const r_m = props.rings?.[String(s)]
    if (typeof r_m !== "number") continue
    const r_px = r_m * SCALE.value

    // place slightly outside the ring radius on the right
    const xLabel = CX + r_px - 15
    ctx.fillText(String(s), xLabel, CY)
  }

  ctx.restore()

  // X ring outline
  const x_m = props.rings?.["X"]
  if (typeof x_m === "number") {
    const x_px = x_m * SCALE.value
    ctx.strokeStyle = "rgba(255,255,255,0.55)"
    ctx.beginPath()
    ctx.arc(CX, CY, x_px, 0, Math.PI * 2)
    ctx.stroke()
  }

  for (let i = 0; i < props.shots.length; i++) {
    const s = props.shots[i]
    const n = i + 1

    const x = CX + s.x * SCALE.value
    const y = CY - s.y * SCALE.value

    // simple red dot
    ctx.fillStyle = "#ff2a2a"
    ctx.beginPath()
    ctx.arc(x, y, 4, 0, Math.PI * 2)
    ctx.fill()

    // order label
    ctx.save()
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace"
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"

    ctx.lineWidth = 3
    ctx.strokeStyle = "rgba(0,0,0,0.65)"
    ctx.strokeText(String(n), x, y - 12)

    ctx.fillStyle = "rgba(255,255,255,0.95)"
    ctx.fillText(String(n), x, y - 12)
    ctx.restore()
  }
}

onMounted(() => requestAnimationFrame(draw))
watch(
  () => [props.shots, props.rings],
  () => requestAnimationFrame(draw),
  { deep: true }
)
</script>


<style scoped>
.target-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
}
canvas {
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.1);
}
</style>