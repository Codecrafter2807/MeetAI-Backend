from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .chat_service import MeetAIChatAssistant

class AIChatView(APIView):
    """API endpoint for interacting with the MeetAI support chatbot."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        messages = request.data.get('messages', [])
        
        if not messages:
            return Response(
                {"error": "No messages provided"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Basic validation to ensure messages is a list of objects with role/content
        if not isinstance(messages, list) or not all(isinstance(m, dict) and 'role' in m and 'content' in m for m in messages):
             return Response(
                {"error": "Invalid messages format. Expected list of {role, content}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        assistant = MeetAIChatAssistant()
        response_text = assistant.get_response(messages)

        return Response({
            "response": response_text
        }, status=status.HTTP_200_OK)
