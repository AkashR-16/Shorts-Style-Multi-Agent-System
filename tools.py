import os
import textwrap
import tempfile
import shutil
import subprocess
import re
import requests

from elevenlabs.client import ElevenLabs

elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
voice_id="jsCqWAovK2LkecY7zXl4"
STABILITY_API_KEY = os.getenv("STABILITY_API_KEY")


def sanitize_text_for_ffmpeg(text: str) -> str:
    """
    Sanitize text for use with FFmpeg's drawtext filter.
    Escapes special characters that cause issues with FFmpeg.
    
    Args:
        text: The input text to sanitize
        
    Returns:
        Sanitized text safe for FFmpeg drawtext
    """
    # First, escape backslashes (must come first to avoid double-escaping)
    text = text.replace('\\', '\\\\')
    
    # Escape single quotes (most important for drawtext=text='...')
    text = text.replace("'", "\\'")
    
    # Escape other potentially problematic characters
    text = text.replace(':', '\\:')
    text = text.replace(',', '\\,')
    text = text.replace(';', '\\;')
    
    return text

def wrap_caption(caption: str, max_width=20) -> str:
    """
    Wrap caption text into multiple lines for better readability on screen.
    
    Args:
        caption: The text to wrap
        max_width: Maximum characters per line
        
    Returns:
        String with newline characters at appropriate positions
    """
    wrapped_lines = textwrap.wrap(caption, width=max_width)
    return "\n".join(wrapped_lines)


#Tools
#1. Generate video
def generate_video(captions: list[str]):
    """
    Generate a YouTube Shorts style video with animated images, voiceovers and music.
    
    Args:
        captions: List of text captions to display on each segment
    """
    # Configuration
    images_folder = "images"
    voiceovers_folder = "voiceovers"
    music_file = "music/cosmos.mp3" # Add your own (not included)
    output_video = "yt_shorts_video.mp4"
    IMAGE_DURATION = 5  # seconds per image/segment
    
    # Get sorted lists of image and voiceover files
    images = sorted([
        os.path.join(images_folder, f) 
        for f in os.listdir(images_folder)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    ])
    
    voiceovers = sorted([
        os.path.join(voiceovers_folder, f)
        for f in os.listdir(voiceovers_folder)
        if f.lower().endswith(".mp3")
    ])

    # Validate inputs
    if len(images) != len(voiceovers):
        raise ValueError("Number of images and voiceovers must match!")
    
    if captions is None:
        captions = [os.path.splitext(os.path.basename(vo))[0] for vo in voiceovers]
    elif len(captions) != len(images):
        raise ValueError("Number of captions must match number of images!")

    total_duration = len(images) * IMAGE_DURATION
    print(f"Total Duration: {total_duration} seconds")

    # Create temporary directory for intermediate files
    temp_dir = tempfile.mkdtemp(prefix="video_gen_")
    print(f"Using temporary directory: {temp_dir}")

    try:
        segment_files = []

        # Step 1: Create individual video segments with Ken Burns effect and text
        for i, (image_path, caption) in enumerate(zip(images, captions)):
            # Clean and format caption text
            safe_caption = sanitize_text_for_ffmpeg(caption)
            wrapped_caption = wrap_caption(safe_caption)
            
            # Output path for this segment
            segment_path = os.path.join(temp_dir, f"segment_{i}.mp4")
            
            # Because FFmpeg's drawtext filter requires careful escaping of characters,
            # we'll write the caption to a temporary file and use the 'textfile' option
            # This is more reliable than trying to escape everything properly inline
            caption_file = os.path.join(temp_dir, f"caption_{i}.txt")
            with open(caption_file, "w", encoding="utf-8") as f:
                f.write(wrapped_caption)
            
            # Video filter for Ken Burns effect and caption overlay
            video_filter = (
                # Scale and crop with slow pan (Ken Burns effect)
                "scale=-1:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:x=(in_w-1080)*(t/{IMAGE_DURATION}):y=0,"
                
                # Add caption text using textfile instead of inline text
                f"drawtext=textfile='{caption_file}':"
                "fontfile=/System/Library/Fonts/Supplemental/Verdana.ttf:"
                "fontsize=64:fontcolor=white:"
                "box=1:boxcolor=black@0.6:boxborderw=10:"
                "line_spacing=10:"
                "x=(w-text_w)/2:"  # center horizontally
                "y=h-text_h-150:"  # position near bottom
                "alpha=1"
            )
            
            # FFmpeg command to create segment
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", image_path,
                "-vf", video_filter,
                "-t", str(IMAGE_DURATION),
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                segment_path
            ]
            
            print(f"\nCreating segment {i+1}/{len(images)}...")
            subprocess.run(cmd, check=True)
            segment_files.append(segment_path)

        # Step 2: Concatenate video segments
        print("\nCombining video segments...")
        concat_list_path = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for segment in segment_files:
                f.write(f"file '{segment}'\n")

        silent_video_path = os.path.join(temp_dir, "silent_video.mp4")
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            silent_video_path
        ]
        subprocess.run(concat_cmd, check=True)

        # Step 3: Create audio mix (voiceovers + background music)
        print("\nMixing audio...")
        final_audio_path = os.path.join(temp_dir, "final_audio.mp3")
        
        # Build complex audio filter chain
        filter_parts = []
        delayed_refs = []
        
        # Process each voiceover file
        for i, voiceover in enumerate(voiceovers):
            # Calculate delay in milliseconds based on segment position
            start_ms = i * IMAGE_DURATION * 1000
            
            # Adjust each voiceover's timing and volume
            filter_parts.append(
                f"[{i}:a]asetpts=PTS-STARTPTS,"
                f"volume=2.5,adelay={start_ms}|{start_ms}[vo_delayed{i}];"
            )
            delayed_refs.append(f"[vo_delayed{i}]")

        # Mix all voiceovers together
        filter_parts.append(
            "".join(delayed_refs) +
            f"amix=inputs={len(voiceovers)}:duration=longest:normalize=0[voicemix];"
            "[voicemix]volume=2.5[voicemix_loud];"
        )
        
        # Process background music
        music_index = len(voiceovers)
        filter_parts.append(
            f"[{music_index}:a]"
            "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            "volume=0.8,"
            f"afade=t=out:st={total_duration-1}:d=1[musicvol];"
            f"[musicvol]atrim=0:{total_duration},asetpts=PTS-STARTPTS[music];"
        )
        
        # Combine voiceovers with background music
        filter_parts.append(
            f"[voicemix_loud]apad=pad_dur={total_duration}[voicepadded];"
            "[voicepadded][music]amix=inputs=2:duration=first:normalize=0[afinal]"
        )
        
        # Build and run FFmpeg command for audio mixing
        audio_filter_complex = "".join(filter_parts)
        audio_cmd = ["ffmpeg", "-y"]
        
        # Add inputs for all voiceovers
        for voiceover in voiceovers:
            audio_cmd.extend(["-i", voiceover])
            
        # Add input for background music
        audio_cmd.extend(["-i", music_file])
        
        # Complete the command
        audio_cmd.extend([
            "-filter_complex", audio_filter_complex,
            "-map", "[afinal]",
            "-c:a", "mp3",
            final_audio_path
        ])
        
        subprocess.run(audio_cmd, check=True)

        # Step 4: Combine silent video with final audio mix
        print("\nCombining video and audio...")
        final_cmd = [
            "ffmpeg", "-y",
            "-i", silent_video_path,
            "-i", final_audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_video
        ]
        subprocess.run(final_cmd, check=True)

        print(f"\nVideo successfully created: {output_video}")

    finally:
        # Clean up temporary files
        print(f"Cleaning up temporary files...")
        shutil.rmtree(temp_dir, ignore_errors=True)

