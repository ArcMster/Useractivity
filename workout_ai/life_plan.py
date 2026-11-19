"""Life Style plan requests: the reply schema, turning the app's payload into text for the AI, and cleaning the AI's
reply. No Django or network code here, so it can be tested on its own (python -m unittest proxy/workout_ai/test_life_plan.py).
The prompts are in life_prompts.py."""
import re

TYPES = ["boolean", "count", "duration", "quantity"]
CATS = ["mind", "body", "social", "sleep", "learning", "health", "other"]
SLOTS = ["morning", "afternoon", "evening", "anytime"]
REASONS = ["no_time", "low_energy", "forgot", "too_hard", "not_relevant", "other"]
INTENSITIES = ["gentle", "balanced", "challenging"]
MIN_TASKS, MAX_TASKS = 3, 6

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "type": {"type": "string", "enum": TYPES},
        "target": {"type": "number"},
        "unit": {"type": "string"},
        "cat": {"type": "string", "enum": CATS},
        "slot": {"type": "string", "enum": SLOTS},
        "diff": {"type": "integer"},
        "why": {"type": "string"},
        "days": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["title", "type", "target", "unit", "cat", "slot", "diff", "why", "days"],
    "additionalProperties": False,
}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "note": {"type": "string", "description": "One or two short sentences on the plan's focus and how it adapts."},
        "tasks": {"type": "array", "items": TASK_SCHEMA},
    },
    "required": ["note", "tasks"],
    "additionalProperties": False,
}

# Backstop for the prompt's safety rules: a task that matches is dropped, whatever the model said.
UNSAFE = re.compile(
    r"\b(fasting|intermittent fast|water fast|fast for \d+|starv\w*|dehydrat\w*|skip(ping)? (a )?(sleep|meals?|breakfast|lunch|dinner)|"
    r"all[- ]?nighter|no sleep|sleep deprivation|stay up all|purg\w*|laxative\w*|crash diet|cold plunge|ice bath|"
    r"(talk|speak|go) (to|with|home with) (a )?strangers? (alone|at night|in private)|follow (a |the )?(stranger|someone)|"
    r"home address|phone number of|meet (a )?stranger|binge|alcohol|drugs?)\b", re.I)


