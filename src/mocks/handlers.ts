import { http, HttpResponse } from 'msw'
import {
  mockCases,
  mockEvidence,
  mockTimeline,
  mockGraph,
  mockEntities,
  mockInconsistencies,
  mockDuplicateGroups,
  mockSummary,
  mockChatSessions,
  mockMessages,
  mockSearchResults,
  mockCustodyLog,
  mockChunks,
  MOCK_NOW,
} from './data'
import type { Case, Citation, Evidence, Inconsistency } from '@/types'

const delay = (ms: number) => new Promise(r => setTimeout(r, ms))
const mockEvidenceFiles = new Map<string, File>()

const createMockChatAnswer = (caseId: string, question: string) => {
  const normalizedQuestion = question.trim().toLowerCase()
  if (/^(hi|hey|hello|heya|good morning|good afternoon|good evening)[!. ,?]*$/.test(normalizedQuestion)) {
    return { answer: 'Hi! Ask me anything about the evidence in this case. You can use casual wording, for example: “what happened before James got fired?”', citations: [] as Citation[] }
  }
  if (/^(thanks|thank you|thx|ty)[!. ,?]*$/.test(normalizedQuestion)) {
    return { answer: 'You’re welcome. If you want, ask a follow-up about one of the cited exhibits or events.', citations: [] as Citation[] }
  }

  const caseEvidenceIds = new Set(
    mockEvidence.filter((evidence) => evidence.case_id === caseId).map((evidence) => evidence.id)
  )
  const isTimelineQuestion = /timeline|sequence|leading up|what happened before|order of events|before.*(fired|terminated|let go)/.test(normalizedQuestion)
  if (isTimelineQuestion) {
    const events = mockTimeline
      .filter((event) => event.case_id === caseId && event.timestamp >= '2023-12-01' && event.timestamp <= '2024-04-02')
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    if (events.length > 0) {
      const citations: Citation[] = []
      const lines = events.map((event) => {
        const source = event.evidence_ids.map((id) => mockEvidence.find((evidence) => evidence.id === id)).find(Boolean)
        if (!source) return `• ${event.timestamp.slice(0, 10)} — ${event.title}: ${event.description}`
        const number = citations.length + 1
        citations.push({
          number,
          evidence_id: source.id,
          evidence_filename: source.original_filename,
          snippet: event.description,
          page: null,
          timestamp: null,
          chunk_id: `timeline-${event.id}`,
        })
        return `• ${event.timestamp.slice(0, 10)} — ${event.title}: ${event.description} [${number}]`
      })
      return {
        answer: `Here’s the timeline I found in this case:\n\n${lines.join('\n')}\n\nThe records show the termination followed the March 15 HR complaint by 18 days.`,
        citations,
      }
    }
  }

  const stopWords = new Set(['about', 'after', 'again', 'does', 'from', 'have', 'into', 'that', 'them', 'then', 'there', 'these', 'they', 'this', 'what', 'when', 'where', 'which', 'with', 'would', 'your', 'could', 'should', 'the', 'and', 'for', 'was', 'were', 'are', 'how', 'who', 'why', 'can', 'did', 'has', 'had', 'his', 'her', 'their', 'it', 'me', 'please', 'summarise', 'summarize', 'summary'])
  const terms = normalizedQuestion.match(/[a-z0-9]+/g)?.filter((word) => word.length > 2 && !stopWords.has(word)) ?? []
  const synonyms: Record<string, string[]> = {
    fire: ['fired', 'firing', 'termination', 'terminated'],
    fired: ['fire', 'firing', 'termination', 'terminated'],
    firing: ['fire', 'fired', 'termination', 'terminated'],
    terminated: ['termination', 'fired', 'fire'],
    laid: ['termination', 'fired', 'terminated'],
    boss: ['manager', 'supervisor'],
    complained: ['complaint', 'reported', 'hr'],
    reported: ['complaint', 'complained', 'hr'],
    warnings: ['warning', 'disciplinary'],
    warning: ['warnings', 'disciplinary'],
    unhappy: ['complaint', 'harassment'],
    bullied: ['harassment', 'complaint'],
  }
  const expandedTerms = new Set(terms)
  terms.forEach((term) => synonyms[term]?.forEach((synonym) => expandedTerms.add(synonym)))
  const normalizedTerms = [...expandedTerms].map((word) => word.replace(/(ing|ed|es|s)$/, ''))

  const isSummaryRequest = /summari[sz](?:e|ing|ation)?|summary|overview|gist/i.test(normalizedQuestion)
  let matches = mockSearchResults
    .filter((result) => caseEvidenceIds.has(result.evidence_id))
    .map((result) => {
      const sourceText = `${result.evidence_filename} ${result.content}`.toLowerCase()
      const sourceWords = sourceText.match(/[a-z0-9]+/g) ?? []
      const score = normalizedTerms.reduce((total, term) => {
        return total + (sourceWords.some((word) => word === term || word.startsWith(term) || term.startsWith(word)) ? 1 : 0)
      }, 0)
      return { result, score }
    })
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)

  if (isSummaryRequest) {
    const namedFileMatches = matches.filter(({ result }) => {
      const filenameWords = result.evidence_filename.toLowerCase().match(/[a-z0-9]+/g) ?? []
      const filenameTermMatches = normalizedTerms.filter((term) =>
        filenameWords.some((word) => word === term || word.startsWith(term) || term.startsWith(word))
      )
      return filenameTermMatches.length >= 2
    })
    if (namedFileMatches.length > 0) matches = namedFileMatches.slice(0, 1)
  }

  if (matches.length === 0) {
    return {
      answer: `I couldn't find an indexed exhibit in this case that directly matches “${question.trim()}”. Try asking about a person, date, event, or document title in the case evidence.`,
      citations: [] as Citation[],
    }
  }

  const citations: Citation[] = matches.map(({ result }, index) => ({
    number: index + 1,
    evidence_id: result.evidence_id,
    evidence_filename: result.evidence_filename,
    snippet: result.content,
    page: result.page,
    timestamp: result.timestamp,
    chunk_id: result.chunk_id,
  }))
  if (isSummaryRequest) {
    const summary = matches.map(({ result }, index) =>
      `• ${result.evidence_filename}: ${result.content} [${index + 1}]`
    ).join('\n\n')
    return {
      answer: `Here’s a summary of the matching evidence:\n\n${summary}\n\nThis summary reflects the indexed excerpts available for this file.`,
      citations,
    }
  }

  const excerpts = matches.map(({ result }, index) =>
    `[${index + 1}] ${result.evidence_filename}${result.page ? `, page ${result.page}` : ''}: “${result.content}”`
  )

  return {
    answer: `I found ${matches.length} relevant exhibit${matches.length === 1 ? '' : 's'} for “${question.trim()}”:\n\n${excerpts.join('\n\n')}\n\nThese excerpts are the matching records I can cite. They may not capture the full context of each document.`,
    citations,
  }
}

