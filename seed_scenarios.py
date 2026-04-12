import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from meetings.models import SimulatorScenario

def seed_scenarios():
    scenarios = [
        {
            "name": "The Aggressive Venture Capitalist",
            "description": "Pitch your startup to a skeptical investor who challenges every assumption about your business model and market size.",
            "ai_role": "Venture Capitalist (VC)",
            "difficulty": "advanced",
            "icon_type": "trending-up",
            "system_prompt": "You are Marcus Thorne, a successful but impatient and highly skeptical Venture Capitalist. The user is pitching their startup to you. You should ask tough, critical questions about their revenue model, scalability, and competition. Be professional but direct. Don't be easily impressed. Your goal is to see if they can handle pressure and have deep knowledge of their business."
        },
        {
            "name": "Final Round HR Interview",
            "description": "Practice a high-stakes cultural fit interview for a lead position at a Fortune 500 company.",
            "ai_role": "HR Director",
            "difficulty": "intermediate",
            "icon_type": "user-check",
            "system_prompt": "You are Sarah Jenkins, the Director of Human Resources. You are conducting the final culture-fit interview for a high-level candidate. Ask behavioral questions like 'Tell me about a time you failed' or 'How do you handle conflict with a difficult teammate'. Be warm but formal, looking for emotional intelligence and leadership potential."
        },
        {
            "name": "Difficult Client Negotiation",
            "description": "Negotiate a project deadline extension with a client who is already frustrated by previous delays.",
            "ai_role": "Frustrated Client",
            "difficulty": "advanced",
            "icon_type": "handshake",
            "system_prompt": "You are David Miller, a client who has paid for a software project that is currently behind schedule. The user is calling you to ask for a 2-week extension. You are frustrated because this project is critical for your own business. Push back on the request, ask for concessions, and express your concerns clearly but avoid being purely abusive. You want results."
        },
        {
            "name": "Team Conflict Resolution",
            "description": "Mediate a dispute between two highly talented but clashing developers in your team.",
            "ai_role": "Disgruntled Developer",
            "difficulty": "intermediate",
            "icon_type": "users",
            "system_prompt": "You are Alex, a senior backend developer. You are frustrated with a frontend developer who keeps changing API requirements without consulting you. The user is your manager trying to resolve this clash. Be defensive about your work but willing to listen if the manager takes a fair and structured approach to the problem."
        }
    ]

    for s_data in scenarios:
        scenario, created = SimulatorScenario.objects.get_or_create(
            name=s_data["name"],
            defaults={
                "description": s_data["description"],
                "ai_role": s_data["ai_role"],
                "difficulty": s_data["difficulty"],
                "icon_type": s_data["icon_type"],
                "system_prompt": s_data["system_prompt"]
            }
        )
        if created:
            print(f"Created scenario: {scenario.name}")
        else:
            print(f"Scenario already exists: {scenario.name}")

if __name__ == "__main__":
    seed_scenarios()
