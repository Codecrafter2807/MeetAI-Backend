from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from meetings.media_views import ServeMediaRangeView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('meetings.urls')),
]

if settings.DEBUG:
    # Use custom range-aware server for media files in dev
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', ServeMediaRangeView.as_view()),
    ]