import io
import json
import os
import zipfile

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from .alignment import whisper_text_for_interval
from .models import Meeting, AudioFile, Transcript, SpeakerSegment, Summary, LiveMeeting, ActionItem
from .serializers import AudioUploadSerializer


class MeetingListView(APIView):
    """
    Returns a unified list of both regular uploaded Meetings and LiveMeetings.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Q
        from .models import LiveMeeting, WorkspaceMember
        q = request.query_params.get('q', '').strip()

        # Get all workspaces the user is a member of
        user_workspaces = WorkspaceMember.objects.filter(user=request.user).values_list('workspace_id', flat=True)

        meetings_qs = Meeting.objects.filter(
            Q(created_by=request.user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True))
        ).distinct()
        live_qs = LiveMeeting.objects.filter(
            Q(created_by=request.user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True))
        ).exclude(status='active').distinct()


        if q:
            meetings_qs = meetings_qs.filter(
                Q(title__icontains=q) |
                Q(transcript__full_text__icontains=q) |
                Q(summary__short_summary__icontains=q) |
                Q(summary__detailed_summary__icontains=q) |
                Q(action_items__task__icontains=q)
            ).distinct()

            # For LiveMeeting, JSON fields can't easily be __icontains filtered on SQLite in all versions.
            # But we have transcript_text and summary_short as TextField.
            live_qs = live_qs.filter(
                Q(title__icontains=q) |
                Q(transcript_text__icontains=q) |
                Q(summary_short__icontains=q) |
                Q(summary_detailed__icontains=q)
            ).distinct()

        meetings = list(meetings_qs.order_by('-created_at'))
        live_meetings = list(live_qs.order_by('-started_at'))
        
        results = []
        for m in meetings:
            results.append({
                "id": str(m.uuid),
                "meeting_id": str(m.uuid),
                "status": m.status,
                "created_at": m.created_at if hasattr(m, 'created_at') else None,
                "title": m.title or f"Meeting {m.id}",
                "is_shared": getattr(m, 'is_shared', False),
                "is_host": m.created_by_id == request.user.id,
                "speaker_count": m.segments.values('speaker').distinct().count(),
                "duration": sum(max(0.0, s.end_time - s.start_time) for s in m.segments.all()),
            })
            
        for m in live_meetings:
            # Use scheduled_at for upcoming meetings in the list view
            display_date = m.scheduled_at if (m.status == 'scheduled' and m.scheduled_at) else m.started_at
            results.append({
                "id": f"live_{m.uuid}",
                "meeting_id": f"live_{m.uuid}",
                "status": m.status,
                "created_at": display_date,
                "title": m.title or f"Live Meeting {m.id}",
                "is_shared": getattr(m, 'is_shared', False),
                "is_host": m.created_by_id == request.user.id,
                "speaker_count": len({s.get('speaker') for s in (m.speaker_segments or []) if isinstance(s, dict)}) if m.speaker_segments else 0,
                "duration": (m.ended_at - m.started_at).total_seconds() if m.started_at and m.ended_at else 0,
            })
            
        # Sort combined list by created_at descending
        results.sort(key=lambda x: x["created_at"] or getattr(timezone, 'now')(), reverse=True)
            
        return Response(results, status=status.HTTP_200_OK)


def _ensure_segment_texts_from_whisper(meeting, transcript) -> None:
    """Fill empty SpeakerSegment.text using stored Whisper timed segments (first API hit)."""
    wsegs = transcript.whisper_segments
    if not wsegs:
        return
    to_update = []
    for s in meeting.segments.all():
        if (s.text or "").strip():
            continue
        s.text = whisper_text_for_interval(s.start_time, s.end_time, wsegs)
        to_update.append(s)
    if to_update:
        SpeakerSegment.objects.bulk_update(to_update, ["text"])


def _build_speakers_overview(segment_list: list) -> list[dict]:
    # ... (keeping existing logic but we will map names inside get())
    pass

def _format_speaker_name(name, speaker_map, next_num):
    if not name or name == "Unknown":
        return "Unknown", next_num
    
    if name.startswith("SPEAKER_"):
        if name not in speaker_map:
            speaker_map[name] = f"Speaker {next_num}"
            next_num += 1
        return speaker_map[name], next_num
    return name, next_num



class AudioUploadView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        serializer = AudioUploadSerializer(data=request.data)

        if serializer.is_valid():
            audio_file = serializer.validated_data.get('file')
            
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

            meeting = Meeting.objects.create(
                status="processing", 
                created_by=request.user,
                workspace=workspace
            )

            AudioFile.objects.create(
                meeting=meeting,
                file=audio_file,
            )

            # 👉 Queue task instead of running synchronously
            from .tasks import process_meeting
            process_meeting.delay(meeting.id)

            return Response(
                {
                    "message": "Processing",
                    "meeting_id": str(meeting.uuid),
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MeetingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, meeting_id):
        from .models import LiveMeeting, WorkspaceMember, Meeting
        from django.db.models import Q

        user_workspaces = WorkspaceMember.objects.filter(user=request.user).values_list('workspace_id', flat=True)

        if str(meeting_id).startswith("live_"):
            live_id = str(meeting_id).replace('live_', '')
            from django.core.exceptions import ValidationError
            try:
                meeting = get_object_or_404(
                    LiveMeeting, 
                    Q(uuid=live_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True)))
                )
            except ValidationError:
                try:
                    meeting = get_object_or_404(
                        LiveMeeting, 
                        Q(id=live_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True)))
                    )
                except ValueError:
                    return Response({"error": "Invalid meeting ID"}, status=status.HTTP_400_BAD_REQUEST)
            
            # Clean speaker names for LiveMeeting
            speaker_map = {}
            next_speaker_num = 1
            
            cleaned_segments = []
            for s in meeting.speaker_segments or []:
                if not isinstance(s, dict): continue
                raw_name = s.get("speaker", "Unknown")
                clean_name, next_speaker_num = _format_speaker_name(raw_name, speaker_map, next_speaker_num)
                cleaned_segments.append({**s, "speaker": clean_name})
            
            speaker_segments = cleaned_segments
            speakers_distinct = sorted(list(set(speaker_map.values())) + (["Unknown"] if "Unknown" in {s.get("speaker") for s in speaker_segments} else []))
            
            # format speakers overview similarly
            order = []
            seen = set()
            by_sp = {}
            for s in speaker_segments:
                sp = s.get("speaker", "Unknown")
                if sp not in seen:
                    seen.add(sp)
                    order.append(sp)
                    by_sp[sp] = []
                by_sp[sp].append(s)
            
            speakers_overview = []
            for sp in order:
                rows = by_sp[sp]
                dur = sum(max(0.0, float(r.get("end", 0)) - float(r.get("start", 0))) for r in rows)
                merged = " ".join(r.get("text", "").strip() for r in rows if r.get("text", "").strip())
                speakers_overview.append({
                    "speaker": sp,
                    "segment_count": len(rows),
                    "duration_seconds": round(dur, 1),
                    "merged_text": merged
                })
                
            summary_data = {
                "short": meeting.summary_short,
                "detailed": meeting.summary_detailed,
                "key_points": meeting.summary_key_points or [],
            }
            
            # If scheduled, try to get prep intelligence
            is_prep = False
            if meeting.status == 'scheduled' and not (meeting.summary_short or meeting.summary_detailed):
                from .nlp_service import generate_prep_intelligence
                # Simple keyword extraction
                keywords = [w.lower() for w in (meeting.title or "").split() if len(w) > 3]
                if not keywords: keywords = [meeting.title.lower()] if meeting.title else []
                q_text = Q()
                for kw in keywords: q_text |= Q(title__icontains=kw)
                
                past_mtgs = Meeting.objects.filter(q_text, created_by=request.user, status='completed').order_by('-created_at')[:2]
                findings = []
                for p in past_mtgs:
                    summ = getattr(p, 'summary', None)
                    if summ: findings.append(f"Past '{p.title}': {summ.short_summary}")
                
                context_text = " | ".join(findings)
                ai_data = generate_prep_intelligence(meeting.title, context_text)
                
                summary_data = {
                    "short": ai_data['context'],
                    "detailed": " | ".join(ai_data['suggested_agenda']),
                    "key_points": ai_data['suggested_agenda'],
                }
                is_prep = True

            return Response(
                {
                    "meeting_id": f"live_{meeting.uuid}",
                    "title": meeting.title,
                    "status": meeting.status,
                    "created_at": meeting.scheduled_at if (meeting.status == 'scheduled' and meeting.scheduled_at) else meeting.started_at,
                    "is_shared": meeting.is_shared,
                    "is_host": meeting.created_by_id == request.user.id,
                    "audio_url": request.build_absolute_uri(meeting.audio_file.url) if meeting.audio_file else None,
                    "transcript": meeting.transcript_text,
                    "summary": summary_data if (summary_data["short"] or summary_data["detailed"]) else None,
                    "is_prep_brief": is_prep,
                    "speaker_count": len(speakers_distinct),
                    "speakers": speakers_distinct,
                    "speakers_overview": speakers_overview,
                    "speaker_segments_total": len(speaker_segments),
                    "speaker_segments": speaker_segments,
                    "action_items": [
                        {
                            "id": f"live_{meeting.uuid}_{i}",
                            "task": item if isinstance(item, str) else item.get('task'),
                            "assigned_to": None if isinstance(item, str) else item.get('assigned_to'),
                            "deadline": None if isinstance(item, str) else item.get('deadline'),
                            "priority": 'medium' if isinstance(item, str) else item.get('priority', 'medium'),
                            "completed": False if isinstance(item, str) else item.get('completed', False)
                        } for i, item in enumerate(meeting.action_items or [])
                    ],
                },
                status=status.HTTP_200_OK,
            )


        # Regular Meeting
        from django.core.exceptions import ValidationError
        try:
            meeting = Meeting.objects.get(
                Q(uuid=meeting_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True)))
            )
        except Meeting.DoesNotExist:
            return Response({"error": "Meeting not found"}, status=status.HTTP_404_NOT_FOUND)
        except ValidationError:
            try:
                meeting = Meeting.objects.get(
                    Q(id=meeting_id) & (Q(created_by=request.user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True)))
                )
            except (Meeting.DoesNotExist, ValueError):
                return Response({"error": "Meeting not found"}, status=status.HTTP_404_NOT_FOUND)

        transcript = getattr(meeting, "transcript", None)
        if transcript:
            _ensure_segment_texts_from_whisper(meeting, transcript)

        summary = getattr(meeting, "summary", None)
        segment_list = list(meeting.segments.order_by("start_time", "id"))

        # Clean speaker names for regular Meeting
        speaker_map = {}
        next_speaker_num = 1
        
        cleaned_segment_data = []
        for s in segment_list:
            clean_name, next_speaker_num = _format_speaker_name(s.speaker, speaker_map, next_speaker_num)
            # Create a dynamic object for response (s is a model instance)
            cleaned_segment_data.append({
                "speaker": clean_name,
                "start": s.start_time,
                "end": s.end_time,
                "text": s.text
            })
            
        speakers_distinct = sorted(list(set(speaker_map.values())) + (["Unknown"] if "Unknown" in {s.speaker for s in segment_list} else []))
        
        # Build overview with cleaned names
        order = []
        seen = set()
        by_sp = {}
        for s in cleaned_segment_data:
            sp = s["speaker"]
            if sp not in seen:
                seen.add(sp)
                order.append(sp)
                by_sp[sp] = []
            by_sp[sp].append(s)

        speakers_overview = []
        for sp in order:
            rows = by_sp[sp]
            dur = sum(max(0.0, r["end"] - r["start"]) for r in rows)
            merged = " ".join((r["text"] or "").strip() for r in rows if (r["text"] or "").strip())
            speakers_overview.append({
                "speaker": sp,
                "segment_count": len(rows),
                "duration_seconds": round(dur, 1),
                "merged_text": merged
            })

        action_items = meeting.action_items.all()

        return Response(
            {
                "meeting_id": str(meeting.uuid),
                "title": meeting.title,
                "status": meeting.status,
                "created_at": meeting.created_at,
                "is_shared": getattr(meeting, 'is_shared', False),
                "is_host": meeting.created_by_id == request.user.id,
                "audio_url": request.build_absolute_uri(meeting.audio.file.url) if hasattr(meeting, 'audio') and meeting.audio.file else None,
                "transcript": transcript.full_text if transcript else None,
                "summary": {
                    "short": summary.short_summary if summary else None,
                    "detailed": summary.detailed_summary if summary else None,
                    "key_points": summary.key_points if summary else [],
                }
                if summary
                else None,
                "speaker_count": len(speakers_distinct),
                "speakers": speakers_distinct,
                "speakers_overview": speakers_overview,
                "speaker_segments_total": len(segment_list),
                "speaker_segments": cleaned_segment_data,
                "action_items": [
                    {
                        "id": a.id,
                        "task": a.task,
                        "assigned_to": a.assigned_to,
                        "deadline": a.deadline,
                        "priority": a.priority,
                        "completed": a.completed,
                    }
                    for a in action_items
                ],
            },
            status=status.HTTP_200_OK,
        )

class speaker_segments_view(APIView):
    def get(self, request, meeting_id):
        try:
            meeting = Meeting.objects.get(uuid=meeting_id)
        except Meeting.DoesNotExist:
            return Response({"error": "Meeting not found"}, status=status.HTTP_404_NOT_FOUND)

        segment_list = list(meeting.segments.order_by("start_time", "id"))
        speakers_overview = _build_speakers_overview(segment_list)

        return Response(
            {
                "meeting_id": str(meeting.uuid),
                "speakers_overview": speakers_overview,
                "speaker_segments_total": len(segment_list),
                "speaker_segments": [
                    {
                        "speaker": s.speaker,
                        "start": s.start_time,
                        "end": s.end_time,
                        "text": s.text,
                    }
                    for s in segment_list
                ],
            },
            status=status.HTTP_200_OK,
        )

class ToggleActionItemView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request, item_id):
        # item_id can be numeric ID for regular items or "live_MEETINGID_INDEX" for live items
        if isinstance(item_id, str) and item_id.startswith('live_'):
            parts = item_id.split('_')
            try:
                meeting_id = parts[1]
                idx = int(parts[2])
                meeting = get_object_or_404(LiveMeeting, uuid=meeting_id, created_by=request.user)
                items = list(meeting.action_items)
                
                if 0 <= idx < len(items):
                    item = items[idx]
                    if isinstance(item, str):
                        items[idx] = {'task': item, 'completed': True}
                    else:
                        items[idx]['completed'] = not item.get('completed', False)
                    
                    meeting.action_items = items
                    meeting.save()
                    return Response({'status': 'success', 'completed': items[idx].get('completed')})
            except (IndexError, ValueError):
                pass
            return Response({'error': 'Invalid item ID'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            item = get_object_or_404(ActionItem, id=item_id, meeting__created_by=request.user)
            item.completed = not item.completed
            item.save()
            return Response({'status': 'success', 'completed': item.completed})


class DeleteMeetingView(APIView):
    """Delete a meeting (regular or live) and all related data."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, meeting_id):
        if str(meeting_id).startswith("live_"):
            live_id = str(meeting_id).replace('live_', '')
            meeting = get_object_or_404(LiveMeeting, uuid=live_id, created_by=request.user)
            # Delete audio file from disk
            if meeting.audio_file:
                try:
                    if os.path.exists(meeting.audio_file.path):
                        os.remove(meeting.audio_file.path)
                except Exception:
                    pass
            meeting.delete()
        else:
            meeting = get_object_or_404(Meeting, uuid=meeting_id, created_by=request.user)
            # Delete audio file from disk
            try:
                audio = meeting.audio
                if audio and audio.file and os.path.exists(audio.file.path):
                    os.remove(audio.file.path)
            except (AudioFile.DoesNotExist, Exception):
                pass
            meeting.delete()

        return Response({'status': 'deleted'}, status=status.HTTP_200_OK)


