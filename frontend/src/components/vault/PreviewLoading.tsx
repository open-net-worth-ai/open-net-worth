/** What every previewer shows while a file is still on its way — a blank frame read as
 *  "there is nothing here" (owner, 2026-10-26). */
export function PreviewLoading({ label = 'Loading the file…' }: { label?: string }) {
  return (
    <div className="vault-preview-loading" role="status">
      <span className="vault-preview-spin" aria-hidden="true" />
      {label}
    </div>
  )
}
