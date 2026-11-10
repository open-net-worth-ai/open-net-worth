export function FileDropMask({ className = '' }: { className?: string }) {
  return (
    <div className={'lx-dropmask' + (className ? ' ' + className : '')}>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v13M7 8l5-5 5 5" />
      </svg>
      Drop files to attach
    </div>
  )
}
