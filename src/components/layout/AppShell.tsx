import React from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { Toaster } from '@/components/ui/toaster'
import { SearchPalette } from '@/features/search/SearchPalette'

export function AppShell() {
  return (
    <div className="app-shell flex h-screen w-full overflow-hidden bg-background font-sans antialiased">
      {/* Sidebar navigation */}
      <div className="app-sidebar shrink-0"><Sidebar /></div>

      {/* Main content column */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="app-topbar shrink-0"><TopBar /></div>
        <main className="app-main flex-1 overflow-y-auto bg-muted/20 p-6">
          <Outlet />
        </main>
      </div>

      {/* Global Modals & Notifications */}
      <SearchPalette />
      <Toaster richColors position="top-right" />
    </div>
  )
}
