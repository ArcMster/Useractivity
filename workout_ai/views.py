"""AI plan proxy for ForjArc Fitness Tracker.

The web app posts the Advanced form (body stats, goal text, days, strength, optional photo) with the
user's Firebase ID token. This view:
  1. checks the token and that the account isn't disabled (same rule as firestore.rules isActive()),
  2. enforces a daily limit per user (higher for admins),
  3. asks the AI (Google Gemini by default, or Claude) for insights, a 7-day plan and, if asked, a diet plan as JSON,
  4. tidies the plan into the shape the app stores and returns it.

Nothing the user sends (photo included) is stored. The AI API key never leaves this server.
The body check (ask) view takes a photo and a question and returns a written answer, with its own daily limit.
Settings are read from Django settings first, then environment variables (see PROXY_SETUP.md).
"""
import base64
import binascii
import json
import logging
import os
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import forj_chat as FC
from . import forj_prompts as FPR
from . import life_plan as LP
from . import life_prompts as LPR
from . import plan_import as PI
from .models import PlanRequest

log = logging.getLogger(__name__)


def conf(name, default=None):
    return getattr(settings, name, None) or os.environ.get(name) or default


FIREBASE_PROJECT_ID = conf("FIREBASE_PROJECT_ID", "")
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in conf("AI_ALLOWED_ORIGINS", "").split(",") if o.strip()]
DAILY_LIMIT = int(conf("AI_DAILY_LIMIT", "5"))
ASK_DAILY_LIMIT = int(conf("AI_ASK_DAILY_LIMIT", "10"))  # body check questions
ADMIN_DAILY_LIMIT = int(conf("AI_ADMIN_DAILY_LIMIT", "50"))  # for admins (admins/{uid} exists in Firestore)
PROVIDER = conf("AI_PROVIDER", "gemini").lower()  # "gemini" or "claude"
API_KEY = (conf("GEMINI_API_KEY") or conf("GOOGLE_API_KEY")) if PROVIDER == "gemini" else conf("ANTHROPIC_API_KEY")
MODEL = conf("AI_MODEL", "gemini-3.8-flash" if PROVIDER == "gemini" else "claude-opus-5")
EFFORT = conf("AI_EFFORT", "medium")  # Claude only
TIMEOUT = 170  # seconds: photos plus thinking can take a minute or two; stay under PythonAnywhere's request limit
REQUIRE_ACTIVE = str(conf("AI_REQUIRE_ACTIVE", "1")) != "0"
LIFE_PLAN_LIMIT = int(conf("AI_LIFE_PLAN_DAILY_LIMIT", "5"))  # Life Style plans
LIFE_TASK_LIMIT = int(conf("AI_LIFE_TASK_DAILY_LIMIT", "20"))  # Life Style single-task swaps
LIFE_SUM_LIMIT = int(conf("AI_LIFE_SUMMARY_DAILY_LIMIT", "5"))  # Life Style summaries
FORJ_LIMIT = int(conf("AI_FORJ_DAILY_LIMIT", "40"))  # Forj chat messages
IMPORT_LIMIT = int(conf("AI_IMPORT_DAILY_LIMIT", "10"))  # plan files read by AI
MAX_PHOTO_CHARS = 2_000_000  # base64 characters; the app sends about 300 KB

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_TYPES = ["push", "pull", "legs", "upper", "lower", "full", "legscore", "core", "cardio", "other", "rest"]
UNITS = ["", "sec", "min", "per leg", "per side"]
DIETS = {"kerala": "Kerala", "south": "South Indian", "north": "North Indian"}
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_http = requests.Session()
_google_request = google_requests.Request(session=_http)
_client = None


class AIError(Exception):
    """A failed AI call, with the HTTP status and message to send back to the app."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------- HTTP helpers ----------

def cors(response, request):
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin and (origin in ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS):
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response["Access-Control-Max-Age"] = "86400"
    return response


def fail(request, status, message):
    return cors(JsonResponse({"error": message}, status=status), request)


def health(request):
    return cors(JsonResponse({"ok": True, "provider": PROVIDER, "model": MODEL, "configured": bool(API_KEY and FIREBASE_PROJECT_ID)}), request)


# ---------- who is asking ----------

def verify_user(request):
    """Returns (uid, id_token) or raises PermissionError with a message for the user."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise PermissionError("Sign in to use AI planning.")
    token = auth[7:].strip()
    try:
        claims = id_token.verify_firebase_token(token, _google_request, audience=FIREBASE_PROJECT_ID)
    except Exception as e:  # expired, wrong project, bad signature
        log.info("token rejected: %s", e)
        raise PermissionError("Your sign-in has expired. Reload the app and try again.")
    if not claims or not claims.get("sub"):
        raise PermissionError("Sign in to use AI planning.")
    return claims["sub"], token


def firestore_get(path, token):
    url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/{path}"
    return _http.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)


