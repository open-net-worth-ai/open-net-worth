// Inside the Rule editor every dropdown is the app's own (ComboSelect), never the browser's list;
// elsewhere the editor controls' Select stays a native <select>.
import { createContext } from 'react'

export const ComboDropdowns = createContext(false)
