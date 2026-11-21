import { BUDGET_APPS } from '../automations/datasync/budgetApps'
import { useSyncStatus } from '../imports/sync'

// A category deleted with its activity moved elsewhere is not only a change here: with a budget
// app's sync on in a direction that writes to it, the app follows. Said where the choice is made.

function Line({ appIndex }: { appIndex: number }) {
  const app = BUDGET_APPS[appIndex]!
  const status = useSyncStatus(true, app)
  const s = status.data
  if (!s?.enabled || !s.plan_id || s.direction === 'from_ynab') return null
  return (
    <p className="pl-modal-intro">
      {`Your ${app.name} sync is on, so ${app.name} follows: its transactions move to the category you pick, and this one is hidden there with its Assigned set to 0 in every month.`}
    </p>
  )
}

export function SyncFollowsNote() {
  return (
    <>
      {BUDGET_APPS.map((app, i) => (
        <Line key={app.key} appIndex={i} />
      ))}
    </>
  )
}
