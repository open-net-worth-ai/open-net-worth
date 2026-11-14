const LONG_SPAN_DAYS = 400
const DAY_MS = 86_400_000

/** A chart draws one bar per point, whatever the gap between them: weekly closes before the
 *  last year beside daily ones within it squeezed the older stretch into a sliver. Over more
 *  than a year every point is put on the same weekly step (the week's last close), so equal
 *  time takes equal width. Shorter spans keep every day. */
export function evenSpacing<T extends { date: string }>(points: T[]): T[] {
  if (points.length < 2) return points
  const first = new Date(points[0]!.date).getTime()
  const last = new Date(points[points.length - 1]!.date).getTime()
  if ((last - first) / DAY_MS <= LONG_SPAN_DAYS) return points
  const out: T[] = []
  let week = -1
  for (const p of points) {
    const w = Math.floor((new Date(p.date).getTime() - first) / (7 * DAY_MS))
    if (w === week) out[out.length - 1] = p
    else {
      out.push(p)
      week = w
    }
  }
  return out
}
