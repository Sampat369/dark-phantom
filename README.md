# EvidenceIQ - AI-Powered Legal Evidence Intelligence

EvidenceIQ is an enterprise-grade AI-powered legal investigation platform designed to ingest, verify, analyze, and synthesize large volumes of digital evidence (PDFs, contracts, email threads, Slack chats, audio/video recordings, and institutional documents).

## Features
- **Case Management Dashboard**: Register, organize, and monitor investigations.
- **Evidence Repository & Viewer**: Ingest documents, verify cryptographic SHA-256 integrity, inspect page chunks, and review chain of custody records.
- **AI Evidence Assistant (Chat)**: SSE-streamed conversational AI strictly grounded in verified case exhibits with interactive citation inspection.
- **Case Chronology & Timeline**: Interactive chronological reconstruction synthesized from cross-document timelines and inferred milestones.
- **Entity Relationship Knowledge Graph**: Graph visualization connecting actors, corporate entities, communications, and locations.
- **Contradiction & Inconsistency Detection**: Flag conflicting testimonies, mismatched dates, altered files, and contradictory performance assessments.
- **Duplicate Exhibit Detection**: Automated detection of duplicate or near-duplicate documents via hash and semantic vector similarity.
- **Executive Legal Brief Synthesis**: One-click comprehensive case briefs with key facts, witness summaries, and strategic recommendations.
- **Command Palette Search (⌘K)**: Instant semantic search across all case exhibits, excerpts, and transcripts.

---

## Prerequisites
- **Node.js**: v18.0.0 or higher
- **npm**: v9.0.0 or higher

---

## Setup Instructions

1. Clone or navigate to the project directory:
```bash
cd C:\Users\SINCHANA\.gemini\antigravity\scratch\evidenceiq
```

2. Install dependencies:
```bash
npm install
```

3. Configure environment variables:
```bash
cp .env.example .env
```

4. Start development server with MSW mock API enabled:
```bash
npm run dev
```
Open your browser at `http://localhost:5173`.

---

## Environment Variables

| Variable | Default Value | Description |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | Backend API base URL for REST and SSE endpoints |
| `VITE_USE_MOCKS` | `true` | When `true`, enables browser MSW service worker with full realistic mock data |

---

## Available Scripts

- `npm run dev`: Starts the Vite development server with hot-module reloading.
- `npm run build`: Compiles TypeScript and builds production distribution in `dist/`.
- `npm run preview`: Previews the production build locally.
- `npm run test`: Runs Vitest test suite in jsdom environment.

---

## Screenshot Checklist (All 10 Pages & Views)

- [ ] 1. **Case Management Dashboard** (`/cases`) - Grid of case cards, statuses, tags, and new case dialog
- [ ] 2. **Evidence Library** (`/cases/:id/evidence`) - Exhibit repository table with types, integrity status, and filtering
- [ ] 3. **Evidence Viewer** (`/cases/:id/evidence/:id`) - Document preview, extracted text chunks, and chain-of-custody audit trail
- [ ] 4. **AI Evidence Assistant** (`/cases/:id/chat`) - Conversational interface with citation badges and inspector sidebar
- [ ] 5. **Timeline Chronology** (`/cases/:id/timeline`) - Vertical timeline of chronological milestones with linked exhibits
- [ ] 6. **Entity Relationship Graph** (`/cases/:id/entities`) - Interactive network graph connecting actors, organizations, and communications
- [ ] 7. **Evidentiary Inconsistencies** (`/cases/:id/inconsistencies`) - Side-by-side contradiction cards and severity markers
- [ ] 8. **Duplicate Detection** (`/cases/:id/duplicates`) - Matched duplicate groups and SHA-256 similarity analysis
- [ ] 9. **AI Case Summary Brief** (`/cases/:id/summary`) - Executive summary, core findings, chronology, and strategic recommendations
- [ ] 10. **Command Palette** (`⌘K` / `Ctrl+K`) - Modal search overlay displaying query matches with live score badges
