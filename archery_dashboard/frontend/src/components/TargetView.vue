<template>
  <div class="target-wrap">
    <canvas ref="canvas" :width="canvasSize" :height="canvasSize"></canvas>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, computed } from "vue"

const props = defineProps({
  shots: { type: Array, default: () => [] },
  rings: { type: Object, default: () => ({}) }, // expects { "X":2, "1":40, ... "10":4 } (cm)
  disableAutoZoom: { type: Boolean, default: false }, // when true, keep target at fixed scale regardless of shot positions
  size: { type: Number, default: 520 } // canvas resolution in pixels (higher = more precise for calibration clicks)
})

const canvas = ref(null)

const canvasSize = computed(() => props.size)
const CX = computed(() => props.size / 2)
const CY = computed(() => props.size / 2)
const PAD = 14 // padding so outer ring fits nicely

const maxR = computed(() => {
  const ring1 = props.rings?.["1"]
  const base = (typeof ring1 === "number") ? ring1 : 25

  // When disableAutoZoom is true, don't expand view for outside shots
  if (props.disableAutoZoom) {
    return base
  }

  // also consider farthest shot so dots don't go off-canvas
  let maxShot = 0
  for (const s of props.shots) {
    if (typeof s.r === "number" && s.r > maxShot) maxShot = s.r
  }

  return Math.max(base, maxShot)
})

const SCALE = computed(() => {
  return (CX.value - PAD) / maxR.value
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
  const sz = canvasSize.value
  const cx = CX.value
  const cy = CY.value
  ctx.clearRect(0, 0, sz, sz)

  // Subtle dark backdrop (not a target fill). If you want true transparency, remove these 2 lines.
  ctx.fillStyle = "rgba(0,0,0,0.0)"
  ctx.fillRect(0, 0, sz, sz)

  // ---------- Rings only ----------
  ctx.lineWidth = 2
  ctx.strokeStyle = "rgba(255,255,255,0.35)"

  // Draw rings 1..10 (outer -> inner)
  for (let s = 1; s <= 10; s++) {
    const r_cm = props.rings?.[String(s)]
    if (typeof r_cm !== "number") continue
    const r_px = r_cm * SCALE.value

    ctx.beginPath()
    ctx.arc(cx, cy, r_px, 0, Math.PI * 2)
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
    const r_cm = props.rings?.[String(s)]
    if (typeof r_cm !== "number") continue
    const r_px = r_cm * SCALE.value

    // place slightly outside the ring radius on the right
    const xLabel = cx + r_px - 15
    ctx.fillText(String(s), xLabel, cy)
  }

  ctx.restore()

  // X ring outline
  const x_cm = props.rings?.["X"]
  if (typeof x_cm === "number") {
    const x_px = x_cm * SCALE.value
    ctx.strokeStyle = "rgba(255,255,255,0.55)"
    ctx.beginPath()
    ctx.arc(cx, cy, x_px, 0, Math.PI * 2)
    ctx.stroke()
  }

  for (let i = 0; i < props.shots.length; i++) {
    const s = props.shots[i]
    const n = i + 1

    const x = cx + s.x * SCALE.value
    const y = cy - s.y * SCALE.value

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
  () => [props.shots, props.rings, props.size],
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
  width: 70%;
  height: auto;
}
</style>