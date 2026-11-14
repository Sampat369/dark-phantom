import React from 'react'
import { useParams } from 'react-router-dom'
import {
  FileText,
  Sparkles,
  Users,
  CheckCircle2,
  AlertTriangle,
  Lightbulb,
  RefreshCw,
  Download,
} from 'lucide-react'
import { useSummary, useGenerateSummary } from '@/hooks/queries'
import { formatDate } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { toast } from 'sonner'

export default function SummaryView() {
  const { caseId } = useParams<{ caseId: string }>()
  const currentCaseId = caseId || 'case-001'
  const { data: summary, isLoading } = useSummary(currentCaseId)
  const generateSummary = useGenerateSummary()

  const handleGenerate = () => {
    generateSummary.mutate(currentCaseId, {
      onError: (error) => toast.error('Could not generate summary', { description: error.message }),
    })
  }
  const hasSummary = !!summary && typeof summary.executive_summary === 'string'

  const handleDownloadPdf = () => {
    const previousTitle = document.title
    document.title = `case-summary-${currentCaseId}`
    window.addEventListener('afterprint', () => {
      document.title = previousTitle
    }, { once: true })
    window.print()
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b pb-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <FileText className="h-6 w-6 text-primary" />
            AI Case Summary Brief
          </h1>
          <p className="text-sm text-muted-foreground">
            Automated legal synthesis, key findings, and strategic case recommendations.
          </p>
        </div>

        <div className="summary-actions flex flex-col items-start gap-2 sm:items-end">
          {hasSummary && (
            <Button onClick={handleDownloadPdf} variant="outline" className="gap-2">
              <Download className="h-4 w-4" />
              Download PDF
            </Button>
          )}
          <Button
            onClick={handleGenerate}
            disabled={generateSummary.isPending}
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${generateSummary.isPending ? 'animate-spin' : ''}`} />
            {generateSummary.isPending ? 'Analyzing evidence...' : 'Regenerate Brief'}
          </Button>
          {generateSummary.isPending && (
            <p role="status" className="text-xs text-muted-foreground">
              Gemini is reviewing extracted text in batches. Larger cases may take longer.
            </p>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-6">
          <Skeleton className="h-40 w-full" />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        </div>
      ) : !hasSummary ? (
        <div className="text-center py-12 text-muted-foreground">
          <Sparkles className="mx-auto h-8 w-8 mb-2 text-primary opacity-60" />
          {summary ? 'The saved summary is incomplete. Click “Regenerate Brief” to rebuild it.' : 'No summary brief generated for this case yet. Click “Regenerate Brief” to create one.'}
        </div>
      ) : (
        <div id="summary-report" className="space-y-6">
          {summary.coverage && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold">Evidence Analysis Coverage</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-xs">
                <div className="flex flex-wrap justify-between gap-2 text-muted-foreground">
                  <span>{summary.coverage.analyzed_files} of {summary.coverage.uploaded_files} files analyzed</span>
                  <span>{summary.coverage.analyzed_chunks} text segments reviewed</span>
                  <span className="font-semibold text-foreground">{summary.coverage.coverage_percentage}% by file size</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${summary.coverage.coverage_percentage}%` }} />
                </div>
                {summary.coverage.unprocessed_files.length > 0 && (
                  <div>
                    <p className="font-semibold text-amber-700 dark:text-amber-400">Not analyzed by this run</p>
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-muted-foreground">
                      {summary.coverage.unprocessed_files.map((filename) => <li key={filename}>{filename}</li>)}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Executive Summary */}
          <Card className="border-primary/30 bg-primary/5">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base font-bold text-primary flex items-center gap-2">
                  <Sparkles className="h-5 w-5" />
                  Executive Summary
                </CardTitle>
                <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                  <Badge variant="outline">{summary.model || 'Gemini'}</Badge>
                  {summary.generated_at && <span>{formatDate(summary.generated_at)}</span>}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed text-foreground/90">
                {summary.executive_summary}
              </p>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Key Facts */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-green-600" />
                  Core Factual Findings
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2 text-xs">
                  {(Array.isArray(summary.key_facts) ? summary.key_facts : []).map((fact, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="font-mono text-primary font-bold">•</span>
                      <span className="text-muted-foreground">{fact}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            {/* Strategic Recommendations */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Lightbulb className="h-4 w-4 text-amber-500" />
                  Strategic Next Steps
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2 text-xs">
                  {(Array.isArray(summary.recommendations) && summary.recommendations.length > 0
                    ? summary.recommendations
                    : ['No evidence-based next steps are available yet. Process additional exhibits and regenerate the brief.']
                  ).map((rec, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="font-mono text-amber-500 font-bold">→</span>
                      <span className="text-muted-foreground">{rec}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>

          {/* People identified in the record */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Users className="h-4 w-4 text-primary" />
                People Identified in the Record
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
                {(Array.isArray(summary.key_persons) ? summary.key_persons : []).map((p, i) => (
                  <div key={i} className="rounded-md border p-3 bg-muted/20 text-xs space-y-1">
                    <p className="font-semibold text-foreground">{p.name}</p>
                    <p className="text-[11px] text-primary font-medium">{p.role}</p>
                    <p className="text-[11px] text-muted-foreground">{p.significance}</p>
                  </div>
                ))}
                {(!Array.isArray(summary.key_persons) || summary.key_persons.length === 0) && (
                  <p className="col-span-full text-xs text-muted-foreground">No parties or other people were identified in the analyzed text.</p>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Witness Leads</CardTitle>
            </CardHeader>
            <CardContent>
              {Array.isArray(summary.witnesses) && summary.witnesses.length > 0 ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                  {summary.witnesses.map((witness, i) => (
                    <div key={`${witness.name}-${i}`} className="rounded-md border p-3 bg-muted/20 text-xs space-y-1">
                      <p className="font-semibold text-foreground">{witness.name}</p>
                      <p className="text-[11px] text-primary font-medium">{witness.role}</p>
                      <p className="text-[11px] text-muted-foreground">{witness.significance}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No witness leads were supported by the extracted text. This is not a finding that no witnesses exist; review the original files and interview records.
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
