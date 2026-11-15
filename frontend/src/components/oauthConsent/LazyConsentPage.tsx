import { Suspense, lazy } from 'react'

// Only a browser sent here by /oauth/authorize ever fetches this chunk.
const ConsentPage = lazy(() => import('./ConsentPage').then((m) => ({ default: m.ConsentPage })))

export function LazyConsentPage() {
  return (
    <Suspense fallback={null}>
      <ConsentPage />
    </Suspense>
  )
}
