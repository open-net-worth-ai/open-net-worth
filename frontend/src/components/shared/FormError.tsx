import { useEffect, useRef } from 'react'

/** A save error at the foot of a long modal form. It sits below the fold once the form scrolls,
 *  so a refused Save looked like nothing happened: it scrolls itself into view when it appears
 *  and is announced as an alert. */
export function FormError({ message }: { message: string | null }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (message) ref.current?.scrollIntoView?.({ block: 'nearest' })
  }, [message])
  if (!message) return null
  return (
    <div ref={ref} className="disposal-error" role="alert">
      {message}
    </div>
  )
}
