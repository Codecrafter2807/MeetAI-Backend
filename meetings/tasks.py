import logging
import os
import shutil
import subprocess
from celery import shared_task
from django.conf import settings
from .models import Meeting, Transcript, SpeakerSegment, Summary, ActionItem, LiveMeeting, LiveTranscript
from .whisper_service import transcribe_audio
from .diarization_service import diarize_audio
from .alignment import normalize_whisper_segments
from .alignment_service import align_speakers
from django.core.cache import cache

from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task
def send_email_task(subject, message, recipient_list):
    """Send email in the background to avoid blocking API responses."""
    try:
        from django.conf import settings
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list,
            fail_silently=False,
        )
    except Exception as e:
        logger.error(f"Failed to send background email to {recipient_list}: {e}")
def process_meeting(self, meeting_id):
    """
    Celery task to process meeting: transcribe, diarize, align, summarize.
    Runs asynchronously in background.
    """
    try:
        meeting = Meeting.objects.get(id=meeting_id)
        meeting.status = "processing"
        meeting.save()

        logger.info(f"Starting processing for meeting {meeting_id}")
        
        # Create 'Processing Started' notification
        try:
            from .notification_views import create_notification
            if meeting.created_by:
                create_notification(
                    user=meeting.created_by,
                    title="Processing Started",
                    description=f"Your meeting '{meeting.title or 'Untitled'}' is now being processed.",
                    n_type="system",
                    send_email=False # Don't spam email for start, only for end
                )
        except Exception as e:
            logger.error(f"Failed to create start notification for meeting {meeting_id}: {e}")
        
        audio_path = meeting.audio.file.path

        # Clear existing data (safe for retries)
        SpeakerSegment.objects.filter(meeting=meeting).delete()
        ActionItem.objects.filter(meeting=meeting).delete()
        Summary.objects.filter(meeting=meeting).delete()

        # 1. Whisper Transcription
        logger.info(f"Running Whisper for meeting {meeting_id}")
        result = transcribe_audio(audio_path)

        if not result:
            logger.error(f"Whisper failed for meeting {meeting_id}")
            meeting.status = "failed"
            meeting.save()
            return
            
        # Cache the diarization result for Step 2
        if "diarization" in result:
            cache_key = f"diarization_{audio_path.replace(' ', '_')}"
            cache.set(cache_key, result["diarization"], timeout=300)

        whisper_raw_segments = result.get("segments") or []
        Transcript.objects.update_or_create(
            meeting=meeting,
            defaults={
                "full_text": result["text"],
                "whisper_segments": normalize_whisper_segments(whisper_raw_segments) or None,
            },
        )

        # Notification: Transcription Completed
        try:
            from .notification_views import create_notification
            if meeting.created_by:
                create_notification(
                    user=meeting.created_by,
                    title="Transcription Completed",
                    description=f"Transcription for your meeting '{meeting.title or 'Untitled'}' is finished.",
                    n_type="system",
                    send_email=False
                )
        except Exception as e:
            logger.error(f"Failed to create transcription notification for meeting {meeting_id}: {e}")

        # 2. Diarization
        logger.info(f"Running Diarization for meeting {meeting_id}")
        from .diarization_service import diarize_audio
        diarization_result = diarize_audio(audio_path) or []

        # 3. Alignment
        logger.info(f"Aligning speakers for meeting {meeting_id}")
        whisper_result = whisper_raw_segments or []

        if whisper_result:
            aligned_segments = align_speakers(whisper_result, diarization_result)
            SpeakerSegment.objects.bulk_create([
                SpeakerSegment(
                    meeting=meeting,
                    speaker=seg["speaker"],
                    start_time=seg["start"],
                    end_time=seg["end"],
                    text=seg["text"],
                ) for seg in aligned_segments
            ])
        elif diarization_result:
            # Map raw labels (SPEAKER_00) to human-friendly ones (Speaker 1)
            speaker_map = {}
            next_speaker_num = 1
            processed_segments = []
            for seg in diarization_result:
                raw_speaker = seg["speaker"]
                if raw_speaker not in speaker_map:
                    speaker_map[raw_speaker] = f"Speaker {next_speaker_num}"
                    next_speaker_num += 1
                processed_segments.append(
                    SpeakerSegment(
                        meeting=meeting,
                        speaker=speaker_map[raw_speaker],
                        start_time=seg["start"],
                        end_time=seg["end"],
                        text="",
                    )
                )
            SpeakerSegment.objects.bulk_create(processed_segments)

        # Notification: Diarization Completed
        try:
            from .notification_views import create_notification
            if meeting.created_by:
                create_notification(
                    user=meeting.created_by,
                    title="Diarization Completed",
                    description=f"Speaker identification for your meeting '{meeting.title or 'Untitled'}' is finished.",
                    n_type="system",
                    send_email=False
                )
        except Exception as e:
            logger.error(f"Failed to create diarization notification for meeting {meeting_id}: {e}")

        # 4. NLP Summary & Action Items
        try:
            logger.info(f"Running NLP for meeting {meeting_id}")
            from .nlp_service import run_meeting_nlp
            run_meeting_nlp(meeting)

            # Notification: Summary & Action Items Ready
            try:
                from .notification_views import create_notification
                if meeting.created_by:
                    create_notification(
                        user=meeting.created_by,
                        title="AI Insights Ready",
                        description=f"Summary and action items for your meeting '{meeting.title or 'Untitled'}' have been generated.",
                        n_type="system",
                        send_email=False
                    )
            except Exception as e:
                logger.error(f"Failed to create NLP notification for meeting {meeting_id}: {e}")
        except Exception as e:
            logger.exception(f"NLP step failed for meeting {meeting_id}: {e}")

        meeting.status = "completed"
        meeting.save()
        logger.info(f"Completed processing for meeting {meeting_id}")

        # Create notification for user
        try:
            from .notification_views import create_notification
            if meeting.created_by:
                create_notification(
                    user=meeting.created_by,
                    title="Meeting Processed",
                    description=f"Your meeting '{meeting.title or 'Untitled'}' has been successfully processed.",
                    n_type="meeting_completed"
                )
        except Exception as e:
            logger.error(f"Failed to create notification for meeting {meeting_id}: {e}")

    except Meeting.DoesNotExist:
        logger.error(f"Meeting {meeting_id} not found")
        meeting.status = "failed"
        meeting.save()
    except Exception as e:
        logger.exception(f"Celery error processing meeting {meeting_id}: {e}")
        try:
            meeting = Meeting.objects.get(id=meeting_id)
            meeting.status = "failed"
            meeting.save()
        except:
            pass
        # Retry up to max_retries times
        raise self.retry(exc=e, countdown=60)


