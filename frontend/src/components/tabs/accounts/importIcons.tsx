import { LuciusMark } from '../../../ui/LuciusMark'

/** The import flow's stroke icons and the Lucius spark, shared by the modal and its Booked
 *  screen. */
export function Ic({ d, size = 16, sw = 1.8 }: { d: string; size?: number; sw?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      dangerouslySetInnerHTML={{ __html: d }}
    />
  )
}
/** The Lucius mark the Choose, Read and Booked steps all use. */
export function Spark({ size = 15 }: { size?: number }) {
  return <LuciusMark size={size} aria-hidden="true" />
}
export const I_CHECK = '<path d="M20 6 9 17l-5-5"/>'
export const I_WARN =
  '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/>'
