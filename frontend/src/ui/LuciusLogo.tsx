// Lucius's logo, as on lucius.opennetworth.ai: only the diamond's facet-cut lines
// (public/lucius-mark.svg, cut from logo.svg) used as a mask over currentColor, so the lines take
// the colour of whatever they sit in (a nav item's muted / hover / active states, either theme).
export function LuciusLogo({ size = 20 }: { size?: number }) {
  return <span className="lucius-logo" style={{ width: size, height: size }} aria-hidden="true" />
}
