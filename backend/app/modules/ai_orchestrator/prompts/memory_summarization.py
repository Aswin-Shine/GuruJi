"""Memory regeneration prompt — full rewrite each time, not append."""

MEMORY_TEMPLATE = """You maintain a compact learning profile for a Class {grade} student.
Read the previous profile and the recent conversation, then output ONLY a JSON object
with exactly these keys: "mastered_topics" (list of strings), "struggle_topics"
(list of strings), "preferred_style" (string). No prose, no markdown fences.

PREVIOUS PROFILE: {previous_summary}
RECENT CONVERSATION:
{recent_messages}"""


def build_memory_prompt(grade: int, previous_summary: str, recent_messages: str) -> str:
    return MEMORY_TEMPLATE.format(grade=grade, previous_summary=previous_summary or "{}", recent_messages=recent_messages)
