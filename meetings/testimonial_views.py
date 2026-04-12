from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Testimonial

class TestimonialPublicListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        testimonials = Testimonial.objects.filter(is_public=True).order_by('-created_at')[:6]
        data = []
        for t in testimonials:
            data.append({
                'id': t.id,
                'quote': t.quote,
                'name': t.user.full_name,
                'role': t.user.role if t.user.role else 'Verified User',
                'rating': t.rating,
                'created_at': t.created_at
            })
        return Response({"testimonials": data})

class TestimonialCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        quote = request.data.get('quote')
        rating = request.data.get('rating', 5)
        meeting_uuid = request.data.get('meeting_uuid', None)

        if not quote:
            return Response({"error": "Quote is required"}, status=status.HTTP_400_BAD_REQUEST)

        testimonial = Testimonial.objects.create(
            user=request.user,
            meeting_uuid=meeting_uuid,
            quote=quote,
            rating=int(rating),
            is_public=True # auto-approve for demonstration purposes
        )

        return Response({"message": "Review submitted successfully!"}, status=status.HTTP_201_CREATED)