@shared_task
def process_audio_chunk_task(live_meeting_id, chunk_index, timestamp, audio_path):
    """
    Process a single audio chunk from live stream.
    Transcribe with Whisper, save transcript chunk.
    """
    try:
        live_meeting = LiveMeeting.objects.get(id=live_meeting_id)
        
        logger.info(f"Transcribing chunk {chunk_index} for live meeting {live_meeting_id}")
        
        # Debug: check file size and first bytes
        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return
            
        file_size = os.path.getsize(audio_path)
        logger.info(f"Chunk file size: {file_size} bytes")
        
        if file_size < 100:
            logger.warning(f"Chunk {chunk_index} is too small ({file_size} bytes), skipping")
            return
        
        # Convert to WAV if necessary using ffmpeg
        input_ext = os.path.splitext(audio_path)[1].lower()
        if input_ext == '.wav':
            converted_path = audio_path
        else:
            converted_path = os.path.splitext(audio_path)[0] + '_converted.wav'
            
            try:
                # Direct conversion: webm/ogg -> wav (30-second chunks are valid WebM files)
                ffmpeg_args = [
                    'ffmpeg', '-y', '-nostdin',
                    '-i', audio_path,
                    '-vn',
                    '-ac', '1',
                    '-ar', '16000',
                    '-acodec', 'pcm_s16le',
                    converted_path,
                ]
                
                logger.info(f"Converting chunk {chunk_index} to WAV using ffmpeg")
                subprocess.run(ffmpeg_args, check=True, capture_output=True, timeout=60)
                logger.info(f"Successfully converted chunk {chunk_index} to WAV")
                
            except subprocess.TimeoutExpired:
                logger.error(f"FFmpeg timeout for chunk {chunk_index}")
                return
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode(errors='ignore') if e.stderr else 'unknown error'
                logger.error(f"FFmpeg conversion failed for chunk {chunk_index}: {stderr}")
                return
        
        # Check if converted file exists and has content
        if not os.path.exists(converted_path) or os.path.getsize(converted_path) < 100:
            logger.error(f"Converted WAV file is invalid for chunk {chunk_index}")
            return
        
        # Transcribe the converted chunk
        result = transcribe_audio(converted_path)
        
        if not result:
            logger.warning(f"Chunk {chunk_index} transcription failed")
            return
        
        text = result.get("text", "").strip()
        
        if text:
            # Save transcript chunk
            LiveTranscript.objects.create(
                live_meeting=live_meeting,
                chunk_index=chunk_index,
                text=text,
                timestamp=timestamp,
            )
            
            logger.info(f"Saved transcript chunk {chunk_index}: {text[:50]}...")
        else:
            logger.info(f"Chunk {chunk_index} transcription produced no text")
        
        # Save the converted audio chunk for later concatenation
        chunk_dir = os.path.join(settings.MEDIA_ROOT, 'live_audio', 'chunks', str(live_meeting_id))
        os.makedirs(chunk_dir, exist_ok=True)
        chunk_path = os.path.join(chunk_dir, f'chunk_{chunk_index}.wav')
        
        if os.path.exists(converted_path):
            try:
                shutil.move(converted_path, chunk_path)
                logger.info(f"Saved audio chunk {chunk_index} to {chunk_path}")
            except Exception as e:
                logger.error(
                    f"Failed to move converted chunk {chunk_index} to storage path: {e}"
                )
                # Fall back to copy if move fails (cross-drive issue)
                try:
                    shutil.copy2(converted_path, chunk_path)
                    logger.info(
                        f"Copied audio chunk {chunk_index} to {chunk_path} after move failure"
                    )
                except Exception as copy_exc:
                    logger.error(
                        f"Failed to copy converted chunk {chunk_index}: {copy_exc}"
                    )
                    return
        else:
            logger.warning(f"Converted file not found for chunk {chunk_index}")
        
        # Clean up original uploaded file if it's not the saved chunk
        try:
            if os.path.exists(audio_path) and audio_path != chunk_path:
                os.remove(audio_path)
        except Exception as e:
            logger.warning(f"Could not clean up original file for chunk {chunk_index}: {e}")

        # If the live meeting has already been ended, trigger final NLP once ALL transcripts are available.
        try:
            if live_meeting.ended_at and live_meeting.status == 'processing':
                total_chunks = cache.get(f"live_total_{live_meeting_id}")
                processed_count = LiveTranscript.objects.filter(live_meeting=live_meeting).count()
                
                # Update transcript text progressively
                full_text = ' '.join([t.text for t in live_meeting.transcripts.order_by('chunk_index') if t.text.strip()])
                if full_text.strip() and live_meeting.transcript_text.strip() != full_text.strip():
                    live_meeting.transcript_text = full_text.strip()
                    live_meeting.save(update_fields=['transcript_text'])

                if total_chunks is not None:
                    if processed_count >= total_chunks:
                        logger.info(f"All {total_chunks} chunks processed for live meeting {live_meeting_id}; queuing NLP task")
                        process_live_meeting_nlp.delay(live_meeting_id=live_meeting_id)
                elif processed_count > 0:
                    # Fallback check if cache is missing
                    process_live_meeting_nlp.delay(live_meeting_id=live_meeting_id)
        except Exception as e:
            logger.exception(f"Failed to check completion after chunk {chunk_index} for live meeting {live_meeting_id}: {e}")
    except LiveMeeting.DoesNotExist:
        logger.error(f"Live meeting {live_meeting_id} not found")
    except Exception as e:
        logger.exception(f"Error processing audio chunk {chunk_index}: {e}")


