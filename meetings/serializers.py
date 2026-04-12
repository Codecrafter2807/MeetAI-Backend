import os

from rest_framework import serializers


class AudioUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        allowed_types = {
            "audio/mpeg",
            "audio/mp3",
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
            "audio/mp4",
            "audio/x-m4a",
            "audio/webm",
        }
        ct = (value.content_type or "").lower()
        if ct in allowed_types:
            return value
        ext = os.path.splitext(getattr(value, "name", "") or "")[1].lower()
        if ct in ("", "application/octet-stream") and ext in (
            ".mp3",
            ".wav",
            ".m4a",
            ".webm",
        ):
            return value
        raise serializers.ValidationError("Unsupported file type")