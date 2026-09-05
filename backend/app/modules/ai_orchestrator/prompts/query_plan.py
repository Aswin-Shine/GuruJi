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

A second gate failure mode, found on the first Mathematics eval run (2026-08-21):
a puzzle- or scenario-phrased question with no textbook vocabulary in it — "how
many times can you fold a paper in half" — got gated OUT as needs_textbook=false,
empty query, nothing ever searched. The rule text already covered this in
principle, but every few-shot example was either clear off-topic chat or already
used a chapter's own vocabulary ("pressure", "cyclone"). Nothing showed the model
the specific case of a puzzle that reads like small talk on the surface but asks
about a real concept underneath. Two examples of that shape were added below;
this class of question needs re-testing whenever the eval set grows, since word
problems are Mathematics' natural phrasing far more than Science's.

A third failure mode, found the same session, is a REWRITE problem rather than a
gate problem: "factorise kaise karte hain algebra mein?" reached the model fine,
but the rewrite it produced ("algebraic factorisation... factors and
factorisation") only echoed the student's own word. Checked directly against the
ingested corpus: the chapter that actually teaches algebraic factorisation
contains the phrase "distributive law" 67 times and the word "factorise" zero
times — it teaches the identical skill under different vocabulary, because NCERT
frames it as applying the distributive law rather than as "factorising". A
same-numbered chapter elsewhere in the same corpus DOES use "factorise", for an
unrelated number-theory skill (checking perfect squares via prime factorisation),
so the plain rewrite reliably found the wrong chapter. One example was added
teaching the model to supply the textbook's actual vocabulary, not just translate
the student's word into English. Worth checking for the same shape elsewhere:
any concept NCERT names differently across two chapters, or differently than
students naturally would, is a candidate for this exact failure.
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

This includes a question phrased as an everyday puzzle or scenario, with none of a
chapter's own vocabulary in it — "how many times can you fold a paper in half"
is exponential growth, not small talk, even though nothing in the sentence says
"exponent". Judge by what a correct answer would need, not by whether the wording
sounds academic. This is where the gate has been wrong in practice: a puzzle with
no technical noun in it reads like the "off-topic chat" case above on the surface,
and the two are easy to conflate without holding this distinction explicitly.

The query must be:
- English, even when the student wrote Hinglish or Hindi
- a plain topic phrase, not a question ("pressure force per unit area" not "what is pressure?")
- specific: include the concept nouns a textbook chapter would use, even when the
  student's own words never used them — the rewrite's job is to supply exactly the
  vocabulary the question above describes as often missing
- TRANSLATED into the textbook's own words when they differ from the student's, not
  just the student's word repeated back. "Factorise" means two unrelated things in
  this corpus — breaking a number into primes, and rewriting an algebraic expression
  using the distributive law — and the chapter that teaches the second one never
  uses the word "factorise" at all, only "distributive law" and "common factor". A
  rewrite that only echoes "factorisation" finds the number-theory chapter every
  time, even when the question says "algebra" outright. When the domain word in the
  question (algebra, geometry, probability) points at a specific kind of content,
  name that domain AND supply the vocabulary that domain's chapter actually uses.

Examples:
Latest: "pressure kya hota hai?" -> {"needs_textbook": true, "query": "pressure force per unit area"}
Latest: "answer this in english" -> {"needs_textbook": false, "query": ""}
Latest: "samajh nahi aaya" after a turn about convex mirrors -> {"needs_textbook": true, "query": "convex mirror image formation reflection"}
Latest: "hello bhaiya" -> {"needs_textbook": false, "query": ""}
Latest: "ghatega" after being asked whether pressure rises or falls -> {"needs_textbook": false, "query": ""}
Latest: "cyclone kaise banta hai" -> {"needs_textbook": true, "query": "cyclone formation storms winds low pressure"}
Latest: "kitni baar paper ko adha mod sakte ho?" -> {"needs_textbook": true, "query": "exponential growth doubling repeated halving powers"}
Latest: "ek photo ko same shape mein bada ya chota karna hai" -> {"needs_textbook": true, "query": "proportional scaling ratio enlarging reducing figures"}
Latest: "factorise kaise karte hain algebra mein?" -> {"needs_textbook": true, "query": "algebraic factorisation using the distributive law common factor expanding brackets"}"""


def build_query_plan_prompt(last_turns: str, message: str) -> str:
    return (
        f"CONVERSATION SO FAR:\n{last_turns or '(this is the first message)'}\n\n"
        f"LATEST STUDENT MESSAGE:\n{message}"
    )