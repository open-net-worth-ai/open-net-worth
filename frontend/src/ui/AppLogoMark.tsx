// The Open Net Worth diamond as an icon: public/logo-mark.svg used as a mask over currentColor,
// so it takes the colour of whatever it sits in (a nav item's muted / hover / active states,
// either theme) without copying the logo's path here.
export function AppLogoMark({ size = 20 }: { size?: number }) {
  return <span className="app-logo-mark" style={{ width: size, height: size }} aria-hidden="true" />
}
