from django.contrib import admin
from django.utils.html import format_html

from .models import ActionItem, AudioFile, LiveMeeting, LiveTranscript, Meeting, SpeakerSegment, Summary, Transcript
from django.contrib.auth import get_user_model

User = get_user_model()

@admin.register(User)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ('email', 'full_name', 'is_staff')


class AudioFileInline(admin.StackedInline):
    model = AudioFile
    extra = 0
    can_delete = False


class TranscriptInline(admin.StackedInline):
    model = Transcript
    extra = 0
    can_delete = False


class SummaryInline(admin.StackedInline):
    model = Summary
    extra = 0
    can_delete = False


class SpeakerSegmentInline(admin.TabularInline):
    model = SpeakerSegment
    extra = 0


class ActionItemInline(admin.TabularInline):
    model = ActionItem
    extra = 0


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "created_by", "status", "created_at")
    list_filter = ("status", "created_by")
    search_fields = ("title", "created_by__email", "created_by__full_name")
    readonly_fields = ("created_at",)
    inlines = (
        AudioFileInline,
        TranscriptInline,
        SummaryInline,
        SpeakerSegmentInline,
        ActionItemInline,
    )


@admin.register(AudioFile)
class AudioFileAdmin(admin.ModelAdmin):
    list_display = ("id", "meeting", "get_user", "uploaded_at")
    list_filter = ("uploaded_at", "meeting__created_by")
    raw_id_fields = ("meeting",)

    def get_user(self, obj):
        return obj.meeting.created_by
    get_user.short_description = 'User'


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    list_display = ("id", "meeting", "get_user", "created_at")
    list_filter = ("created_at", "meeting__created_by")
    raw_id_fields = ("meeting",)

    def get_user(self, obj):
        return obj.meeting.created_by
    get_user.short_description = 'User'


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ("id", "meeting", "get_user", "created_at")
    list_filter = ("created_at", "meeting__created_by")
    raw_id_fields = ("meeting",)

    def get_user(self, obj):
        return obj.meeting.created_by
    get_user.short_description = 'User'


@admin.register(SpeakerSegment)
class SpeakerSegmentAdmin(admin.ModelAdmin):
    list_display = ("id", "meeting", "get_user", "speaker", "start_time")
    list_filter = ("meeting__created_by", "speaker")
    raw_id_fields = ("meeting",)

    def get_user(self, obj):
        return obj.meeting.created_by
    get_user.short_description = 'User'


@admin.register(ActionItem)
class ActionItemAdmin(admin.ModelAdmin):
    list_display = ("id", "meeting", "get_user", "task", "priority")
    list_filter = ("priority", "meeting__created_by")
    raw_id_fields = ("meeting",)

    def get_user(self, obj):
        return obj.meeting.created_by
    get_user.short_description = 'User'


@admin.register(LiveTranscript)
class LiveTranscriptAdmin(admin.ModelAdmin):
    list_display = ("id", "live_meeting", "chunk_index", "timestamp", "created_at")
    list_filter = ("live_meeting",)
    raw_id_fields = ("live_meeting",)


class LiveTranscriptInline(admin.TabularInline):
    model = LiveTranscript
    extra = 0


@admin.register(LiveMeeting)
class LiveMeetingAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "created_by", "status", "started_at")
    list_filter = ("status", "created_by")
    search_fields = ("title", "created_by__email", "created_by__full_name")
    readonly_fields = ("started_at", "ended_at")
    inlines = (LiveTranscriptInline,)
