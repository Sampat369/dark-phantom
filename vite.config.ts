import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'
import path from 'path'
import { loadEnv, type Plugin } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function geminiProxy(apiKey: string): Plugin {
  return {
    name: 'local-gemini-proxy',
    configureServer(server) {
      server.middlewares.use('/__ai/gemini', async (request, response) => {
        if (request.method !== 'POST') {
          response.statusCode = 405
          response.end(JSON.stringify({ detail: 'POST required.' }))
          return
        }
        if (!apiKey) {
          response.statusCode = 503
          response.setHeader('Content-Type', 'application/json')
          response.end(JSON.stringify({ detail: 'Gemini is not configured. Add GEMINI_API_KEY to .env.local and restart the dev server.' }))
          return
        }
        try {
          const body: Buffer[] = []
          for await (const part of request) body.push(Buffer.from(part))
          const prompt = JSON.parse(Buffer.concat(body).toString('utf8')) as { prompt?: string }
          if (typeof prompt.prompt !== 'string' || !prompt.prompt.trim()) {
            response.statusCode = 400
            response.setHeader('Content-Type', 'application/json')
            response.end(JSON.stringify({ detail: 'A non-empty prompt is required.' }))
            return
          }
          const upstream = await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
            signal: AbortSignal.timeout(120_000),
            body: JSON.stringify({
              contents: [{ role: 'user', parts: [{ text: prompt.prompt }] }],
              generationConfig: { responseMimeType: 'application/json', temperature: 0.1, maxOutputTokens: 4096 },
            }),
          })
          const payload = await upstream.json() as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>; error?: { message?: string } }
          if (!upstream.ok) {
            response.statusCode = upstream.status
            response.setHeader('Content-Type', 'application/json')
            response.end(JSON.stringify({ detail: payload.error?.message || `Gemini request failed (${upstream.status}).` }))
            return
          }
          const text = payload.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('') || ''
          response.statusCode = 200
          response.setHeader('Content-Type', 'application/json')
          response.end(JSON.stringify({ text }))
        } catch (error) {
          response.statusCode = 502
          response.setHeader('Content-Type', 'application/json')
          response.end(JSON.stringify({ detail: error instanceof Error ? error.message : 'Gemini proxy request failed.' }))
        }
      })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  return {
  plugins: [react(), geminiProxy(process.env.GEMINI_API_KEY || env.GEMINI_API_KEY || '')],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000' } },
  test: { globals: true, environment: 'jsdom', setupFiles: ['./src/test/setup.ts'] },
  }
})