def is_active(uid, token):
    """Same rule as isActive() in firestore.rules, read with the user's own token (no service account needed)."""
    r = firestore_get(f"accounts/{uid}", token)
    if r.status_code == 200:
        f = r.json().get("fields", {})
        status = f.get("status", {}).get("stringValue")
        if status is None:
            status = "disabled" if f.get("disabled", {}).get("booleanValue") else "active"
        return status in ("active", "pending")  # "pending" is left over from when new people needed approval
    if r.status_code == 404:  # joined before accounts existed: active if they have a profile
        return firestore_get(f"profiles/{uid}", token).status_code == 200
    raise RuntimeError(f"Firestore returned {r.status_code}")


def is_admin(uid, token):
    """Same rule as isAdmin() in firestore.rules. If Firestore can't be read, they get the normal limit."""
    try:
        return (firestore_get(f"admins/{uid}", token).status_code == 200
                or firestore_get(f"superAdmins/{uid}", token).status_code == 200)
    except requests.RequestException:
        log.warning("couldn't check admin status for %s", uid)
        return False


# ---------- the prompt ----------

SYSTEM = """You are an experienced strength and conditioning coach inside a workout tracking app. \
You write a one-week training plan that repeats weekly, plus short, honest insights about the person.

How to write:
- Plain, friendly English. Short sentences. Speak to the person as "you".
- Insights must be specific to what they told you and, if given, what the photo shows. No generic filler.
- Unless the answers ask for a diet plan: training advice only, with no calorie targets, diets, \
supplements or nutrition plans. You may say that nutrition and sleep matter, without numbers.
- Not medical advice. If they mention an injury, pain or a health condition, adapt the plan conservatively \
and tell them to check with a professional.

The photo (if any):
- Comment only on training-relevant things you can actually see: overall build, visible muscle balance \
between areas (for example upper vs lower body, front vs back), posture cues such as rounded shoulders \
or anterior pelvic tilt. Say what you see, then how the plan addresses it.
- Be respectful and encouraging. Never shame, never guess body-fat percentages or weight, never comment \
on attractiveness, never try to identify the person.
- If the photo is unclear, doesn't show a person, or shows too little to judge, say so in one sentence \
and base the plan on the other answers.
- If there is no photo, set insights.photo to an empty string.

The plan:
- Exactly 7 days, Monday to Sunday, each weekday once. Training days must match the number of days \
per week they asked for; the rest are type "rest" with no exercises. Spread rest days sensibly.
- Day type is one of: push, pull, legs, upper, lower, full, legscore (legs + core), core, cardio, other, rest.
- 4 to 8 exercises per training day. For each: sets (1-6), a rep range low-high, and a unit: "" for reps, \
"sec" or "min" for timed work, "per leg" or "per side" for unilateral work.
- Prefer exercise names from the app's list when one fits, spelled exactly the same, so the person's \
history carries over. Use a new name only when nothing on the list fits.
- Match rep ranges and exercise choice to their goal, level and equipment hints in the goal text.
- If they gave best lifts, put suggested starting weights in the day's note (conservative: about 70-80% \
of their estimated max for the rep range, rounded down to 2.5 kg or 5 lb). Beginners: tell them to start \
light and add weight once they hit the top of the range on every set.
- Each training day gets a short focus line and a practical note (warm-up, progression, form cue).
- Plan name: short, under 40 characters, describing the plan (for example "Upper/Lower strength, 4 days")."""

# Added to SYSTEM when the person asks for a diet plan.
SYSTEM_DIET = """

The diet plan (only when the answers ask for one):
- One day of eating that fits their goal, training and body weight, in the regional cuisine they chose. \
Use everyday home foods from that cuisine (for Kerala, for example: puttu, appam, idiyappam, kanji, \
matta rice, fish curry, thoran, avial, kadala curry), in portions a person can measure at home \
(cups, pieces, grams, palm-sized servings).
- Give approximate daily targets for calories and for protein, carbs and fat in grams, worked out from \
their weight, height, training and goal. Protein first: usually 1.6-2.2 g per kg of body weight, higher \
end when losing fat. Fat about 20-30% of calories. Carbs fill the rest, more on a muscle-gain goal. \
Round sensibly and say in the summary it's a starting point to adjust by how their weight changes.
- 4 to 6 meals (for example early morning, breakfast, lunch, evening snack, dinner, and a pre- or \
post-workout meal). For each: a time of day and 2 or 3 interchangeable options, each a complete meal \
with portions, so the week doesn't get repetitive.
- Estimate calories, protein, carbs and fat for every option from its portions, using typical values for \
home-cooked food. Keep each consistent (calories close to 4 x protein + 4 x carbs + 9 x fat). Options for \
the same meal should be close in calories and protein, and one option from each meal should add up to \
roughly the daily targets.
- If their goal mentions being vegetarian, eggetarian, vegan, allergies, foods they avoid or a budget, \
follow it strictly. Otherwise include both vegetarian and non-vegetarian options that are common in that cuisine.
- Whole foods first. No crash diets, no meal replacement products, no supplements beyond saying a \
protein powder is optional if they struggle to reach the protein target.
- 3 to 6 short practical tips: water, cooking oil (coconut oil is fine in moderation), rice portions, \
eating out, festivals and so on, whatever helps most for this person and cuisine.
- Not medical or dietitian advice: if they mention diabetes, a kidney or heart condition, pregnancy or \
an eating disorder, keep the plan general and tell them to check with a doctor or dietitian."""

