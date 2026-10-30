import { describe, expect, it } from 'vitest'
import { fixSpelling } from './spelling'

// a few of src/spelling.py's fixes; the same cases are in tests/test_spelling.py, so both sides agree
const F = { dont: "don't", teh: 'the', im: "I'm", definately: 'definitely', wierd: 'weird', alot: 'a lot' }

describe('spelling, as src/spelling.py fixes it', () => {
  it('fixes words on the list, in their own case, and says what it changed', () => {
    expect(fixSpelling('dont do that i think its teh best', F)).toEqual(["don't do that I think its the best", [['dont', "don't"], ['i', 'I'], ['teh', 'the']]])
    expect(fixSpelling('DONT STOP', F)[0]).toBe("DON'T STOP")
    expect(fixSpelling('Im definately not wierd', F)[0]).toBe("I'm definitely not weird")
    expect(fixSpelling("i'm so done", F)[0]).toBe("I'm so done")
    expect(fixSpelling('alot of fun', F)[0]).toBe('a lot of fun')
  })
  it('never touches hashtags, @names, links or words it doesn\'t know', () => {
    expect(fixSpelling('check #dont @im https://x.com/dont KEKW pog', F)).toEqual(['check #dont @im https://x.com/dont KEKW pog', []])
  })
})
