"""
Spelling fixes for titles and post captions (4.30).

Titles from chat and from what was said come out of typing in a hurry and speech-to-text: "dont",
"im", a lone "i", "definately". fix() corrects the common ones and says what it changed. It only touches
words on its list, so slang, names, emotes and game words stay as they are; hashtags, @names and links
are never changed. The UI fetches the list (GET /api/spelling) and suggests the same fixes as you type,
and the app window's own spellcheck underlines anything else.

  fix("dont do that i think its teh best")  ->  ("don't do that I think its the best", [("dont", "don't"), ...])
"""

from __future__ import annotations

import re

# chat-style contractions without the apostrophe. Not "its", "ill", "id", "lets", "were", "well", "hell",
# "shell", "wed": those are words too, and guessing wrong is worse than leaving them
CONTRACTIONS = {
    "dont": "don't", "cant": "can't", "wont": "won't", "didnt": "didn't", "doesnt": "doesn't", "isnt": "isn't",
    "wasnt": "wasn't", "werent": "weren't", "arent": "aren't", "couldnt": "couldn't", "shouldnt": "shouldn't",
    "wouldnt": "wouldn't", "havent": "haven't", "hasnt": "hasn't", "hadnt": "hadn't", "mustnt": "mustn't",
    "im": "I'm", "ive": "I've", "youre": "you're", "youve": "you've", "youll": "you'll", "theyre": "they're",
    "theyve": "they've", "theyll": "they'll", "thats": "that's", "whats": "what's", "wheres": "where's",
    "theres": "there's", "heres": "here's", "hes": "he's", "shes": "she's", "whos": "who's", "yall": "y'all",
    "aint": "ain't", "weve": "we've", "wouldve": "would've", "couldve": "could've", "shouldve": "should've",
    "mightve": "might've", "howd": "how'd", "whyd": "why'd",
}