def text_field(v, n):
    """Plain single-line text, control characters and angle brackets removed (so nothing can fake our <user_data> tags)."""
    s = re.sub(r"[\x00-\x1f\x7f<>]", " ", str(v if v is not None else ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if v != v else max(lo, min(hi, v))


def describe(data, *, replace=None, keep=None):
    """The app's payload as one <user_data> block. Every value is cleaned and length-limited here."""
    ob = data.get("onboarding") if isinstance(data.get("onboarding"), dict) else {}
    goals = [text_field(g.get("text"), 200) for g in (data.get("goals") if isinstance(data.get("goals"), list) else [])[:3] if isinstance(g, dict)]
    goals = [g for g in goals if g]
    lines = ["Goals: " + ("; ".join(goals) if goals else "none given (general wellbeing)")]
    for label, key, n in (("Experience level", "level", 40), ("Time available per day", "minutes", 40),
                          ("Preferred time of day", "timeOfDay", 40), ("Constraints and notes", "constraints", 300)):
        v = text_field(ob.get(key), n)
        if v:
            lines.append(f"{label}: {v}")
    inten = ob.get("intensity") if ob.get("intensity") in INTENSITIES else "balanced"
    lines.append(f"Intensity preference: {inten}")

    hist = data.get("history") if isinstance(data.get("history"), list) else []
    hl = []
    for h in hist[:30]:
        if not isinstance(h, dict) or not text_field(h.get("title"), 60):
            continue
        days = int(_num(h.get("days"), 0, 60, 0))
        if not days:
            continue
        rate = int(round(_num(h.get("rate"), 0, 1, 0) * 100))
        rs = h.get("reasons") if isinstance(h.get("reasons"), dict) else {}
        why = ", ".join(f"{k} x{int(_num(v, 0, 60, 0))}" for k, v in rs.items() if k in REASONS and _num(v, 0, 60, 0) >= 1)
        tg = f", target {_num(h.get('target'), 0, 10000, 0):g} {text_field(h.get('unit'), 16)}".rstrip() if h.get("target") else ""
        hl.append(f"- {text_field(h.get('title'), 60)} ({text_field(h.get('type'), 10)}{tg}): done {rate}% of {days} days" + (f"; skipped because: {why}" if why else ""))
    if hl:
        lines.append("Their recent history (last 14 days):\n" + "\n".join(hl))
    else:
        lines.append("No history yet: this is a fresh start. Begin easy.")

    cur = data.get("current") if isinstance(data.get("current"), list) else []
    cl = [f"- {text_field(c.get('title'), 60)}" for c in cur[:20] if isinstance(c, dict) and text_field(c.get("title"), 60)]
    if cl:
        lines.append("Tasks they already have (adjust or vary, don't just repeat):\n" + "\n".join(cl))

    adj = [text_field(a, 200) for a in (data.get("adjustments") if isinstance(data.get("adjustments"), list) else [])[:3]]
    adj = [a for a in adj if a]
    if adj:
        lines.append("Adjustments they approved from their last summary:\n" + "\n".join("- " + a for a in adj))

    if replace is not None:
        lines.append("Task to replace: " + text_field(replace.get("title"), 60) + f" ({text_field(replace.get('type'), 10)})")
        kl = [text_field(k, 60) for k in (keep or [])[:8] if text_field(k, 60)]
        if kl:
            lines.append("Keep (do not duplicate): " + "; ".join(kl))
    return "<user_data>\n" + "\n".join(lines) + "\n</user_data>"


def normalize_task(t):
    """One AI task as the app's task shape, or None if it can't be used or isn't safe."""
    if not isinstance(t, dict):
        return None
    title = text_field(t.get("title"), 60)
    ty = t.get("type")
    if not title or ty not in TYPES:
        return None
    why = text_field(t.get("why"), 140)
    if UNSAFE.search(title) or UNSAFE.search(why):
        return None
    if ty == "boolean":
        target, unit = 1, ""
    else:
        target = round(_num(t.get("target"), 0, 10000, 0), 2)
        if target <= 0:
            return None
        unit = "min" if ty == "duration" else text_field(t.get("unit"), 16)
        if ty == "duration":
            target = min(target, 240)
        if not unit:
            return None
    days = sorted({int(d) for d in (t.get("days") if isinstance(t.get("days"), list) else []) if isinstance(d, (int, float)) and 1 <= int(d) <= 7})
    recur = {"kind": "days", "days": days} if days and len(days) < 7 else {"kind": "daily"}
    return {
        "title": title, "type": ty, "target": target, "unit": unit,
        "cat": t.get("cat") if t.get("cat") in CATS else "other",
        "slot": t.get("slot") if t.get("slot") in SLOTS else "anytime",
        "diff": int(_num(t.get("diff"), 1, 3, 1)), "why": why, "recur": recur,
    }


def normalize_plan(result, *, single=False):
    """The AI's JSON as { note, tasks }, or None when too few usable tasks came back (so the caller can retry)."""
    if not isinstance(result, dict):
        return None
    seen, tasks = set(), []
    for raw in (result.get("tasks") if isinstance(result.get("tasks"), list) else []):
        t = normalize_task(raw)
        if t and t["title"].lower() not in seen:
            seen.add(t["title"].lower())
            tasks.append(t)
    tasks = tasks[:1 if single else MAX_TASKS]
    if len(tasks) < (1 if single else MIN_TASKS):
        return None
    return {"note": text_field(result.get("note"), 300), "tasks": tasks}


# ---------- summaries ----------
SUMMARY_TYPES = ["workout", "lifestyle", "combined", "weekly"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_LIFE_DAYS, MIN_WORKOUT_DAYS = 5, 3  # below this the summary must say there isn't enough data to link the two

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "went_well": {"type": "array", "items": {"type": "string"}},
        "missed": {"type": "array", "items": {"type": "string"}},
        "adjustments": {"type": "array", "items": {"type": "string"}},
        "link": {"type": "string"},
    },
    "required": ["went_well", "missed", "adjustments", "link"],
    "additionalProperties": False,
}


def _date(v):
    v = str(v or "")
    return v if DATE_RE.match(v) else ""


def _pct(v):
    return None if v is None or isinstance(v, bool) else (int(round(_num(v, 0, 1, 0) * 100)) if isinstance(v, (int, float)) else None)


def describe_summary(data):
    """The app's summary payload as one <user_data> block, plus whether a workout/life link can be described.
    Returns (text, type) or raises ValueError for a bad request."""
    ty = data.get("type")
    if ty not in SUMMARY_TYPES:
        raise ValueError("Unknown summary type.")
    frm, to = _date(data.get("from")), _date(data.get("to"))
    if not frm or not to:
        raise ValueError("Missing dates.")
    lines = [f"Summary type: {ty}", f"Period: {frm} to {to}"]
    goals = [text_field(g, 120) for g in (data.get("goals") if isinstance(data.get("goals"), list) else [])[:3]]
    goals = [g for g in goals if g]
    if goals:
        lines.append("Their goals: " + "; ".join(goals))

    life = data.get("life") if isinstance(data.get("life"), dict) else None
    if life and ty != "workout":
        lines.append("LIFE TASKS (a task is 'done' when its target was met):")
        for d in (life.get("days") if isinstance(life.get("days"), list) else [])[:7]:
            if not isinstance(d, dict) or not _date(d.get("date")):
                continue
            p = _pct(d.get("pct"))
            tag = "rest day (planned)" if d.get("rest") else "closed by the app, not by the person" if d.get("status") == "auto" else "ended by the person"
            lines.append(f"{_date(d.get('date'))}: {p if p is not None else 'no'}% done, {tag}")
            for t in (d.get("tasks") if isinstance(d.get("tasks"), list) else [])[:12]:
                if not isinstance(t, dict) or not text_field(t.get("title"), 60):
                    continue
                unit = text_field(t.get("unit"), 16)
                amt = "" if t.get("type") == "boolean" else f" ({_num(t.get('value'), 0, 100000, 0):g} of {_num(t.get('target'), 0, 100000, 0):g} {unit})".replace(" )", ")")
                why = text_field(t.get("reason"), 20)
                lines.append(f"  - {text_field(t.get('title'), 60)}: {'done' if t.get('done') else 'not done'}{amt}" + (f"; reason: {why}" if why and not t.get("done") and why in REASONS else ""))
    wk = data.get("workout") if isinstance(data.get("workout"), dict) else None
    if wk and ty != "lifestyle":
        lines.append("WORKOUTS:")
        for d in (wk.get("days") if isinstance(wk.get("days"), list) else [])[:7]:
            if not isinstance(d, dict) or not _date(d.get("date")):
                continue
            mins = f", {int(_num(d.get('minutes'), 0, 600, 0))} min" if d.get("minutes") else ""
            lines.append(f"{_date(d.get('date'))}: {text_field(d.get('title'), 60) or 'Workout'}, {int(_num(d.get('sets'), 0, 500, 0))} of {int(_num(d.get('planned'), 0, 500, 0))} sets, "
                         f"volume {int(_num(d.get('volumeKg'), 0, 10_000_000, 0))} kg{mins}")
    trend = [t for t in (data.get("trend") if isinstance(data.get("trend"), list) else [])[:14] if isinstance(t, dict) and _date(t.get("d"))]
    life_days = sum(1 for t in trend if _pct(t.get("pct")) is not None)
    work_days = sum(1 for t in trend if _num(t.get("sets"), 0, 500, 0) > 0)
    if ty in ("combined", "weekly") and life and wk:
        enough = life_days >= MIN_LIFE_DAYS and work_days >= MIN_WORKOUT_DAYS
        lines.append("DAY BY DAY, last 14 days (life = % of tasks done, sleep = % of sleep tasks done, vol = workout volume in kg, - = none):")
        for t in trend:
            sl = _pct(t.get("sleep"))
            lines.append(f"{_date(t.get('d'))}: life {_pct(t.get('pct')) if _pct(t.get('pct')) is not None else '-'}, "
                         f"sleep {sl if sl is not None else '-'}, vol {int(_num(t.get('vol'), 0, 10_000_000, 0)) or '-'}")
        lines.append(f"Days with life data: {life_days}. Days with a workout: {work_days}. "
                     + ("Enough data to describe a pattern if one is clearly visible." if enough else
                        "NOT enough data to describe any link between workouts and life tasks. Say so in link."))
    elif ty in ("combined", "weekly"):
        lines.append("There is no workout data in this period, so link must be an empty string." if not wk else "There is no life task data, so link must be an empty string.")
    return "<user_data>\n" + "\n".join(lines) + "\n</user_data>", ty


def normalize_summary(result, ty):
    """The AI's summary as { wentWell, missed, adjustments, link }, or None if it has nothing usable."""
    if not isinstance(result, dict):
        return None

    def items(key, n):
        out = []
        for x in (result.get(key) if isinstance(result.get(key), list) else []):
            t = text_field(x, 280)
            if t and not UNSAFE.search(t) and t not in out:
                out.append(t)
        return out[:n]

    went, miss, adj = items("went_well", 4), items("missed", 4), items("adjustments", 2)
    link = text_field(result.get("link"), 400)
    if UNSAFE.search(link) or ty in ("workout", "lifestyle"):
        link = ""
    if not (went or miss) or not adj:
        return None
    return {"wentWell": went, "missed": miss, "adjustments": adj, "link": link}
