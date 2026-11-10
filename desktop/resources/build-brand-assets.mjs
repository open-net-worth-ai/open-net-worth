import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import sharp from "sharp";

// An .ico is a directory of PNG images (Vista and later read PNG entries natively).
function pngsToIco(images) {
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach(({ size, png }, index) => {
    const entry = 6 + 16 * index;
    header.writeUInt8(size === 256 ? 0 : size, entry);
    header.writeUInt8(size === 256 ? 0 : size, entry + 1);
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...images.map(({ png }) => png)]);
}

// `appIcon` (optional) is the app icon's own artwork; `logo` still drives the tray template.
export async function buildBrandAssets(logo, output, { appIcon = logo } = {}) {
  const svg = Buffer.from(appIcon);
  const icon = async (size, rounded = process.platform === "darwin") => {
    const artwork = rounded ? Math.round(size * 824 / 1024) : size;
    const inset = Math.floor((size - artwork) / 2);
    const base = sharp(svg).resize(artwork, artwork).extend({
      top: inset, left: inset,
      bottom: size - artwork - inset, right: size - artwork - inset,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    });
    return base.png().toBuffer();
  };
  await writeFile(join(output, "icon.png"), await icon(512));
  const windowsSizes = [16, 24, 32, 48, 64, 128, 256];
  const windowsIcons = await Promise.all(
    windowsSizes.map(async (size) => ({ size, png: await icon(size, false) })),
  );
  await writeFile(join(output, "icon.ico"), pngsToIco(windowsIcons));
  const template = Buffer.from(
    logo.replace(/<rect\b[^>]*\/>/, "").replaceAll(/#ffffff/gi, "#000000"),
  );
  // A solid disc edge to edge reads a size larger than the menu bar's line glyphs: draw it at 16 of
  // the 20 px slot (the size of the system's own circular items), centred with clear space round it.
  for (const [size, suffix] of [[20, ""], [40, "@2x"]]) {
    const mark = Math.round(size * 0.8), pad = (size - mark) / 2;
    await sharp(template).resize(mark, mark)
      .extend({ top: pad, bottom: pad, left: pad, right: pad, background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png().toFile(join(output, `trayTemplate${suffix}.png`));
  }
  if (process.platform !== "darwin") return;
  const temporary = await mkdtemp(join(tmpdir(), "onw-brand-"));
  const iconset = join(temporary, "icon.iconset");
  await mkdir(iconset);
  try {
    for (const size of [16, 32, 128, 256, 512]) {
      for (const scale of [1, 2]) {
        const png = await icon(size * scale);
        await writeFile(join(iconset, `icon_${size}x${size}${scale === 2 ? "@2x" : ""}.png`), png);
      }
    }
    await promisify(execFile)("/usr/bin/iconutil", ["--convert", "icns", iconset, "--output", join(output, "icon.icns")]);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}
