import { useRef } from 'react'
import { useVoiceLevels } from './voiceLevels'

// The light on the full call screen's mic button: only while you talk, over Lucius too, so you can
// see you're being heard when you cut in. Never for his voice (owner, 2026-11-06).
export function VoiceCallMicLight({ live }: { live: boolean }) {
  const ref = useRef<HTMLSpanElement>(null)
  useVoiceLevels(ref, live)
  return <span ref={ref} className="lx-callstage-miclight" aria-hidden="true" />
}
