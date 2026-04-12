#!/usr/bin/env python
import os
import django
from pathlib import Path

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from meetings.nlp_service import request_grok_insights

# Test with a simple transcript
test_transcript = "Alice: Hello everyone, let's discuss the project.\nBob: I think we should start with planning."

result = request_grok_insights(test_transcript)
if result:
    print("Success! Groq API returned results:")
    print(result)
else:
    print("No results: API key might be invalid or API failed.")