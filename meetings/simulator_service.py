import logging
from typing import Any, List, Dict
from groq import Groq
from django.conf import settings
from .nlp_service import _groq_api_key, parse_llm_json

logger = logging.getLogger(__name__)

def generate_simulator_response(session, user_text: str) -> str:
    """
    Generates AI persona response for a simulator session turn.
    """
    key = _groq_api_key()
    if not key:
        return "AI Simulator: I'm currently in offline mode (API key missing). I can't generate a persona response right now."

    model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Hydrate context
    messages = [
        {"role": "system", "content": session.scenario.system_prompt}
    ]
    
    # Add past conversation history
    # Limit history to last 10 messages for token efficiency in practice sessions
    past_messages = session.messages.order_by('timestamp').values('role', 'text_content')
    for msg in past_messages:
        role = "user" if msg['role'] == 'user' else "assistant"
        messages.append({"role": role, "content": msg['text_content']})
    
    # Add current user turn if not already saved (or just pass it)
    messages.append({"role": "user", "content": user_text})

    try:
        client = Groq(api_key=key)
        chat_completion = client.chat.completions.create(
            messages=messages,
            model=model,
            temperature=0.7, # Higher temperature for more natural roleplay
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Simulator turn failed for session {session.id}: {e}")
        return "I'm sorry, I'm having trouble responding right now. Can you try saying that again?"

def generate_session_feedback(session) -> Dict[str, Any]:
    """
    Generates holistic feedback for a simulator session after it ends.
    """
    key = _groq_api_key()
    if not key:
        return {
            "confidence_score": 0,
            "strengths": ["Feedback system offline"],
            "weaknesses": ["Check API configuration"],
            "improvements": ["Connect to internet"]
        }

    model = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Extract full transcript
    transcript_lines = []
    for msg in session.messages.order_by('timestamp'):
        role_label = "Student" if msg.role == 'user' else session.scenario.ai_role
        transcript_lines.append(f"{role_label}: {msg.text_content}")
    
    full_transcript = "\n".join(transcript_lines)
    
    prompt = f"""You are a Strategic Communication Coach.
Analyze the following roleplay session transcript between a User (Practice Mode) and an AI representing: "{session.scenario.ai_role}".
Scenario: {session.scenario.name} - {session.scenario.description}

Full Transcript:
{full_transcript}

Your Task:
Evaluate the User's performance and return a JSON object with:
1. confidence_score: A number from 0-100.
2. strengths: A list of 3-4 bullet points highlighting what the user did well.
3. weaknesses: A list of 2-3 bullet points of what they missed or did poorly.
4. improvements: A list of 2-3 actionable tips.

Style: Be encouraging but precise and data-driven.
Return exactly this JSON structure:
{{"confidence_score": 85, "strengths": ["...", "..."], "weaknesses": ["...", "..."], "improvements": ["...", "..."]}}
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
        return parsed or {}
    except Exception as e:
        logger.error(f"Failed to generate feedback for simulator session {session.id}: {e}")
        return {}