DIET_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 sentences: the approach and why it suits their goal."},
        "calories": {"type": "integer", "description": "Approximate daily calories (kcal)."},
        "protein": {"type": "integer", "description": "Daily protein target in grams."},
        "carbs": {"type": "integer", "description": "Daily carbohydrate target in grams."},
        "fat": {"type": "integer", "description": "Daily fat target in grams."},
        "meals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "For example Breakfast."},
                    "time": {"type": "string", "description": "For example 8:00 am."},
                    "options": {
                        "type": "array",
                        "description": "2 or 3 interchangeable complete meals.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "food": {"type": "string", "description": "The meal with portions."},
                                "calories": {"type": "integer", "description": "Estimated kcal."},
                                "protein": {"type": "integer", "description": "Estimated grams."},
                                "carbs": {"type": "integer", "description": "Estimated grams."},
                                "fat": {"type": "integer", "description": "Estimated grams."},
                            },
                            "required": ["food", "calories", "protein", "carbs", "fat"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "time", "options"],
                "additionalProperties": False,
            },
        },
        "tips": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "calories", "protein", "carbs", "fat", "meals", "tips"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "2-4 sentences: where they are now and what the plan does about it."},
                "photo": {"type": "string", "description": "What the photo shows that matters for training, or empty if no photo."},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "priorities": {"type": "array", "items": {"type": "string"}},
                "cautions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "photo", "strengths", "priorities", "cautions"],
            "additionalProperties": False,
        },
        "plan": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "overview": {"type": "string", "description": "One or two sentences on how the week is laid out and how to progress."},
                "days": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "weekday": {"type": "string", "enum": WEEKDAYS},
                            "type": {"type": "string", "enum": DAY_TYPES},
                            "title": {"type": "string"},
                            "focus": {"type": "string"},
                            "note": {"type": "string"},
                            "exercises": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "sets": {"type": "integer"},
                                        "low": {"type": "integer"},
                                        "high": {"type": "integer"},
                                        "unit": {"type": "string", "enum": UNITS},
                                    },
                                    "required": ["name", "sets", "low", "high", "unit"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["weekday", "type", "title", "focus", "note", "exercises"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["name", "overview", "days"],
            "additionalProperties": False,
        },
    },
    "required": ["insights", "plan"],
    "additionalProperties": False,
}


def text_field(v, n):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def describe(data):
    """The form answers as plain text for the AI. Everything is cleaned and length-limited here."""
    unit = "lb" if data.get("unit") == "lb" else "kg"
    days = data.get("days")
    days = days if isinstance(days, int) and 1 <= days <= 7 else 4
    lines = [
        f"Body weight: {text_field(data.get('weight'), 20)} {unit}",
        f"Height: {text_field(data.get('height'), 30)}",
        f"Training days per week: {days}",
        f"Goal, in their words: {text_field(data.get('goal'), 1500)}",
    ]
    lifts = data.get("lifts") if isinstance(data.get("lifts"), list) else []
    lift_lines = [f"- {text_field(l.get('name'), 40)}: {text_field(l.get('weight'), 10)} {unit} x {text_field(l.get('reps'), 5) or 1}"
                  for l in lifts[:8] if isinstance(l, dict) and l.get("weight")]
    if lift_lines:
        lines.append("Best recent sets:\n" + "\n".join(lift_lines))
    else:
        lines.append("Strength: beginner, no best lifts given.")
    lines.append(f"Weights in the plan notes should be in {unit}.")
    diet = DIETS.get(data.get("diet"))
    if diet:
        lines.append(f"Diet plan: yes, please include one in {diet} cuisine.")
    known = data.get("exercises") if isinstance(data.get("exercises"), list) else []
    known = [text_field(x, 60) for x in known[:250] if isinstance(x, str) and x.strip()]
    if known:
        lines.append("Exercise names the app already uses:\n" + "; ".join(known))
    return "\n".join(lines)


def clean_list(v, n=6, size=300):
    return [text_field(x, size) for x in (v if isinstance(v, list) else []) if text_field(x, size)][:n]


def clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def normalize_diet(d):
    """The AI's diet plan with values in range, or None if it's missing or empty."""
    if not isinstance(d, dict):
        return None
    meals = []
    for m in (d.get("meals") if isinstance(d.get("meals"), list) else [])[:8]:
        if not isinstance(m, dict):
            continue
        options = []
        for o in (m.get("options") if isinstance(m.get("options"), list) else [])[:4]:
            if isinstance(o, str):
                o = {"food": o}
            food = text_field(o.get("food"), 400) if isinstance(o, dict) else ""
            if food:
                options.append({"food": food, "calories": clamp(o.get("calories"), 0, 3000, 0), "protein": clamp(o.get("protein"), 0, 300, 0),
                                "carbs": clamp(o.get("carbs"), 0, 600, 0), "fat": clamp(o.get("fat"), 0, 300, 0)})
        if options:
            meals.append({"name": text_field(m.get("name"), 40) or "Meal", "time": text_field(m.get("time"), 30), "options": options})
    if not meals:
        return None
    return {
        "summary": text_field(d.get("summary"), 800),
        "calories": clamp(d.get("calories"), 0, 6000, 0),
        "protein": clamp(d.get("protein"), 0, 400, 0),
        "carbs": clamp(d.get("carbs"), 0, 1000, 0),
        "fat": clamp(d.get("fat"), 0, 400, 0),
        "meals": meals,
        "tips": clean_list(d.get("tips"), 8, 300),
    }


def normalize(result):
    """The AI's JSON into the app's plan shape: 7 days, Monday first, values in range."""
    ins = result.get("insights") or {}
    plan = result.get("plan") or {}
    by_day = {}
    for d in plan.get("days") or []:
        wd = d.get("weekday")
        if wd in WEEKDAYS and wd not in by_day:
            by_day[wd] = d
    days = []
    for wd in WEEKDAYS:
        d = by_day.get(wd)
        t = d.get("type") if d else "rest"
        ex = []
        if d and t != "rest":
            for x in (d.get("exercises") or [])[:12]:
                name = text_field(x.get("name"), 80)
                if not name:
                    continue
                unit = x.get("unit") if x.get("unit") in UNITS else ""
                top = 600 if unit in ("sec", "min") else 100
                lo = clamp(x.get("low"), 1, top, 8)
                hi = clamp(x.get("high"), lo, top, lo)
                ex.append({"n": name, "s": clamp(x.get("sets"), 1, 10, 3), "lo": lo, "hi": hi, "u": unit})
        if t == "rest" or not ex:
            days.append({"t": "rest", "title": "Rest", "focus": text_field(d.get("focus") if d else "", 160) or "Recovery day", "note": "", "ex": []})
        else:
            days.append({"t": t if t in DAY_TYPES else "other", "title": text_field(d.get("title"), 40) or t.title(),
                         "focus": text_field(d.get("focus"), 160), "note": text_field(d.get("note"), 600), "ex": ex})
    return {
        "insights": {
            "summary": text_field(ins.get("summary"), 1200),
            "photo": text_field(ins.get("photo"), 1200),
            "strengths": clean_list(ins.get("strengths")),
            "priorities": clean_list(ins.get("priorities")),
            "cautions": clean_list(ins.get("cautions")),
        },
        "plan": {"name": text_field(plan.get("name"), 60) or "AI plan", "overview": text_field(plan.get("overview"), 600), "days": days},
        "diet": normalize_diet(result.get("diet")),
    }


# ---------- the AI call ----------
# Each returns the reply's JSON text. Gemini needs only requests; Claude needs the anthropic package.

def ask_gemini(photo, text, system, schema, max_tokens=16000):
    """Gemini's REST API through requests, so it runs on any Python (the google-genai SDK needs 3.10+).
    The request body is the same one google-genai sends for generate_content."""
    parts = []
    if photo:
        parts.append({"inlineData": {"mimeType": photo[0], "data": base64.b64encode(photo[1]).decode()}})
    parts.append({"text": text})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": max_tokens,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    try:
        r = _http.post(url, json=body, headers={"x-goog-api-key": API_KEY}, timeout=TIMEOUT)
    except requests.RequestException:  # timeouts, proxy and connection errors
        log.exception("couldn't reach Gemini")
        raise AIError(504, "Couldn't reach the AI service. Try again in a minute.")
    try:
        data = r.json()
    except ValueError:
        data = {}

    if r.status_code != 200:
        err = data.get("error") or {}
        status, message = str(err.get("status", "")), str(err.get("message", r.text[:300]))
        log.warning("Gemini error %s %s: %s", r.status_code, status, message)
        if r.status_code == 429:
            raise AIError(503, "AI planning has reached its limit for now. Try again later.")
        if r.status_code in (401, 403) or "api key" in message.lower() or "API_KEY" in json.dumps(err):
            raise AIError(503, "AI planning isn't set up correctly on the server.")
        if r.status_code == 404:
            raise AIError(503, "The AI model on the server isn't available. Check AI_MODEL.")
        if r.status_code < 500:
            raise AIError(400, "The AI couldn't read that request. If you added a photo, try a different one.")
        raise AIError(502, "The AI service is having trouble right now. Try again in a minute.")

    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        log.info("Gemini blocked the prompt: %s", block)
        raise AIError(422, "The AI couldn't help with that request. Try rewording your goal or using a different photo.")
    cand = (data.get("candidates") or [{}])[0]
    reason = cand.get("finishReason", "")
    if reason == "MAX_TOKENS":
        raise AIError(502, "The reply came back incomplete. Try again.")
    reply = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    if not reply:
        log.info("Gemini returned no text: %s", reason)
        raise AIError(422, "The AI couldn't help with that request. Try rewording your goal or using a different photo.")
    u = data.get("usageMetadata") or {}
    log.info("Gemini tokens: %s in, %s out", u.get("promptTokenCount"), u.get("candidatesTokenCount"))
    return reply


