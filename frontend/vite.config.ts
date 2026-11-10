import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { inlineAppleTouchIcon } from './inlineAppleTouchIcon'

// Dev-server proxy target. Defaults to the local backend on :8080.
// WARNING: :5174 (npm run dev) proxies /api here — that is the owner's LIVE
// app on REAL data. QA must NEVER drive :5174. All UI QA goes through
// qa/browser-use/boot_app.sh (high-port preview → high-port clone backend
// with ONW_QA_MODE=1). Override for a non-live backend: VITE_DEV_API_TARGET.
const devApiTarget = process.env.VITE_DEV_API_TARGET || 'http://localhost:8080'

// Preview-server proxy target (used by `vite preview` in the QA harness).
// boot_app.sh / make qa-e2e set VITE_API_URL=http://localhost:<BACKEND_PORT>
// before launching `npx vite preview`.  Without this proxy the built SPA's
// relative /api/* calls would hit the preview port itself (which only serves
// static files) and get 404 — the root cause of all previous QA lane failures.
const previewApiTarget = process.env.VITE_API_URL || devApiTarget

// Hostnames a self-hoster serves this dev server from. Read at config load from the
// environment or `frontend/.env.local` (both gitignored) rather than committed, because a
// hostname in a public repo identifies its operator and points at their financial data.
// Read directly instead of via Vite's `loadEnv` so the config can stay a plain object.
function extraAllowedHosts(): string[] {
  let raw = process.env.VITE_EXTRA_ALLOWED_HOSTS ?? ''
  if (!raw) {
    try {
      const envFile = readFileSync(new URL('.env.local', import.meta.url), 'utf8')
      raw = /^VITE_EXTRA_ALLOWED_HOSTS=(.*)$/m.exec(envFile)?.[1] ?? ''
    } catch {
      // No .env.local — the committed defaults are the whole allowlist.
    }
  }
  return raw.split(',').map((h) => h.trim()).filter(Boolean)
}