@shared_task(bind=True, max_retries=5)
def wait_for_live_meeting_transcript(self, live_meeting_id):
    """Wait for a delayed live transcript after the meeting has ended."""
    try:
        live_meeting = LiveMeeting.objects.get(id=live_meeting_id)

        if not live_meeting.ended_at or live_meeting.status != 'processing':
            logger.info(
                "Live meeting %s is not in processing state; skipping transcript wait.",
                live_meeting_id,
            )
            return

        transcripts = list(live_meeting.transcripts.order_by('chunk_index'))
        full_text = ' '.join([t.text for t in transcripts if t.text.strip()])

        if full_text.strip():
            if live_meeting.transcript_text.strip() != full_text.strip():
                live_meeting.transcript_text = full_text.strip()
                live_meeting.save(update_fields=['transcript_text'])

            logger.info(
                "Live meeting %s transcript became available after end; queuing NLP",
                live_meeting_id,
            )
            process_live_meeting_nlp.delay(live_meeting_id=live_meeting_id)
            return

        if self.request.retries >= self.max_retries:
            logger.warning(
                "Live meeting %s transcript never arrived after end; giving up.",
                live_meeting_id,
            )
            return

        logger.info(
            "Live meeting %s transcript not ready yet; retrying in 5 seconds.",
            live_meeting_id,
        )
        raise self.retry(countdown=5)

    except LiveMeeting.DoesNotExist:
        logger.error(f"Live meeting {live_meeting_id} not found")
    except Exception as e:
        logger.exception(
            "Error waiting for live meeting transcript %s: %s",
            live_meeting_id,
            e,
        )


