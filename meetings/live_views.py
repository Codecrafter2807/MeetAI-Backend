"""API endpoints for live streaming meetings."""
import logging
import os
import subprocess
import uuid
from django.core.files import File
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.http import StreamingHttpResponse

from .models import LiveMeeting, LiveTranscript

logger = logging.getLogger(__name__)


class StartLiveMeetingView(APIView):
    """Start a new live meeting session."""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Check if there is an active or processing live meeting."""
        from .models import LiveMeeting
        from django.db.models import Q
        
        active_meeting = LiveMeeting.objects.filter(
            created_by=request.user,
            status__in=['active', 'processing']
        ).first()
        
        if active_meeting:
            return Response({
                "exists": True,
                "live_meeting_id": active_meeting.id,
                "status": active_meeting.status,
                "title": active_meeting.title,
                "started_at": active_meeting.started_at,
                "ended_at": active_meeting.ended_at,
            })
        return Response({"exists": False})
    
    def post(self, request):
        try:
            title = request.data.get('title', 'Live Meeting')
            meeting_url = request.data.get('meeting_url', '')
            
            # Error if one is already active/processing
            active_meeting = LiveMeeting.objects.filter(
                created_by=request.user,
                status__in=['active', 'processing']
            ).first()
            
            if active_meeting:
                return Response(
                    {"error": "You already have a live meeting in progress.", "live_meeting_id": active_meeting.id},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Assign to active workspace
            from .models import WorkspaceMember, Workspace
            ws_slug = request.headers.get('X-Workspace-Slug')
            user_ws = None
            if ws_slug:
                try:
                    ws = Workspace.objects.get(slug=ws_slug)
                    user_ws = WorkspaceMember.objects.filter(user=request.user, workspace=ws).first()
                except Workspace.DoesNotExist:
                    pass
            if not user_ws:
                user_ws = WorkspaceMember.objects.filter(user=request.user).first()
            workspace = user_ws.workspace if user_ws else None

            # 2. Or activate an existing scheduled one
            existing_id = request.data.get('existing_id')
            if existing_id:
                live_meeting = get_object_or_404(LiveMeeting, uuid=existing_id, created_by=request.user)
                if live_meeting.status != 'scheduled':
                    return Response({"error": "This meeting is not in a scheduled state."}, status=status.HTTP_400_BAD_REQUEST)
                
                live_meeting.status = 'active'
                if title: live_meeting.title = title
                live_meeting.save()
            else:
                # Create brand new
                live_meeting = LiveMeeting.objects.create(
                    title=title,
                    meeting_url=meeting_url,
                    status='active',
                    created_by=request.user,
                    workspace=workspace
                )
            
            logger.info(f"Started live meeting {live_meeting.id}")
            
            return Response(
                {
                    "message": "Live meeting started",
                    "live_meeting_id": live_meeting.id,
                    "status": live_meeting.status,
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.exception(f"Error starting live meeting: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class UploadAudioChunkView(APIView):
    """Upload audio chunk and transcribe in real-time."""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, live_meeting_id):
        try:
            from django.db.models import Q
            from .models import WorkspaceMember
            user_ws = WorkspaceMember.objects.filter(user=request.user).values_list('workspace_id', flat=True)
            live_meeting = get_object_or_404(
                LiveMeeting, 
                Q(id=live_meeting_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_ws) & Q(is_shared=True)))
            )
            
            # Get audio file from request
            if 'audio' not in request.FILES:
                return Response(
                    {"error": "No audio file provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            audio_file = request.FILES['audio']
            chunk_index = int(request.data.get('chunk_index', 0))
            timestamp = float(request.data.get('timestamp', 0.0))
            
            # Save to temp location with the original file extension
            import tempfile
            
            temp_dir = tempfile.gettempdir()
            ext = os.path.splitext(audio_file.name)[1].lower() or '.webm'
            if ext not in ['.wav', '.webm', '.mp4', '.ogg', '.opus']:
                ext = '.webm'
            file_id = uuid.uuid4().hex
            temp_path = os.path.join(temp_dir, f"chunk_{live_meeting_id}_{chunk_index}_{file_id}{ext}")
            
            # Save uploaded file to a temp path for immediate transcription.
            with open(temp_path, 'wb') as f:
                for chunk in audio_file.chunks():
                    f.write(chunk)

            # Preserve the final recording for later diarization and NLP.
            if chunk_index == 0 and not live_meeting.audio_file:
                with open(temp_path, 'rb') as temp_f:
                    live_meeting.audio_file.save(audio_file.name, File(temp_f), save=True)

            # Queue transcription task
            from .tasks import process_audio_chunk_task
            process_audio_chunk_task.delay(
                live_meeting_id=live_meeting_id,
                chunk_index=chunk_index,
                timestamp=timestamp,
                audio_path=temp_path,
            )
            
            logger.info(f"Queued chunk {chunk_index} for live meeting {live_meeting_id}")
            
            return Response(
                {
                    "message": "Chunk queued for processing",
                    "chunk_index": chunk_index,
                    "live_meeting_id": live_meeting_id,
                },
                status=status.HTTP_200_OK,
            )
            
        except Exception as e:
            logger.exception(f"Error uploading audio chunk: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class EndLiveMeetingView(APIView):
    """End live meeting and generate summary."""
    permission_classes = [IsAuthenticated]
    
    def post(self, request, live_meeting_id):
        try:
            from django.db.models import Q
            from .models import WorkspaceMember
            user_ws = WorkspaceMember.objects.filter(user=request.user).values_list('workspace_id', flat=True)
            live_meeting = get_object_or_404(
                LiveMeeting, 
                Q(id=live_meeting_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_ws) & Q(is_shared=True)))
            )
            
            live_meeting.status = 'processing'
            live_meeting.ended_at = timezone.now()
            live_meeting.save()
            
            total_chunks = request.data.get('total_chunks')
            if total_chunks is not None:
                from django.core.cache import cache
                cache.set(f"live_total_{live_meeting_id}", int(total_chunks), timeout=86400)
                logger.info(f"Live meeting {live_meeting_id} expected total chunks: {total_chunks}")
            
            # The async Celery tasks (process_audio_chunk_task) are responsible 
            # for kicking off the final process_live_meeting_nlp task when all chunks 
            # have arrived. We no longer prematurely concatenate audio or fire NLP here.
            
            return Response(
                {
                    "message": "Live meeting ended, processing summary",
                    "live_meeting_id": live_meeting_id,
                    "status": live_meeting.status,
                },
                status=status.HTTP_200_OK,
            )
            
        except Exception as e:
            logger.exception(f"Error ending live meeting: {e}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class GetLiveMeetingView(APIView):
    """Get live meeting status and transcript."""
    permission_classes = [IsAuthenticated]
    
    def get(self, request, live_meeting_id):
        try:
            # Allow if creator OR workspace member
            from django.db.models import Q
            from .models import WorkspaceMember
            user_ws = WorkspaceMember.objects.filter(user=request.user).values_list('workspace_id', flat=True)
            live_meeting = get_object_or_404(
                LiveMeeting, 
                Q(id=live_meeting_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_ws) & Q(is_shared=True)))
            )
            
            transcripts = LiveTranscript.objects.filter(
                live_meeting=live_meeting
            ).order_by('chunk_index')
            
            full_transcript = live_meeting.transcript_text.strip()
            if not full_transcript:
                full_transcript = ' '.join([t.text for t in transcripts if t.text.strip()])
            
            return Response(
                {
                    "live_meeting_id": live_meeting.id,
                    "title": live_meeting.title,
                    "meeting_url": live_meeting.meeting_url,
                    "status": live_meeting.status,
                    "started_at": live_meeting.started_at,
                    "ended_at": live_meeting.ended_at,
                    "full_transcript": full_transcript,
                    "chunk_count": transcripts.count(),
                    "chunks": [
                        {
                            "index": t.chunk_index,
                            "text": t.text,
                            "timestamp": t.timestamp,
                        }
                        for t in transcripts
                    ],
                    "summary_short": live_meeting.summary_short,
                    "summary_detailed": live_meeting.summary_detailed,
                    "summary_key_points": live_meeting.summary_key_points,
                    "action_items": live_meeting.action_items,
                    "speaker_segments": live_meeting.speaker_segments,
                },
                status=status.HTTP_200_OK,
            )
            
        except LiveMeeting.DoesNotExist:
            return Response(
                {"error": "Live meeting not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
