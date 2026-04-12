from rest_framework import serializers
from .models import Workspace, WorkspaceMember, WorkspaceInvitation, CustomUser

class UserMiniSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = ['id', 'full_name', 'email', 'avatar_url', 'role']

    def get_avatar_url(self, obj):
        request = self.context.get('request')
        if obj.avatar and request:
            return request.build_absolute_uri(obj.avatar.url)
        return None

class WorkspaceSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ['id', 'name', 'slug', 'owner', 'created_at', 'member_count']

    def get_member_count(self, obj):
        return obj.members.count()

class WorkspaceMemberSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)
    
    class Meta:
        model = WorkspaceMember
        fields = ['user', 'role', 'joined_at']

class WorkspaceInvitationSerializer(serializers.ModelSerializer):
    workspace_name = serializers.CharField(source='workspace.name', read_only=True)
    inviter_name = serializers.CharField(source='inviter.full_name', read_only=True)

    class Meta:
        model = WorkspaceInvitation
        fields = ['token', 'workspace_name', 'inviter_name', 'status', 'created_at']

from .models import WorkspaceMessage
class WorkspaceMessageSerializer(serializers.ModelSerializer):
    sender = UserMiniSerializer(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkspaceMessage
        fields = ['id', 'sender', 'content', 'file_url', 'created_at']

    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file_attachment and request:
            return request.build_absolute_uri(obj.file_attachment.url)
        return None
