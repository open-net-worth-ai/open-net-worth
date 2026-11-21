// Stamps the last input modality on <html> so CSS can tell a mouse-driven programmatic
// .focus() from keyboard navigation, which :focus-visible alone cannot.
export function watchInputModality(root: HTMLElement = document.documentElement): void {
  const set = (v: 'pointer' | 'keyboard') => {
    if (root.dataset.input !== v) root.dataset.input = v
  }
  window.addEventListener('pointerdown', () => set('pointer'), { capture: true, passive: true })
  window.addEventListener(
    'keydown',
    (e) => {
      if (!e.metaKey && !e.ctrlKey && !e.altKey) set('keyboard')
    },
    { capture: true },
  )
}
