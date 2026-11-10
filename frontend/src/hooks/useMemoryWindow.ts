// The memory window and its topic page as history steps (useHistoryStep): Back closes the topic
// page, then the window, then leaves the page — never the page first. Each surface that can open
// the window (the Lucius tab, the chat panel) opens its own, so two mounted at once never both
// show it.
import { useHistoryStep, useLeaveSteps } from './useHistoryStep'

export const MEMORY_STEP = 'onwMemory'
export const MEMORY_TOPIC_STEP = 'onwMemoryTopic'

export type MemoryTopicStep = { section: string; key: string }

export function useMemoryWindow(surface: 'home' | 'panel') {
  const step = useHistoryStep<string>(MEMORY_STEP)
  const leave = useLeaveSteps([MEMORY_TOPIC_STEP, MEMORY_STEP])
  return {
    open: step.value === surface,
    show: () => step.open(surface),
    close: () => {
      if (!leave()) step.close()
    },
  }
}
