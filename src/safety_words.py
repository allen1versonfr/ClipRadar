"""
Word lists for the content check (safety.py).

Syntax of a term:
  word        that word (letters may be stretched: "fuuuck")
  word~       plus common endings (s, es, ed, er, ers, ing, in, y, z)
  word*       anything starting with it
  two words   a phrase, matched across consecutive spoken words
  term:N      overrides the category's severity (1 low, 2 medium, 3 high)

NOT_FLAGGED lists ordinary words a term's endings would otherwise catch ("spicy" isn't a slur, "cocky"
isn't sexual). SAFE_PHRASES are phrases that make a flagged word harmless ("suicide squad", "terrorists
win"): a word inside one of them isn't flagged.

Hate slurs are stored ROT13-encoded so this file doesn't read as a wall of slurs;
safety.py decodes them when it loads.
"""

TERMS = {
    "slurs_rot13": [
        "avttre~", "avttn~", "snttbg~", "snt~", "ergneq~", "gneq~", "genaal~", "puvax~", "fcvp~", "xvxr~",
        "jrgonpx~", "tbbx~", "ornare~", "enturnq~", "gbjryurnq~", "qlxr~", "pbba", "cnxv~",
    ],
    "harassment": [
        "kill yourself", "kill urself", "kill your self", "kys", "go die", "neck yourself", "hang yourself",
        "kill myself", "suicide", "suicidal", "self harm", "cut myself", "end my life", "an hero",
    ],
    "violence": [
        "i'll kill you:1", "ill kill you:1", "i will kill you:1", "gonna kill you:1", "shoot up the school",
        "shoot up a school", "shoot up my school", "shoot up the place", "school shooting",
        "mass shooting", "bomb threat", "behead*", "terrorist~", "terrorism", "massacre", "genocide", "murder~:1",
        "stab you", "rape~:3", "rapist~:3",
    ],
    "sexual": [
        "porn*", "onlyfans", "nude~", "naked:1", "horny", "cum", "cumming", "boobs", "tits", "titties", "dildo~",
        "blowjob~", "handjob~", "masturbat*", "cock~", "pussy", "penis", "vagina", "sexting", "nsfw", "hentai",
        "send nudes", "strip club", "milf~", "orgasm~", "erection", "jerk off", "jerking off", "whore~", "slut~",
    ],
    "drugs": [
        "cocaine", "heroin", "meth", "methamphetamine", "fentanyl", "ketamine", "mdma", "ecstasy", "lsd",
        "shrooms", "xanax", "get high", "getting high", "weed", "marijuana", "smoke weed", "crack cocaine",
    ],
    "profanity": [
        "fuck~", "motherfuck*", "fck~", "shit~", "bullshit~", "bitch~", "asshole~", "bastard~", "dick", "dicks",
        "dickhead~", "cunt~:2", "piss~", "pissed", "wanker~", "twat~", "bollocks", "prick~", "dumbass~", "jackass~",
        "dipshit~", "shithead~", "shitshow", "fuckboy~", "batshit",
    ],
    "gambling": [
        # not "slots" or "betting" alone: inventory slots, "I'm betting he dies"
        "casino~", "gambl*", "roobet", "stake casino", "bet365", "slot machine~", "play slots", "playing slots",
        "slots stream~", "slot stream~", "sports betting", "betting site~", "betting app~", "sportsbook~", "free spins",
    ],
}

NOT_FLAGGED = {
    "spicy", "spicier", "spiciest", "tardy", "tardiness", "cocky", "cockier", "cockiest", "cockiness",
    "prickly", "dickens", "pissarro", "fagin", "shitake", "shiitake",
}

SAFE_PHRASES = [
    "suicide squad", "suicide mission", "suicide run", "suicide burn", "suicide door~", "suicide king~",
    "counter terrorist~", "terrorists win", "terrorist win", "terrorist~ win", "anti terrorist~",
    "weed out", "weed killer", "weed whacker", "weeding", "cum laude", "magna cum laude", "summa cum laude",
    "moby dick", "naked eye", "dick grayson",
]
