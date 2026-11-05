import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const host = loadEnv(mode, process.cwd(), 'MOBILE_TEST_HOST').MOBILE_TEST_HOST?.trim().toLowerCase()
  if (host && !/^[a-z0-9-]+\.trycloudflare\.com$/.test(host)) {
    throw new Error('MOBILE_TEST_HOST must be one exact trycloudflare.com hostname')
  }

  return {
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      allowedHosts: host ? [host] : [],
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8001',
          changeOrigin: true,
        },
        '/health': {
          target: 'http://127.0.0.1:8001',
          changeOrigin: true,
        },
      },
    },
  }
})
