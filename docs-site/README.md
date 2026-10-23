# Open Net Worth — docs site

The public documentation site: [Astro](https://astro.build) +
[Starlight](https://starlight.astro.build), deployed by Cloudflare Pages.

This site has **no content of its own**. Every page it renders is built from
[`../docs/product/**/*.md`](../docs/product) — the one corpus, also read by the backend's
`search_docs`/`read_doc` AI tools and the in-app `/help` route. See
`docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md`
for the full contract. **Never add or edit a `.md` file under `docs-site/`** — corpus content
belongs in `docs/product/`, in this site's `landing page (src/pages/index.astro)` links into it
but restates none of it.

## Run it locally

```bash
npm install
npm run dev       # → http://localhost:4321, live-reloads on changes to ../docs/product too
```

## Build it

```bash
npm install
npm run build      # → dist/, then runs the internal-link integrity check automatically
npm run preview     # serve the built dist/ locally to sanity-check before deploying
```

A build fails if:

- any corpus page's frontmatter doesn't match the frozen contract (missing/invalid required
  keys — see the spec's "Corpus contract" section), or
- the rendered output contains a dead internal link — a link to a raw `.md` file (the corpus
  cross-link contract requires relative `.md` links; something is wrong if one survives
  unrewritten) or a path that isn't a real page/asset in `dist/`
  (`scripts/check-internal-links.mjs`, run as `postbuild`).

Both are deliberate: this is a self-hosted, privacy-focused product and its docs must not
silently ship a broken or overselling page.

## How it reads the corpus

`src/content.config.ts` defines the `docs` collection with Astro's own `glob()` loader
(`astro/loaders`), pointed at `base: '../docs/product'` — **not** Starlight's built-in
`docsLoader()`, which only ever scans `src/content/docs/` inside this project. A page's id is
its `doc_id` verbatim (`features/budgeting.md` → `features/budgeting` → the route
`/features/budgeting/`), so a link is portable between this site and the in-app `/help` route.

The sidebar is **not** Starlight's `autogenerate` (which assumes the same fixed
`src/content/docs/` layout and silently produces a broken/empty tree once the collection lives
elsewhere — the reason we don't use it). `src/lib/corpus.mjs` scans `docs/product/` directly at
config-eval time and builds an explicit, hand-ordered `sidebar` array grouped by category,
matching the spec's directory ordering (`getting-started → concepts → features → lucius →
connectors → operations → troubleshooting → reference`) with human-readable group labels.

Two remark plugins in the markdown pipeline (`astro.config.mjs`) adapt the corpus for this
renderer specifically, without changing the corpus itself:

- `src/lib/remark-strip-leading-h1.mjs` — the corpus requires every file to open with a `#
  Title` matching its frontmatter `title` (so a raw-markdown reader — the AI tool, the in-app
  viewer — is self-contained). Starlight already renders `frontmatter.title` as the page's own
  H1, so this strips the redundant one from the body to avoid showing the title twice.
- `src/lib/remark-resolve-corpus-links.mjs` — rewrites the corpus's relative `.md` cross-links
  (e.g. `[the ledger](../concepts/the-ledger.md)`) to this site's actual routes
  (`/concepts/the-ledger/`), anchors included. **Astro does not do this automatically** —
  verified against a real build, a plain `.md` link renders completely literally and 404s.

`status: coming-soon` / `status: beta` (shown as "Experimental") render a visible badge both in the sidebar (from
`corpus.mjs`) and next to the page's H1 (`src/components/PageTitle.astro`, a Starlight
component override) — the docs must never oversell something not yet shipped.

## Deploying

Cloudflare Pages project `onw-docs` builds `main` of `open-net-worth-ai/open-net-worth` with root
directory `docs-site`, build command `npm run build` and output directory `dist`. Every push to
`main` deploys; pull requests get preview builds (Cloudflare comments the link). Confirm the
deployment and the public page before reporting a docs change as live.

`SITE_URL` and `SITE_BASE` can override the production origin and root base;
the defaults in `astro.config.mjs` already target the public docs site.

`PUBLIC_GA_MEASUREMENT_ID` (`G-…`, set in the Cloudflare Pages build env) turns on Google
Analytics 4 for the public site. Unset means no tag. The in-app embed build never gets it —
the app promises no analytics.
