// Lucius's logo, as on lucius.opennetworth.ai: only the diamond's facet-cut lines
// (lucius-mark.svg, cut from logo.svg) used as a mask over currentColor, so the lines take
// the colour of whatever they sit in (a nav item's muted / hover / active states, either theme).
// Inlined into the bundle (`?inline`), never fetched: the only nav icon that was a separate
// request went blank for good whenever that request failed on a slow server (2026-11-23).
import markUrl from './lucius-mark.svg?inline'

export function LuciusLogo({ size = 20 }: { size?: number }) {
  return (
    <span
      className="lucius-logo"
      style={{ width: size, height: size, maskImage: `url("${markUrl}")` }}
      aria-hidden="true"
    />
  )
}
