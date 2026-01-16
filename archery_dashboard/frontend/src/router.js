import { createRouter, createWebHistory } from "vue-router"
import Dashboard from "./pages/Dashboard.vue"
import CalibrateView from "./pages/CalibrateView.vue"

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Dashboard },
    { path: "/calibrate", component: CalibrateView },
  ],
})