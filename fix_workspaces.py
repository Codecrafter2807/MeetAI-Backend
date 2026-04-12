import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from meetings.models import LiveMeeting, Meeting, WorkspaceMember

def fix_workspaces():
    lms = LiveMeeting.objects.filter(workspace__isnull=True)
    count_live = 0
    for lm in lms:
        member = WorkspaceMember.objects.filter(user=lm.created_by).first()
        if member:
            lm.workspace = member.workspace
            lm.save()
            count_live += 1
            
    ms = Meeting.objects.filter(workspace__isnull=True)
    count_meet = 0
    for m in ms:
        member = WorkspaceMember.objects.filter(user=m.created_by).first()
        if member:
            m.workspace = member.workspace
            m.save()
            count_meet += 1
            
    print(f"Fixed {count_live} LiveMeetings and {count_meet} Meetings")

if __name__ == '__main__':
    fix_workspaces()
