import { connectorLogo } from '../../connectors/logos'

// YNAB's own mark, on the same white plate the Connectors page gives every brand logo, so the
// data sync reads as the same YNAB the connector is (`connectors/logos.ts` owns the file).

export function YnabMark() {
  return (
    <span className="a3-mark">
      <img className="a3-mark-logo" src={connectorLogo('ynab')} alt="" />
    </span>
  )
}
