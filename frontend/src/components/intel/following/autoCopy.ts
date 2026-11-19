import type { Automation } from '../../../api/automations'

/** The sentence an Auto-copy automation starts from (the `copy-followed-portfolio` template),
 *  with the portfolio's name filled in. A saved automation keeps only its instructions, so this
 *  is how one is matched to the portfolio it copies. */
export const autoCopySentence = (name: string) =>
  `Copy the followed portfolio ${name} in my broker account`

/** The running Auto-copy automations that copy ``name``. */
export function activeAutoCopies(automations: Automation[], name: string): Automation[] {
  const sentence = autoCopySentence(name)
  return automations.filter((a) => a.status === 'active' && a.instructions.includes(sentence))
}
