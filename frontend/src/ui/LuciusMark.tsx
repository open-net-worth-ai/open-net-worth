import type { SVGProps } from 'react'

// Lucius's mark: an eight-point star cut from the Open Net Worth diamond (public/logo-mark.svg,
// same coordinates) — its four compass facets and four corner facets, nothing else. It takes
// lucide's props so it drops in where a Sparkles icon used to be.
const STAR =
  'M227.33 70.18 302.43 0 227.33 -70.18 204.61 0Z' +
  'M-227.33 70.18 -204.61 0 -227.33 -70.18 -302.43 0Z' +
  'M70.18 227.33 0 204.61 -70.18 227.33 0 302.43Z' +
  'M70.18 -227.33 0 -302.43 -70.18 -227.33 0 -204.61Z' +
  'M144.68 -144.68 210.38 -111.12 213.85 -213.85 111.12 -210.38Z' +
  'M144.68 144.68 111.12 210.38 213.85 213.85 210.38 111.12Z' +
  'M-210.38 -111.12 -144.68 -144.68 -111.12 -210.38 -213.85 -213.85Z' +
  'M-111.12 210.38 -144.68 144.68 -210.38 111.12 -213.85 213.85Z'

export type LuciusMarkProps = Omit<SVGProps<SVGSVGElement>, 'ref'> & {
  size?: number | string
}

export function LuciusMark({ size = 15, ...rest }: LuciusMarkProps) {
  return (
    <svg width={size} height={size} viewBox="-315 -315 630 630" fill="currentColor" {...rest}>
      <path d={STAR} />
    </svg>
  )
}
