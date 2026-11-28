import { ONW_AI_KEY } from '../tabs/settings/sections/onwAiStatus'

/** Open Net Worth AI routes every model only to hosts that keep no prompts and never train on them. */
export function isZeroRetentionProvider(key: string | null | undefined): boolean {
  return key === ONW_AI_KEY
}

/** Google may keep prompts up to 30 days for abuse monitoring, even under zero-retention routing. */
export function zeroRetentionCaveat(modelId: string): string | undefined {
  return /^gemini/i.test(modelId) ? 'Google keeps 30 days' : undefined
}
