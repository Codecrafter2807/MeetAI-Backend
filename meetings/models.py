from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.conf import settings
from django.utils.text import slugify
import uuid

class CustomUserManager(BaseUserManager):
    def create_user(self, email, full_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, full_name=full_name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, full_name, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, full_name, password, **extra_fields)

class CustomUser(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=100, blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    def __str__(self):
        return f"{self.full_name} ({self.email})"

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'


class Workspace(models.Model):
    """A collection of users and meetings."""
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owned_workspaces')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure uniqueness
            original_slug = self.slug
            counter = 1
            while Workspace.objects.filter(slug=self.slug).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class WorkspaceMember(models.Model):
    """Membership relationship between a User and a Workspace."""
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('member', 'Member'),
    ]
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workspace_memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('workspace', 'user')

    def __str__(self):
        return f"{self.user.email} in {self.workspace.name}"

class WorkspaceInvitation(models.Model):
    """Public or targeted invitation to join a workspace."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('expired', 'Expired'),
    ]
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='invitations')
    inviter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_invitations')
    email = models.EmailField(blank=True, null=True) # Optional if using a public link
    token = models.CharField(max_length=100, unique=True, default=uuid.uuid4)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Invite to {self.workspace.name} (Token: {self.token[:8]})"


class Meeting(models.Model):
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    title = models.CharField(max_length=255, blank=True, null=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='meetings', null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploaded')
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    is_shared = models.BooleanField(default=False, help_text="If false, only visible to creator")

    def __str__(self):
        return f"Meeting {self.id} - {self.status}"


class AudioFile(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="audio")
    file = models.FileField(upload_to='audio/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Audio for Meeting {self.meeting.id}"


class Transcript(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="transcript")
    full_text = models.TextField()
    whisper_segments = models.JSONField(
        null=True,
        blank=True,
        help_text="Whisper timed segments for aligning text to diarization",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Transcripts"

    def __str__(self):
        return f"Transcript for Meeting {self.meeting.id}"


class SpeakerSegment(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="segments")
    speaker = models.CharField(max_length=50)
    start_time = models.FloatField()  # seconds
    end_time = models.FloatField()    # seconds
    text = models.TextField()

    def __str__(self):
        return f"{self.speaker} ({self.start_time}-{self.end_time})"


class Summary(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="summary")
    short_summary = models.TextField()
    detailed_summary = models.TextField()
    key_points = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Summaries"

    def __str__(self):
        return f"Summary for Meeting {self.meeting.id}"


class ActionItem(models.Model):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="action_items")
    task = models.TextField()
    assigned_to = models.CharField(max_length=255, blank=True, null=True)
    deadline = models.CharField(max_length=100, blank=True, null=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Action Items"

    def __str__(self):
        return f"Task: {self.task[:30]}"


class LiveMeeting(models.Model):
    """Real-time streaming meeting (separate from uploaded meetings)."""
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('active', 'Active'),
        ('ended', 'Ended'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
    ]
    
    title = models.CharField(max_length=255, default='Live Meeting')
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='live_meetings', null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    transcript_text = models.TextField(default='')  # Live accumulated transcript
    audio_file = models.FileField(upload_to='live_audio/', null=True, blank=True)
    summary_short = models.TextField(blank=True, default='')
    summary_detailed = models.TextField(blank=True, default='')
    summary_key_points = models.JSONField(default=list, blank=True)
    action_items = models.JSONField(default=list, blank=True)
    speaker_segments = models.JSONField(default=list, blank=True)
    meeting_url = models.URLField(max_length=500, blank=True, null=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    is_shared = models.BooleanField(default=False, help_text="If false, only visible to creator")
    
    # Scheduling metadata (for strategy/roadmap tracking)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.IntegerField(default=60)
    
    def __str__(self):
        return f"LiveMeeting {self.title}"



class LiveTranscript(models.Model):
    """Incremental transcripts for each audio chunk."""
    live_meeting = models.ForeignKey(LiveMeeting, on_delete=models.CASCADE, related_name="transcripts")
    chunk_index = models.IntegerField()  # order of chunks
    text = models.TextField()
    timestamp = models.FloatField()  # seconds from start
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['chunk_index']
    
    def __str__(self):
        return f"Chunk {self.chunk_index} of LiveMeeting {self.live_meeting.id}"

class EmailOTP(models.Model):
    email = models.EmailField()
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email} - {self.otp}"

class Notification(models.Model):
    TYPE_CHOICES = [
        ('meeting_completed', 'Meeting Processed'),
        ('meeting_reminder', 'Meeting Reminder'),
        ('action_item', 'Action Item Due'),
        ('share', 'Meeting Shared'),
        ('system', 'System Update'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    description = models.TextField()
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='system')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.title}"

class Testimonial(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='testimonials')
    meeting_uuid = models.CharField(max_length=100, blank=True, null=True) # Optional link to specific meeting
    quote = models.TextField()
    rating = models.IntegerField(default=5)
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Review by {self.user.full_name} ({self.rating}/5)"


class SimulatorScenario(models.Model):
    """Predefined settings for AI Practice sessions."""
    name = models.CharField(max_length=255)
    description = models.TextField()
    system_prompt = models.TextField(help_text="The system instructions for the AI persona.")
    difficulty = models.CharField(max_length=50, choices=[('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced')], default='intermediate')
    ai_role = models.CharField(max_length=100, help_text="e.g., Venture Capitalist, HR Manager")
    icon_type = models.CharField(max_length=50, default='users', help_text="Lucide icon name")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class SimulatorSession(models.Model):
    """A specific practice match for a user."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='simulator_sessions')
    scenario = models.ForeignKey(SimulatorScenario, on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='active', choices=[('active', 'Active'), ('completed', 'Completed')])
    feedback_data = models.JSONField(null=True, blank=True, help_text="AI generated feedback score and metrics")
    
    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.user.email} - {self.scenario.name} ({self.started_at.date()})"

class SimulatorMessage(models.Model):
    """Individual messages in a simulator session."""
    session = models.ForeignKey(SimulatorSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=[('user', 'User'), ('ai', 'AI')])
    text_content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.role}: {self.text_content[:50]}..."

class WorkspaceMessage(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.TextField(blank=True, null=True)
    file_attachment = models.FileField(blank=True, null=True, upload_to='workspace_files/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Message from {self.sender.email} in {self.workspace.name}"