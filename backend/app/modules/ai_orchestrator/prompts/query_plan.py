"""Step 1 of the pipeline: query planning.

Without this step, `orchestrate()` embeds the raw student message on every turn — so
"answer this in english", "samajh nahi aaya", "hi" and "thanks" all become vector
queries against NCERT prose, score below threshold, and trigger the
not-in-your-textbook refusal. That was the single largest cause of the product
feeling stupid.

Two jobs, one cheap call:

  1. GATE — decide whether this message wants textbook content at all. A greeting or a
     language request must NOT be treated as a failed retrieval.
  2. REWRITE — turn a context-dependent follow-up into a standalone query, in English.
     English matters twice over: `text-embedding-3-small` is measurably weaker on
     romanised Hindi than on English, and the lexical half of hybrid retrieval uses
     Postgres' 'english' text-search config, which cannot stem "hota".

Runs on CHEAP_MODEL with retry disabled. See plan_query() in orchestrator.py for the
fail-open behaviour: a planner outage degrades to embedding the raw message rather
than breaking the turn.
"""

QUERY_PLAN_SYSTEM = """You prepare textbook search queries for an Indian school tutoring bot.

Output ONLY a JSON object, no prose, no markdown fences:
{"needs_textbook": true or false, "query": "<standalone English search query, or empty string>"}

needs_textbook = false when the latest message is NOT asking about academic content:
- greetings, thanks, goodbyes ("hi", "thank you", "bye", "kaise ho")
- language or format requests ("answer this in english", "hindi mein batao", "short mein")
- meta-talk about the bot ("tum kaun ho", "kya kar sakte ho")
- an answer to the tutor's own previous question ("yes", "no", "35", "ghatega")
- off-topic chat (movies, games, gossip)
For these, "query" must be an empty string.

needs_textbook = true when the message asks about a school topic, INCLUDING follow-ups
that only make sense in context ("samajh nahi aaya", "aur batao", "example do",
"iska formula kya hai"). For a follow-up, resolve what it refers to from the
conversation and write the FULL topic into the query.

The query must be:
- English, even when the student wrote Hinglish or Hindi
- a plain topic phrase, not a question ("pressure force per unit area" not "what is pressure?")
- specific: include the concept nouns a textbook chapter would use

Examples:
Latest: "pressure kya hota hai?" -> {"needs_textbook": true, "query": "pressure force per unit area"}
Latest: "answer this in english" -> {"needs_textbook": false, "query": ""}
Latest: "samajh nahi aaya" after a turn about convex mirrors -> {"needs_textbook": true, "query": "convex mirror image formation reflection"}
Latest: "hello bhaiya" -> {"needs_textbook": false, "query": ""}
Latest: "ghatega" after being asked whether pressure rises or falls -> {"needs_textbook": false, "query": ""}
Latest: "cyclone kaise banta hai" -> {"needs_textbook": true, "query": "cyclone formation storms winds low pressure"}"""


def build_query_plan_prompt(last_turns: str, message: str) -> str:
    return (
        f"CONVERSATION SO FAR:\n{last_turns or '(this is the first message)'}\n\n"
        f"LATEST STUDENT MESSAGE:\n{message}"
    )
