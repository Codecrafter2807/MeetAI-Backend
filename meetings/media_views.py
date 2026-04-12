import os
import re
import mimetypes
from django.http import StreamingHttpResponse, HttpResponse
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

class RangeFileResponse(StreamingHttpResponse):
    """
    A StreamingHttpResponse that supports Range headers (Partial Content).
    """
    def __init__(self, request, file_path, content_type=None, *args, **kwargs):
        if not content_type:
            content_type, _ = mimetypes.guess_type(file_path)
        
        file_size = os.path.getsize(file_path)
        range_header = request.META.get('HTTP_RANGE', '').strip()
        range_re = re.compile(r'bytes=(\d+)-(\d*)')
        match = range_re.match(range_header)

        if match:
            first_byte, last_byte = match.groups()
            first_byte = int(first_byte) if first_byte else 0
            last_byte = int(last_byte) if last_byte else file_size - 1
            if last_byte >= file_size:
                last_byte = file_size - 1
            length = last_byte - first_byte + 1
            
            def file_iterator(path, offset, length):
                with open(path, 'rb') as f:
                    f.seek(offset)
                    chunk_size = 8192
                    remaining = length
                    while remaining > 0:
                        read_size = min(chunk_size, remaining)
                        data = f.read(read_size)
                        if not data:
                            break
                        yield data
                        remaining -= len(data)

            super().__init__(file_iterator(file_path, first_byte, length), status=206, content_type=content_type, *args, **kwargs)
            self['Content-Length'] = str(length)
            self['Content-Range'] = f'bytes {first_byte}-{last_byte}/{file_size}'
        else:
            super().__init__(open(file_path, 'rb'), content_type=content_type, *args, **kwargs)
            self['Content-Length'] = str(file_size)
        
        self['Accept-Ranges'] = 'bytes'

class ServeMediaRangeView(APIView):
    """
    View to serve media files with Range support.
    """
    permission_classes = [AllowAny] # In dev we let it be public or tie to meeting logic

    def get(self, request, path):
        # Prevent directory traversal
        path = os.path.normpath(path).lstrip(os.sep)
        full_path = os.path.join(settings.MEDIA_ROOT, path)
        
        if not os.path.exists(full_path) or os.path.isdir(full_path):
            return HttpResponse(status=404)
            
        return RangeFileResponse(request, full_path)
