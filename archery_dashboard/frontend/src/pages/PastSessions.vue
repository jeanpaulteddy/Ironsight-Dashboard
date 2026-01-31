<template>
  <div class="past-sessions-page">
    <header class="page-header">
      <h1>Past Training Sessions</h1>
      <div class="header-actions">
        <router-link to="/" class="btn btn-primary">New Session</router-link>
      </div>
    </header>

    <div class="filters">
      <div class="filter-group">
        <label>
          <input type="checkbox" v-model="completeOnly" @change="loadSessions" />
          Complete sessions only
        </label>
      </div>
    </div>

    <div v-if="loading" class="loading">Loading sessions...</div>

    <div v-else-if="sessions.length === 0" class="empty-state">
      <p>No training sessions yet.</p>
      <router-link to="/" class="btn btn-primary">Start Your First Session</router-link>
    </div>

    <div v-else class="sessions-table">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Configuration</th>
            <th>Score</th>
            <th>Arrows</th>
            <th>Avg Score</th>
            <th>X Count</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="session in sessions" :key="session.id" @click="viewSession(session.id)" class="session-row">
            <td>{{ formatDate(session.start_time) }}</td>
            <td>{{ session.arrows_per_end }}×{{ session.num_ends }}</td>
            <td class="score">{{ session.total_score }}</td>
            <td>{{ session.total_arrows }}</td>
            <td>{{ (session.total_score / session.total_arrows).toFixed(1) }}</td>
            <td>—</td>
            <td>
              <span :class="['status-badge', session.is_complete ? 'complete' : 'incomplete']">
                {{ session.is_complete ? 'Complete' : 'Incomplete' }}
              </span>
            </td>
            <td class="actions" @click.stop>
              <button @click="viewSession(session.id)" class="btn btn-sm">View</button>
              <button @click="deleteSession(session.id)" class="btn btn-sm btn-danger">Delete</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="totalSessions > sessions.length" class="pagination">
        <button @click="loadMore" class="btn">Load More</button>
      </div>
    </div>

    <!-- Session Detail Modal -->
    <div v-if="selectedSessionId" class="modal-overlay" @click="closeModal">
      <div class="modal-content" @click.stop>
        <button class="modal-close" @click="closeModal">×</button>
        <SessionDetailModal v-if="selectedSession" :session="selectedSession" @close="closeModal" />
        <div v-else class="loading">Loading session details...</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import SessionDetailModal from '../components/SessionDetailModal.vue'

const sessions = ref([])
const totalSessions = ref(0)
const loading = ref(true)
const completeOnly = ref(false)
const limit = ref(50)
const offset = ref(0)

const selectedSessionId = ref(null)
const selectedSession = ref(null)

const host = window.location.hostname

async function loadSessions() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      limit: limit.value,
      offset: offset.value,
      complete_only: completeOnly.value
    })

    const response = await fetch(`http://${host}:8000/api/sessions?${params}`)
    const data = await response.json()

    sessions.value = data.sessions || []
    totalSessions.value = data.total || 0
  } catch (error) {
    console.error('Failed to load sessions:', error)
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  offset.value += limit.value
  await loadSessions()
}

async function viewSession(sessionId) {
  selectedSessionId.value = sessionId
  selectedSession.value = null

  try {
    const response = await fetch(`http://${host}:8000/api/sessions/${sessionId}`)
    const data = await response.json()

    if (data.ok) {
      selectedSession.value = data.session
    }
  } catch (error) {
    console.error('Failed to load session details:', error)
  }
}

function closeModal() {
  selectedSessionId.value = null
  selectedSession.value = null
}

async function deleteSession(sessionId) {
  if (!confirm('Are you sure you want to delete this session? This cannot be undone.')) {
    return
  }

  try {
    const response = await fetch(`http://${host}:8000/api/sessions/${sessionId}`, {
      method: 'DELETE'
    })

    if (response.ok) {
      // Reload sessions
      await loadSessions()
    }
  } catch (error) {
    console.error('Failed to delete session:', error)
  }
}

function formatDate(timestamp) {
  const date = new Date(timestamp * 1000)
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

onMounted(() => {
  loadSessions()
})
</script>

<style scoped>
.past-sessions-page {
  padding: 2rem;
  background: #0b0f17;
  min-height: 100vh;
  color: #e7ecf5;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
}

.page-header h1 {
  font-size: 2rem;
  margin: 0;
  color: #e7ecf5;
}

.header-actions {
  display: flex;
  gap: 0.75rem;
}

.btn {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 6px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  text-decoration: none;
  display: inline-block;
}

.btn-primary {
  background: #1f6feb;
  color: white;
}

.btn-primary:hover {
  background: #1a5cd7;
}

.btn-sm {
  padding: 0.25rem 0.75rem;
  font-size: 0.875rem;
}

.btn-danger {
  background: #da3633;
  color: white;
}

.btn-danger:hover {
  background: #c93229;
}

.filters {
  margin-bottom: 1.5rem;
  padding: 1rem;
  background: #161b22;
  border-radius: 6px;
}

.filter-group label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.loading, .empty-state {
  text-align: center;
  padding: 3rem;
  color: #8b949e;
}

.empty-state p {
  margin-bottom: 1.5rem;
  font-size: 1.125rem;
}

.sessions-table {
  background: #161b22;
  border-radius: 6px;
  overflow: hidden;
}

table {
  width: 100%;
  border-collapse: collapse;
}

thead {
  background: #0d1117;
}

th {
  padding: 0.75rem 1rem;
  text-align: left;
  font-weight: 600;
  color: #e7ecf5;
  border-bottom: 1px solid #21262d;
}

.session-row {
  cursor: pointer;
  transition: background 0.2s ease;
}

.session-row:hover {
  background: #1c2128;
}

td {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #21262d;
  color: #c9d1d9;
}

.score {
  font-weight: 600;
  color: #1f6feb;
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

.actions {
  display: flex;
  gap: 0.5rem;
}

.pagination {
  padding: 1rem;
  text-align: center;
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: #0d1117;
  border-radius: 12px;
  max-width: 90vw;
  max-height: 90vh;
  overflow-y: auto;
  position: relative;
  border: 1px solid #21262d;
}

.modal-close {
  position: absolute;
  top: 1rem;
  right: 1rem;
  background: #21262d;
  border: none;
  color: #e7ecf5;
  font-size: 1.5rem;
  width: 2rem;
  height: 2rem;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}

.modal-close:hover {
  background: #30363d;
}

@media (max-width: 768px) {
  .past-sessions-page {
    padding: 1rem;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 1rem;
  }

  .sessions-table {
    overflow-x: auto;
  }

  table {
    min-width: 600px;
  }
}
</style>
