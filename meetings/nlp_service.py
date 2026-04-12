"""Groq: summary, key points, and action items from aligned transcript."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from groq import Groq
from django.conf import settings

from .models import ActionItem, Meeting, Summary

logger = logging.getLogger(__name__)


def _groq_api_key() -> str:
    return (
        (getattr(settings, "GROQ_API_KEY", None) or "").strip()
        or (os.environ.get("GROQ_API_KEY", "") or "").strip()
    )


def _extract_message_content(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return ""


def format_transcript_for_llm(meeting: Meeting) -> str:
    lines: list[str] = []
    for seg in meeting.segments.order_by("start_time", "id"):
        t = (seg.text or "").strip()
        if not t:
            continue
        lines.append(f"{seg.speaker}: {t}")
    if lines:
        return "\n".join(lines)
    tr = getattr(meeting, "transcript", None)
    if tr and (tr.full_text or "").strip():
        return (tr.full_text or "").strip()
    return ""


def parse_llm_json(content: str) -> dict[str, Any] | None:
    if not content or not str(content).strip():
        return None
    raw = str(content).strip()
    raw = re.sub(r"^\s*```(?:json)?\s*", "", raw, flags=re.IGNORECASE | re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw).strip()
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            out = json.loads(raw[start : end + 1])
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _get_action_count_instruction(duration_seconds: float) -> str:
    minutes = duration_seconds / 60
    if minutes < 5:
        return "at least 3 action items"
    elif 5 <= minutes <= 15:
        return "between 5 and 7 action items"
    else:
        return "between 8 and 10 action items"


def _generate_local_insights(transcript_text: str, duration_seconds: float = 0) -> dict[str, Any]:
    text = transcript_text.strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    short_summary = ' '.join(sentences[:2]).strip() or text[:300].strip()
    detailed_summary = ' '.join(sentences[:4]).strip() or text[:600].strip()

    key_points = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(key_points) >= 3:
            break
        if len(sentence) > 30:
            key_points.append(sentence)
    if not key_points and sentences:
        key_points = [sentences[0].strip()]

    # Determine target count
    minutes = duration_seconds / 60
    if minutes < 5:
        min_items, max_items = 3, 5
    elif 5 <= minutes <= 15:
        min_items, max_items = 5, 7
    else:
        min_items, max_items = 8, 10

    action_items: list[dict[str, str]] = []
    for sentence in sentences:
        if re.search(r'\b(need|should|must|action|follow up|follow-up|deadline|due|assign|assigning|complete|task)\b', sentence, re.IGNORECASE):
            action_items.append(
                {
                    "task": sentence.strip(),
                    "assigned_to": "",
                    "deadline": "",
                    "priority": "medium",
                }
            )
            if len(action_items) >= max_items:
                break
    
    # If not enough keyword-based items, fill with remaining sentences if available
    if len(action_items) < min_items:
        for sentence in sentences:
            if len(action_items) >= min_items:
                break
            clean_sentence = sentence.strip()
            if not clean_sentence or any(item["task"] == clean_sentence for item in action_items):
                continue
            action_items.append({
                "task": clean_sentence,
                "assigned_to": "",
                "deadline": "",
                "priority": "medium",
            })

    if not action_items and sentences:
        action_items = [
            {
                "task": sentences[0].strip(),
                "assigned_to": "",
                "deadline": "",
                "priority": "medium",
            }
        ]

    # Generate a title from the first sentence or first few words
    # Try to find a meaningful sentence
    potential_title = "New Meeting"
    for s in sentences:
        s = s.strip()
        if len(s) > 10 and not any(gt in s.lower() for gt in ["hello", "hi ", "welcome"]):
            potential_title = s.split(',')[0].split('.')[0][:60].strip()
            break
            
    if potential_title == "New Meeting" and sentences:
        potential_title = sentences[0][:60].strip()

    return {
        "title": potential_title,
        "short_summary": short_summary,
        "detailed_summary": detailed_summary,
        "key_points": key_points,
        "action_items": action_items,
    }


def request_grok_insights(transcript_text: str, duration_seconds: float = 0) -> dict[str, Any] | None:
    key = _groq_api_key()
    t = transcript_text.strip()
    if not t:
        return None

    if not key:
        logger.warning(
            "GROQ_API_KEY is empty — using local fallback summary and action items."
        )
        return _generate_local_insights(t, duration_seconds)

    model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    max_chars = int(getattr(settings, "GROQ_MAX_TRANSCRIPT_CHARS", 100_000))

    if len(t) > max_chars:
        logger.info(
            "Truncating transcript for Groq: %s -> %s characters",
            len(t),
            max_chars,
        )
        t = t[:max_chars] + "\n\n[Transcript truncated for API size limits.]"

    count_instruction = _get_action_count_instruction(duration_seconds)
    messages = [
        {"role": "system", "content": "You are an AI meeting assistant. Generate a highly descriptive and specific title based on the meeting content."},
        {"role": "user", "content": f"""Given the following meeting transcript, generate:
1. Descriptive and Specific Title (max 60 characters). DO NOT use generic titles like 'External Meeting' or 'Live Meeting'.
2. Short summary (a few sentences)
3. Detailed summary (paragraphs)
4. Key points (short bullet strings)
5. Action items with task, assigned_to, deadline, priority (low/medium/high). Please {count_instruction}.

Return a single JSON object only (no markdown), exactly this shape:
{{"title":"","short_summary":"","detailed_summary":"","key_points":["…"],"action_items":[{{"task":"","assigned_to":"","deadline":"","priority":"medium"}}]}}