@shared_task
def process_live_meeting_nlp(live_meeting_id):
    """
    Process completed live meeting: run diarization, speaker alignment, summary, and action items.
    """
    lock_id = f"live_meeting_nlp_lock_{live_meeting_id}"
    if not cache.add(lock_id, "true", timeout=60 * 60):
        logger.warning(f"NLP already processing for live meeting {live_meeting_id}. Skipping duplicate task.")
        return

    try:
        live_meeting = LiveMeeting.objects.get(id=live_meeting_id)

        transcript_text = live_meeting.transcript_text.strip()
        transcripts = list(live_meeting.transcripts.order_by('chunk_index'))
        if not transcript_text and transcripts:
            transcript_text = ' '.join([t.text for t in transcripts if t.text.strip()])

        if not transcript_text:
            logger.warning(f"No transcript text for live meeting {live_meeting_id}")
            live_meeting.status = "completed"
            live_meeting.save()
            return
            
        # 0. CONCATENATION STEP (moved from EndLiveMeetingView to ensure completeness)
        chunk_dir = os.path.join(settings.MEDIA_ROOT, 'live_audio', 'chunks', str(live_meeting_id))
        if os.path.exists(chunk_dir):
            chunk_files = []
            for filename in os.listdir(chunk_dir):
                if filename.startswith('chunk_') and filename.endswith('.wav'):
                    try:
                        index = int(filename.split('_')[1].split('.')[0])
                        chunk_files.append((index, os.path.join(chunk_dir, filename)))
                    except ValueError:
                        continue
            
            chunk_files.sort(key=lambda x: x[0])
            if chunk_files:
                output_path = os.path.join(settings.MEDIA_ROOT, 'live_audio', f'live_meeting_{live_meeting_id}.mp3')
                concat_file = os.path.join(chunk_dir, 'concat_list.txt')
                with open(concat_file, 'w') as f:
                    for _, chunk_path in chunk_files:
                        f.write(f"file '{chunk_path}'\n")
                try:
                    subprocess.run(
                        ['ffmpeg', '-y', '-nostdin', '-f', 'concat', '-safe', '0', '-i', concat_file, '-acodec', 'libmp3lame', '-q:a', '2', output_path],
                        check=True, capture_output=True, timeout=300
                    )
                    from django.core.files import File
                    with open(output_path, 'rb') as f:
                        live_meeting.audio_file.save(f'live_meeting_{live_meeting_id}.mp3', File(f))
                    logger.info(f"Concatenated {len(chunk_files)} audio chunks before NLP for meeting {live_meeting_id}")
                    
                    # Cleanup
                    os.remove(concat_file)
                    for _, chunk_path in chunk_files:
                        os.remove(chunk_path)
                    os.rmdir(chunk_dir)
                except Exception as e:
                    logger.error(f"Error concatenating chunks before NLP {live_meeting_id}: {e}")

        logger.info(f"Running diarization and Whisper transcription for live meeting {live_meeting_id}")
        diarization_result = []
        whisper_segments = []
        audio_path = None
        if live_meeting.audio_file and live_meeting.audio_file.path:
            audio_path = live_meeting.audio_file.path
            if os.path.exists(audio_path):
                # Use final concatenated audio to get accurate speaker segments.
                whisper_result = transcribe_audio(audio_path)
                if whisper_result:
                    transcript_text = whisper_result.get("text", "").strip() or transcript_text
                    whisper_segments = normalize_whisper_segments(whisper_result.get("segments") or [])
                    
                    # Notification: Transcription Completed
                    try:
                        from .notification_views import create_notification
                        if live_meeting.created_by:
                            create_notification(
                                user=live_meeting.created_by,
                                title="Transcription Completed",
                                description=f"Transcription for your live meeting '{live_meeting.title}' is finished.",
                                n_type="system",
                                send_email=False
                            )
                    except Exception as e:
                        logger.error(f"Failed to create transcription notification for live meeting {live_meeting_id}: {e}")

                diarization_result = diarize_audio(audio_path) or []
                if diarization_result:
                    # Notification: Diarization Completed
                    try:
                        from .notification_views import create_notification
                        if live_meeting.created_by:
                            create_notification(
                                user=live_meeting.created_by,
                                title="Diarization Completed",
                                description=f"Speaker identification for your live meeting '{live_meeting.title}' is finished.",
                                n_type="system",
                                send_email=False
                            )
                    except Exception as e:
                        logger.error(f"Failed to create diarization notification for live meeting {live_meeting_id}: {e}")
            else:
                logger.warning(
                    "Live meeting audio file missing for diarization: %s",
                    audio_path,
                )

        if not whisper_segments and transcripts:
            whisper_segments = [
                {
                    "start": float(t.timestamp),
                    "end": float(t.timestamp + 5.0),
                    "text": t.text,
                }
                for t in transcripts
            ]

        if whisper_segments and diarization_result:
            logger.info(f"Aligning speakers for live meeting {live_meeting_id}")
            aligned_segments = align_speakers(whisper_segments, diarization_result)
            live_meeting.speaker_segments = aligned_segments
        elif whisper_segments:
            live_meeting.speaker_segments = [
                {
                    "speaker": "Unknown",
                    "start": float(t.timestamp),
                    "end": float(t.timestamp + 5.0),
                    "text": t.text,
                }
                for t in transcripts
            ]
        else:
            live_meeting.speaker_segments = []

        if transcript_text and transcript_text != live_meeting.transcript_text.strip():
            live_meeting.transcript_text = transcript_text

        logger.info(f"Running NLP for live meeting {live_meeting_id}")

        from .nlp_service import request_grok_insights

        duration_seconds = 0
        if live_meeting.ended_at and live_meeting.started_at:
            duration_seconds = (live_meeting.ended_at - live_meeting.started_at).total_seconds()
        
        insights = request_grok_insights(transcript_text, duration_seconds=duration_seconds)
        if insights:
            # 👉 Update title if generic
            new_title = (insights.get("title") or "").strip()
            generic_titles = ["live meeting", "external meeting", "untitled meeting", "new meeting", "meeting", "scheduled meeting"]
            current_title_lower = (live_meeting.title or "").lower()
            
            is_generic = not live_meeting.title or any(gt in current_title_lower for gt in generic_titles)
            
            if new_title and is_generic:
                logger.info(f"Updating generic title '{live_meeting.title}' to AI title '{new_title}' for live meeting {live_meeting_id}")
                live_meeting.title = new_title
                live_meeting.save(update_fields=['title'])
            else:
                logger.info(f"Skipping title update for live meeting {live_meeting_id}. New title: '{new_title}', Current: '{live_meeting.title}', IsGeneric: {is_generic}")

            live_meeting.summary_short = (insights.get("short_summary") or "").strip()
            live_meeting.summary_detailed = (insights.get("detailed_summary") or "").strip()
            key_points = insights.get("key_points") or []
            if isinstance(key_points, str):
                key_points = [key_points]
            if not isinstance(key_points, list):
                key_points = []
            live_meeting.summary_key_points = [str(x).strip() for x in key_points if str(x).strip()]

            action_items = insights.get("action_items") or []
            if not isinstance(action_items, list):
                action_items = []
            normalized_items = []
            for item in action_items:
                if not isinstance(item, dict):
                    continue
                normalized_items.append(
                    {
                        "task": (item.get("task") or "").strip(),
                        "assigned_to": (item.get("assigned_to") or "").strip(),
                        "deadline": (item.get("deadline") or "").strip(),
                        "priority": (item.get("priority") or "medium").strip().lower(),
                    }
                )
            live_meeting.action_items = [item for item in normalized_items if item.get("task")]

            logger.info(
                "Saved insights for live meeting %s: %s key points, %s action items",
                live_meeting_id,
                len(live_meeting.summary_key_points),
                len(live_meeting.action_items),
            )

            # Notification: Summary & Action Items Ready
            try:
                from .notification_views import create_notification
                if live_meeting.created_by:
                    create_notification(
                        user=live_meeting.created_by,
                        title="AI Insights Ready",
                        description=f"Summary and action items for your live meeting '{live_meeting.title}' have been generated.",
                        n_type="meeting_completed",
                        send_email=False
                    )
            except Exception as e:
                logger.error(f"Failed to create NLP notification for live meeting {live_meeting_id}: {e}")
        else:
            logger.warning(f"NLP returned no results for live meeting {live_meeting_id}")

        live_meeting.status = "completed"
        live_meeting.save()
        logger.info(f"Completed NLP for live meeting {live_meeting_id}")

        # Create notification for user
        try:
            from .notification_views import create_notification
            if live_meeting.created_by:
                create_notification(
                    user=live_meeting.created_by,
                    title="Live Meeting Completed",
                    description=f"Your live meeting '{live_meeting.title}' has been processed and is ready for review.",
                    n_type="meeting_completed"
                )
        except Exception as e:
            logger.error(f"Failed to create notification for live meeting {live_meeting_id}: {e}")

    except LiveMeeting.DoesNotExist:
        logger.error(f"Live meeting {live_meeting_id} not found")
    except Exception as e:
        logger.exception(f"Error processing live meeting NLP {live_meeting_id}: {e}")
    finally:
        # Clear the lock when finished processing (optional but good practice)
        pass


