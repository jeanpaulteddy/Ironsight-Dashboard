<template>
  <nav class="navbar">
    <div class="nav-brand">IronSight</div>
    <div class="nav-links">
      <router-link to="/" :class="{ active: $route.path === '/' }">
        Dashboard
      </router-link>
      <router-link to="/calibrate" :class="{ active: $route.path === '/calibrate' }">
        Calibrate
      </router-link>
      <router-link to="/sessions" :class="{ active: $route.path === '/sessions' }">
        Past Sessions
      </router-link>
    </div>
  </nav>
</template>

<script setup>
// No props or state needed
</script>

<style scoped>
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1.5rem;
  background: #0d1117;
  border-bottom: 1px solid #21262d;
  position: sticky;
  top: 0;
  z-index: 100;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
}

.nav-brand {
  font-size: 20px;
  font-weight: 700;
  color: #e7ecf5;
}

.nav-links {
  display: flex;
  gap: 1rem;
}

.nav-links a {
  padding: 0.5rem 1rem;
  color: #8b949e;
  text-decoration: none;
  border-radius: 6px;
  transition: all 0.2s ease;
  font-weight: 500;
}

.nav-links a:hover {
  background: #161b22;
  color: #e7ecf5;
}

.nav-links a.active {
  background: #1f6feb;
  color: #ffffff;
}

.nav-links a.active:hover {
  background: #1a5cd7;
}

@media (max-width: 600px) {
  .navbar {
    flex-direction: column;
    gap: 0.75rem;
    align-items: flex-start;
  }

  .nav-links {
    width: 100%;
    justify-content: space-between;
  }

  .nav-links a {
    flex: 1;
    text-align: center;
    padding: 0.5rem 0.5rem;
    font-size: 0.9rem;
  }
}
</style>
