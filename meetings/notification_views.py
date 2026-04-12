from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .models import Notification
from django.core.mail import send_mail
from django.conf import settings

class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        notifications = Notification.objects.filter(user=request.user).order_by('-created_at')[:50]
        data = [{
            'id': n.id,
            'title': n.title,
            'description': n.description,
            'type': n.type,
            'is_read': n.is_read,
            'created_at': n.created_at.isoformat(),
        } for n in notifications]
        
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        
        return Response({
            'notifications': data,
            'unread_count': unread_count
        })

class MarkNotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, notification_id):
        try:
            notification = Notification.objects.get(id=notification_id, user=request.user)
            notification.is_read = True
            notification.save()
            return Response({'status': 'success'})
        except Notification.DoesNotExist:
            return Response({'error': 'Notification not found'}, status=status.HTTP_404_NOT_FOUND)

class MarkAllNotificationsReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'success'})

class DeleteNotificationView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, notification_id):
        try:
            notification = Notification.objects.get(id=notification_id, user=request.user)
            notification.delete()
            return Response({'status': 'success'}, status=status.HTTP_200_OK)
        except Notification.DoesNotExist:
            return Response({'error': 'Notification not found'}, status=status.HTTP_404_NOT_FOUND)

class DeleteAllNotificationsView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        Notification.objects.filter(user=request.user).delete()
        return Response({'status': 'success'}, status=status.HTTP_200_OK)

def create_notification(user, title, description, n_type='system', send_email=True):
    """Utility function to create notification and optionally send email."""
    notification = Notification.objects.create(
        user=user,
        title=title,
        description=description,
        type=n_type
    )
    
    if send_email:
        try:
            send_mail(
                f'Notification: {title}',
                f'Hello {user.full_name},\n\n{description}\n\nCheck your dashboard for more details.\n\nBest,\nMeetAI Team',
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=True,
            )
        except Exception as e:
            print(f"Failed to send notification email: {e}")
            
    return notification
