import os
import subprocess
import json
from subprocess import STDOUT, CalledProcessError, check_output
from unidecode import unidecode
from .utils import get_available_songs
from .song_queue import enqueue

def get_default_youtube_dl_path(platform):
    if platform == "windows":
        return os.path.join(os.path.dirname(__file__), "..\.venv\Scripts\yt-dlp.exe")
    return os.path.join(os.path.dirname(__file__), "../.venv/bin/yt-dlp")


def get_youtubedl_version(youtubedl_path):
    youtubedl_version = (
        check_output([youtubedl_path, "--version"]).strip().decode("utf8")
    )
    return youtubedl_version


def upgrade_youtubedl(logger, config):
    logger.info(
        f"Upgrading youtube-dl, current version: %s" % config.get("youtubedl_version")
    )
    try:
        output = (
            check_output([config.get("youtubedl_path"), "-U"], stderr=STDOUT)
            .decode("utf8")
            .strip()
        )
    except CalledProcessError as e:
        output = e.output.decode("utf8")

    logger.info(output)
    if "You installed yt-dlp with pip or using the wheel from PyPi" in output:
        try:
            logger.info("Attempting youtube-dl upgrade via pip3...")
            output = check_output(["pip3", "install", "--upgrade", "yt-dlp"]).decode(
                "utf8"
            )
        except FileNotFoundError:
            logger.info("Attempting youtube-dl upgrade via pip...")
            output = check_output(["pip", "install", "--upgrade", "yt-dlp"]).decode(
                "utf8"
            )
        logger.info(output)
    youtubedl_version = get_youtubedl_version(config.get("youtubedl_path"))
    logger.info("Done. New version: %s" % youtubedl_version)
    return "Done. New version: %s" % youtubedl_version


def get_search_results(logger, config, textToSearch):
    logger.info("Searching YouTube for: " + textToSearch)
    num_results = 10
    yt_search = 'ytsearch%d:"%s"' % (num_results, unidecode(textToSearch))
    cmd = [config.get("youtubedl_path"), "-j", "--no-playlist", "--flat-playlist", yt_search]
    logger.debug("Youtube-dl search command: " + " ".join(cmd))
    try:
        output = check_output(cmd).decode("utf-8", "ignore")
        logger.debug("Search results: " + output)
        rc = []
        for each in output.split("\n"):
            if len(each) > 2:
                j = json.loads(each)
                if (not "title" in j) or (not "url" in j):
                    continue
                rc.append([j["title"], j["url"], j["id"]])
        return rc
    except Exception as e:
        logger.debug("Error while executing search: " + str(e))
        raise e

def get_karaoke_search_results(logger, config, songTitle):
    return get_search_results(logger, config, songTitle + " karaoke")


def get_youtube_id_from_url(logger, url):
    s = url.split("watch?v=")
    if len(s) == 2:
        return s[1]
    else:
        logger.error("Error parsing youtube id from url: " + url)
        return None

def find_song_by_youtube_id(logger, config, youtube_id):
    for each in config.get("available_songs"):
        if youtube_id in each:
            return each
    logger.error(f"No available song found with youtube id: {youtube_id}")
    return None

def download_video(logger, config, video_url, add_to_queue=False, user="Pikaraoke"):
    logger.info("Downloading video: " + video_url)
    dl_path = config.get("download_path") + "%(title)s---%(id)s.%(ext)s"
    file_quality = (
        "bestvideo[ext!=webm][height<=1080]+bestaudio[ext!=webm]/best[ext!=webm]"
        if config.get("high_quality")
        else "mp4"
    )
    cmd = [config.get("youtubedl_path"), "-f", file_quality, "-o", dl_path, video_url]
    logger.debug("Youtube-dl command: " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        logger.error("Error code while downloading, retrying once...")
        rc = subprocess.call(cmd)  # retry once. Seems like this can be flaky
    if rc != 0:
        logger.error(f"Error downloading song: {video_url}")
        return
    
    logger.debug("Song successfully downloaded: " + video_url)
    config["available_songs"] = get_available_songs(logger, config.get("download_path"))
    if add_to_queue:
        youtube_id = get_youtube_id_from_url(logger, video_url)
        song = find_song_by_youtube_id(logger, config, youtube_id)
        if song:
            enqueue(logger, config, song, user)
            return
        
        logger.error(f"Error queueing song: {video_url}")

