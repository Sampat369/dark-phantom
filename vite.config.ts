import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'
import path from 'path'
import { Readable } from 'node:stream'
import { loadEnv, type Plugin } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function geminiProxy(apiKey: string): Plugin {
  return {
    name: 'local-gemini-proxy',
    configureServer(server) {
      server.middlewares.use('/__ai/gemini-file', async (request, response) => {
        const sendJson = (status: number, value: unknown) => {
          response.statusCode = status
          response.setHeader('Content-Type', 'application/json')
          response.end(JSON.stringify(value))
        }
        if (request.method !== 'POST') return sendJson(405, { detail: 'POST required.' })
        if (!apiKey) return sendJson(503, { detail: 'Gemini is not configured. Add GEMINI_API_KEY to .env.local and restart Vite.' })

        const size = Number(request.headers['content-length'] || 0)
        if (!size) return sendJson(411, { detail: 'The evidence upload did not include a content length.' })
        if (size > 500 * 1024 * 1024) return sendJson(413, { detail: 'This file exceeds the local Gemini analysis limit of 500 MB.' })
        const encodedName = String(request.headers['x-evidence-name'] || 'evidence')
        let displayName = 'evidence'
        try { displayName = decodeURIComponent(encodedName).replace(/[\r\n\0]/g, '').slice(0, 180) } catch { displayName = 'evidence' }
        const extension = displayName.split('.').pop()?.toLowerCase() || ''
        const mimeFromExtension: Record<string, string> = {
          pdf: 'application/pdf', mp4: 'video/mp4', mov: 'video/quicktime', avi: 'video/x-msvideo',
          webm: 'video/webm', mkv: 'video/x-matroska', mpeg: 'video/mpeg', mpg: 'video/mpeg',
          png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp',
          mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4', aac: 'audio/aac', ogg: 'audio/ogg', flac: 'audio/flac',
        }
        const providedMime = String(request.headers['content-type'] || '').split(';')[0].toLowerCase()
        const rawMime = providedMime && providedMime !== 'application/octet-stream' ? providedMime : mimeFromExtension[extension] || providedMime
        const allowedMime = rawMime === 'application/pdf' || rawMime.startsWith('video/') || rawMime.startsWith('image/') || rawMime.startsWith('audio/')
        if (!allowedMime) return sendJson(415, { detail: 'Direct Gemini file analysis supports PDF, image, audio, and video files. Text and Word files are indexed locally.' })
        if (rawMime === 'application/pdf' && size > 50 * 1024 * 1024) return sendJson(413, { detail: 'Gemini file analysis supports PDF files up to 50 MB. Split this PDF into smaller files and upload them again.' })

        let uploadedName = ''
        try {
          const prompt = Buffer.from(String(request.headers['x-ai-prompt'] || ''), 'base64').toString('utf8')
          const schemaHeader = String(request.headers['x-ai-schema'] || '')
          const responseJsonSchema = schemaHeader ? JSON.parse(Buffer.from(schemaHeader, 'base64').toString('utf8')) as Record<string, unknown> : undefined
          if (!prompt.trim()) return sendJson(400, { detail: 'An evidence analysis prompt is required.' })

          const start = await fetch('https://generativelanguage.googleapis.com/upload/v1beta/files', {
            method: 'POST',
            headers: {
              'x-goog-api-key': apiKey,
              'X-Goog-Upload-Protocol': 'resumable',
              'X-Goog-Upload-Command': 'start',
              'X-Goog-Upload-Header-Content-Length': String(size),
              'X-Goog-Upload-Header-Content-Type': rawMime,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ file: { display_name: displayName } }),
            signal: AbortSignal.timeout(30_000),
          })
          if (!start.ok) {
            const error = await start.json().catch(() => ({})) as { error?: { message?: string } }
            return sendJson(start.status, { detail: error.error?.message || `Gemini could not start the file upload (${start.status}).` })
          }
          const uploadUrl = start.headers.get('x-goog-upload-url')
          if (!uploadUrl) return sendJson(502, { detail: 'Gemini did not return a resumable upload URL.' })

          const upload = await fetch(uploadUrl, {
            method: 'POST',
            headers: {
              'Content-Length': String(size),
              'X-Goog-Upload-Offset': '0',
              'X-Goog-Upload-Command': 'upload, finalize',
            },
            body: Readable.toWeb(request),
            duplex: 'half',
            signal: AbortSignal.timeout(10 * 60_000),
          } as RequestInit & { duplex: 'half' })
          const uploaded = await upload.json() as { file?: { name?: string; uri?: string; mimeType?: string; state?: string }; error?: { message?: string } }
          if (!upload.ok || !uploaded.file?.name || !uploaded.file.uri) {
            return sendJson(upload.status || 502, { detail: uploaded.error?.message || 'Gemini could not store the evidence file.' })
          }
          uploadedName = uploaded.file.name
          let file = uploaded.file
          const processingDeadline = Date.now() + 4 * 60_000
          while (file.state && file.state !== 'ACTIVE' && Date.now() < processingDeadline) {
            if (file.state === 'FAILED') return sendJson(422, { detail: `Gemini could not process ${displayName}.` })
            await new Promise((resolve) => setTimeout(resolve, 2500))
            const statusResponse = await fetch(`https://generativelanguage.googleapis.com/v1beta/${file.name}`, {
              headers: { 'x-goog-api-key': apiKey },
              signal: AbortSignal.timeout(20_000),
            })
            if (!statusResponse.ok) break
            const statusPayload = await statusResponse.json() as { file?: typeof file; name?: string; uri?: string; mimeType?: string; state?: string }
            file = statusPayload.file || statusPayload
          }
          if (file.state && file.state !== 'ACTIVE') return sendJson(504, { detail: `Gemini is still processing ${displayName}. Try generating the report again in a moment.` })

          const result = await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
            signal: AbortSignal.timeout(10 * 60_000),
            body: JSON.stringify({
              contents: [{ role: 'user', parts: [
                { file_data: { mime_type: file.mimeType || rawMime, file_uri: file.uri } },
                { text: prompt },
              ] }],
              generationConfig: {
                responseMimeType: 'application/json',
                ...(responseJsonSchema ? { responseJsonSchema } : {}),
                temperature: 0.1,
                maxOutputTokens: 8192,
              },
            }),
          })
          const generated = await result.json() as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>; error?: { message?: string } }
          if (!result.ok) return sendJson(result.status, { detail: generated.error?.message || `Gemini evidence analysis failed (${result.status}).` })
          const text = generated.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('') || ''
          return sendJson(200, { text })
        } catch (error) {
          return sendJson(502, { detail: error instanceof Error ? error.message : 'Gemini evidence analysis failed.' })
        } finally {
          if (uploadedName) {
            void fetch(`https://generativelanguage.googleapis.com/v1beta/${uploadedName}`, {
              method: 'DELETE',
              headers: { 'x-goog-api-key': apiKey },
              signal: AbortSignal.timeout(10_000),
            }).catch(() => undefined)
          }
        }
      })

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
          const prompt = JSON.parse(Buffer.concat(body).toString('utf8')) as { prompt?: string; responseJsonSchema?: Record<string, unknown> }
          if (typeof prompt.prompt !== 'string' || !prompt.prompt.trim()) {
            response.statusCode = 400
            response.setHeader('Content-Type', 'application/json')
            response.end(JSON.stringify({ detail: 'A non-empty prompt is required.' }))
            return
          }
          const upstream = await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
            signal: AbortSignal.timeout(120_000),
            body: JSON.stringify({
              contents: [{ role: 'user', parts: [{ text: prompt.prompt }] }],
              generationConfig: {
                responseMimeType: 'application/json',
                ...(prompt.responseJsonSchema ? { responseJsonSchema: prompt.responseJsonSchema } : {}),
                temperature: 0.1,
                maxOutputTokens: 8192,
              },
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