@shared_task
def check_pending_action_items():
    """
    Check for pending action items in completed meetings and send reminders every hour.
    """
    from .models import Meeting, LiveMeeting, Notification
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    one_hour_ago = now - timedelta(hours=1)

    # 1. Regular Meetings
    # Meetings completed more than 1 hour ago (or recently reminded more than 1 hour ago)
    # with at least one incomplete action item.
    from django.db.models import Exists, OuterRef
    from .models import ActionItem
    
    meetings = Meeting.objects.filter(
        status="completed",
        action_items__completed=False
    ).distinct()

    for m in meetings:
        should_remind = False
        if not m.last_reminder_at:
            if m.created_at < one_hour_ago:
                should_remind = True
        elif m.last_reminder_at < one_hour_ago:
            should_remind = True

        if should_remind:
            pending_count = m.action_items.filter(completed=False).count()
            if pending_count > 0:
                Notification.objects.create(
                    user=m.created_by,
                    title=f"Pending Tasks: {m.title}",
                    description=f"You still have {pending_count} pending action items from this meeting.",
                    type="action_item"
                )
                m.last_reminder_at = now
                m.save(update_fields=['last_reminder_at'])

    # 2. Live Meetings
    live_meetings = LiveMeeting.objects.filter(
        status="completed",
        ended_at__lte=one_hour_ago
    )

    for lm in live_meetings:
        pending_items = [
            item for item in (lm.action_items or [])
            if isinstance(item, dict) and not item.get('completed', False)
        ]
        
        if not pending_items:
            continue

        should_remind = False
        if not lm.last_reminder_at or lm.last_reminder_at < one_hour_ago:
            should_remind = True

        if should_remind:
            Notification.objects.create(
                user=lm.created_by,
                title=f"Pending Tasks: {lm.title}",
                description=f"You still have {len(pending_items)} pending action items from this live meeting.",
                type="action_item"
            )
            lm.last_reminder_at = now
            lm.save(update_fields=['last_reminder_at'])

    return "Check completed"

