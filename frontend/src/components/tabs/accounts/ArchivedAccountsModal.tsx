import { Button, ErrorState, Loading, Modal } from '../../ui'
import { Archive, ChevronLeft, RotateCcw } from '../../../ui/icons'
import { TYPE_LABELS } from './categories'
import { fCur } from './format'
import { fDate } from '../../../utils/format'
import type { Account, Txn } from './types'

function transactionAmount(txn: Txn, account: Account): number | null {
  if (txn.toAccountId === account.id) return txn.toAmount
  if (txn.fromAccountId === account.id) return txn.fromAmount === null ? null : -txn.fromAmount
  return null
}

export function ArchivedAccountsModal({
  open,
  accounts,
  loading,
  error,
  selected,
  transactions,
  loadingTransactions,
  onClose,
  onSelect,
  onBack,
  onRestore,
  onDelete,
}: {
  open: boolean
  accounts: Account[]
  loading: boolean
  error: string | null
  selected: Account | null
  transactions: Txn[]
  loadingTransactions: boolean
  onClose: () => void
  onSelect: (account: Account) => void
  onBack: () => void
  onRestore: (account: Account) => void
  onDelete: (account: Account) => void
}) {
  const detail = selected !== null
  const title = detail ? selected.name : 'Archived accounts'

  return (
    <Modal open={open} onClose={onClose} title={title} size="lg" className="acct-archived-modal">
      {detail ? (
        <div className="acct-archived-detail">
          <div className="acct-archived-detail-head">
            <Button size="sm" variant="ghost" onClick={onBack} iconLeft={<ChevronLeft size={15} />}>
              Archived accounts
            </Button>
            <span className="acct-archived-badge">
              <Archive size={14} aria-hidden="true" /> Archived
            </span>
          </div>
          <div className="acct-archived-summary">
            <span>{TYPE_LABELS[selected.type as keyof typeof TYPE_LABELS] ?? selected.type}</span>
            <span>•</span>
            <span>{selected.currency}</span>
            <strong className="money">{fCur(selected.balance, selected.currency)}</strong>
          </div>
          <p className="acct-archived-note">
            This account is closed. Its history is read-only until you restore it.
          </p>
          <div className="acct-archived-actions">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onRestore(selected)}
              iconLeft={<RotateCcw size={15} />}
            >
              Restore account
            </Button>
            <Button size="sm" variant="danger" onClick={() => onDelete(selected)}>
              Delete permanently
            </Button>
          </div>
          <div className="acct-archived-register" aria-label="Archived account transactions">
            {loadingTransactions ? (
              <Loading label="Loading transactions…" />
            ) : transactions.length === 0 ? (
              <p className="acct-archived-empty">No transactions in this account.</p>
            ) : (
              transactions.map((txn) => {
                const amount = transactionAmount(txn, selected)
                return (
                  <div className="acct-archived-txn" key={txn.id}>
                    <span className="acct-archived-txn-date">
                      {fDate(txn.date, 'numeric', 'always')}
                    </span>
                    <span className="acct-archived-txn-description">
                      {txn.description || txn.memo || 'Transaction'}
                    </span>
                    <strong className={'money ' + (amount !== null && amount < 0 ? 'neg' : 'pos')}>
                      {amount === null ? '—' : fCur(amount, selected.currency)}
                    </strong>
                  </div>
                )
              })
            )}
          </div>
        </div>
      ) : loading ? (
        <Loading label="Loading archived accounts…" />
      ) : error ? (
        <ErrorState title="Couldn’t load archived accounts" message={error} />
      ) : accounts.length === 0 ? (
        <div className="acct-archived-empty-state">No archived accounts.</div>
      ) : (
        <div className="acct-archived-list">
          <p className="acct-archived-note">
            Closed accounts stay out of active balances, but their history remains available here.
          </p>
          {accounts.map((account) => (
            <button
              className="acct-archived-row"
              key={account.id}
              onClick={() => onSelect(account)}
            >
              <span>
                <strong>{account.name}</strong>
                <small>
                  {TYPE_LABELS[account.type as keyof typeof TYPE_LABELS] ?? account.type} •{' '}
                  {account.currency}
                </small>
              </span>
              <strong className="money">{fCur(account.balance, account.currency)}</strong>
            </button>
          ))}
        </div>
      )}
    </Modal>
  )
}
