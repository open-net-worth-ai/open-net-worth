// The small sounds of a call that aren't the ring — WebAudio, no file to ship.

const NOTE_S = 0.07
const LOW_HZ = 440
const HIGH_HZ = 660
const VOLUME = 0.1

/** A falling pair of notes for muted, rising for live again, so you can tell which without looking
 *  (owner, 2026-11-06). Soft, and through the call's own context: a phone already lets that one
 *  play, and a second would disturb the audio route the call is using. */
export function playMuteTone(ctx: AudioContext, muted: boolean): void {
  void ctx.resume()
  const notes = muted ? [HIGH_HZ, LOW_HZ] : [LOW_HZ, HIGH_HZ]
  notes.forEach((hz, i) => {
    const at = ctx.currentTime + i * NOTE_S
    const gain = ctx.createGain()
    gain.gain.setValueAtTime(0.0001, at)
    gain.gain.exponentialRampToValueAtTime(VOLUME, at + 0.01)
    gain.gain.exponentialRampToValueAtTime(0.0001, at + NOTE_S)
    gain.connect(ctx.destination)
    const osc = ctx.createOscillator()
    osc.frequency.value = hz
    osc.connect(gain)
    osc.start(at)
    osc.stop(at + NOTE_S)
  })
}
