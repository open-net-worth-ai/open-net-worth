// @ts-check
import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';
import { buildSidebar } from './src/lib/corpus.mjs';
import { remarkStripLeadingH1 } from './src/lib/remark-strip-leading-h1.mjs';
import { remarkResolveCorpusLinks } from './src/lib/remark-resolve-corpus-links.mjs';
import { rehypeEmailOff } from './src/lib/rehype-email-off.mjs';

// The PUBLIC repo, which is not where this code lives today: the working repo is
// private and carries personal financial data in its history, so the plan is a
// fresh, history-free clone published under the open-net-worth-ai org. Until that
// exists, GITHUB_REPO points at a repo visitors cannot open — so the header link
// is omitted entirely rather than shipped as a 404. Set PUBLIC_REPO at deploy time
// to switch it on.
const GITHUB_REPO = process.env.PUBLIC_REPO || '';

// The corpus contract fixes each doc's public URL to its doc_id
// (`docs/product/features/budgeting.md` -> `/features/budgeting/`) so a link is
// portable between this site and the in-app /help route. That only holds true
// when the site is served from the domain root, so the default here is root
// (`base: '/'`). If this ever deploys as a GitHub Pages *project* site without a
// custom domain (served from `/my-net-worth/` instead of `/`), override SITE_BASE
// in the deploy workflow — but know that breaks doc_id/URL portability until a
// custom domain is attached.
const site = process.env.SITE_URL || 'https://docs.opennetworth.ai';

// EMBED build — the SAME site, built a second time to be served by the app itself and shown
// inside the in-app Help modal (backend/main.py mounts it at /docs). It exists so there is
// exactly ONE docs reader to maintain: before this, the app had its own React reader with its
// own stylesheet, hand-synced against this one every time the site's look changed.
// It differs from the published build in three ways and no others: a /docs/ base, its own
// output directory, and one extra stylesheet that drops the chrome a modal already provides
// (wordmark, theme switch, social links). Same content, same components, same custom.css.
const EMBED = process.env.EMBED_IN_APP === '1';
const base = process.env.SITE_BASE || (EMBED ? '/docs/' : '/');

// Google Analytics 4 is for the PUBLIC site only. The embed ships inside the app, which promises
// "no analytics, no usage tracking" (docs/product/operations/security-and-privacy.md), so it never
// gets an ID — decided here, once, rather than trusted to whoever runs the build. Unset = no tag.
const rawGaId = EMBED ? '' : (process.env.PUBLIC_GA_MEASUREMENT_ID || '').trim();
if (rawGaId && !/^G-[A-Z0-9]{4,}$/.test(rawGaId)) {
  throw new Error(`PUBLIC_GA_MEASUREMENT_ID must look like G-XXXXXXX, got "${rawGaId}".`);
}

// https://astro.build/config
export default defineConfig({
  // Kept out of dist/ so one build never clobbers the other — the published site and the
  // app-embedded copy are produced from this same config by two different commands.
  outDir: EMBED ? './dist-embed' : './dist',
  // Every in-site click was a full document load, which is what made switching
  // pages feel like a refresh. Prefetch on hover turns it into a warm cache hit.
  prefetch: { prefetchAll: true, defaultStrategy: 'hover' },
  site,
  base,
  vite: {
    define: {
      'import.meta.env.ONW_DOCS_EMBED': JSON.stringify(EMBED),
      'import.meta.env.ONW_GA_ID': JSON.stringify(rawGaId),
    },
  },
  markdown: {
    processor: unified({
      remarkPlugins: [
        // remark-strip-leading-h1.mjs: the corpus's own `# Title` is dropped
        // from the rendered body since Starlight's PageTitle already renders it.
        remarkStripLeadingH1,
        // remark-resolve-corpus-links.mjs: the corpus's relative `.md` cross-links
        // (docs/product's frozen "Cross-links" contract) become real site routes.
        [remarkResolveCorpusLinks, { base }],
      ],
      // rehype-email-off.mjs: keeps Cloudflare from turning addresses into 404ing links.
      rehypePlugins: [rehypeEmailOff],
    }),
  },
  integrations: [
    starlight({
      // The header wordmark. "Docs" is carried here (owner, 2026-08-23) so the
      // bar names the surface, not just the product — the site is reached from
      // the product itself, where "Open Net Worth" alone says nothing new.
      // It must stay ellipsis-safe on a phone: .site-title span truncates
      // rather than pushing the search pill (see custom.css).
      // The tab title is set separately in Head.astro.
      title: 'Open Net Worth Docs',
      titleDelimiter: '–',
      description:
        'An open-source agent harness for personal finance — a real multi-currency ledger an AI can act on, self-hosted on a machine you control.',
      favicon: '/favicon.svg?v=facet-solid',
      logo: {
        dark: './src/assets/logo.svg',
        light: './src/assets/logo-light.svg',
        replacesTitle: false,
      },
      social: GITHUB_REPO
        ? [{ icon: 'github', label: 'GitHub', href: `https://github.com/${GITHUB_REPO}` }]
        : [],
      // No editLink.baseUrl: the corpus lives one directory above this site's own
      // src tree, so Starlight's built-in "edit this page" link (which assumes a
      // file under this project's src/content/docs) would compute the wrong path.
      customCss: ['./src/styles/custom.css', ...(EMBED ? ['./src/styles/embed.css'] : [])],
      // The font MUST be linked here, not @import-ed from custom.css: an @import
      // is a request chained BEHIND the stylesheet, so every navigation painted
      // fallback text first and reflowed when the real face arrived.
      head: [
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.googleapis.com' } },
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true } },
        { tag: 'link', attrs: { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap' } },
      ],
      components: {
        PageTitle: './src/components/PageTitle.astro',
        MobileTableOfContents: './src/components/MobileTableOfContents.astro',
        Head: './src/components/Head.astro',
        Search: './src/components/Search.astro',
      },
      sidebar: buildSidebar(),
    }),
  ],
});
