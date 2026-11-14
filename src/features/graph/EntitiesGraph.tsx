import React, { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Network, Users, Building, Mail, Phone, MapPin } from 'lucide-react'
import { useGraph, useEntities } from '@/hooks/queries'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'

export default function EntitiesGraph() {
  const { caseId } = useParams<{ caseId: string }>()
  const { data: graphData, isLoading: isGraphLoading } = useGraph(caseId || 'case-001')
  const { data: entitiesData, isLoading: isEntitiesLoading } = useEntities(caseId || 'case-001')
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null)

  const entities = (entitiesData?.items || []).filter((entity) => entity.case_id === caseId)
  const entityIds = new Set(entities.map((entity) => entity.id))
  const nodes = (graphData?.nodes || []).filter((node) => !entitiesData || entityIds.has(node.id))
  const edges = (graphData?.edges || []).filter((edge) =>
    !entitiesData || (entityIds.has(edge.source) && entityIds.has(edge.target))
  )
  const nodeLabels = new Map(nodes.map((node) => [node.id, node.label]))
  const preferredPositions: Record<string, { x: number; y: number }> = {
    'ent-001': { x: 230, y: 220 },
    'ent-002': { x: 410, y: 75 },
    'ent-003': { x: 510, y: 220 },
    'ent-004': { x: 150, y: 365 },
    'ent-005': { x: 650, y: 365 },
    'ent-006': { x: 125, y: 85 },
    'ent-007': { x: 680, y: 85 },
    'ent-009': { x: 410, y: 405 },
  }
  const positions = new Map(nodes.map((node, index) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / Math.max(nodes.length, 1)
    return [node.id, preferredPositions[node.id] || {
      x: 400 + Math.cos(angle) * 300,
      y: 230 + Math.sin(angle) * 175,
    }]
  }))

  const relationshipsFor = (entityId: string) => edges.flatMap((edge) => {
    if (edge.source === entityId) {
      return [`${edge.label} ${nodeLabels.get(edge.target) || edge.target}`]
    }
    if (edge.target === entityId) {
      const inverseLabel: Record<string, string> = {
        'employed by': 'employer of',
        'managed by': 'manages',
        'filed complaint with': 'received complaint from',
        terminated: 'was terminated by',
        'works at': 'employer of',
        'email address': 'email associated with',
        managed: 'managed by',
      }
      return [`${inverseLabel[edge.label] || `related via “${edge.label}” to`} ${nodeLabels.get(edge.source) || edge.source}`]
    }
    return []
  })

  const getEntityIcon = (type: string) => {
    switch (type) {
      case 'person': return <Users className="h-4 w-4" />
      case 'organization': return <Building className="h-4 w-4" />
      case 'email': return <Mail className="h-4 w-4" />
      case 'phone': return <Phone className="h-4 w-4" />
      case 'location': return <MapPin className="h-4 w-4" />
      default: return <Network className="h-4 w-4" />
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="border-b pb-4">
        <h1 className="text-2xl font-bold tracking-tight">Entity Relationship Graph</h1>
        <p className="text-sm text-muted-foreground">
          Visual knowledge graph mapping actors, organizations, communications, and institutional relationships.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Interactive Visual Graph Area */}
        <div className="lg:col-span-2 space-y-4">
          <Card className="overflow-hidden border bg-card">
            <CardHeader className="flex flex-row items-center justify-between border-b p-4 bg-muted/20">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Network className="h-4 w-4 text-primary" />
                Network Visualization Canvas
              </CardTitle>
              <Badge variant="outline" className="text-xs">
                {nodes.length} Nodes • {edges.length} Connections
              </Badge>
            </CardHeader>
            <CardContent className="p-6">
              {isGraphLoading ? (
                <Skeleton className="h-[420px] w-full" />
              ) : (
                <div className="h-[420px] w-full rounded-md border bg-slate-950/5 p-2 dark:bg-slate-950/40">
                  <svg
                    viewBox="0 0 800 460"
                    role="img"
                    aria-label="Entity graph with labeled relationships"
                    className="h-full w-full"
                  >
                    <defs>
                      <marker id="relationship-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                        <path d="M0,0 L8,4 L0,8 z" fill="currentColor" className="text-muted-foreground" />
                      </marker>
                    </defs>
                    {edges.map((edge) => {
                      const source = positions.get(edge.source)
                      const target = positions.get(edge.target)
                      if (!source || !target) return null
                      const midX = (source.x + target.x) / 2
                      const midY = (source.y + target.y) / 2
                      return (
                        <g key={edge.id} className="text-muted-foreground">
                          <line
                            x1={source.x}
                            y1={source.y}
                            x2={target.x}
                            y2={target.y}
                            stroke="currentColor"
                            strokeWidth="1.5"
                            markerEnd="url(#relationship-arrow)"
                          />
                          <text
                            x={midX}
                            y={midY - 5}
                            textAnchor="middle"
                            fontSize="10"
                            fill="currentColor"
                            stroke="var(--card)"
                            strokeWidth="4"
                            paintOrder="stroke"
                          >
                            {edge.label}
                          </text>
                        </g>
                      )
                    })}
                    {nodes.map((node) => {
                      const point = positions.get(node.id)
                      if (!point) return null
                      const width = Math.min(210, Math.max(130, node.label.length * 7 + 34))
                      const isSelected = selectedEntityId === node.id
                      return (
                        <g
                          key={node.id}
                          transform={`translate(${point.x} ${point.y})`}
                          role="button"
                          tabIndex={0}
                          aria-label={`${node.label} (${node.type})`}
                          onClick={() => setSelectedEntityId(node.id)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') setSelectedEntityId(node.id)
                          }}
                          className="cursor-pointer"
                        >
                          <rect
                            x={-width / 2}
                            y="-19"
                            width={width}
                            height="38"
                            rx="19"
                            fill="hsl(var(--card))"
                            stroke={isSelected ? 'hsl(var(--primary))' : 'hsl(var(--border))'}
                            strokeWidth={isSelected ? 3 : 1.5}
                          />
                          <circle cx={-width / 2 + 14} cy="0" r="5" fill={node.color} />
                          <text
                            x={-width / 2 + 25}
                            y="4"
                            fontSize="11"
                            fontWeight="600"
                            fill="hsl(var(--foreground))"
                          >
                            {node.label.length > 25 ? `${node.label.slice(0, 22)}…` : node.label}
                          </text>
                        </g>
                      )
                    })}
                  </svg>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">
                Observed Relationships ({edges.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {edges.map((edge) => (
                <div
                  key={edge.id}
                  className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-md border p-3 text-xs"
                >
                  <button
                    className="text-left font-semibold text-foreground hover:text-primary"
                    onClick={() => setSelectedEntityId(edge.source)}
                  >
                    {nodeLabels.get(edge.source) || edge.source}
                  </button>
                  <span className="text-center text-muted-foreground">
                    {edge.label} <span aria-hidden="true">→</span>
                  </span>
                  <button
                    className="text-right font-semibold text-foreground hover:text-primary"
                    onClick={() => setSelectedEntityId(edge.target)}
                  >
                    {nodeLabels.get(edge.target) || edge.target}
                  </button>
                  <span className="col-span-3 text-[10px] text-muted-foreground">
                    Supported by {edge.evidence_ids.length} exhibit{edge.evidence_ids.length === 1 ? '' : 's'}: {edge.evidence_ids.join(', ')}
                  </span>
                </div>
              ))}
              {edges.length === 0 && !isGraphLoading && (
                <p className="text-xs text-muted-foreground">No relationships have been extracted for this case yet.</p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Entities List & Details */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">
                Recognized Entities ({entities.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 max-h-[460px] overflow-y-auto">
              {isEntitiesLoading ? (
                [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-12 w-full" />)
              ) : (
                entities.map((ent) => (
                  <div
                    key={ent.id}
                    onClick={() => setSelectedEntityId(ent.id)}
                    className={`flex items-start justify-between p-2.5 rounded-md border text-xs cursor-pointer transition-colors ${
                      selectedEntityId === ent.id
                        ? 'border-primary bg-primary/5'
                        : 'hover:bg-muted/40'
                    }`}
                  >
                    <div className="flex items-start gap-2.5">
                      <div className="p-1.5 rounded bg-muted text-muted-foreground mt-0.5">
                        {getEntityIcon(ent.type)}
                      </div>
                      <div>
                        <p className="font-semibold text-foreground">{ent.name}</p>
                        <p className="text-[11px] text-muted-foreground line-clamp-1">
                          {ent.description || ent.type}
                        </p>
                        {relationshipsFor(ent.id).length > 0 && (
                          <p className="mt-1 text-[11px] text-primary/80">
                            {relationshipsFor(ent.id).join(' · ')}
                          </p>
                        )}
                      </div>
                    </div>
                    <Badge variant="secondary" className="text-[10px] py-0 px-1.5">
                      {ent.mention_count} mentions
                    </Badge>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
