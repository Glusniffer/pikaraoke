import hashlib
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlparse
import qrcode


def filename_from_path(file_path, remove_youtube_id=True):
    rc = os.path.basename(file_path)
    rc = os.path.splitext(rc)[0]
    if remove_youtube_id:
        try:
            rc = rc.split("---")[0]  # removes youtube id if present
        except TypeError:
            # more fun python 3 hacks
            rc = rc.split("---".encode("utf-8", "ignore"))[0]
    return rc


def arg_path_parse(path):
    if type(path) == list:
        return " ".join(path)
    else:
        return path


def hash_dict(d):
    return hashlib.md5(
        json.dumps(d, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
    ).hexdigest()


def get_default_dl_dir(platform):
    songs_path = "~/pikaraoke-songs"
    legacy_songs_path = "~/pikaraoke/songs"

    if platform == "raspberry_pi":
        return songs_path
    if platform == "windows":
        legacy_directory = os.path.expanduser(legacy_songs_path)
        if os.path.exists(legacy_directory):
            return legacy_directory

        return songs_path

    if os.path.exists(legacy_songs_path):
        return legacy_songs_path
    else:
        return songs_path


def get_default_youtube_dl_path(platform):
    if platform == "windows":
        return os.path.join(os.path.dirname(__file__), "..\.venv\Scripts\yt-dlp.exe")
    return os.path.join(os.path.dirname(__file__), "../.venv/bin/yt-dlp")


def get_available_songs(logger, path):
    logger.info(f"Fetching available songs in: {path}")
    types = [".mp4", ".mp3", ".zip", ".mkv", ".avi", ".webm", ".mov"]
    files_grabbed = []
    P = Path(path)
    for file in P.rglob("*.*"):
        base, ext = os.path.splitext(file.as_posix())
        if ext.lower() in types:
            if os.path.isfile(file.as_posix()):
                logger.debug(f"adding song: {file.name}")
                files_grabbed.append(file.as_posix())

    return sorted(files_grabbed, key=lambda f: str.lower(os.path.basename(f)))


# Other ip-getting methods are unreliable and sometimes return 127.0.0.1
# https://stackoverflow.com/a/28950776
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


def is_admin(config: dict, cookies: dict):
    if config.get("admin_password") == None:
        return True
    return cookies.get("admin") == config.get("admin_password")


def generate_qr_code(logger, config: dict):
    logger.info("Generating URL QR code")
    qr = qrcode.QRCode(
        version=1,
        box_size=1,
        border=4,
    )
    qr.add_data(config.get("url"))
    qr_code_path = os.path.join(
        os.path.dirname(__file__), "..", "static", "images", "qrcode.png"
    )
    qr.make_image().save(qr_code_path)
    return qr_code_path

def make_url(logger, config):
    logger.info("IP address (for QR code and splash screen): " + config.get("ip"))

    if config.get("url") != None:
        logger.info(f"Overriding URL with {config.get("url")}")
    else:
        if config.get("prefer_hostname"):
            config["url"] = f"http://{socket.getfqdn().lower()}:{config.get("port")}"
        else:
            config["url"] = f"http://{config.get("ip")}:{config.get("port")}"
    
    url_parsed = urlparse(config.get("url"))
    config["ffmpeg_url"] = config["ffmpeg_url"] if config["ffmpeg_url"] else f"{url_parsed.scheme}://{url_parsed.hostname}:{config.get("ffmpeg_port")}"

def filename_from_path(file_path):
    rc = os.path.basename(file_path)
    rc = os.path.splitext(rc)[0]
    rc = rc.split("---")[0]  # removes youtube id if present
    return rc