@shared_task
def delete_expired_meetings():
    """
    Delete scheduled meetings that are more than 24 hours past their time 
    and were never actually started (no transcript chunks).
    """
    from .models import LiveMeeting
    from django.utils import timezone
    from django.db.models import Count
    from datetime import timedelta

    # Threshold: 24 hours ago
    threshold = timezone.now() - timedelta(hours=24)
    
    # We find meetings that:
    # 1. Were scheduled more than 24h ago
    # 2. Are still in 'active' status (not ended/completed)
    # 3. Have 0 transcript chunks (meaning the call never actually started)
    expired_meetings = LiveMeeting.objects.filter(
        scheduled_at__lt=threshold,
        status='active'
    ).annotate(chunk_count=Count('transcripts')).filter(chunk_count=0)
    
    count = expired_meetings.count()
    expired_meetings.delete()
    
    return f"Cleanup complete. Deleted {count} expired meetings."

@shared_task
def send_upcoming_meeting_reminders():
    """
    Check for scheduled meetings starting in ~30 minutes and notify users.
    """
    from .models import LiveMeeting, Notification
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    reminder_window_start = now + timedelta(minutes=25)
    reminder_window_end = now + timedelta(minutes=35)

    upcoming_meetings = LiveMeeting.objects.filter(
        status='scheduled',
        scheduled_at__range=(reminder_window_start, reminder_window_end)
    )

    for m in upcoming_meetings:
        # Avoid double notification
        if m.last_reminder_at and m.last_reminder_at > (now - timedelta(hours=1)):
            continue

        Notification.objects.create(
            user=m.created_by,
            title="Meeting Starting Soon",
            description=f"Your scheduled meeting '{m.title}' starts in about 30 minutes.",
            type="meeting_reminder"
        )
        m.last_reminder_at = now
        m.save(update_fields=['last_reminder_at'])

    return f"Processed {upcoming_meetings.count()} possible reminders"
