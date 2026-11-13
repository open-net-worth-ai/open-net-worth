import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { tokenGroups } from "../../frontend/src/design/tokens.source.mjs";
import { buildBrandAssets } from "./build-brand-assets.mjs";

const root = new URL("../../frontend/", import.meta.url);
const wanted = new Set([
  "bg",
  "ink",
  "bad",
  "muted",
  "faint",
  "line",
  "text-md",
  "text-base",
  "text-2xl",
  "space-3",
  "space-4",
  "space-5",
  "space-6",
  "space-8",
  "space-9",
  "radius-sm",
  "text-display-xl",
  // The report dialog draws the app's modal card over the main window.
  "space-1",
  "modal-bg",
  "modal-border",
  "radius-xl",
  "shadow-modal",
  "line-strong",
  "text-lg",
  "text-sm",
]);
const tokens = tokenGroups
  .flatMap((group) => group.tokens)
  .filter(([name]) => wanted.has(name));
const stylesheet = await readFile(new URL("src/index.css", root), "utf8");
const gold = [...stylesheet.matchAll(/--fr-gold:\s*([^;]+);/g)].map(
  (match) => match[1],
);
if (gold.length !== 2)
  throw new Error(
    "The onboarding gold palette changed; update the desktop asset build.",
  );
const theme = (index) =>
  tokens
    .map(
      ([name, dark, warm, light]) =>
        `--${name}:${[dark, warm ?? dark, light ?? warm ?? dark][index]};`,
    )
    .join("") + `--fr-gold:${index === 0 ? gold[0] : gold[1]};`;
let css = `:root{${theme(0)}}@media(prefers-color-scheme:light){:root{${theme(2)}}}:root[data-theme=dark]{${theme(0)}}:root[data-theme=light]{${theme(2)}}:root[data-theme=warm]{${theme(1)}}`;
css +=
  "@media(prefers-color-scheme:light){.brand-logo{filter:invert(1)}}:root[data-theme=dark] .brand-logo{filter:none}:root:is([data-theme=light],[data-theme=warm]) .brand-logo{filter:invert(1)}";
for (const weight of [400, 600]) {
  const font = await readFile(
    new URL(
      `node_modules/@fontsource/plus-jakarta-sans/files/plus-jakarta-sans-latin-${weight}-normal.woff2`,
      root,
    ),
  );
  css += `@font-face{font-family:'Plus Jakarta Sans';font-style:normal;font-weight:${weight};font-display:block;src:url(data:font/woff2;base64,${font.toString("base64")}) format('woff2')}`;
}
const mark = await readFile(new URL("public/logo-mark.svg", root), "utf8");
const logo = mark.replace(/<svg\b[^>]*>/, (tag) =>
  tag
    .replace(/ (width|height)="[^"]*"/g, "")
    .replace('role="img"', 'class="brand-logo" aria-hidden="true"')
    .replace("<svg ", '<svg width="64" height="64" '),
);
// The app icon is the official tile (`app-icon.svg`, identical to `logo.svg`); the
// tray template follows the logo mark, drawn at 16 of the 20px menu-bar slot.
const appIcon = await readFile(new URL("./app-icon.svg", import.meta.url), "utf8");
await buildBrandAssets(mark, fileURLToPath(new URL("./", import.meta.url)), { appIcon });
await writeFile(
  fileURLToPath(new URL("./onboarding.generated.json", import.meta.url)),
  JSON.stringify({ css, logo }),
);
