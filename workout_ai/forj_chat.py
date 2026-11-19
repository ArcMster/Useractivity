"""Forj chat requests: the reply schema, turning the app's payload into text for the AI, and cleaning the reply.
No Django or network code here, so it can be tested on its own (python -m unittest proxy/workout_ai/test_forj_chat.py).
The prompt is in forj_prompts.py."""
import json
import re

MAX_TURNS = 10        # most recent messages kept
MAX_MESSAGE = 1000    # characters per message
MAX_CONTEXT = 14000   # characters of activity snapshot
MAX_REPLY = 2500

REPLY_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string", "description": "Forj's answer, plain text."}},
    "required": ["reply"],
    "additionalProperties": False,
}


def text_field(v, n):
    return re.sub(r"[ \t]+", " ", str(v or "").replace("\r", "")).strip()[:n]


def clean_messages(raw):
    """The last turns as [(role, text)], oldest first, ending on a user message. Raises ValueError if there is no question."""
    out = []
    for m in (raw if isinstance(raw, list) else [])[-MAX_TURNS:]:
        if not isinstance(m, dict):
            continue
        role = "forj" if m.get("role") in ("forj", "assistant", "model") else "user"
        t = text_field(m.get("text"), MAX_MESSAGE)
        if t:
            out.append((role, t))
    while out and out[-1][0] != "user":
        out.pop()
    if not out or len(out[-1][1]) < 1:
        raise ValueError("Type a question for Forj.")
    while out and out[0][0] != "user":
        out.pop(0)
    return out


def describe(messages, context):
    """The single text sent to the model: the activity snapshot and the conversation, each in its own data block."""
    try:
        snap = json.dumps(context if isinstance(context, dict) else {}, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        snap = "{}"
    if len(snap) > MAX_CONTEXT:
        snap = snap[:MAX_CONTEXT] + " ...(cut)"
    snap = snap.replace("</user_data>", "")
    convo = "\n".join(f"{'Person' if r == 'user' else 'Forj'}: {t}".replace("</conversation>", "") for r, t in messages)
    return (
        "<user_data>\nThe person's activity in the app (JSON):\n" + snap + "\n</user_data>\n\n"
        "<conversation>\n" + convo + "\n</conversation>\n\n"
        "Reply as Forj to the person's last message."
    )


def normalize(result):
    """The cleaned reply text, or None if it can't be used."""
    if not isinstance(result, dict):
        return None
    t = re.sub(r"\n{3,}", "\n\n", str(result.get("reply") or "").replace("\r", "")).strip()
    t = re.sub(r"\*\*|__|^#+\s*", "", t, flags=re.M)  # plain text only
    return t[:MAX_REPLY] or None
