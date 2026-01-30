<template>
  <div class="modal-overlay" @click="$emit('close')">
    <div class="modal-content" @click.stop>
      <h2>Configure New Session</h2>
      <p class="modal-subtitle">Set up your training session parameters</p>

      <form @submit.prevent="startSession">
        <div class="form-group">
          <label for="arrows-per-end">Arrows per End</label>
          <input
            id="arrows-per-end"
            v-model.number="arrowsPerEnd"
            type="number"
            min="1"
            max="20"
            required
          />
          <small>Number of arrows to shoot before pausing (typically 3 or 6)</small>
        </div>

        <div class="form-group">
          <label for="num-ends">Number of Ends</label>
          <input
            id="num-ends"
            v-model.number="numEnds"
            type="number"
            min="1"
            max="50"
            required
          />
          <small>How many ends to shoot in this session</small>
        </div>

        <div class="session-summary">
          <strong>Total arrows:</strong> {{ arrowsPerEnd * numEnds }}
        </div>

        <div class="modal-actions">
          <button type="button" @click="$emit('close')" class="btn btn-secondary">
            Cancel
          </button>
          <button type="submit" class="btn btn-primary">
            Start Session
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const emit = defineEmits(['close', 'start'])

// Default values
const arrowsPerEnd = ref(3)
const numEnds = ref(10)

async function startSession() {
  emit('start', {
    arrows_per_end: arrowsPerEnd.value,
    num_ends: numEnds.value
  })
}
</script>

<style scoped>
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
  border: 1px solid #21262d;
  border-radius: 12px;
  padding: 2rem;
  max-width: 500px;
  width: 90%;
  color: #e7ecf5;
}

.modal-content h2 {
  margin: 0 0 0.5rem 0;
  font-size: 1.5rem;
}

.modal-subtitle {
  margin: 0 0 2rem 0;
  color: #8b949e;
}

.form-group {
  margin-bottom: 1.5rem;
}

.form-group label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
  color: #e7ecf5;
}

.form-group input {
  width: 100%;
  padding: 0.75rem;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  color: #e7ecf5;
  font-size: 1rem;
}

.form-group input:focus {
  outline: none;
  border-color: #1f6feb;
}

.form-group small {
  display: block;
  margin-top: 0.25rem;
  color: #8b949e;
  font-size: 0.875rem;
}

.session-summary {
  padding: 1rem;
  background: #161b22;
  border-radius: 6px;
  margin-bottom: 2rem;
  text-align: center;
  font-size: 1.125rem;
}

.session-summary strong {
  color: #1f6feb;
}

.modal-actions {
  display: flex;
  gap: 1rem;
  justify-content: flex-end;
}

.btn {
  padding: 0.75rem 1.5rem;
  border: none;
  border-radius: 6px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-primary {
  background: #1f6feb;
  color: white;
}

.btn-primary:hover {
  background: #1a5cd7;
}

.btn-secondary {
  background: #21262d;
  color: #e7ecf5;
}

.btn-secondary:hover {
  background: #30363d;
}

/* Remove spinner from number inputs (optional) */
input[type="number"]::-webkit-inner-spin-button,
input[type="number"]::-webkit-outer-spin-button {
  opacity: 1;
}
</style>