# common misspellings (one right spelling each)
MISSPELLINGS = {
    "teh": "the", "adn": "and", "taht": "that", "waht": "what", "becuase": "because", "beacuse": "because",
    "becasue": "because", "alot": "a lot", "recieve": "receive", "recieved": "received", "beleive": "believe",
    "belive": "believe", "definately": "definitely", "definatly": "definitely", "definetly": "definitely",
    "defintely": "definitely", "seperate": "separate", "occured": "occurred", "untill": "until", "wierd": "weird",
    "thier": "their", "freind": "friend", "freinds": "friends", "goverment": "government", "tommorow": "tomorrow",
    "tomorow": "tomorrow", "tommorrow": "tomorrow", "embarass": "embarrass", "embarassing": "embarrassing",
    "embarrasing": "embarrassing", "accomodate": "accommodate", "acheive": "achieve", "adress": "address",
    "agressive": "aggressive", "apparantly": "apparently", "arguement": "argument", "basicly": "basically",
    "begining": "beginning", "buisness": "business", "calender": "calendar", "cemetary": "cemetery",
    "collegue": "colleague", "comming": "coming", "commited": "committed", "completly": "completely",
    "concious": "conscious", "curiousity": "curiosity", "decieve": "deceive", "dissapear": "disappear",
    "dissapeared": "disappeared", "dissapoint": "disappoint", "dissapointed": "disappointed",
    "dissapointing": "disappointing", "enviroment": "environment", "existance": "existence",
    "familar": "familiar", "finaly": "finally", "foriegn": "foreign", "fourty": "forty", "futher": "further",
    "gaurd": "guard", "happend": "happened", "harrass": "harass", "hieght": "height", "immediatly": "immediately",
    "independant": "independent", "interupt": "interrupt", "knowlege": "knowledge", "libary": "library",
    "liscense": "license", "lisence": "license", "millenium": "millennium", "mischevious": "mischievous",
    "mispell": "misspell", "neccessary": "necessary", "necesary": "necessary", "noticable": "noticeable",
    "occassion": "occasion", "occurence": "occurrence", "ocurred": "occurred", "oppurtunity": "opportunity",
    "paralell": "parallel", "persistant": "persistent", "posession": "possession", "potatos": "potatoes",
    "prefered": "preferred", "presense": "presence", "probaly": "probably", "probly": "probably",
    "proffesional": "professional", "publically": "publicly", "realy": "really", "reccomend": "recommend",
    "recomend": "recommend", "refered": "referred", "relevent": "relevant", "religous": "religious",
    "remeber": "remember", "rember": "remember", "resistence": "resistance", "rythm": "rhythm", "sence": "sense",
    "sieze": "seize", "similiar": "similar", "sincerly": "sincerely", "speach": "speech", "stoped": "stopped",
    "succesful": "successful", "successfull": "successful", "suprise": "surprise", "suprised": "surprised",
    "surprize": "surprise", "tatoo": "tattoo", "tendancy": "tendency", "threshhold": "threshold",
    "tounge": "tongue", "truely": "truly", "unforseen": "unforeseen", "unfortunatly": "unfortunately",
    "usualy": "usually", "vaccum": "vacuum", "vehical": "vehicle", "wich": "which", "withold": "withhold",
    "writting": "writing", "acutally": "actually", "actualy": "actually", "absolutly": "absolutely",
    "aparently": "apparently", "beautifull": "beautiful", "beatiful": "beautiful", "befor": "before",
    "competion": "competition", "confortable": "comfortable", "dosent": "doesn't",
     "excercise": "exercise", "experiance": "experience", "explaination": "explanation",
    "favourit": "favourite", "favorit": "favorite", "gratefull": "grateful", "greatful": "grateful",
    "guarentee": "guarantee", "hopefuly": "hopefully", "imediately": "immediately", "incase": "in case",
    "infront": "in front", "insted": "instead", "intresting": "interesting", "interessting": "interesting",
    "jsut": "just", "knwo": "know", "lightening": "lightning", "loosing": "losing", "mabye": "maybe",
    "maybee": "maybe", "minuite": "minute", "nieghbor": "neighbor", "ofcourse": "of course", "oppinion": "opinion",
    "peice": "piece", "perfomance": "performance", "persue": "pursue", "posible": "possible", 
    "rediculous": "ridiculous", "ridicilous": "ridiculous", "rythym": "rhythm", "saftey": "safety",
    "shoudl": "should", "somthing": "something", "sombody": "somebody", "strenght": "strength",
    "thnaks": "thanks", "togehter": "together", "tought": "thought", "tripple": "triple",
    "unbeleivable": "unbelievable", "unbelivable": "unbelievable", "whereever": "wherever",
    "wiht": "with", "woudl": "would", "yeild": "yield", "youself": "yourself",
}

FIXES = {**MISSPELLINGS, **CONTRACTIONS}

_WORD = re.compile(r"(?<![\w#@/.'’])([A-Za-z]+)(?![\w'’]|\.\w)")


def _cased(src: str, fix: str) -> str:
    """The fix in the word's own case: "Dont" -> "Don't", "DONT" -> "DON'T"; "I'm" keeps its capital."""
    if src.isupper() and len(src) > 1:
        return fix.upper()
    if src[0].isupper():
        return fix[0].upper() + fix[1:]
    return fix


def fix(text: str) -> tuple:
    """(text with the common spelling mistakes fixed, [(was, now)] in the order they appear)."""
    changes: list = []
    if not text:
        return text, changes

    def one(m):
        w = m.group(1)
        k = w.lower()
        if k == "i":
            if w == "i":
                changes.append((w, "I"))
                return "I"
            return w
        rep = FIXES.get(k)
        if not rep:
            return w
        new = _cased(w, rep)
        if new != w:
            changes.append((w, new))
        return new
    # links are left whole
    parts = re.split(r"(https?://\S+|www\.\S+)", text)
    out = "".join(p if i % 2 else _WORD.sub(one, p) for i, p in enumerate(parts))
    # "i'm", "i'll", "i've": the I (the rest of the word is fine)
    def cap_i(m):
        changes.append((m.group(0), "I" + m.group(0)[1:]))
        return "I" + m.group(0)[1:]
    out = re.sub(r"(?<![\w#@])i(?=['’][a-z])['’][a-z]+", cap_i, out)
    return out, changes


def view() -> dict:
    """GET /api/spelling: the fixes, for the UI's suggestions as you type."""
    return {"fixes": FIXES}