#2. Generate Voiceovers
def generate_voiceovers(messages: list[str]) -> list[str]:
    """
    Generate voiceovers for a list of messages using ElevenLabs API.
    
    Args:
        messages: List of messages to convert to speech
        
    Returns:
        List of file paths to the generated audio files
    """
    os.makedirs("voiceovers", exist_ok=True)
    
    # Check for existing files first
    audio_file_paths = []
    for i in range(1, len(messages) + 1):
        file_path = f"voiceovers/voiceover_{i}.mp3"
        if os.path.exists(file_path):
            audio_file_paths.append(file_path)
            
    # If all files exist, return them
    if len(audio_file_paths) == len(messages):
        print("All voiceover files already exist. Skipping generation.")
        return audio_file_paths
        
    # Generate missing files one by one
    audio_file_paths = []
    for i, message in enumerate(messages, 1):
        try:
            save_file_path = f"voiceovers/voiceover_{i}.mp3"
            if os.path.exists(save_file_path):
                print(f"File {save_file_path} already exists, skipping generation.")
                audio_file_paths.append(save_file_path)
                continue

            print(f"Generating voiceover {i}/{len(messages)}...")
            
            # Generate audio with ElevenLabs
            response = elevenlabs_client.text_to_speech.convert(
                text=message,
                voice_id=voice_id,
                model_id="eleven_multilingual_v2",
                output_format="mp3_22050_32",
            )
            
            # Collect audio chunks
            audio_chunks = []
            for chunk in response:
                if chunk:
                    audio_chunks.append(chunk)
            
            # Save to file
            with open(save_file_path, "wb") as f:
                for chunk in audio_chunks:
                    f.write(chunk)
                        
            print(f"Voiceover {i} generated successfully")
            audio_file_paths.append(save_file_path)
        
        except Exception as e:
            print(f"Error generating voiceover for message: {message}. Error: {e}")
            continue
            
    return audio_file_paths

#3. Generate Images
def generate_images(prompts: list[str]):
    """
    Generate images based on text prompts using Stability AI API.
    
    Args:
        prompts: List of text prompts to generate images from
    """
    seed = 42
    output_dir = "images"
    os.makedirs(output_dir, exist_ok=True)

    # API config
    stability_api_url = "https://api.stability.ai/v2beta/stable-image/generate/core"
    headers = {
        "Authorization": f"Bearer {STABILITY_API_KEY}",
        "Accept": "image/*"
    }

    for i, prompt in enumerate(prompts, 1):
        print(f"Generating image {i}/{len(prompts)} for prompt: {prompt}")

        # Skip if image already exists
        image_path = os.path.join(output_dir, f"image_{i}.webp")
        if not os.path.exists(image_path):
            # Prepare request payload
            payload = {
                "prompt": (None, prompt),
                "output_format": (None, "webp"),
                "height": (None, "1920"),
                "width": (None, "1080"),
                "seed": (None, str(seed))
            }

            try:
                response = requests.post(stability_api_url, headers=headers, files=payload)
                if response.status_code == 200:
                    with open(image_path, "wb") as image_file:
                        image_file.write(response.content)
                    print(f"Image saved to {image_path}")
                else:
                    print(f"Error generating image {i}: {response.json()}")
            except Exception as e:
                print(f"Error generating image {i}: {e}")