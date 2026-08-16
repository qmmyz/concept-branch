import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendUrl = process.env.CONCEPT_BRANCH_BACKEND_URL || 'http://127.0.0.1:8421'

const redactProxyErrorQuery = (proxy) => {
  // Vite registers its own error logger after this hook. Redact the URL on
  // the error response first so that logger never sees query-string content.
  proxy.on('error', (_error, _request, response) => {
    if (response && typeof response === 'object' && 'req' in response && response.req?.url) {
      response.req.url = response.req.url.split('?')[0]
    }
  })
}

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: backendUrl,
        configure: redactProxyErrorQuery,
      },
    },
  },
})
