from django.urls import path
from .views import (
    AudioUploadView, MeetingDetailView, MeetingListView, ToggleActionItemView,
    DeleteMeetingView, DownloadMeetingView, ShareMeetingView, ShareWithWorkspaceView
)
from .live_views import (
    StartLiveMeetingView,
    UploadAudioChunkView,
    EndLiveMeetingView,
    GetLiveMeetingView,
)
from .simulator_views import (
    ScenarioListView, SimulatorStartView, SimulatorTurnView, SimulatorFeedbackView
)
from .auth_views import (
    RegisterView, LoginView, ProfileView, VerifyOTPView, 
    RequestPasswordResetOTPView, ResetPasswordView, GoogleLoginView,
    PublicProfileView
)
from .dashboard_views import DashboardStatsView
from .testimonial_views import TestimonialPublicListView, TestimonialCreateView
from .notification_views import (
    NotificationListView, 
    MarkNotificationReadView, 
    MarkAllNotificationsReadView,
    DeleteNotificationView,
    DeleteAllNotificationsView
)
from .chat_views import AIChatView
from .workspace_views import (
    WorkspaceListView, 
    WorkspaceMemberView,
    WorkspaceMemberDetailView,
    WorkspaceInviteView, 
    AcceptInvitationView,
    WorkspaceChatView
)
from .hub_views import MeetingHubView




urlpatterns = [
    # API routes — Chatbot
    path('api/chatbot/chat/', AIChatView.as_view(), name='chatbot_chat'),
    
    # API routes — Workspace & Teams
    path('api/workspaces/', WorkspaceListView.as_view(), name='workspace_list'),
    path('api/workspaces/<slug:workspace_slug>/members/', WorkspaceMemberView.as_view(), name='workspace_members'),
    path('api/workspaces/<slug:workspace_slug>/members/<int:user_id>/', WorkspaceMemberDetailView.as_view(), name='workspace_member_detail'),
    path('api/workspaces/<slug:workspace_slug>/invite/', WorkspaceInviteView.as_view(), name='workspace_invite'),
    path('api/workspaces/accept-invite/', AcceptInvitationView.as_view(), name='workspace_accept_invite'),
    path('api/workspaces/<slug:workspace_slug>/chat/', WorkspaceChatView.as_view(), name='workspace_chat'),
    
    # API routes — Auth
    path('api/auth/register/', RegisterView.as_view()),
    path('api/auth/verify-otp/', VerifyOTPView.as_view()),
    path('api/auth/login/', LoginView.as_view()),
    path('api/auth/profile/', ProfileView.as_view()),
    path('api/auth/profile/<int:user_id>/', PublicProfileView.as_view()),
    path('api/auth/password-reset-otp/', RequestPasswordResetOTPView.as_view()),
    path('api/auth/password-reset-confirm/', ResetPasswordView.as_view()),
    path('api/auth/google/', GoogleLoginView.as_view()),
    path('api/dashboard/stats/', DashboardStatsView.as_view()),

    # API routes — Notifications
    path('api/notifications/', NotificationListView.as_view()),
    path('api/notifications/mark-all-read/', MarkAllNotificationsReadView.as_view()),
    path('api/notifications/<int:notification_id>/mark-read/', MarkNotificationReadView.as_view()),
    path('api/notifications/delete-all/', DeleteAllNotificationsView.as_view()),
    path('api/notifications/delete/<int:notification_id>/', DeleteNotificationView.as_view()),

    # API routes — Meetings
    path('api/meetings/', MeetingListView.as_view()),
    path('api/meetings/hub/', MeetingHubView.as_view(), name='meeting_hub'),
    path('api/upload/', AudioUploadView.as_view()),

    path('api/meetings/<str:meeting_id>/', MeetingDetailView.as_view()),
    path('api/meetings/<str:meeting_id>/delete/', DeleteMeetingView.as_view()),
    path('api/meetings/<str:meeting_id>/download/', DownloadMeetingView.as_view()),
    path('api/meetings/<str:meeting_id>/share/', ShareMeetingView.as_view()),
    path('api/meetings/<str:meeting_id>/toggle-share/', ShareWithWorkspaceView.as_view()),
    path('api/action-items/<str:item_id>/toggle/', ToggleActionItemView.as_view()),
    
    # API routes — Live Streaming
    path('api/live/start/', StartLiveMeetingView.as_view()),
    path('api/live/<int:live_meeting_id>/upload-chunk/', UploadAudioChunkView.as_view()),
    path('api/live/<int:live_meeting_id>/end/', EndLiveMeetingView.as_view()),
    path('api/live/<int:live_meeting_id>/', GetLiveMeetingView.as_view()),
    
    
    # API routes — Testimonials
    path('api/public/testimonials/', TestimonialPublicListView.as_view()),
    path('api/testimonials/', TestimonialCreateView.as_view()),

    # API routes — Simulator
    path('api/simulator/scenarios/', ScenarioListView.as_view(), name='simulator_scenarios'),
    path('api/simulator/start/', SimulatorStartView.as_view(), name='simulator_start'),
    path('api/simulator/<int:session_id>/turn/', SimulatorTurnView.as_view(), name='simulator_turn'),
    path('api/simulator/<int:session_id>/feedback/', SimulatorFeedbackView.as_view(), name='simulator_feedback'),
]