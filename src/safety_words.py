"""
Word lists for the content check (safety.py).

Syntax of a term:
  word        that word (letters may be stretched: "fuuuck")
  word~       plus common endings (s, es, ed, er, ers, ing, in, y, z)
  word*       anything starting with it
  two words   a phrase, matched across consecutive spoken words
  term:N      overrides the category's severity (1 low, 2 medium, 3 high)

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
        "i'll kill you:1", "ill kill you:1", "i will kill you:1", "gonna kill you:1", "shoot up", "school shooting",
        "mass shooting", "bomb threat", "behead*", "terrorist~", "terrorism", "massacre", "genocide", "murder~:1",
        "stab you", "rape~:3", "rapist~:3",
    ],
    "sexual": [
        "porn*", "onlyfans", "nude~", "naked:1", "horny", "cum", "cumming", "boobs", "tits", "titties", "dildo~",
        "blowjob~", "handjob~", "masturbat*", "cock~", "pussy", "penis", "vagina", "sexting", "nsfw", "hentai",
        "send nudes", "strip club", "milf~", "orgasm~", "erection", "jerk off", "jerking off",
    ],
    "drugs": [
        "cocaine", "heroin", "meth", "methamphetamine", "fentanyl", "ketamine", "mdma", "ecstasy", "lsd",
        "shrooms", "xanax", "get high", "getting high", "weed", "marijuana", "smoke weed", "crack cocaine",
    ],
    "profanity": [
        "fuck~", "motherfuck*", "fck~", "shit~", "bullshit", "bitch~", "asshole~", "bastard~", "dick", "dicks",
        "dickhead~", "cunt~:2", "piss~", "pissed", "wanker~", "twat~", "bollocks", "prick~",
    ],
    "gambling": [
        "casino~", "gambl*", "roobet", "bet365", "slots", "slot machine~", "betting", "sportsbook~", "free spins",
    ],
}
