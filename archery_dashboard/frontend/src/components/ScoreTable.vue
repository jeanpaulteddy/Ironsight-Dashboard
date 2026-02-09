<template>
  <div v-if="table" class="wrap">
    <div class="table-scroll">
    <table class="score" :class="{ compact: arrowsPerEnd > 6 }">
      <thead>
        <tr>
          <th class="endcol" rowspan="2">End</th>
          <th class="arrowshead" :colspan="arrowsPerEnd">Arrows</th>
          <th class="scorecol" rowspan="2">Score</th>
          <th class="rtcol" rowspan="2">R.Total</th>
        </tr>
        <tr>
          <th v-for="i in arrowsPerEnd" :key="i" class="arrowcol">{{ i }}</th>
        </tr>
      </thead>

      <tbody>
        <tr v-for="row in paddedEnds" :key="row.end">
          <td class="endcol">{{ row.end }}</td>

          <td v-for="i in arrowsPerEnd" :key="i" :class="{ active: row.end === currentPos.end && i === currentPos.arrow }">
            <div class="cell">
              <span v-if="row.arrows[i-1] !== ''" class="badge" :class="badgeClass(row.arrows[i-1])">
                {{ row.arrows[i-1] }}
              </span>
            </div>
          </td>

          <td class="num">{{ row.score }}</td>
          <td class="num">{{ row.running }}</td>
        </tr>
      </tbody>
    </table>
    </div>
    <div class="footer">
      <div class="counts">
        <div class="count" v-for="k in footerKeys" :key="k">
          <span class="k">{{ k }}</span>
          <span class="v">{{ footerCount(k) }}</span>
        </div>
      </div>

      <div class="totals">
        <span><b>Total</b> {{ table.total }}</span>
        <span class="sep">•</span>
        <span><b>Arrows</b> {{ table.total_arrows }}</span>
      </div>
    </div>  
    
  </div>

  <div v-else class="empty">No score data.</div>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  table: Object
})

const arrowsPerEnd = computed(() => props.table?.arrows_per_end ?? 3)
const numEnds = computed(() => props.table?.num_ends ?? 10)
const currentPos = computed(() => {
  const ends = props.table?.ends ?? []
  const ape = arrowsPerEnd.value

  if (!ends.length) return { end: 1, arrow: 1 }

  const last = ends[ends.length - 1]
  const filled = (last.arrows ?? []).length

  // if last end is full, next is first arrow of next end
  if (filled >= ape) return { end: last.end + 1, arrow: 1 }

  return { end: last.end, arrow: filled + 1 }
})
const counts = computed(() => props.table?.counts ?? {})

// pad ends to always show 10 rows like a scorecard (optional)
const paddedEnds = computed(() => {
  const ends = props.table?.ends ?? []
  const out = ends.map(e => ({
    end: e.end,
    arrows: [...e.arrows, ...Array(arrowsPerEnd.value).fill("")].slice(0, arrowsPerEnd.value),
    score: e.score,
    running: e.running
  }))

  // pad to the configured number of ends
  for (let i = out.length + 1; i <= numEnds.value; i++) {
    out.push({ end: i, arrows: Array(arrowsPerEnd.value).fill(""), score: "", running: "" })
  }
  return out
})

function badgeClass(v) {
  if (v === "X" || v === 10) return "yellow"
  if (v === 9 || v === 8) return "yellow"
  if (v === 7 || v === 6) return "red"
  if (v === 5 || v === 4) return "blue"
  if (v === 3 || v === 2) return "black"
  return "miss" // 1 or 0
}

function footerCount(k) {
  const c = props.table?.counts ?? {}
  if (k === "M") return c[0] ?? 0
  if (k === "X") return c["X"] ?? 0
  return c[Number(k)] ?? 0
}

const footerKeys = computed(() => {
  // Ordered like a scorecard, but only include values that exist (> 0)
  const ordered = ["X", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1", "M"]
  return ordered.filter(k => footerCount(k) > 0)
})
</script>

<style scoped>
.wrap { display:flex; flex-direction:column; gap:10px; }

.table-scroll {
  max-height: 480px;
  overflow-y: auto;
}

.table-scroll thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #161b22;
}
.num { width: 72px; }

.badge {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 14px;
  letter-spacing: 0.2px;
  text-shadow: 0 1px 1px rgba(0,0,0,0.35);
}

/* High-contrast badge text per color */
.badge.yellow {
  background: #f2d600;
  color: #111;          /* dark text on yellow */
}

.badge.red {
  background: #d62828;
  color: #fff;
}

.badge.blue {
  background: #1e73be;
  color: #fff;
}

.badge.black {
  background: #111;
  color: #fff;
}

.badge.miss {
  background: #e6e6e6;
  color: #111;
}

.footer {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  padding-top: 10px;
  flex-wrap: wrap; /* allow wrapping instead of squishing */
}

.counts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(56px, 1fr));
  gap: 8px 10px;
  flex: 1 1 520px;
}

.count {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 10px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.10);
  min-width: 0; /* important for grid shrink */
}

.k {
  font-weight: 700;
  opacity: 0.85;
}

.v {
  font-weight: 900;
}

.totals {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 14px;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.12);
  white-space: nowrap;
  font-size: clamp(14px, 1.2vw, 18px);
  flex: 0 0 auto;
}

.totals b {
  font-weight: 900;
}

.sep {
  opacity: 0.5;
}

@media (max-width: 900px) {
  .counts {
    flex-basis: 100%;
  }
  .totals {
    width: 100%;
  }
}

.score {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  table-layout: fixed;
}

th, td {
  border: 1px solid rgba(255,255,255,0.14);
  padding: 10px 8px;
  text-align: center;
}

thead th {
  background: rgba(255,255,255,0.06);
  font-weight: 700;
}

.endcol { width: 56px; }
.arrowcol { width: 84px; }
.scorecol { width: 86px; }
.rtcol { width: 86px; }

.cell {
  height: 34px; /* bigger like score sheets */
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Compact mode for > 6 arrows per end */
.compact th, .compact td {
  padding: 6px 4px;
  font-size: 12px;
}

.compact .arrowcol { width: 52px; }

.compact .badge {
  width: 26px;
  height: 26px;
  font-size: 11px;
}

.compact .cell {
  height: 26px;
}

td.active {
  outline: 2px solid rgba(255,255,255,0.35);
  outline-offset: -2px;
  background: rgba(255,255,255,0.04);
}

</style>
