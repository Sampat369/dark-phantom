import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'
import { casesApi, evidenceApi, timelineApi, graphApi, entitiesApi, inconsistenciesApi, duplicatesApi, summaryApi, searchApi } from '@/api/endpoints'
import { POLLING_INTERVAL, QUERY_STALE_TIME } from '@/lib/constants'
import type { Case, Evidence, Inconsistency } from '@/types'

// ─── Cases ───────────────────────────────────────────────────────────────────

export function useCases(options?: Partial<UseQueryOptions>) {
  return useQuery({
    queryKey: ['cases'],
    queryFn: () => casesApi.list(),
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
    ...(options as object),
  })
}

export function useCase(id: string | undefined) {
  return useQuery({
    queryKey: ['cases', id],
    queryFn: () => casesApi.get(id!),
    enabled: !!id,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
  })
}

export function useCreateCase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { name: string; description: string; tags?: string[] }) =>
      casesApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cases'] }),
  })
}

export function useUpdateCase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Pick<Case, 'name' | 'description' | 'tags'>> }) =>
      casesApi.update(id, data),
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['cases'] })
      qc.invalidateQueries({ queryKey: ['cases', id] })
    },
  })
}

export function useDeleteCase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => casesApi.delete(id),
    onSuccess: (_, id) => {
      void qc.invalidateQueries({ queryKey: ['cases'] })
      for (const key of ['cases', 'evidence', 'entities', 'graph', 'duplicates', 'summary', 'timeline', 'inconsistencies', 'search']) {
        qc.removeQueries({ queryKey: [key, id] })
      }
    },
  })
}

// ─── Evidence ────────────────────────────────────────────────────────────────

export function useEvidence(caseId: string | undefined) {
  return useQuery({
    queryKey: ['evidence', caseId],
    queryFn: () => evidenceApi.list(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
    refetchInterval: (query) => {
      const data = query.state.data as { items: Evidence[] } | undefined
      const hasActive = data?.items.some(
        e => e.processing_status === 'processing' || e.processing_status === 'queued'
      )
      return hasActive ? POLLING_INTERVAL : false
    },
  })
}

export function useEvidenceItem(id: string | undefined) {
  return useQuery({
    queryKey: ['evidence-item', id],
    queryFn: () => evidenceApi.get(id!),
    enabled: !!id,
    staleTime: QUERY_STALE_TIME,
  })
}

export function useUploadEvidence() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ caseId, files }: { caseId: string; files: File[] }) => {
      const formData = new FormData()
      files.forEach((file) => formData.append('files', file))
      return evidenceApi.upload(caseId, formData)
    },
    onSuccess: (_, { caseId }) => {
      void qc.invalidateQueries({ queryKey: ['cases'] })
      for (const key of ['evidence', 'entities', 'graph', 'duplicates', 'summary', 'timeline', 'inconsistencies', 'search']) {
        void qc.invalidateQueries({ queryKey: [key, caseId] })
      }
    },
  })
}

export function useEvidenceChunks(id: string | undefined) {
  return useQuery({
    queryKey: ['evidence-chunks', id],
    queryFn: () => evidenceApi.getChunks(id!),
    enabled: !!id,
    staleTime: QUERY_STALE_TIME,
  })
}

export function useCustodyLog(id: string | undefined) {
  return useQuery({
    queryKey: ['custody-log', id],
    queryFn: () => evidenceApi.getCustodyLog(id!),
    enabled: !!id,
    staleTime: QUERY_STALE_TIME,
  })
}

export function useVerifyEvidence() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => evidenceApi.verify(id),
    onSuccess: (result, id) => {
      qc.setQueryData<Evidence>(['evidence-item', id], (current) =>
        current ? { ...current, integrity_status: result.integrity_status } : current
      )
      void qc.invalidateQueries({ queryKey: ['evidence-item', id] })
      void qc.invalidateQueries({ queryKey: ['evidence'] })
      void qc.invalidateQueries({ queryKey: ['custody-log', id] })
    },
  })
}

// ─── Timeline ────────────────────────────────────────────────────────────────

export function useTimeline(caseId: string | undefined) {
  return useQuery({
    queryKey: ['timeline', caseId],
    queryFn: () => timelineApi.list(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
  })
}

export function useGenerateTimeline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (caseId: string) => timelineApi.regenerate(caseId),
    onSuccess: (_, caseId) => {
      void qc.invalidateQueries({ queryKey: ['timeline', caseId] })
    },
  })
}

// ─── Graph & Entities ─────────────────────────────────────────────────────────

export function useGraph(caseId: string | undefined) {
  return useQuery({
    queryKey: ['graph', caseId],
    queryFn: () => graphApi.get(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
  })
}

export function useEntities(caseId: string | undefined) {
  return useQuery({
    queryKey: ['entities', caseId],
    queryFn: () => entitiesApi.list(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
    select: (response) => ({
      ...response,
      items: response.items.filter((entity) => entity.case_id === caseId),
    }),
  })
}

// ─── Inconsistencies ──────────────────────────────────────────────────────────

export function useInconsistencies(caseId: string | undefined) {
  return useQuery({
    queryKey: ['inconsistencies', caseId],
    queryFn: () => inconsistenciesApi.list(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
  })
}

export function useUpdateInconsistency() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Pick<Inconsistency, 'status' | 'reviewer_notes'>> }) =>
      inconsistenciesApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inconsistencies'] }),
  })
}

// ─── Duplicates ───────────────────────────────────────────────────────────────

export function useDuplicates(caseId: string | undefined) {
  return useQuery({
    queryKey: ['duplicates', caseId],
    queryFn: () => duplicatesApi.list(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
    select: (response) => ({
      ...response,
      items: response.items.filter((group) => group.case_id === caseId),
    }),
  })
}

// ─── Summary ──────────────────────────────────────────────────────────────────

export function useSummary(caseId: string | undefined) {
  return useQuery({
    queryKey: ['summary', caseId],
    queryFn: () => summaryApi.get(caseId!),
    enabled: !!caseId,
    staleTime: QUERY_STALE_TIME,
    refetchOnMount: 'always',
    retry: false,
    select: (summary) => (summary?.case_id === caseId ? summary : null),
  })
}

export function useGenerateSummary() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (caseId: string) => summaryApi.generate(caseId),
    onSuccess: (_, caseId) => {
      for (const key of ['summary', 'entities', 'graph', 'timeline', 'inconsistencies', 'duplicates']) {
        qc.invalidateQueries({ queryKey: [key, caseId] })
      }
    },
  })
}

// ─── Search ───────────────────────────────────────────────────────────────────

export function useSearch(caseId: string | undefined, q: string) {
  return useQuery({
    queryKey: ['search', caseId, q],
    queryFn: () => searchApi.search(caseId!, q),
    enabled: !!caseId && q.length >= 2,
    staleTime: 10_000,
  })
}
