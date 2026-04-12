from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.shortcuts import get_object_or_404
from .models import Workspace, WorkspaceMember, WorkspaceInvitation
from .workspace_serializers import (
    WorkspaceSerializer, 
    WorkspaceMemberSerializer, 
    WorkspaceInvitationSerializer,
    WorkspaceMessageSerializer
)

class WorkspaceListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # Workspaces where user is a member
        memberships = WorkspaceMember.objects.filter(user=request.user)
        workspaces = [m.workspace for m in memberships]

        # Auto-create a personal workspace if the user has none
        if not workspaces:
            workspace = Workspace.objects.create(
                name=f"{request.user.full_name}'s Workspace",
                owner=request.user
            )
            WorkspaceMember.objects.create(
                workspace=workspace,
                user=request.user,
                role='admin'
            )
            workspaces = [workspace]

        serializer = WorkspaceSerializer(workspaces, many=True)
        return Response(serializer.data)

    def post(self, request):
        name = request.data.get('name')
        if not name:
            return Response({"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        workspace = Workspace.objects.create(name=name, owner=request.user)
        WorkspaceMember.objects.create(workspace=workspace, user=request.user, role='admin')
        
        serializer = WorkspaceSerializer(workspace)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class WorkspaceMemberView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, workspace_slug):
        workspace = get_object_or_404(Workspace, slug=workspace_slug)
        # Check if user is a member
        if not WorkspaceMember.objects.filter(workspace=workspace, user=request.user).exists():
            return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
            
        members = workspace.members.all()
        serializer = WorkspaceMemberSerializer(members, many=True, context={'request': request})
        return Response(serializer.data)

class WorkspaceInviteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, workspace_slug):
        workspace = get_object_or_404(Workspace, slug=workspace_slug)
        
        # Any workspace member can invite
        membership = get_object_or_404(WorkspaceMember, workspace=workspace, user=request.user)
            
        # Create a new invitation token
        invitation = WorkspaceInvitation.objects.create(
            workspace=workspace,
            inviter=request.user
        )
        
        return Response({
            "invite_url": f"/invite/{invitation.token}",
            "token": str(invitation.token)
        }, status=status.HTTP_201_CREATED)

class AcceptInvitationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = request.data.get('token')
        invitation = get_object_or_404(WorkspaceInvitation, token=token, status='pending')
        
        # Add user to workspace
        if WorkspaceMember.objects.filter(workspace=invitation.workspace, user=request.user).exists():
             return Response({"message": "You are already a member of this workspace"}, status=status.HTTP_200_OK)
             
        WorkspaceMember.objects.create(
            workspace=invitation.workspace,
            user=request.user,
            role='member'
        )
        
        # Optional: update invitation status
        # invitation.status = 'accepted'
        # invitation.save()
        
        return Response({
            "message": "Successfully joined workspace",
            "workspace": WorkspaceSerializer(invitation.workspace).data
        }, status=status.HTTP_200_OK)

from .models import WorkspaceMessage
from .workspace_serializers import WorkspaceMessageSerializer

class WorkspaceChatView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, workspace_slug):
        workspace = get_object_or_404(Workspace, slug=workspace_slug)
        if not WorkspaceMember.objects.filter(workspace=workspace, user=request.user).exists():
            return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
            
        messages = workspace.messages.all().order_by('created_at')
        serializer = WorkspaceMessageSerializer(messages, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request, workspace_slug):
        workspace = get_object_or_404(Workspace, slug=workspace_slug)
        if not WorkspaceMember.objects.filter(workspace=workspace, user=request.user).exists():
            return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)
            
        content = request.data.get('content', '')
        file = request.FILES.get('file')

        if not content and not file:
            return Response({"error": "Must provide content or a file"}, status=status.HTTP_400_BAD_REQUEST)

        message = WorkspaceMessage.objects.create(
            workspace=workspace,
            sender=request.user,
            content=content,
            file_attachment=file
        )
        serializer = WorkspaceMessageSerializer(message, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class WorkspaceMemberDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, workspace_slug, user_id):
        workspace = get_object_or_404(Workspace, slug=workspace_slug)
        member_to_remove = get_object_or_404(WorkspaceMember, workspace=workspace, user_id=user_id)

        # 1. Host (Owner) can remove any member
        # 2. Users can remove themselves from other's workspace
        # 3. Users cannot remove themselves from their OWN workspace
        
        is_owner = (workspace.owner == request.user)
        is_self = (request.user.id == int(user_id))
        
        if is_owner:
            # Owner can remove anyone except themselves via this endpoint (prevent accidents)
            if is_self:
                return Response({"error": "As the owner, you cannot leave your own workspace."}, status=status.HTTP_400_BAD_REQUEST)
            member_to_remove.delete()
            return Response({"message": "Member removed successfully"}, status=status.HTTP_200_OK)
        
        elif is_self:
            # Non-owner can remove themselves (Leave)
            member_to_remove.delete()
            return Response({"message": "You have left the workspace"}, status=status.HTTP_200_OK)
            
        else:
            return Response({"error": "You do not have permission to remove this member"}, status=status.HTTP_403_FORBIDDEN)
