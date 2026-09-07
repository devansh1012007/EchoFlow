import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// All proxy targets route to the nginx TLS terminator on :443.
// nginx then forwards to the in-network gunicorn (:8000) over plain HTTP.
// Direct :8005 hops are intentionally removed — keeping the dev front-end
// on https:// makes XHR/Secure-Cookie/Mixed-Content behavior identical to
// production, so "works in dev" matches "works in prod" instead of
// masking TLS-only failure modes.
const API_TARGET = 'https://localhost:443'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3021,
    proxy: {
      '/auth': API_TARGET,
      '/feed': API_TARGET,
      '/suggestions': API_TARGET,
      '/tags': API_TARGET,
      '/clips': API_TARGET,
      '/interactions': API_TARGET,
      '/comments': API_TARGET,
      '/share': API_TARGET,
      '/follow': API_TARGET,
      '/profile': API_TARGET,
      // The vite dev server itself listens on http:// — but every API
      // call from the browser still goes out as https:// to nginx, which
      // is the only thing that can talk to a secure-cookie Django.
      // (HSTS is set on the API origin, not on the vite origin.)
    }
  }
})