def ask_claude(photo, text, system, schema, max_tokens=16000):
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic(api_key=API_KEY, timeout=float(TIMEOUT), max_retries=1)
    content = []
    if photo:  # an image, or a PDF for plan import
        content.append({"type": "document" if photo[0] == "application/pdf" else "image",
                        "source": {"type": "base64", "media_type": photo[0], "data": base64.b64encode(photo[1]).decode()}})
    content.append({"type": "text", "text": text})
    try:
        response = _client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.BadRequestError as e:
        log.warning("bad request: %s", e.message)
        raise AIError(400, "The AI couldn't read that request. If you added a photo, try a different one.")
    except anthropic.AuthenticationError:
        log.error("Anthropic API key rejected")
        raise AIError(503, "AI planning isn't set up correctly on the server.")
    except anthropic.RateLimitError:
        raise AIError(503, "AI planning is busy right now. Try again in a minute.")
    except anthropic.APIStatusError as e:
        log.warning("Anthropic API error %s: %s", e.status_code, e.message)
        raise AIError(502, "The AI service is having trouble right now. Try again in a minute.")
    except anthropic.APIConnectionError:
        log.exception("couldn't reach Anthropic")
        raise AIError(504, "Couldn't reach the AI service. Try again in a minute.")

    if response.stop_reason == "refusal":
        raise AIError(422, "The AI couldn't help with that request. Try rewording your goal or using a different photo.")
    if response.stop_reason == "max_tokens":
        raise AIError(502, "The reply came back incomplete. Try again.")
    log.info("Claude tokens: %s in, %s out", response.usage.input_tokens, response.usage.output_tokens)
    return next((b.text for b in reversed(response.content) if b.type == "text"), "")


def read_photo(p):
    """(mime type, bytes) from the app's {type, data} photo, None if there isn't one, or ValueError with a message for the user."""
    if not (isinstance(p, dict) and p.get("data")):
        return None
    if len(str(p["data"])) > MAX_PHOTO_CHARS:
        raise ValueError("That photo is too large. Try a smaller one.")
    try:
        return (p.get("type") if p.get("type") in PHOTO_TYPES else "image/jpeg", base64.b64decode(str(p["data"]), validate=True))
    except (binascii.Error, ValueError):
        raise ValueError("Couldn't read that photo. Try a different one.")


# ---------- the endpoint ----------

@csrf_exempt
def plan(request):
    if request.method == "OPTIONS":
        return cors(JsonResponse({}), request)
    if request.method != "POST":
        return fail(request, 405, "Use POST.")
    if not API_KEY or not FIREBASE_PROJECT_ID or PROVIDER not in ("gemini", "claude"):
        return fail(request, 503, "AI planning isn't set up on the server yet.")

    try:
        uid, token = verify_user(request)
        if REQUIRE_ACTIVE and not is_active(uid, token):
            return fail(request, 403, "Your account is disabled, so AI planning isn't available.")
    except PermissionError as e:
        return fail(request, 401, str(e))
    except Exception:
        log.exception("account check failed")
        return fail(request, 502, "Couldn't check your account. Try again in a minute.")

    since = timezone.now() - timedelta(days=1)
    used = PlanRequest.objects.filter(uid=uid, kind="plan", created__gte=since, ok=True).count()
    limit = ADMIN_DAILY_LIMIT if is_admin(uid, token) else DAILY_LIMIT
    if used >= limit:
        return fail(request, 429, f"You've made {limit} AI plans in the last 24 hours. Try again tomorrow.")

    try:
        data = json.loads(request.body or b"{}")
    except RequestDataTooBig:
        return fail(request, 413, "That photo is too large. Try a smaller one.")
    except (ValueError, UnicodeDecodeError):
        return fail(request, 400, "Couldn't read the request.")
    if not text_field(data.get("goal"), 1500):
        return fail(request, 400, "Describe your goal.")

    answers = describe(data)
    try:
        photo = read_photo(data.get("photo"))
    except ValueError as e:
        return fail(request, 413 if "large" in str(e) else 400, str(e))
    text = ("The attached image is the person's photo, for training insights." if photo else "No photo was given.") + "\n\n" + answers

    system, schema = SYSTEM, SCHEMA
    if data.get("diet") in DIETS:
        system += SYSTEM_DIET
        schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "diet": DIET_SCHEMA}, "required": SCHEMA["required"] + ["diet"]}

    record = PlanRequest.objects.create(uid=uid)
    reply = ""
    try:
        reply = (ask_gemini if PROVIDER == "gemini" else ask_claude)(photo, text, system, schema)
        result = normalize(json.loads(reply))
    except AIError as e:
        return fail(request, e.status, str(e))
    except (ValueError, AttributeError, TypeError):
        log.warning("unparseable reply: %.500s", reply)
        return fail(request, 502, "The plan came back in a form the app couldn't read. Try again.")
    except Exception:  # anything unexpected: log it, and still give the app a message it can show
        log.exception("AI plan failed")
        return fail(request, 500, "Something went wrong on the planning server. Try again later.")

    record.ok = True
    record.save(update_fields=["ok"])
    result["remaining"] = max(0, limit - used - 1)
    result["model"] = MODEL
    return cors(JsonResponse(result), request)


