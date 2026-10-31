import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.layers.css' // @layer declaration — must be first (RFC Phase C-1)
import '@fontsource/plus-jakarta-sans/400.css'
import '@fontsource/plus-jakarta-sans/500.css'
import '@fontsource/plus-jakarta-sans/600.css'
import '@fontsource/plus-jakarta-sans/700.css'
import './index.tokens.generated.css'
import './index.breakpoints.generated.css' // @custom-media definitions — must precede consumers (RFC Phase D-1)
import './index.css'
import './chat.css'
import './lucius.css'
import App from './App.tsx'
import { ThemeProvider } from './ThemeProvider.tsx'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'
import { ReportProblemHost } from './components/ReportProblemDialog.tsx'
import { initTheme } from './theme'
import { Gallery } from './components/ui/Gallery.tsx'
import { LazyApprovalCardGallery } from './components/chat/approval/LazyApprovalCardGallery.tsx'
import { LazyPhoneSetupPage } from './components/phoneSetup/LazyPhoneSetupPage.tsx'
import { wireDataSource } from './lucius/adapters'
import { onwDataSource, readEmbeddedDescriptors } from './lucius/onwDataSource'
import { refreshFromAdapter, seedEntityRegistry } from './lucius/entityRegistry'
import { stampPwaDisplayMode } from './utils/pwa'
import { watchStatusBarTint } from './utils/statusBarTint'

// CSS must know whether browser chrome exists before the mobile shell renders. The iOS
// navigator.standalone fallback is more reliable than the display-mode query by itself.
stampPwaDisplayMode()
watchStatusBarTint()

// Wire the ONW DataSourceAdapter — the seam between the core mention engine and
// the FastAPI backend (GET /api/mentionables, POST /api/mentions/resolve,
// GET /api/entity-types). Called once before the app renders. A no-backend host
// supplies its own adapter here instead. See RFC §14.2.1.
wireDataSource(onwDataSource)

// Re-stamp the pre-paint theme attribute (idempotent) and start following OS
// scheme changes while the preference is 'system'. See src/theme.ts.
initTheme()

// Seed the entity descriptor registry (chip icons/labels/tones) from the copy the server
// embedded in index.html — synchronous, so the first frame is right, and present even before
// login. Then refresh from GET /api/entity-types (fire-and-forget; also the only source under
// the Vite dev server, whose index.html carries no embed).
seedEntityRegistry(readEmbeddedDescriptors())
if (onwDataSource.descriptors) {
  refreshFromAdapter(onwDataSource.descriptors.bind(onwDataSource))
}

// Dev-only galleries: /?gallery=1 screenshots the whole UI-kit vocabulary, /?gallery=cards
// every approval card (approval/fixtures). The normal app path is untouched without the param.
const galleryParam = new URLSearchParams(window.location.search).get('gallery')
const showGallery = galleryParam !== null

// /phone-setup?t=… is opened by a phone with no session (the QR in Settings → Phone shortcuts):
// it renders before the sign-in gate, with the app's theme and design system but none of the app.
const showPhoneSetup = window.location.pathname === '/phone-setup'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {showGallery ? (
      galleryParam === 'cards' ? (
        <LazyApprovalCardGallery />
      ) : (
        <Gallery />
      )
    ) : showPhoneSetup ? (
      <ThemeProvider>
        <ErrorBoundary label="phone setup">
          <LazyPhoneSetupPage />
        </ErrorBoundary>
      </ThemeProvider>
    ) : (
      <ThemeProvider>
        <ErrorBoundary label="the app">
          <App />
        </ErrorBoundary>
        <ReportProblemHost />
      </ThemeProvider>
    )}
  </StrictMode>,
)