export default defineConfig({
  define: {
    // Stamped once per build. `state/queryCache.ts` mirrors server reads to localStorage and
    // hydrates them before first paint, so a response persisted by an OLDER build must never be
    // handed to today's components — the shape may have changed and no generic validation can
    // catch it. Namespacing the storage key by build id makes a deploy start from an empty
    // mirror instead. VITE_BUILD_ID lets CI pin it; otherwise every build gets its own.
    __BUILD_ID__: JSON.stringify(process.env.VITE_BUILD_ID || String(Date.now())),
  },
  test: {
    // Default node environment for plain .ts tests (fs/path; no DOM needed for CSS parsing).
    // .tsx tests opt in to jsdom via the `@vitest-environment jsdom` file-level docblock.
    environment: 'node',
    include: [
      'src/__tests__/**/*.test.ts',
      'src/**/*.test.ts',
      'src/**/*.test.tsx',
    ],
    // Bridge jsdom Storage objects over Node 26's experimental localStorage/sessionStorage stubs,
    // which otherwise block vitest's populateGlobal from exposing the jsdom versions globally.
    setupFiles: ['src/vitest.setup.ts'],
  },
  plugins: [
    react(),
    inlineAppleTouchIcon(),
  ],
  build: {
    // KEEP the previous build's chunks when VITE_KEEP_OLD_CHUNKS is set (the hosted demo).
    //
    // Vite empties outDir before every build. Chunk filenames are content-hashed, so a rebuild
    // renames all of them AND deletes the old ones — and any tab still open on the previous
    // build then fails its next lazy import with "Failed to fetch dynamically imported module",
    // because it is asking for a file that was just removed. The tab is dead until someone
    // works out that a hard reload fixes it.
    //
    // Hashed names mean the two builds cannot collide, so simply not deleting the old files
    // lets an open tab keep working off the build it loaded, while anything loading fresh gets
    // the new index.html and the new chunks. The cost is that outDir accumulates supersedeed
    // chunks until someone clears it, which is the right trade for a demo people have open.
    emptyOutDir: !process.env.VITE_KEEP_OLD_CHUNKS,
    // The main app chunk is ~750 kB of minified app code (components + hooks + utilities).
    // All extractable vendor libs are split into their own chunks below; the remaining weight
    // is application code that requires route-level lazy loading to reduce further (a separate
    // concern from this config-only split). Raise the limit to the observed app-code baseline
    // so the warning fires only if new vendor code accidentally lands in the app chunk.
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        // Split the big third-party libs into their own cacheable chunks so the main app chunk
        // stays well under Vite's 500 kB warning (charts + markdown were shipping as one ~960 kB
        // blob — overnight-ui-qa #10). NOTE: this repo builds with rolldown-vite, which only
        // accepts the FUNCTION form of manualChunks (the object form throws "not a function").
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          // React runtime — always first so scheduler/ is captured before vendor fallthrough
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/'))
            return 'vendor-react'
          // Chart.js (and any chartjs adapters/plugins) + lightweight-charts (the
          // asset-detail OHLCV chart) — both charting libs share one cacheable chunk.
          if (id.includes('/chart.js/') || id.includes('/chartjs') || id.includes('/lightweight-charts/'))
            return 'vendor-charts'
          // @dnd-kit family: core + sortable + modifiers + utilities + accessibility
          if (id.includes('/@dnd-kit/'))
            return 'vendor-dnd'
          // Markdown rendering: react-markdown + remark-gfm + their entire transitive tree
          // (micromark-*, mdast-util-*, unist-util-*, hast-util-*, unified, vfile*, trough,
          //  trim-lines, zwitch, ccount, character-entities*, stringify-entities,
          //  decode-named-character-reference, space-separated-tokens, bail, style-to-object,
          //  style-to-js, comma-separated-tokens, longest-streak, escape-string-regexp)
          if (
            id.includes('/react-markdown/') ||
            id.includes('/remark') ||
            id.includes('/micromark') ||
            id.includes('/mdast-util-') ||
            id.includes('/unist-util-') ||
            id.includes('/hast-util-') ||
            id.includes('/vfile/') ||
            id.includes('/vfile-') ||
            id.includes('/unified/') ||
            id.includes('/trough/') ||
            id.includes('/trim-lines/') ||
            id.includes('/zwitch/') ||
            id.includes('/ccount/') ||
            id.includes('/character-entities') ||
            id.includes('/stringify-entities/') ||
            id.includes('/decode-named-character-reference/') ||
            id.includes('/space-separated-tokens/') ||
            id.includes('/comma-separated-tokens/') ||
            id.includes('/longest-streak/') ||
            id.includes('/bail/') ||
            id.includes('/style-to-object/') ||
            id.includes('/style-to-js/')
          )
            return 'vendor-markdown'
          return undefined
        },
      },
    },
  },
  server: {
    port: 5174,
    // Dev-only. Every tab is a lazy chunk, so the first navigation to one pays for Vite
    // transforming its whole module graph right then — measured at ~6.9s for Settings
    // (~330 modules) against ~0.4s in the built bundle. Warming the route entries at server
    // start moves that compile off the interaction. Build output is unaffected.
    warmup: {
      clientFiles: [
        './src/main.tsx',
        './src/components/tabs/Settings.tsx',
        './src/components/tabs/settings/sections/*.tsx',
      ],
    },
    // Vite blocks requests whose Host header isn't recognized ("Blocked request.
    // This host is not allowed"). Wildcard covers ngrok's rotating free-tier subdomain.
    // Personal/self-hosted hostnames belong in the environment, not in a public repo: a
    // committed hostname both names its operator and advertises where a server holding a
    // complete financial book lives. Put yours in `frontend/.env.local` (gitignored):
    //     VITE_EXTRA_ALLOWED_HOSTS=my-host.example,other-host.example
    allowedHosts: ['.ngrok-free.app', 'demo.opennetworth.ai', ...extraAllowedHosts()],
    proxy: {
      '/api':          devApiTarget,
      '/data.csv':     devApiTarget,
      '/actions.json': devApiTarget,
      // The in-app docs (Help frames these). Without it Vite answers /docs/* with its own
      // SPA fallback — the app's index.html — so the Help frame sees "not the docs", falls
      // back to docs.opennetworth.ai, and dev silently reads the PUBLISHED site instead of
      // this checkout's. It looks almost right, which is what makes it worth a line here.
      '/docs':         devApiTarget,
    },
  },
  // QA harness uses `vite preview` (not `vite`), which does NOT inherit
  // server.proxy.  Declare it explicitly so /api/* requests from the built
  // SPA are forwarded to the QA backend (VITE_API_URL=http://localhost:8091).
  preview: {
    allowedHosts: ['demo.opennetworth.ai'],
    proxy: {
      '/api':          previewApiTarget,
      '/data.csv':     previewApiTarget,
      '/actions.json': previewApiTarget,
      '/docs':         previewApiTarget,
    },
  },
})