# ---------- body check: a photo and a question in, a written answer out ----------

SYSTEM_ASK = """You are an experienced strength and conditioning coach inside a workout tracking app. \
The person sends a photo of a body part or their physique and a question about it. You look at the photo and \
answer their question in plain text.

How to answer:
- Plain, friendly English. Short sentences. Speak to the person as "you". No markdown symbols such as ** or #.
- First say what you can actually see that relates to their question. Be honest and specific.
- Then decide which of these fits and say so clearly:
  1. It looks normal. Most people are not perfectly symmetrical: one arm, shoulder or leg being a little \
bigger, shorter or differently shaped is very common, often from handedness, genetics, muscle insertion \
points or how the photo was taken (angle, flexing, lighting). If this is what you see, reassure them \
and explain why it is normal, and only add optional tips.
  2. There is a visible imbalance or posture issue that training can help with. Explain the likely \
cause, then give corrective steps: specific exercises with sets and reps, unilateral work, starting with \
the weaker side, matching reps, tempo, mind-muscle focus, how often to train it and how long to give it.
  3. The photo is unclear, doesn't show what they asked about, or can't answer the question. Say so in \
one sentence and say what photo would help (angle, lighting, relaxed and flexed).
- Never say a muscle's shape or length can be changed when it can't. Muscle belly length and insertion \
points are set by genetics; training changes size and strength, not shape. Be honest about that.
- Be respectful and encouraging. Never shame, never guess body-fat percentage or weight, never comment on \
attractiveness, never try to identify the person.
- Not medical advice. If you see or they describe something that could be medical, such as a lump, a \
sudden change, a swelling, a bruise that doesn't go away, pain, or a tendon that looks ruptured (for \
example a sudden bulge in the biceps), tell them to see a doctor soon.
- Keep it to about 150 to 350 words, with short paragraphs and, where useful, a simple list using "- " lines."""

ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["normal", "can_improve", "unclear", "see_doctor"],
                    "description": "normal: nothing to fix. can_improve: training can help. unclear: photo or question can't be judged. see_doctor: could be medical."},
        "answer": {"type": "string", "description": "The full reply to the person, as plain text."},
    },
    "required": ["verdict", "answer"],
    "additionalProperties": False,
}
VERDICTS = ["normal", "can_improve", "unclear", "see_doctor"]


@csrf_exempt
def ask(request):
    if request.method == "OPTIONS":
        return cors(JsonResponse({}), request)
    if request.method != "POST":
        return fail(request, 405, "Use POST.")
    if not API_KEY or not FIREBASE_PROJECT_ID or PROVIDER not in ("gemini", "claude"):
        return fail(request, 503, "AI isn't set up on the server yet.")

    try:
        uid, token = verify_user(request)
        if REQUIRE_ACTIVE and not is_active(uid, token):
            return fail(request, 403, "Your account is disabled, so AI isn't available.")
    except PermissionError as e:
        return fail(request, 401, str(e))
    except Exception:
        log.exception("account check failed")
        return fail(request, 502, "Couldn't check your account. Try again in a minute.")

    since = timezone.now() - timedelta(days=1)
    used = PlanRequest.objects.filter(uid=uid, kind="ask", created__gte=since, ok=True).count()
    limit = ADMIN_DAILY_LIMIT if is_admin(uid, token) else ASK_DAILY_LIMIT
    if used >= limit:
        return fail(request, 429, f"You've asked {limit} body check questions in the last 24 hours. Try again tomorrow.")

    try:
        data = json.loads(request.body or b"{}")
    except RequestDataTooBig:
        return fail(request, 413, "That photo is too large. Try a smaller one.")
    except (ValueError, UnicodeDecodeError):
        return fail(request, 400, "Couldn't read the request.")
    if not isinstance(data, dict):
        return fail(request, 400, "Couldn't read the request.")
    question = text_field(data.get("question"), 1000)
    if len(question) < 3:
        return fail(request, 400, "Ask your question.")
    try:
        photo = read_photo(data.get("photo"))
    except ValueError as e:
        return fail(request, 413 if "large" in str(e) else 400, str(e))
    if not photo:
        return fail(request, 400, "Add a photo so the AI can look.")
    text = f"The attached image is the person's photo.\n\nTheir question: {question}"

    record = PlanRequest.objects.create(uid=uid, kind="ask")
    reply = ""
    try:
        reply = (ask_gemini if PROVIDER == "gemini" else ask_claude)(photo, text, SYSTEM_ASK, ASK_SCHEMA, max_tokens=4000)
        out = json.loads(reply)
        answer = re.sub(r"\n{3,}", "\n\n", str(out.get("answer") or "").replace("\r", "")).strip()[:4000]
        if not answer:
            raise ValueError("empty answer")
    except AIError as e:
        return fail(request, e.status, str(e))
    except (ValueError, AttributeError, TypeError):
        log.warning("unparseable reply: %.500s", reply)
        return fail(request, 502, "The answer came back in a form the app couldn't read. Try again.")
    except Exception:
        log.exception("AI ask failed")
        return fail(request, 500, "Something went wrong on the server. Try again later.")

    record.ok = True
    record.save(update_fields=["ok"])
    verdict = out.get("verdict") if out.get("verdict") in VERDICTS else "unclear"
    return cors(JsonResponse({"answer": answer, "verdict": verdict, "remaining": max(0, limit - used - 1), "model": MODEL}), request)


