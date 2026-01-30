<template>
  <div class="session-detail">
    <div class="detail-header">
      <h2>Session Details</h2>
      <div class="session-meta">
        <span>{{ formatDate(session.start_time) }}</span>
        <span>{{ session.arrows_per_end }}×{{ session.num_ends }} Configuration</span>
        <span :class="['status-badge', session.is_complete ? 'complete' : 'incomplete']">
          {{ session.is_complete ? 'Complete' : 'Incomplete' }}
        </span>
      </div>
    </div>

    <div class="detail-grid">
      <!-- Left column: Score stats and table -->
      <div class="detail-section">
        <h3>Score Summary</h3>
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">Total Score</div>
            <div class="stat-value">{{ session.total_score }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Total Arrows</div>
            <div class="stat-value">{{ session.total_arrows }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Average Score</div>
            <div class="stat-value">{{ (session.total_score / session.total_arrows).toFixed(2) }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Duration</div>
            <div class="stat-value">{{ formatDuration(session.start_time, session.end_time) }}</div>
          </div>
        </div>

        <h3>Shot List</h3>
        <div class="shots-table">
          <table>
            <thead>
              <tr>
                <th>End</th>
                <th>Shot</th>
                <th>Score</th>
                <th>Position</th>
                <th>Screenshot</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="shot in session.shots" :key="shot.id">
                <td>{{ shot.end_number }}</td>
                <td>{{ shot.shot_number }}</td>
                <td>
                  <span :class="['score-badge', getScoreClass(shot.score, shot.is_x)]">
                    {{ shot.is_x ? 'X' : shot.score }}
                  </span>
                </td>
                <td class="position">
                  ({{ shot.x.toFixed(3) }}, {{ shot.y.toFixed(3) }})
                  <br>
                  <small>r={{ shot.r.toFixed(3) }}m</small>
                </td>
                <td>
                  <button
                    v-if="shot.screenshot_path"
                    @click="viewScreenshot(shot.screenshot_path)"
                    class="btn btn-sm"
                  >
                    View
                  </button>
                  <span v-else class="no-screenshot">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Right column: Target visualization -->
      <div class="detail-section">
        <h3>Target View</h3>
        <div class="target-container">
          <TargetView :shots="targetShots" :rings="rings" />
        </div>

        <h3>Screenshot Gallery</h3>
        <div class="screenshot-gallery">
          <div
            v-for="shot in session.shots.filter(s => s.screenshot_path)"
            :key="shot.id"
            class="screenshot-thumb"
            @click="viewScreenshot(shot.screenshot_path)"
          >
            <img :src="`http://${host}:8000/screenshots/${shot.screenshot_path}`" :alt="`Shot ${shot.shot_number}`" />
            <div class="thumb-label">
              End {{ shot.end_number }}, Shot {{ shot.shot_number }}
              <span :class="['score-badge', getScoreClass(shot.score, shot.is_x)]">
                {{ shot.is_x ? 'X' : shot.score }}
              </span>
            </div>
          </div>
          <div v-if="session.shots.filter(s => s.screenshot_path).length === 0" class="no-screenshots">
            No screenshots available
          </div>
        </div>
      </div>
    </div>

    <!-- Screenshot lightbox -->
    <div v-if="viewingScreenshot" class="screenshot-lightbox" @click="viewingScreenshot = null">
      <img :src="`http://${host}:8000/screenshots/${viewingScreenshot}`" @click.stop />
      <button class="lightbox-close" @click="viewingScreenshot = null">×</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import TargetView from './TargetView.vue'

const props = defineProps({
  session: {
    type: Object,
    required: true
  }
})

const host = window.location.hostname
const viewingScreenshot = ref(null)

// Default ring configuration (same as Dashboard)
const rings = ref({
  "X": 0.010,
  10: 0.020,
  9: 0.040,
  8: 0.060,
  7: 0.080,
  6: 0.100,
  5: 0.120,
  4: 0.140,
  3: 0.160,
  2: 0.180,
  1: 0.200,
})

// Convert shots to format expected by TargetView
const targetShots = computed(() => {
  return props.session.shots.map(shot => ({
    x: shot.x,
    y: shot.y,
    r: shot.r,
    score: shot.is_x ? 'X' : shot.score
  }))
})

function viewScreenshot(path) {
  viewingScreenshot.value = path
}

function formatDate(timestamp) {
  const date = new Date(timestamp * 1000)
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatDuration(startTime, endTime) {
  if (!endTime) return 'In Progress'
  const duration = endTime - startTime
  const minutes = Math.floor(duration / 60)
  const seconds = Math.floor(duration % 60)
  return `${minutes}m ${seconds}s`
}

function getScoreClass(score, isX) {
  if (isX) return 'score-x'
  if (score >= 9) return 'score-high'
  if (score >= 7) return 'score-mid'
  if (score >= 4) return 'score-low'
  return 'score-miss'
}
</script>

<style scoped>
.session-detail {
  padding: 2rem;
  color: #e7ecf5;
}

.detail-header {
  margin-bottom: 2rem;
}

.detail-header h2 {
  margin: 0 0 0.5rem 0;
  font-size: 1.75rem;
}

.session-meta {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  color: #8b949e;
}

.status-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 500;
}

.status-badge.complete {
  background: #2da44e;
  color: white;
}

.status-badge.incomplete {
  background: #f85149;
  color: white;
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2rem;
}

.detail-section {
  background: #161b22;
  padding: 1.5rem;
  border-radius: 8px;
}

.detail-section h3 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1.125rem;
  color: #e7ecf5;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.stat-card {
  background: #0d1117;
  padding: 1rem;
  border-radius: 6px;
  text-align: center;
}

.stat-label {
  font-size: 0.875rem;
  color: #8b949e;
  margin-bottom: 0.5rem;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 600;
  color: #1f6feb;
}

.shots-table {
  max-height: 400px;
  overflow-y: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th {
  position: sticky;
  top: 0;
  background: #0d1117;
  padding: 0.5rem;
  text-align: left;
  font-size: 0.875rem;
  border-bottom: 1px solid #21262d;
}

td {
  padding: 0.5rem;
  border-bottom: 1px solid #21262d;
  font-size: 0.875rem;
}

.position {
  font-family: monospace;
  font-size: 0.75rem;
  color: #8b949e;
}

.score-badge {
  display: inline-block;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-weight: 600;
  font-size: 0.875rem;
}

.score-x, .score-high {
  background: #fbbf24;
  color: #1c1917;
}

.score-mid {
  background: #ef4444;
  color: white;
}

.score-low {
  background: #3b82f6;
  color: white;
}

.score-miss {
  background: #6b7280;
  color: white;
}

.target-container {
  background: #0d1117;
  padding: 1rem;
  border-radius: 6px;
  margin-bottom: 2rem;
}

.screenshot-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 1rem;
  max-height: 400px;
  overflow-y: auto;
}

.screenshot-thumb {
  cursor: pointer;
  border-radius: 6px;
  overflow: hidden;
  background: #0d1117;
  transition: transform 0.2s ease;
}

.screenshot-thumb:hover {
  transform: scale(1.05);
}

.screenshot-thumb img {
  width: 100%;
  height: 120px;
  object-fit: cover;
}

.thumb-label {
  padding: 0.5rem;
  font-size: 0.75rem;
  color: #8b949e;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.no-screenshots {
  grid-column: 1 / -1;
  text-align: center;
  padding: 2rem;
  color: #8b949e;
}

.no-screenshot {
  color: #6b7280;
}

.btn {
  padding: 0.25rem 0.75rem;
  background: #1f6feb;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
}

.btn:hover {
  background: #1a5cd7;
}

.btn-sm {
  padding: 0.25rem 0.5rem;
  font-size: 0.75rem;
}

/* Screenshot Lightbox */
.screenshot-lightbox {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.95);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
  cursor: zoom-out;
}

.screenshot-lightbox img {
  max-width: 90%;
  max-height: 90%;
  object-fit: contain;
}

.lightbox-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: white;
  font-size: 2rem;
  width: 3rem;
  height: 3rem;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.lightbox-close:hover {
  background: rgba(255, 255, 255, 0.3);
}

@media (max-width: 1024px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }

  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 640px) {
  .session-detail {
    padding: 1rem;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }

  .screenshot-gallery {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  }
}
</style>
