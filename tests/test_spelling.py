"""
Spelling fixes for titles and post captions (src/spelling.py): the common mistakes and chat-style
contractions are fixed in the word's own case, nothing else changes (slang, names, emotes, hashtags,
@names, links), and titles from chat come out fixed. web/src/features/spelling/spelling.test.ts checks
the same cases against the UI's copy of the rules.

  python tests/test_spelling.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import spelling  # noqa: E402
import titles  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


check("fixed in order, with what changed", spelling.fix("dont do that i think its teh best") ==
      ("don't do that I think its the best", [("dont", "don't"), ("i", "I"), ("teh", "the")]))
check("in the word's own case", spelling.fix("DONT STOP")[0] == "DON'T STOP" and spelling.fix("Dont")[0] == "Don't")
check("several on one line", spelling.fix("Im definately not wierd")[0] == "I'm definitely not weird")
check("i'm gets its capital", spelling.fix("i'm so done")[0] == "I'm so done")
check("one word can become two", spelling.fix("alot of fun")[0] == "a lot of fun")
same = "check #dont @im https://x.com/dont KEKW pog its ill well"
check("hashtags, @names, links, emotes and real words stay", spelling.fix(same) == (same, []), spelling.fix(same))
check("no word on the list fixes to itself", all(k != v.lower() for k, v in spelling.FIXES.items()))
check("every key is one plain lowercase word", all(k.isalpha() and k.islower() for k in spelling.FIXES))
check("titles from chat come out fixed", titles.polish("i cant believe he did that") == "I can't believe he did that",
      titles.polish("i cant believe he did that"))
check("the UI gets the same list", spelling.view() == {"fixes": spelling.FIXES})

print("PASSED" if not failures else f"FAILED: {', '.join(failures)}")
sys.exit(1 if failures else 0)
