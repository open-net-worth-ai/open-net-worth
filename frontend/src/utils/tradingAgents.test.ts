import { describe, expect, it } from 'vitest'
import { condenseDecision, runAgeText } from './tradingAgents'

describe('condenseDecision', () => {
  it('keeps a short decision whole', () => {
    expect(condenseDecision('Overweight ACME: the thesis holds.')).toEqual({
      text: 'Overweight ACME: the thesis holds.',
      cut: false,
    })
  })

  it('keeps the first three sentences and drops the rest', () => {
    const out = condenseDecision('One. Two? Three! Four. Five.')
    expect(out).toEqual({ text: 'One. Two? Three!', cut: true })
  })

  it('counts a newline as a sentence end', () => {
    const out = condenseDecision('- first point\n- second point\n- third point\n- fourth point')
    expect(out).toEqual({ text: '- first point\n- second point\n- third point', cut: true })
  })

  it('stops at the last whole sentence that fits the character budget', () => {
    const sentence = `${'word '.repeat(30).trim()}.`
    const out = condenseDecision(`${sentence} ${sentence} ${sentence} ${sentence}`, 10, 400)
    expect(out.text).toBe(`${sentence} ${sentence}`)
    expect(out.cut).toBe(true)
    expect(out.text.length).toBeLessThanOrEqual(400)
  })

  it('cuts one over-long sentence at a word with an ellipsis', () => {
    const out = condenseDecision('lorem '.repeat(200).trim())
    expect(out.cut).toBe(true)
    expect(out.text.endsWith('lorem…')).toBe(true)
    expect(out.text.length).toBeLessThanOrEqual(401)
  })

  it('hard-cuts text with no break at all', () => {
    const out = condenseDecision('x'.repeat(900))
    expect(out.text).toBe(`${'x'.repeat(400)}…`)
    expect(out.cut).toBe(true)
  })
})

describe('runAgeText', () => {
  it.each([
    [0, 'today'],
    [1, 'yesterday'],
    [3, '3 days ago'],
  ])('%i days -> %s', (days, text) => {
    expect(runAgeText(days)).toBe(text)
  })
})