class DownloadMeetingView(APIView):
    """Download a ZIP containing transcript, summary, action items, and audio."""
    permission_classes = [IsAuthenticated]

    def get(self, request, meeting_id):
        buf = io.BytesIO()

        if str(meeting_id).startswith("live_"):
            live_id = str(meeting_id).replace('live_', '')
            meeting = get_object_or_404(LiveMeeting, uuid=live_id, created_by=request.user)
            title = meeting.title or f"Live Meeting {meeting.id}"

            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Transcript
                if meeting.transcript_text:
                    zf.writestr('transcript.txt', meeting.transcript_text)

                # Speaker segments
                if meeting.speaker_segments:
                    lines = []
                    for seg in meeting.speaker_segments:
                        if isinstance(seg, dict):
                            speaker = seg.get('speaker', 'Unknown')
                            start = seg.get('start', 0)
                            end = seg.get('end', 0)
                            text = seg.get('text', '')
                            lines.append(f"[{self._fmt(start)} -> {self._fmt(end)}] {speaker}: {text}")
                    zf.writestr('speaker_transcript.txt', '\n'.join(lines))

                # Summary
                summary_parts = []
                if meeting.summary_short:
                    summary_parts.append(f"## Short Summary\n{meeting.summary_short}\n")
                if meeting.summary_detailed:
                    summary_parts.append(f"## Detailed Summary\n{meeting.summary_detailed}\n")
                if meeting.summary_key_points:
                    summary_parts.append("## Key Points\n" + '\n'.join(f"- {kp}" for kp in meeting.summary_key_points) + '\n')
                if summary_parts:
                    zf.writestr('summary.md', '\n'.join(summary_parts))

                # Action Items
                if meeting.action_items:
                    ai_lines = []
                    for i, item in enumerate(meeting.action_items, 1):
                        if isinstance(item, dict):
                            task = item.get('task', '')
                            assigned = item.get('assigned_to', '')
                            priority = item.get('priority', 'medium')
                            completed = '✓' if item.get('completed') else '☐'
                            ai_lines.append(f"{completed} {i}. {task} (Assigned: {assigned}, Priority: {priority})")
                    zf.writestr('action_items.txt', '\n'.join(ai_lines))

                # Audio file
                if meeting.audio_file:
                    try:
                        audio_path = meeting.audio_file.path
                        if os.path.exists(audio_path):
                            ext = os.path.splitext(audio_path)[1]
                            zf.write(audio_path, f'audio{ext}')
                    except Exception:
                        pass

        else:
            meeting = get_object_or_404(Meeting, uuid=meeting_id, created_by=request.user)
            title = meeting.title or f"Meeting {meeting.id}"

            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Transcript
                try:
                    transcript = meeting.transcript
                    if transcript and transcript.full_text:
                        zf.writestr('transcript.txt', transcript.full_text)
                except Transcript.DoesNotExist:
                    pass

                # Speaker segments
                segments = list(meeting.segments.order_by('start_time'))
                if segments:
                    lines = []
                    for seg in segments:
                        lines.append(f"[{self._fmt(seg.start_time)} -> {self._fmt(seg.end_time)}] {seg.speaker}: {seg.text}")
                    zf.writestr('speaker_transcript.txt', '\n'.join(lines))

                # Summary
                try:
                    summary = meeting.summary
                    if summary:
                        parts = []
                        if summary.short_summary:
                            parts.append(f"## Short Summary\n{summary.short_summary}\n")
                        if summary.detailed_summary:
                            parts.append(f"## Detailed Summary\n{summary.detailed_summary}\n")
                        if summary.key_points:
                            parts.append("## Key Points\n" + '\n'.join(f"- {kp}" for kp in summary.key_points) + '\n')
                        if parts:
                            zf.writestr('summary.md', '\n'.join(parts))
                except Summary.DoesNotExist:
                    pass

                # Action Items
                action_items = list(meeting.action_items.all())
                if action_items:
                    ai_lines = []
                    for i, item in enumerate(action_items, 1):
                        completed = '✓' if item.completed else '☐'
                        ai_lines.append(f"{completed} {i}. {item.task} (Assigned: {item.assigned_to or 'N/A'}, Priority: {item.priority})")
                    zf.writestr('action_items.txt', '\n'.join(ai_lines))

                # Audio file
                try:
                    audio = meeting.audio
                    if audio and audio.file:
                        audio_path = audio.file.path
                        if os.path.exists(audio_path):
                            ext = os.path.splitext(audio_path)[1]
                            zf.write(audio_path, f'audio{ext}')
                except (AudioFile.DoesNotExist, Exception):
                    pass

        buf.seek(0)
        safe_title = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title).strip()
        response = HttpResponse(buf.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{safe_title}.zip"'
        return response

    def _fmt(self, seconds):
        """Format seconds into MM:SS."""
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"


class ShareMeetingView(APIView):
    """Generate a shareable data payload for a meeting."""
    permission_classes = [IsAuthenticated]

    def get(self, request, meeting_id):
        if str(meeting_id).startswith("live_"):
            live_id = str(meeting_id).replace('live_', '')
            meeting = get_object_or_404(LiveMeeting, uuid=live_id, created_by=request.user)
            share_data = {
                'title': meeting.title or f"Live Meeting {meeting.id}",
                'date': meeting.started_at.isoformat() if meeting.started_at else None,
                'transcript': meeting.transcript_text or '',
                'summary': meeting.summary_short or meeting.summary_detailed or '',
                'key_points': meeting.summary_key_points or [],
                'action_items': meeting.action_items or [],
                'speaker_segments': meeting.speaker_segments or [],
            }
        else:
            meeting = get_object_or_404(Meeting, uuid=meeting_id, created_by=request.user)
            transcript = getattr(meeting, 'transcript', None)
            summary = getattr(meeting, 'summary', None)
            segments = list(meeting.segments.order_by('start_time'))
            action_items = list(meeting.action_items.all())

            share_data = {
                'title': meeting.title or f"Meeting {meeting_id}",
                'date': meeting.created_at.isoformat() if meeting.created_at else None,
                'transcript': transcript.full_text if transcript else '',
                'summary': (summary.short_summary if summary else '') or '',
                'key_points': (summary.key_points if summary else []) or [],
                'action_items': [
                    {
                        'task': a.task,
                        'assigned_to': a.assigned_to,
                        'priority': a.priority,
                        'completed': a.completed,
                    } for a in action_items
                ],
                'speaker_segments': [
                    {
                        'speaker': s.speaker,
                        'start': s.start_time,
                        'end': s.end_time,
                        'text': s.text,
                    } for s in segments
                ],
            }

        return Response(share_data, status=status.HTTP_200_OK)

class ShareWithWorkspaceView(APIView):
    """Toggle the is_shared flag on a meeting to share it with the workspace."""
    permission_classes = [IsAuthenticated]

    def post(self, request, meeting_id):
        from .models import Meeting, LiveMeeting, WorkspaceMember, Notification
        is_shared = request.data.get('is_shared', True)
        
        if str(meeting_id).startswith("live_"):
            live_id = str(meeting_id).replace('live_', '')
            meeting = get_object_or_404(LiveMeeting, uuid=live_id, created_by=request.user)
        else:
            meeting = get_object_or_404(Meeting, uuid=meeting_id, created_by=request.user)
            
        meeting.is_shared = is_shared
        meeting.save()
        
        # Send notifications to other members in the workspace if shared
        if is_shared and meeting.workspace:
            members = WorkspaceMember.objects.filter(workspace=meeting.workspace).exclude(user=request.user)
            notifications = []
            for member in members:
                notifications.append(
                    Notification(
                        user=member.user,
                        title="New Shared Meeting",
                        description=f"{request.user.full_name} shared a meeting: {meeting.title or 'Untitled Meeting'}",
                        type='share'
                    )
                )
            if notifications:
                Notification.objects.bulk_create(notifications)
        
        return Response({"status": "success", "is_shared": meeting.is_shared})