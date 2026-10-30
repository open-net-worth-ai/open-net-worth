// Model-written text shown in the UI (Lucius's one-line `reason` for a suggested connector):
// one line, no control or bidi-override characters, at most 200 characters.

// eslint-disable-next-line no-control-regex
const UNSAFE_TEXT = /[\u0000-\u001f\u007f‎‏‪-‮⁦-⁩]/g

export const oneLine = (text: string): string => text.replace(UNSAFE_TEXT, ' ').trim().slice(0, 200)
