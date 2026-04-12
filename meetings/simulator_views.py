import os
import uuid
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.conf import settings

from .models import SimulatorScenario, SimulatorSession, SimulatorMessage
from .simulator_service import generate_simulator_response, generate_session_feedback
from .whisper_service import transcribe_audio

class ScenarioListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        scenarios = SimulatorScenario.objects.all().order_by('difficulty', 'name')
        data = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "difficulty": s.difficulty,
                "ai_role": s.ai_role,
                "icon_type": s.icon_type,
            } for s in scenarios
        ]
        return Response(data)

class SimulatorStartView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        scenario_id = request.data.get('scenario_id')
        scenario = get_object_or_404(SimulatorScenario, id=scenario_id)
        
        # End any previous active sessions for this user to avoid conflicts
        SimulatorSession.objects.filter(user=request.user, status='active').update(status='completed', ended_at=timezone.now())

        session = SimulatorSession.objects.create(
            user=request.user,
            scenario=scenario,
            status='active'
        )
        
        return Response({
            "session_id": session.id,
            "scenario_name": scenario.name,
            "ai_role": scenario.ai_role,
            "system_prompt": scenario.system_prompt
        }, status=status.HTTP_201_CREATED)

class SimulatorTurnView(APIView):
    """
    Handles a user's verbal turn. 
    1. Receives audio.
    2. Transcribes with Whisper.
    3. Saves user message.
    4. Generates AI response via simulator_service.
    5. Saves AI message.
    6. Returns AI text.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(SimulatorSession, id=session_id, user=request.user)
        
        if session.status != 'active':
            return Response({"error": "Session is closed."}, status=status.HTTP_400_BAD_REQUEST)

        audio_file = request.FILES.get('audio')
        user_text = request.data.get('text', '').strip()

        if audio_file:
            # Save temporary file for Whisper
            temp_name = f"sim_{uuid.uuid4().hex}.webm"
            temp_path = os.path.join(settings.MEDIA_ROOT, 'temp_audio', temp_name)
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            
            with open(temp_path, 'wb+') as destination:
                for chunk in audio_file.chunks():
                    destination.write(chunk)
            
            try:
                # Transcribe
                result = transcribe_audio(temp_path)
                if result:
                    user_text = result.get('text', '').strip()
                
                # Cleanup temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return Response({"error": f"Transcription failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not user_text:
            return Response({"error": "No speech detected. Please try again."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Save User Message
        SimulatorMessage.objects.create(
            session=session,
            role='user',
            text_content=user_text
        )

        # 2. Get AI Response
        ai_response = generate_simulator_response(session, user_text)

        # 3. Save AI Message
        SimulatorMessage.objects.create(
            session=session,
            role='ai',
            text_content=ai_response
        )

        return Response({
            "user_text": user_text,
            "ai_response": ai_response
        })

class SimulatorFeedbackView(APIView):
    """
    Ends the session and generates comprehensive feedback.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_object_or_404(SimulatorSession, id=session_id, user=request.user)
        if not session.feedback_data:
            return Response({"error": "Feedback not yet generated."}, status=status.HTTP_404_NOT_FOUND)
        return Response(session.feedback_data)

    def post(self, request, session_id):
        session = get_object_or_404(SimulatorSession, id=session_id, user=request.user)
        
        # End session if still active
        if session.status == 'active':
            session.status = 'completed'
            session.ended_at = timezone.now()
            session.save()

        # Generate feedback if not already generated
        if not session.feedback_data:
            feedback = generate_session_feedback(session)
            session.feedback_data = feedback
            session.save()

        return Response(session.feedback_data)
