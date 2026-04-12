from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models import Meeting, LiveMeeting, ActionItem, Summary, WorkspaceMember, Workspace, Transcript
from django.utils import timezone
import collections

class MeetingHubView(APIView):
    """Provides aggregated intelligence for the Strategic Insight Hub."""
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        """Update a strategic target's details."""
        target_id = request.data.get('id')
        title = request.data.get('title')
        scheduled_at = request.data.get('scheduled_at')
        
        if not target_id:
            return Response({"error": "ID required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            clean_id = target_id.replace('live_', '')
            target = LiveMeeting.objects.get(uuid=clean_id, created_by=request.user)
            if title: target.title = title
            if scheduled_at: target.scheduled_at = scheduled_at
            target.save()
            return Response({"message": "Target updated"}, status=status.HTTP_200_OK)
        except LiveMeeting.DoesNotExist:
            return Response({"error": "Target not found"}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, request):

        """Remove a strategic target from the hub."""
        target_id = request.data.get('id')
        if not target_id:
            return Response({"error": "ID required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Handle both string UUIDs and 'live_' prefixed IDs from frontend
            clean_id = target_id.replace('live_', '')
            target = LiveMeeting.objects.get(uuid=clean_id, created_by=request.user)
            target.delete()
            return Response({"message": "Target removed"}, status=status.HTTP_200_OK)
        except LiveMeeting.DoesNotExist:
            return Response({"error": "Target not found"}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request):

        """Manually schedule a meeting target for AI preparation."""
        title = request.data.get('title', 'Upcoming Meeting')
        scheduled_at = request.data.get('scheduled_at')
        duration = request.data.get('duration', 60)
        
        # Workspace assignment
        ws_slug = request.headers.get('X-Workspace-Slug')
        workspace = None
        if ws_slug:
            try:
                workspace = Workspace.objects.get(slug=ws_slug)
            except Workspace.DoesNotExist:
                pass
        
        if not workspace:
            member = WorkspaceMember.objects.filter(user=request.user).first()
            workspace = member.workspace if member else None

        meeting = LiveMeeting.objects.create(
            title=title,
            scheduled_at=scheduled_at,
            duration_minutes=duration,
            created_by=request.user,
            workspace=workspace,
            status='scheduled'
        )

        return Response({
            "id": str(meeting.uuid),
            "title": meeting.title,
            "message": "Strategic target added to Hub"
        }, status=status.HTTP_201_CREATED)

    def get(self, request):
        user = request.user
        user_workspaces = WorkspaceMember.objects.filter(user=user).values_list('workspace_id', flat=True)

        # 1. Strategic Timeline
        timeline = []
        meetings = Meeting.objects.filter(
            Q(created_by=user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True)),
            status='completed'
        ).order_by('-created_at')[:10]

        for m in meetings:
            summary = getattr(m, 'summary', None)
            timeline.append({
                'id': str(m.uuid),
                'title': m.title or f"Meeting {m.id}",
                'date': m.created_at,
                'type': 'uploaded',
                'description': summary.short_summary if summary else "No summary available.",
                'milestones': summary.key_points[:2] if summary and summary.key_points else []
            })

        live_meetings_completed = LiveMeeting.objects.filter(
            Q(created_by=user) | (Q(workspace_id__in=user_workspaces) & Q(is_shared=True)),
            status='completed'
        ).order_by('-started_at')[:10]

        for lm in live_meetings_completed:
            timeline.append({
                'id': f"live_{lm.uuid}",
                'title': lm.title,
                'date': lm.started_at,
                'type': 'live',
                'description': lm.summary_short or "No summary available.",
                'milestones': lm.summary_key_points[:2] if lm.summary_key_points else []
            })
        
        timeline = sorted(timeline, key=lambda x: x['date'], reverse=True)[:10]

        # 2. Aggregated Action Hub
        actions = []
        regular_actions = ActionItem.objects.filter(meeting__created_by=user, completed=False).order_by('-created_at')
        for item in regular_actions:
            actions.append({
                'id': item.id,
                'task': item.task,
                'meeting_title': item.meeting.title or "Unknown Meeting",
                'priority': item.priority,
                'deadline': item.deadline
            })

        for lm in live_meetings_completed:
            for item in (lm.action_items or []):
                if isinstance(item, dict) and not item.get('completed'):
                    actions.append({
                        'id': f"live_{lm.id}_{actions.__len__()}",
                        'task': item.get('task'),
                        'meeting_title': lm.title,
                        'priority': item.get('priority', 'medium'),
                        'deadline': item.get('deadline')
                    })
        
        # 3. Preparation Radar (Dynamic Context)
        now = timezone.now()
        upcoming = LiveMeeting.objects.filter(
            created_by=user,
            status='scheduled',
            scheduled_at__isnull=False,
            scheduled_at__gte=now
        ).order_by('scheduled_at')

        # Auto-expire really old ones
        expired = LiveMeeting.objects.filter(
            created_by=user,
            status='scheduled',
            scheduled_at__lt=now - timezone.timedelta(hours=2)
        )
        if expired.exists():
            expired.update(status='ended')

        from .nlp_service import generate_prep_intelligence

        prep_radar = []
        for up in upcoming:
            context_data = self._get_topic_context(user, up.title)
            # Enhance with AI if possible
            ai_data = generate_prep_intelligence(up.title, context_data['context'])
            
            prep_radar.append({
                "id": str(up.uuid),
                "title": up.title,
                "time": up.scheduled_at.strftime('%A, %I:%M %p'),
                "context": ai_data['context'],
                "suggested_agenda": ai_data['suggested_agenda']
            })

        if not prep_radar:
            prep_radar = [{
                "title": "Welcome to Strategic Insights",
                "time": "Getting Started",
                "context": "Add your first 'Insight Target' to see AI-driven preparation here.",
                "suggested_agenda": ["Add upcoming meeting", "Upload previous sessions", "Capture live data"]
            }]

        return Response({
            'timeline': timeline,
            'actions': actions[:15],
            'preparation': prep_radar,
            'topic_nodes': self._get_topic_nodes(user)
        })

    def _get_topic_context(self, user, topic_title):
        """Deep search across history to weave context for a target topic."""
        keywords = [w.lower() for w in (topic_title or "").split() if len(w) > 3]
        if not keywords:
            # Fallback for very short titles
            keywords = [topic_title.lower()] if topic_title else []

        # Aggregate findings
        findings = []

        # 1. Past Meeting Titles & Summaries (Uploaded & Live)
        q_title = Q()
        for kw in keywords:
            q_title |= Q(title__icontains=kw)
        
        past_mtgs = list(Meeting.objects.filter(q_title, created_by=user, status='completed').order_by('-created_at')[:2])
        live_mtgs = list(LiveMeeting.objects.filter(q_title, created_by=user, status='completed').order_by('-started_at')[:2])
        
        for p in past_mtgs:
            summ = getattr(p, 'summary', None)
            if summ: findings.append(f"Past Meeting '{p.title}': {summ.short_summary}")
        for l in live_mtgs:
            if l.summary_short: findings.append(f"Past Live Session '{l.title}': {l.summary_short}")

        # 2. Transcripts (Uploaded & Live)
        if len(findings) < 3:
            q_content = Q()
            for kw in keywords:
                q_content |= Q(full_text__icontains=kw)
            
            transcript_matches = Transcript.objects.filter(q_content, meeting__created_by=user)[:2]
            for tm in transcript_matches:
                findings.append(f"Heard in {tm.meeting.title}: {tm.full_text[:200]}")

            # Also search LiveMeeting transcripts
            q_live_trans = Q()
            for kw in keywords:
                q_live_trans |= Q(transcript_text__icontains=kw)
            live_trans_matches = LiveMeeting.objects.filter(q_live_trans, created_by=user, status='completed')[:2]
            for ltm in live_trans_matches:
                findings.append(f"Mentioned in Live Meeting {ltm.title}: {ltm.transcript_text[:200]}")

        # 3. Pending Tasks
        if len(findings) < 4:
            q_task = Q()
            for kw in keywords:
                q_task |= Q(task__icontains=kw)
            tasks = ActionItem.objects.filter(q_task, meeting__created_by=user, completed=False)[:3]
            for t in tasks:
                findings.append(f"Pending Task: {t.task}")

        context_text = " | ".join(findings)
        return {"context": context_text, "agenda": []}  # We'll let AI generate the agenda now


    def _get_topic_nodes(self, user):
        keywords = collections.Counter()
        titles = Meeting.objects.filter(created_by=user).values_list('title', flat=True)
        live_titles = LiveMeeting.objects.filter(created_by=user).values_list('title', flat=True)
        
        stopwords = {'meeting', 'the', 'and', 'for', 'a', 'in', 'live', 'sync', 'call', 'session'}
        for t in list(titles) + list(live_titles):
            if not t: continue
            words = [w.lower() for w in t.split() if w.lower() not in stopwords and len(w) > 3]
            keywords.update(words)
            
        nodes = []
        for word, count in keywords.most_common(10):
            nodes.append({'name': word, 'weight': count})
        return nodes
