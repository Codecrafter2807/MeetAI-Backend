import random
import requests
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from django.conf import settings
from django.core.mail import send_mail
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.authtoken.models import Token
from django.contrib.auth import get_user_model, authenticate
from rest_framework.permissions import IsAuthenticated

User = get_user_model()
from .models import Meeting, LiveMeeting, ActionItem, SpeakerSegment, EmailOTP
from django.db.models import Sum, Max
from .tasks import send_email_task

class RegisterView(APIView):
    def post(self, request):
        name = request.data.get('name')
        email = request.data.get('email')
        password = request.data.get('password')
        
        if not email or not password or not name:
            return Response({'error': 'Name, email, and password are required'}, status=status.HTTP_400_BAD_REQUEST)
            
        if User.objects.filter(email=email).exists():
            return Response({'error': 'Email is already registered'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Create inactive user
        user = User.objects.create_user(
            email=email, 
            password=password,
            full_name=name,
            is_active=False
        )
        
        # Generate OTP
        otp = str(random.randint(100000, 999999))
        EmailOTP.objects.create(email=email, otp=otp)
        
        # Send email
        try:
            send_email_task.delay(
                'Verify Your MeetingAI Account',
                f'Your OTP for registration is: {otp}',
                [email]
            )
        except Exception as e:
            print(f"Failed to send email to {email}. OTP is {otp}. Error: {e}")
            return Response({'error': 'Failed to send verification email. Please check your SMTP settings.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'message': 'OTP sent to your email. Please verify to complete registration.',
            'email': email
        }, status=status.HTTP_201_CREATED)

class VerifyOTPView(APIView):
    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')
        
        if not email or not otp:
            return Response({'error': 'Email and OTP are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        otp_record = EmailOTP.objects.filter(email=email, otp=otp).last()
        if not otp_record:
            return Response({'error': 'Invalid OTP'}, status=status.HTTP_400_BAD_REQUEST)
        
        user = User.objects.filter(email=email).first()
        if not user:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
        
        user.is_active = True
        user.save()
        
        # Delete OTP records for this email
        EmailOTP.objects.filter(email=email).delete()
        
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        
        # Send account creation confirmation email
        try:
            send_email_task.delay(
                'Account Created - MeetAI',
                f'Hello {user.full_name},\n\nYour account on MeetAI is Created successfully. Welcome aboard!',
                [user.email]
            )
        except Exception as e:
            print(f"Failed to send welcome email: {e}")
        
        return Response({
            'message': 'Email verified successfully',
            'token': token.key,
            'user': {
                'id': user.id, 
                'name': user.full_name, 
                'email': user.email,
                'avatar_url': request.build_absolute_uri(user.avatar.url) if user.avatar else None
            }
        }, status=status.HTTP_200_OK)

class LoginView(APIView):
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        
        if not email or not password:
            return Response({'error': 'Email and password are required'}, status=status.HTTP_400_BAD_REQUEST)
            
        user = authenticate(email=email, password=password)
        if user:
            Token.objects.filter(user=user).delete()
            token = Token.objects.create(user=user)
            
            # Send login notification email
            try:
                send_email_task.delay(
                    'New Login - MeetAI',
                    f'Hello {user.full_name},\n\nYour account is now logined in MeetAI.',
                    [user.email]
                )
            except Exception as e:
                print(f"Failed to send login notification: {e}")

            return Response({
                'token': token.key,
                'user': {
                    'id': user.id, 
                    'name': user.full_name, 
                    'email': user.email,
                    'avatar_url': request.build_absolute_uri(user.avatar.url) if user.avatar else None
                }
            }, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

class ProfileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        user = request.user
        
        # Stats
        meetings_count = Meeting.objects.filter(created_by=user).count() + LiveMeeting.objects.filter(created_by=user).count()
        
        # Approximate duration
        total_seconds = 0
        
        # Uploaded meetings duration (sum of max end_time per meeting)
        from django.db.models import Max
        meeting_durations = SpeakerSegment.objects.filter(meeting__created_by=user).values('meeting').annotate(duration=Max('end_time'))
        for item in meeting_durations:
            total_seconds += (item['duration'] or 0)
            
        # Live meetings duration
        live_meetings = LiveMeeting.objects.filter(created_by=user)
        for lm in live_meetings:
            if lm.started_at and lm.ended_at:
                total_seconds += (lm.ended_at - lm.started_at).total_seconds()
            elif lm.speaker_segments:
                try:
                    last_end = max(s.get('end', 0) for s in lm.speaker_segments)
                    total_seconds += last_end
                except: pass

        # Action Items Count
        action_items_count = ActionItem.objects.filter(meeting__created_by=user).count()
        for lm in live_meetings:
            if isinstance(lm.action_items, list):
                action_items_count += len(lm.action_items)

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        duration_formatted = f"{hours} hr {minutes} min" if hours > 0 else f"{minutes} min"

        return Response({
            'full_name': user.full_name,
            'email': user.email,
            'role': user.role,
            'gender': user.gender,
            'avatar_url': request.build_absolute_uri(user.avatar.url) if user.avatar else None,
            'stats': {
                'meetings_count': meetings_count,
                'action_items_count': action_items_count,
                'total_duration': duration_formatted,
                'member_since': user.date_joined.strftime('%b %d, %Y')
            }
        })

    def put(self, request):
        user = request.user
        user.full_name = request.data.get('full_name', user.full_name)
        user.role = request.data.get('role', user.role)
        user.gender = request.data.get('gender', user.gender)
        
        if 'avatar' in request.FILES:
            user.avatar = request.FILES['avatar']
            
        user.save()
        return Response({
            'message': 'Profile updated successfully',
            'full_name': user.full_name,
            'email': user.email,
            'role': user.role,
            'gender': user.gender,
            'avatar_url': request.build_absolute_uri(user.avatar.url) if user.avatar else None
        })

    def delete(self, request):
        user = request.user
        # All related data (Meetings, etc) will be deleted via CASCADE if configured,
        # otherwise we should handle it. Meeting model has ForeignKey(User, on_delete=models.CASCADE).
        user.delete()
        return Response({'message': 'Account deleted successfully'}, status=status.HTTP_200_OK)

class PublicProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        try:
            target_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        # Basic Stats calculation (same as ProfileView but for target_user)
        meetings_count = Meeting.objects.filter(created_by=target_user).count() + LiveMeeting.objects.filter(created_by=target_user).count()
        
        total_seconds = 0
        meeting_durations = SpeakerSegment.objects.filter(meeting__created_by=target_user).values('meeting').annotate(duration=Max('end_time'))
        for item in meeting_durations:
            total_seconds += (item['duration'] or 0)
            
        live_meetings = LiveMeeting.objects.filter(created_by=target_user)
        for lm in live_meetings:
            if lm.started_at and lm.ended_at:
                total_seconds += (lm.ended_at - lm.started_at).total_seconds()
            elif lm.speaker_segments:
                try:
                    last_end = max(s.get('end', 0) for s in lm.speaker_segments)
                    total_seconds += last_end
                except: pass

        action_items_count = ActionItem.objects.filter(meeting__created_by=target_user).count()
        for lm in live_meetings:
            if isinstance(lm.action_items, list):
                action_items_count += len(lm.action_items)

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        duration_formatted = f"{hours} hr {minutes} min" if hours > 0 else f"{minutes} min"

        return Response({
            'id': target_user.id,
            'full_name': target_user.full_name,
            'email': target_user.email,
            'role': target_user.role,
            'gender': target_user.gender,
            'avatar_url': request.build_absolute_uri(target_user.avatar.url) if target_user.avatar else None,
            'stats': {
                'meetings_count': meetings_count,
                'action_items_count': action_items_count,
                'total_duration': duration_formatted,
                'member_since': target_user.date_joined.strftime('%b %d, %Y')
            }
        })

class RequestPasswordResetOTPView(APIView):
    def post(self, request):
        email = request.data.get('email')
        # If logged in, prioritize current user's email
        if not email and request.user.is_authenticated:
            email = request.user.email
            
        if not email:
            return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)
            
        user = User.objects.filter(email=email).first()
        if not user:
            return Response({'message': 'If an account exists, an OTP has been sent.'})
            
        otp = str(random.randint(100000, 999999))
        EmailOTP.objects.create(email=email, otp=otp)
        
        try:
            send_email_task.delay(
                'Password Change OTP - MeetAI',
                f'Your OTP for password change is: {otp}. If you did not request this, please ignore this email.',
                [email]
            )
        except Exception as e:
            return Response({'error': 'Failed to send email'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        return Response({'message': 'OTP sent to your email.'})

class ResetPasswordView(APIView):
    def post(self, request):
        email = request.data.get('email')
        otp = request.data.get('otp')
        new_password = request.data.get('new_password')
        
        if not email or not otp or not new_password:
            return Response({'error': 'Email, OTP, and new password are required'}, status=status.HTTP_400_BAD_REQUEST)
            
        otp_record = EmailOTP.objects.filter(email=email, otp=otp).last()
        if not otp_record:
            return Response({'error': 'Invalid or expired OTP'}, status=status.HTTP_400_BAD_REQUEST)
            
        user = User.objects.filter(email=email).first()
        if not user:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
            
        user.set_password(new_password)
        user.save()
        
        EmailOTP.objects.filter(email=email).delete()
        
        return Response({'message': 'Password has been updated successfully.'})

class GoogleLoginView(APIView):
    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({'error': 'Google token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            idinfo = None

            # Try id_token verification first (from GoogleLogin component)
            try:
                import google.auth.transport.requests
                from google.oauth2 import id_token as google_id_token
                idinfo = google_id_token.verify_oauth2_token(
                    token,
                    google.auth.transport.requests.Request(),
                    audience=None  # skip audience check for flexibility
                )
            except Exception:
                pass

            # Fallback: treat as access_token and call userinfo endpoint
            if not idinfo:
                response = requests.get(
                    'https://www.googleapis.com/oauth2/v3/userinfo',
                    params={'access_token': token}
                )
                if not response.ok:
                    return Response({'error': 'Failed to verify Google token'}, status=status.HTTP_400_BAD_REQUEST)
                idinfo = response.json()

            email = idinfo.get('email')
            if not email:
                return Response({'error': 'Could not get email from Google'}, status=status.HTTP_400_BAD_REQUEST)

            name = idinfo.get('name', '')
            avatar = idinfo.get('picture', '')

            # Check if user exists
            user = User.objects.filter(email=email).first()
            if not user:
                import string
                random_password = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
                user = User.objects.create_user(
                    email=email,
                    password=random_password,
                    full_name=name,
                    is_active=True
                )

            Token.objects.filter(user=user).delete()
            token_obj = Token.objects.create(user=user)

            return Response({
                'token': token_obj.key,
                'user': {
                    'id': user.id,
                    'name': user.full_name,
                    'email': user.email,
                    'avatar_url': avatar if not user.avatar else request.build_absolute_uri(user.avatar.url)
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': f'Google login failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