export const handlers = [
  // ─── Cases ────────────────────────────────────────────────────────────────

  http.get('/api/v1/cases', async () => {
    await delay(300)
    return HttpResponse.json({ items: mockCases, total: mockCases.length, page: 1, page_size: 20, has_next: false })
  }),

  http.get('/api/v1/cases/:id', async ({ params }) => {
    await delay(200)
    const c = mockCases.find(c => c.id === params.id)
    if (!c) return HttpResponse.json({ detail: 'Case not found', status_code: 404 }, { status: 404 })
    return HttpResponse.json(c)
  }),

  http.post('/api/v1/cases', async ({ request }) => {
    await delay(400)
    const body = await request.json() as Partial<Case>
    const newCase: Case = {
      id: `case-${Date.now()}`,
      name: body.name ?? 'New Case',
      description: body.description ?? '',
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
      evidence_count: 0,
      processing_status: 'queued',
      tags: body.tags ?? [],
    }
    mockCases.push(newCase)
    return HttpResponse.json(newCase, { status: 201 })
  }),

  http.patch('/api/v1/cases/:id', async ({ params, request }) => {
    await delay(300)
    const idx = mockCases.findIndex(c => c.id === params.id)
    if (idx === -1) return HttpResponse.json({ detail: 'Not found', status_code: 404 }, { status: 404 })
    const body = await request.json() as Partial<Case>
    mockCases[idx] = { ...mockCases[idx], ...body, updated_at: MOCK_NOW }
    return HttpResponse.json(mockCases[idx])
  }),

  http.delete('/api/v1/cases/:id', async ({ params }) => {
    await delay(300)
    const idx = mockCases.findIndex(c => c.id === params.id)
    if (idx === -1) return HttpResponse.json({ detail: 'Not found', status_code: 404 }, { status: 404 })
    mockCases.splice(idx, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  // ─── Evidence ─────────────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/evidence', async ({ params }) => {
    await delay(300)
    const items = mockEvidence.filter(e => e.case_id === params.caseId)
    return HttpResponse.json({ items, total: items.length, page: 1, page_size: 50, has_next: false })
  }),

  http.post('/api/v1/cases/:caseId/evidence', async ({ params, request }) => {
    await delay(500)
    const formData = await request.formData()
    const files = formData.getAll('files').filter((entry): entry is File => entry instanceof File)
    if (files.length === 0) {
      return HttpResponse.json({ detail: 'Select at least one file to upload' }, { status: 400 })
    }

    const items = files.map((file) => {
      const extension = file.name.split('.').pop()?.toLowerCase() || ''
      const type: Evidence['type'] = extension === 'pdf' ? 'pdf'
        : ['jpg', 'jpeg', 'png', 'gif', 'webp', 'tiff'].includes(extension) ? 'image'
        : ['eml', 'msg'].includes(extension) ? 'email'
        : ['mp3', 'wav', 'm4a', 'aac', 'ogg', 'oga', 'flac'].includes(extension) ? 'audio'
        : ['mp4', 'mov', 'avi', 'mkv', 'webm', 'mpeg', 'mpg', '3gp'].includes(extension) ? 'video'
        : extension === 'json' ? 'chat'
        : ['txt', 'doc', 'docx'].includes(extension) ? 'document'
        : 'other'
      const newEv = {
        id: `ev-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        case_id: params.caseId as string,
        filename: file.name,
        original_filename: file.name,
        type,
        size: file.size,
        sha256: '',
        integrity_status: 'unchecked' as const,
        processing_status: 'queued' as const,
        upload_date: MOCK_NOW,
        processed_at: null,
        page_count: null,
        duration: null,
        is_duplicate: false,
        duplicate_of: null,
        metadata: {},
      }
      mockEvidence.push(newEv)
      mockEvidenceFiles.set(newEv.id, file)
      return newEv
    })
    const parentCase = mockCases.find(c => c.id === params.caseId)
    if (parentCase) {
      parentCase.evidence_count += items.length
      parentCase.updated_at = MOCK_NOW
    }
    return HttpResponse.json({ items }, { status: 201 })
  }),

  http.get('/api/v1/evidence/:id', async ({ params }) => {
    await delay(200)
    const ev = mockEvidence.find(e => e.id === params.id)
    if (!ev) return HttpResponse.json({ detail: 'Not found', status_code: 404 }, { status: 404 })
    return HttpResponse.json(ev)
  }),

  http.get('/api/v1/evidence/:id/file', async ({ params }) => {
    const file = mockEvidenceFiles.get(params.id as string)
    if (!file) return HttpResponse.json({ detail: 'Preview unavailable. Upload this file again to preview it.' }, { status: 404 })
    const extension = file.name.split('.').pop()?.toLowerCase() || ''
    const mimeTypes: Record<string, string> = {
      pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
      gif: 'image/gif', webp: 'image/webp', txt: 'text/plain', csv: 'text/csv',
      json: 'application/json', eml: 'message/rfc822', mp3: 'audio/mpeg', wav: 'audio/wav',
      mp4: 'video/mp4',
    }
    return new HttpResponse(file, {
      headers: {
        'Content-Type': file.type || mimeTypes[extension] || 'application/octet-stream',
        'Content-Disposition': `inline; filename*=UTF-8''${encodeURIComponent(file.name)}`,
      },
    })
  }),

  http.get('/api/v1/evidence/:id/chunks', async ({ params }) => {
    await delay(200)
    const chunks = mockChunks.filter(c => c.evidence_id === params.id)
    return HttpResponse.json({ items: chunks, total: chunks.length, page: 1, page_size: 50, has_next: false })
  }),

  http.get('/api/v1/evidence/:id/custody-log', async ({ params }) => {
    await delay(200)
    const events = mockCustodyLog.filter(e => e.evidence_id === params.id)
    return HttpResponse.json({ items: events, total: events.length, page: 1, page_size: 50, has_next: false })
  }),

  http.post('/api/v1/evidence/:id/verify', async ({ params }) => {
    await delay(1000)
    const ev = mockEvidence.find(e => e.id === params.id)
    if (!ev) return HttpResponse.json({ detail: 'Not found', status_code: 404 }, { status: 404 })
    return HttpResponse.json({ evidence_id: params.id, integrity_status: ev.integrity_status, verified_at: MOCK_NOW })
  }),

  // ─── Timeline ─────────────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/timeline', async ({ params }) => {
    await delay(300)
    const items = mockTimeline.filter(t => t.case_id === params.caseId)
    return HttpResponse.json({ items, total: items.length })
  }),

  // ─── Graph & Entities ─────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/graph', async () => {
    await delay(400)
    return HttpResponse.json(mockGraph)
  }),

  http.get('/api/v1/cases/:caseId/entities', async ({ params }) => {
    await delay(300)
    const items = mockEntities.filter(e => e.case_id === params.caseId)
    return HttpResponse.json({ items, total: items.length })
  }),

  // ─── Inconsistencies ──────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/inconsistencies', async ({ params }) => {
    await delay(300)
    const items = mockInconsistencies.filter(i => i.case_id === params.caseId)
    return HttpResponse.json({ items, total: items.length })
  }),

  http.patch('/api/v1/inconsistencies/:id', async ({ params, request }) => {
    await delay(300)
    const idx = mockInconsistencies.findIndex(i => i.id === params.id)
    if (idx === -1) return HttpResponse.json({ detail: 'Not found', status_code: 404 }, { status: 404 })
    const body = await request.json() as Partial<Inconsistency>
    mockInconsistencies[idx] = { ...mockInconsistencies[idx], ...body }
    return HttpResponse.json(mockInconsistencies[idx])
  }),

  // ─── Duplicates ───────────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/duplicates', async () => {
    await delay(300)
    return HttpResponse.json({ items: mockDuplicateGroups, total: mockDuplicateGroups.length })
  }),

  // ─── Summary ──────────────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/summary', async () => {
    await delay(400)
    return HttpResponse.json(mockSummary)
  }),

  http.post('/api/v1/cases/:caseId/summary', async () => {
    await delay(2000)
    return HttpResponse.json({ ...mockSummary, generated_at: MOCK_NOW }, { status: 201 })
  }),

  // ─── Search ───────────────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/search', async ({ request }) => {
    await delay(300)
    const url = new URL(request.url)
    const q = url.searchParams.get('q')?.toLowerCase() ?? ''
    const results = q.length >= 2
      ? mockSearchResults.filter(r => r.content.toLowerCase().includes(q))
      : []
    return HttpResponse.json({ items: results, total: results.length, query: q })
  }),

  // ─── Chat (SSE streaming mock) ────────────────────────────────────────────

  http.post('/api/v1/cases/:caseId/chat', async ({ params, request }) => {
    await delay(100)
    const body = await request.json() as { message: string; session_id?: string }
    const userMessage = body.message ?? ''

    const { answer, citations } = createMockChatAnswer(params.caseId as string, userMessage)
    const assistantTokens = answer.split(/(?<=\s)/)

    const stream = new ReadableStream({
      async start(controller) {
        const encoder = new TextEncoder()

        // Send session info
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'session', session_id: 'session-' + Date.now(), message_id: 'msg-' + Date.now() })}\n\n`))
        await delay(50)

        // Stream tokens
        for (const token of assistantTokens) {
          await delay(12)
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'token', content: token })}\n\n`))
        }

        // Send citations
        await delay(100)
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'citations', citations })}\n\n`))

        // Done
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'done', user_message: userMessage })}\n\n`))
        controller.close()
      }
    })

    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
    })
  }),

  // ─── Chat Sessions ────────────────────────────────────────────────────────

  http.get('/api/v1/cases/:caseId/chat/sessions', async ({ params }) => {
    await delay(200)
    const items = mockChatSessions.filter(s => s.case_id === params.caseId)
    return HttpResponse.json({ items, total: items.length })
  }),

  http.get('/api/v1/cases/:caseId/chat/sessions/:sessionId/messages', async () => {
    await delay(200)
    return HttpResponse.json({ items: mockMessages, total: mockMessages.length })
  }),
]