# ---------- Life Style: AI task plans ----------

def life_gate(request, kind, default_limit, noun, max_body=60_000):
    """The checks every Life Style call shares. Returns (uid, limit, used, data) or an error response."""
    if request.method == "OPTIONS":
        return cors(JsonResponse({}), request)
    if request.method != "POST":
        return fail(request, 405, "Use POST.")
    if not API_KEY or not FIREBASE_PROJECT_ID or PROVIDER not in ("gemini", "claude"):
        return fail(request, 503, "AI isn't set up on the server yet.")
    try:
        uid, token = verify_user(request)
        if REQUIRE_ACTIVE and not is_active(uid, token):
            return fail(request, 403, "Your account is disabled, so AI isn't available.")
    except PermissionError as e:
        return fail(request, 401, str(e))
    except Exception:
        log.exception("account check failed")
        return fail(request, 502, "Couldn't check your account. Try again in a minute.")
    since = timezone.now() - timedelta(days=1)
    used = PlanRequest.objects.filter(uid=uid, kind=kind, created__gte=since, ok=True).count()
    limit = ADMIN_DAILY_LIMIT if is_admin(uid, token) else default_limit
    if used >= limit:
        return fail(request, 429, f"You've used {limit} {noun} in the last 24 hours. Try again tomorrow.")
    try:
        too_big = len(request.body or b"") > max_body
    except RequestDataTooBig:  # over Django's DATA_UPLOAD_MAX_MEMORY_SIZE
        too_big = True
    if too_big:
        return fail(request, 413, "That request is too large.")
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return fail(request, 400, "Couldn't read the request.")
    if not isinstance(data, dict):
        return fail(request, 400, "Couldn't read the request.")
    return uid, limit, used, data


def life_ask(kind, system, text, schema, normalize):
    """One AI call, asked again once if the reply can't be used. Returns the cleaned result or raises AIError."""
    ask = ask_gemini if PROVIDER == "gemini" else ask_claude
    for attempt in range(2):
        reply = ask(None, text, system, schema, max_tokens=4000)
        try:
            out = normalize(json.loads(reply))
        except (ValueError, AttributeError, TypeError):
            out = None
        if out:
            return out
        log.warning("life %s: unusable reply (attempt %s): %.300s", kind, attempt + 1, reply)
    raise AIError(502, "The AI's plan wasn't usable. Please try again, or add tasks yourself.")


@csrf_exempt
def life_plan(request):
    """A daily task plan from goals, onboarding answers and recent history, or one replacement task ("replace")."""
    g = life_gate(request, "life_task" if _wants_single(request) else "life_plan", LIFE_TASK_LIMIT if _wants_single(request) else LIFE_PLAN_LIMIT,
                  "single-task swaps" if _wants_single(request) else "AI plans")
    if not isinstance(g, tuple):
        return g
    uid, limit, used, data = g
    replace = data.get("replace") if isinstance(data.get("replace"), dict) else None
    single = replace is not None
    kind = "life_task" if single else "life_plan"
    goals = [x for x in (data.get("goals") if isinstance(data.get("goals"), list) else []) if isinstance(x, dict) and LP.text_field(x.get("text"), 200)]
    if not goals and not single and not isinstance(data.get("onboarding"), dict):
        return fail(request, 400, "Pick a goal first.")
    text = LP.describe(data, replace=replace, keep=data.get("keep") if isinstance(data.get("keep"), list) else [])
    record = PlanRequest.objects.create(uid=uid, kind=kind)
    try:
        result = life_ask(kind, LPR.TASK_SYSTEM if single else LPR.PLAN_SYSTEM, text, LP.PLAN_SCHEMA,
                          lambda r: LP.normalize_plan(r, single=single))
    except AIError as e:
        return fail(request, e.status, str(e))
    except Exception:
        log.exception("life plan failed")
        return fail(request, 500, "Something went wrong on the server. Try again later.")
    record.ok = True
    record.save(update_fields=["ok"])
    result["remaining"] = max(0, limit - used - 1)
    result["model"] = MODEL
    return cors(JsonResponse(result), request)


