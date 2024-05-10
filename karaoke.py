import contextlib
# import json
import logging
import os
import random
# import socket
import subprocess
import time
# from pathlib import Path
from queue import Empty, Queue
# from subprocess import CalledProcessError, check_output
from threading import Thread
# from urllib.parse import urlparse

import ffmpeg
# from unidecode import unidecode

from helper.utils import filename_from_path
from lib.file_resolver import FileResolver
from lib.get_platform import get_platform


# Support function for reading  lines from ffmpeg stderr without blocking
def enqueue_output(out, queue):
    for line in iter(out.readline, b""):
        queue.put(line)
    out.close()


def decode_ignore(input):
    return input.decode("utf-8", "ignore").strip()


class Karaoke:

    queue = []
    available_songs = []

    # These all get sent to the /nowplaying endpoint for client-side polling
    now_playing = None
    now_playing_filename = None
    now_playing_user = None
    now_playing_transpose = 0
    now_playing_url = None
    now_playing_command = None

    is_playing = False
    is_paused = True
    process = None
    qr_code_path = None
    base_path = os.path.dirname(__file__)
    volume = None
    loop_interval = 500  # in milliseconds
    default_logo_path = os.path.join(base_path, "static", "images", "logo.png")
    screensaver_timeout = 300  # in seconds

    ffmpeg_process = None

    def __init__(
        self,
        port=5555,
        ffmpeg_port=5556,
        download_path="/usr/lib/pikaraoke/songs",
        hide_url=False,
        hide_raspiwifi_instructions=False,
        high_quality=False,
        volume=0.85,
        log_level=logging.DEBUG,
        splash_delay=2,
        youtubedl_path="/usr/local/bin/yt-dlp",
        logo_path=None,
        hide_overlay=False,
        screensaver_timeout=300,
        url=None,
        ffmpeg_url=None,
        prefer_hostname=True,
        config: dict = {}
    ):

        # override with supplied constructor args if provided
        self.port = port
        self.ffmpeg_port = ffmpeg_port
        self.hide_url = hide_url
        self.hide_raspiwifi_instructions = hide_raspiwifi_instructions
        self.download_path = download_path
        self.high_quality = high_quality
        self.splash_delay = int(splash_delay)
        self.volume = volume
        self.youtubedl_path = youtubedl_path
        self.logo_path = self.default_logo_path if logo_path == None else logo_path
        self.hide_overlay = hide_overlay
        self.screensaver_timeout = screensaver_timeout
        self.url_override = url
        self.prefer_hostname = prefer_hostname
        self.config = config

        # other initializations
        self.platform = get_platform()
        self.screen = None

        logging.debug(
            f"""
    http port: {self.port}
    ffmpeg port {self.ffmpeg_port}
    hide URL: {self.hide_url}
    prefer hostname: {self.prefer_hostname}
    url override: {self.url_override}
    hide RaspiWiFi instructions: {self.hide_raspiwifi_instructions}
    splash_delay: {self.splash_delay}
    screensaver_timeout: {self.screensaver_timeout}
    high quality video: {self.high_quality}
    download path: {self.download_path}
    default volume: {self.volume}
    youtube-dl path: {self.youtubedl_path}
    logo path: {self.logo_path}
    log_level: {log_level}
    hide overlay: {self.hide_overlay}
"""
        )
        self.available_songs = config.get("available_songs")

    def play_file(self, file_path, semitones=0):
        logging.info(f"Playing file: {file_path} transposed {semitones} semitones")
        stream_uid = int(time.time())
        stream_url = f"{self.config.get("ffmpeg_url")}/{stream_uid}"
        # pass a 0.0.0.0 IP to ffmpeg which will work for both hostnames and direct IP access
        ffmpeg_url = f"http://0.0.0.0:{self.config.get("ffmpeg_port")}/{stream_uid}"

        pitch = 2 ** (
            semitones / 12
        )  # The pitch value is (2^x/12), where x represents the number of semitones

        try:
            fr = FileResolver(file_path)
        except Exception as e:
            logging.error("Error resolving file: " + str(e))
            self.config.get("queue").pop(0)
            return False

        # use h/w acceleration on pi
        default_vcodec = (
            "h264_v4l2m2m" if self.platform == "raspberry_pi" else "libx264"
        )
        # just copy the video stream if it's an mp4 or webm file, since they are supported natively in html5
        # otherwise use the default h264 codec
        vcodec = (
            "copy"
            if fr.file_extension == ".mp4" or fr.file_extension == ".webm"
            else default_vcodec
        )
        vbitrate = "5M"  # seems to yield best results w/ h264_v4l2m2m on pi, recommended for 720p.

        # copy the audio stream if no transposition, otherwise use the aac codec
        is_transposed = semitones != 0
        acodec = "aac" if is_transposed else "copy"
        input = ffmpeg.input(fr.file_path)
        audio = (
            input.audio.filter("rubberband", pitch=pitch)
            if is_transposed
            else input.audio
        )

        if fr.cdg_file_path != None:  # handle CDG files
            logging.info("Playing CDG/MP3 file: " + file_path)
            # copyts helps with sync issues, fps=25 prevents ffmpeg from needlessly encoding cdg at 300fps
            cdg_input = ffmpeg.input(fr.cdg_file_path, copyts=None)
            video = cdg_input.video.filter("fps", fps=25)
            # cdg is very fussy about these flags. pi needs to encode to aac and cant just copy the mp3 stream
            output = ffmpeg.output(
                audio,
                video,
                ffmpeg_url,
                vcodec=vcodec,
                acodec="aac",
                pix_fmt="yuv420p",
                listen=1,
                f="mp4",
                video_bitrate=vbitrate,
                movflags="frag_keyframe+default_base_moof",
            )
        else:
            video = input.video
            output = ffmpeg.output(
                audio,
                video,
                ffmpeg_url,
                vcodec=vcodec,
                acodec=acodec,
                listen=1,
                f="mp4",
                video_bitrate=vbitrate,
                movflags="frag_keyframe+default_base_moof",
            )

        args = output.get_args()
        logging.debug(f"COMMAND: ffmpeg " + " ".join(args))

        self.kill_ffmpeg()

        self.ffmpeg_process = output.run_async(pipe_stderr=True, pipe_stdin=True)

        # ffmpeg outputs everything useful to stderr for some insane reason!
        # prevent reading stderr from being a blocking action
        q = Queue()
        t = Thread(target=enqueue_output, args=(self.ffmpeg_process.stderr, q))
        t.daemon = True
        t.start()

        while self.ffmpeg_process.poll() is None:
            try:
                output = q.get_nowait()
                logging.debug("[FFMPEG] " + decode_ignore(output))
            except Empty:
                pass
            else:
                if "Stream #" in decode_ignore(output):
                    logging.debug("Stream ready!")
                    # Ffmpeg outputs "Stream #0" when the stream is ready to consume
                    self.now_playing = filename_from_path(file_path)
                    self.now_playing_filename = file_path
                    self.now_playing_transpose = semitones
                    self.now_playing_url = stream_url
                    self.now_playing_user = self.config.get("queue")[0]["user"]
                    self.is_paused = False
                    self.config.get("queue").pop(0)

                    # Keep logging output until the splash screen reports back that the stream is playing
                    max_retries = 100
                    while self.is_playing == False and max_retries > 0:
                        time.sleep(0.1)  # prevents loop from trying to replay track
                        try:
                            output = q.get_nowait()
                            logging.debug("[FFMPEG] " + decode_ignore(output))
                        except Empty:
                            pass
                        max_retries -= 1
                    if self.is_playing:
                        logging.debug("Stream is playing")
                        break
                    else:
                        logging.error(
                            "Stream was not playable! Run with debug logging to see output. Skipping track"
                        )
                        self.end_song()
                        break

    def kill_ffmpeg(self):
        logging.debug("Killing ffmpeg process")
        if self.ffmpeg_process:
            self.ffmpeg_process.kill()

    def start_song(self):
        logging.info(f"Song starting: {self.now_playing}")
        self.is_playing = True

    def end_song(self):
        logging.info(f"Song ending: {self.now_playing}")
        self.reset_now_playing()
        self.kill_ffmpeg()
        logging.debug("ffmpeg process killed")

    def transpose_current(self, semitones):
        logging.info(
            f"Transposing current song {self.now_playing} by {semitones} semitones"
        )
        # Insert the same song at the top of the queue with transposition
        self.enqueue(self.now_playing_filename, self.now_playing_user, semitones, True)
        self.skip()

    def is_file_playing(self):
        return self.is_playing

    def queue_add_random(self, amount):
        logging.info("Adding %d random songs to queue" % amount)
        songs = list(self.available_songs)  # make a copy
        if len(songs) == 0:
            logging.warn("No available songs!")
            return False
        i = 0
        while i < amount:
            r = random.randint(0, len(songs) - 1)
            if self.is_song_in_queue(songs[r]):
                logging.warn("Song already in queue, trying another... " + songs[r])
            else:
                self.enqueue(songs[r], "Randomizer")
                i += 1
            songs.pop(r)
            if len(songs) == 0:
                logging.warn("Ran out of songs!")
                return False
        return True

    def queue_clear(self):
        logging.info("Clearing queue!")
        self.queue = []
        self.skip()

    def queue_edit(self, song_name, action):
        index = 0
        song = None
        for each in self.queue:
            if song_name in each["file"]:
                song = each
                break
            else:
                index += 1
        if song == None:
            logging.error("Song not found in queue: " + song["file"])
            return False
        if action == "up":
            if index < 1:
                logging.warn("Song is up next, can't bump up in queue: " + song["file"])
                return False
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                return True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warn(
                    "Song is already last, can't bump down in queue: " + song["file"]
                )
                return False
            else:
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
                return True
        elif action == "delete":
            logging.info("Deleting song from queue: " + song["file"])
            del self.queue[index]
            return True
        else:
            logging.error("Unrecognized direction: " + action)
            return False

    def skip(self):
        if self.is_file_playing():
            logging.info("Skipping: " + self.now_playing)
            self.now_playing_command = "skip"
            return True
        else:
            logging.warning("Tried to skip, but no file is playing!")
            return False

    def pause(self):
        if self.is_file_playing():
            logging.info("Toggling pause: " + self.now_playing)
            self.now_playing_command = "pause"
            self.is_paused = not self.is_paused
            return True
        else:
            logging.warning("Tried to pause, but no file is playing!")
            return False

    def volume_change(self, vol_level):
        self.volume = vol_level
        logging.debug(f"Setting volume to: {self.volume}")
        if self.is_file_playing():
            self.now_playing_command = f"volume_change: {self.volume}"
        return True

    def vol_up(self):
        self.volume += 0.1
        logging.debug(f"Increasing volume by 10%: {self.volume}")
        if self.is_file_playing():
            self.now_playing_command = "vol_up"
            return True
        else:
            logging.warning("Tried to volume up, but no file is playing!")
            return False

    def vol_down(self):
        self.volume -= 0.1
        logging.debug(f"Decreasing volume by 10%: {self.volume}")
        if self.is_file_playing():
            self.now_playing_command = "vol_down"
            return True
        else:
            logging.warning("Tried to volume down, but no file is playing!")
            return False

    def restart(self):
        if self.is_file_playing():
            self.now_playing_command = "restart"
            return True
        else:
            logging.warning("Tried to restart, but no file is playing!")
            return False

    def stop(self):
        self.running = False

    def handle_run_loop(self):
        time.sleep(self.loop_interval / 1000)

    def reset_now_playing(self):
        self.now_playing = None
        self.now_playing_filename = None
        self.now_playing_user = None
        self.now_playing_url = None
        self.is_paused = True
        self.is_playing = False
        self.now_playing_transpose = 0

    def run(self):
        logging.info("Starting PiKaraoke!")
        logging.info(f"Connect the player host to: {self.config.get("url")}/splash")
        self.running = True
        while self.running:
            try:
                if not self.is_file_playing() and self.now_playing != None:
                    self.reset_now_playing()
                if len(self.config.get("queue")) > 0:
                    if not self.is_file_playing():
                        self.reset_now_playing()
                        i = 0
                        while i < (self.splash_delay * 1000):
                            self.handle_run_loop()
                            i += self.loop_interval
                        self.play_file(
                            self.config.get("queue")[0]["file"], self.config.get("queue")[0]["semitones"]
                        )
                self.handle_run_loop()
            except KeyboardInterrupt:
                logging.warn("Keyboard interrupt: Exiting pikaraoke...")
                self.running = False
