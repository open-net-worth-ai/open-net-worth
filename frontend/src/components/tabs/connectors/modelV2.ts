import { LEVELS } from './model'

/** permissions_v2: the same three levels, named as Settings → Permissions names them — Ask and
 *  Block rather than Needs approval and Blocked. The stored values do not change. */
export const LEVELS_V2: typeof LEVELS = {
  ...LEVELS,
  needs_approval: { ...LEVELS.needs_approval, label: 'Ask' },
  blocked: { ...LEVELS.blocked, label: 'Block' },
}
