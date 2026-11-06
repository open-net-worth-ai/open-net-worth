import { connectorLogo } from '../../connectors/logos'

// Actual's own mark, on the same white plate as YNAB's, so the data sync reads as the same Actual
// the connector is (`connectors/logos.ts` owns the file).

export function ActualMark() {
  return (
    <span className="a3-mark">
      <img className="a3-mark-logo" src={connectorLogo('actual_budget')} alt="" />
    </span>
  )
}