Transcript:
""" + t}
    ]

    try:
        client = Groq(api_key=key)
        chat_completion = client.chat.completions.create(
            messages=messages,
            model=model,
            temperature=0.3,
            response_format={"type": "json_object"} if getattr(settings, "GROQ_USE_JSON_RESPONSE", True) else None,
        )
        content = chat_completion.choices[0].message.content
    except Exception as exc:
        logger.warning("Groq request failed: %s", exc)
        logger.warning("Falling back to local insights.")
        return _generate_local_insights(transcript_text, duration_seconds)

    parsed = parse_llm_json(content)
    if not parsed:
        logger.warning("Could not parse Groq JSON. Snippet: %s", (content or "")[:400])
        return _generate_local_insights(transcript_text, duration_seconds)
    
    # Normalize title key
    if 'title' not in parsed:
        for key in ['meeting_title', 'Descriptive Title', 'Title', 'topic']:
            if key in parsed:
                parsed['title'] = parsed[key]
                break

    logger.info("Groq returned parseable JSON (keys: %s)", list(parsed.keys()))
    return parsed


_VALID_PRIORITY = {c[0] for c in ActionItem.PRIORITY_CHOICES}


def _normalize_priority(val: Any) -> str:
    s = (str(val or "medium")).lower().strip()
    return s if s in _VALID_PRIORITY else "medium"


def persist_insights(meeting: Meeting, data: dict[str, Any]) -> bool:
    key_points = data.get("key_points") or []
    if isinstance(key_points, str):
        key_points = [key_points]
    if not isinstance(key_points, list):
        key_points = []
    key_points = [str(x).strip() for x in key_points if str(x).strip()]

    short_s = (data.get("short_summary") or "").strip()
    long_s = (data.get("detailed_summary") or "").strip()
    if not short_s and not long_s and not key_points and not (data.get("action_items") or []):
        return False

    # 👉 Update title if generic
    new_title = (data.get("title") or "").strip()
    generic_titles = ["live meeting", "external meeting", "untitled meeting", "new meeting", "meeting"]
    current_title_lower = (meeting.title or "").lower()
    
    is_generic = not meeting.title or any(gt in current_title_lower for gt in generic_titles)
    
    if new_title and is_generic:
        meeting.title = new_title
        meeting.save(update_fields=["title"])

    Summary.objects.update_or_create(
        meeting=meeting,
        defaults={
            "short_summary": short_s or "(no short summary)",
            "detailed_summary": long_s or short_s or "(no detailed summary)",
            "key_points": key_points,
        },
    )

    meeting.action_items.all().delete()
    items = data.get("action_items") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            task = (item.get("task") or "").strip()
            if not task:
                continue
            ActionItem.objects.create(
                meeting=meeting,
                task=task,
                assigned_to=(item.get("assigned_to") or "").strip() or None,
                deadline=(item.get("deadline") or "").strip() or None,
                priority=_normalize_priority(item.get("priority")),
            )
    logger.info(
        "Saved meeting %s summary + %s action item(s)",
        meeting.id,
        meeting.action_items.count(),
    )
    return True


def run_meeting_nlp(meeting: Meeting) -> bool:
    """
    If GROK_API_KEY is set and transcript text exists, call Grok and save Summary + ActionItems.
    Returns True if insights were persisted.
    """
    text = format_transcript_for_llm(meeting)
    if not text.strip():
        return False
    
    # Calculate duration in seconds from segments
    segments = meeting.segments.all().order_by("-end_time")
    duration_seconds = segments[0].end_time if segments.exists() else 0
    
    parsed = request_grok_insights(text, duration_seconds=duration_seconds)
    if not parsed:
        return False
    return persist_insights(meeting, parsed)



def generate_prep_intelligence(topic_title: str, past_context_text: str = "") -> dict[str, Any]:
    """Generates a strategic prep brief and agenda using LLM."""
    key = _groq_api_key()
    if not key:
        return {
            "context": f"Baseline Strategy: No prior history for '{topic_title}'. AI suggests a standard kickoff approach.",
            "agenda": ["Define objective", "Identify stakeholders", "Establish timeline"]
        }

    model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    
    prompt = f"""You are an Expert Strategic Analyst.
Analyze the upcoming meeting topic: "{topic_title}".
Current Knowledge Context (Internal History): {past_context_text or "No internal history found."}

Your Task:
1. Generate a "Strategic Briefing" (2-3 sentences). 
   - If internal history exists, focus on bridging past decisions to this meeting.
   - If NO history exists, provide a high-level strategic overview of the topic itself (core concepts, industry standards, or common strategic pitfalls related to "{topic_title}").
2. Generate a "Tailored Agenda" (Exactly 3 hyper-specific bullet points).

Style: Professional, analytical, and data-driven. Avoid "As we introduce..." boilerplate. Start directly with the insight.

Return exactly this JSON:
{{"brief": "...", "agenda": ["...", "...", "..."]}}
"""


    try:
        client = Groq(api_key=key)
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0.5,
            response_format={"type": "json_object"}
        )
        content = chat_completion.choices[0].message.content
        parsed = parse_llm_json(content)
        if parsed and 'brief' in parsed and 'agenda' in parsed:
            return {
                "context": parsed['brief'],
                "suggested_agenda": parsed['agenda']
            }
    except Exception as exc:
        logger.warning("Groq Prep Generation failed: %s", exc)

    return {
        "context": f"New Strategic Topic: Establishing baseline for '{topic_title}'.",
        "suggested_agenda": ["Define objective", "Identify stakeholders", "Establish timeline"]
    }