def _wants_single(request):
    """Peeks at the body to pick which daily limit applies, before the full checks run."""
    try:
        d = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(d, dict) and isinstance(d.get("replace"), dict)


@csrf_exempt
def life_summary(request):
    """A summary of a day or week of life tasks and workouts, on demand. The app caches it, so this runs once per summary
    unless the person asks to regenerate."""
    g = life_gate(request, "life_sum", LIFE_SUM_LIMIT, "AI summaries")
    if not isinstance(g, tuple):
        return g
    uid, limit, used, data = g
    try:
        text, ty = LP.describe_summary(data)
    except ValueError as e:
        return fail(request, 400, str(e))
    record = PlanRequest.objects.create(uid=uid, kind="life_sum")
    try:
        result = life_ask("life_sum", LPR.SUMMARY_SYSTEM, text, LP.SUMMARY_SCHEMA, lambda r: LP.normalize_summary(r, ty))
    except AIError as e:
        return fail(request, e.status, str(e))
    except Exception:
        log.exception("life summary failed")
        return fail(request, 500, "Something went wrong on the server. Try again later.")
    record.ok = True
    record.save(update_fields=["ok"])
    result["remaining"] = max(0, limit - used - 1)
    result["model"] = MODEL
    return cors(JsonResponse(result), request)


# ---------- Forj: the in-app chat assistant ----------

@csrf_exempt
def forj(request):
    """One Forj reply. The app sends the last few messages and a snapshot of the person's own activity; nothing is stored
    here except a row that counts the message toward the daily limit."""
    g = life_gate(request, "forj", FORJ_LIMIT, "Forj messages")
    if not isinstance(g, tuple):
        return g
    uid, limit, used, data = g
    try:
        messages = FC.clean_messages(data.get("messages"))
    except ValueError as e:
        return fail(request, 400, str(e))
    text = FC.describe(messages, data.get("context"))
    record = PlanRequest.objects.create(uid=uid, kind="forj")
    ask = ask_gemini if PROVIDER == "gemini" else ask_claude
    reply = ""
    try:
        for attempt in range(2):
            raw = ask(None, text, FPR.SYSTEM, FC.REPLY_SCHEMA, max_tokens=3000)
            try:
                reply = FC.normalize(json.loads(raw))
            except (ValueError, AttributeError, TypeError):
                reply = None
            if reply:
                break
            log.warning("forj: unusable reply (attempt %s): %.300s", attempt + 1, raw)
        if not reply:
            raise AIError(502, "Forj couldn't answer that. Try asking it another way.")
    except AIError as e:
        return fail(request, e.status, str(e))
    except Exception:
        log.exception("forj failed")
        return fail(request, 500, "Something went wrong on the server. Try again later.")
    record.ok = True
    record.save(update_fields=["ok"])
    return cors(JsonResponse({"reply": reply, "remaining": max(0, limit - used - 1), "model": MODEL}), request)


# ---------- plan import: a PDF or spreadsheet the app's own reader couldn't follow ----------

@csrf_exempt
def import_plan(request):
    """The app sends the text it read from the file (and the PDF itself when it is a scan with no text); the AI copies the
    plan into the app's shape. The app opens it in the plan editor for the person to check, so nothing is saved here."""
    g = life_gate(request, "import", IMPORT_LIMIT, "AI file imports", max_body=PI.MAX_FILE_CHARS + PI.MAX_TEXT * 4 + 20_000)
    if not isinstance(g, tuple):
        return g
    uid, limit, used, data = g
    try:
        doc = PI.read_file(data.get("file"))
        text = PI.describe(data)
    except ValueError as e:
        return fail(request, 413 if "large" in str(e) else 400, str(e))
    record = PlanRequest.objects.create(uid=uid, kind="import")
    ask = ask_gemini if PROVIDER == "gemini" else ask_claude
    out = None
    try:
        for attempt in range(2):
            raw = ask(doc, text, PI.SYSTEM, PI.SCHEMA)
            try:
                out = PI.normalize(json.loads(raw))
            except (ValueError, AttributeError, TypeError):
                out = None
            if out:
                break
            log.warning("import: unusable reply (attempt %s): %.300s", attempt + 1, raw)
        if not out:
            raise AIError(502, "The AI couldn't read a plan from that file. Try again, or build the plan yourself.")
    except AIError as e:
        return fail(request, e.status, str(e))
    except Exception:
        log.exception("plan import failed")
        return fail(request, 500, "Something went wrong on the server. Try again later.")
    if not out["plan"]:  # read fine, but there was no plan in it: don't count it against the limit
        record.delete()
        return fail(request, 422, out["notes"] or "The AI couldn't find a workout plan in that file.")
    record.ok = True
    record.save(update_fields=["ok"])
    out["remaining"] = max(0, limit - used - 1)
    out["model"] = MODEL
    return cors(JsonResponse(out), request)
