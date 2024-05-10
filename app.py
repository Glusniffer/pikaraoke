# region Imports
import argparse
import datetime
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import quote, unquote

import cherrypy
import flask_babel
import psutil
from flask import (
    Flask,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_babel import Babel
from flask_paginate import Pagination, get_page_parameter

import karaoke
from constant.constants import LANGUAGES, VERSION
from helper.utils import (
    arg_path_parse,
    filename_from_path,
    generate_qr_code,
    get_available_songs,
    get_default_dl_dir,
    get_default_youtube_dl_path,
    hash_dict,
    is_admin,
    get_ip,
    make_url,
)
from helper.song_queue import enqueue as add_song_to_queue, is_song_in_queue, rename, delete
from helper.youtube import get_youtubedl_version, upgrade_youtubedl, get_search_results, get_karaoke_search_results, download_video
from lib.get_platform import get_platform

# endregion Imports

_ = flask_babel.gettext

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.jinja_env.globals.update(filename_from_path=filename_from_path)
app.jinja_env.globals.update(url_escape=quote)
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
babel = Babel(app)
platform = get_platform()
is_raspberry_pi = platform == "raspberry_pi"
k = None
config = {
    "site_name": "PiKaraoke",
    "platform": get_platform(),
    "port": 5555,
    "ffmpeg_port": 5556,
    "download_path": get_default_dl_dir(get_platform()),
    "youtubedl_path": get_default_youtube_dl_path(get_platform()),
    "volume": 0.85,
    "splash_delay": 3,
    "screensaver_timeout": 300,
    "log_level": 10,
    "hide_url": False,
    "prefer_hostname": True,
    "hide_raspiwifi_instructions": False,
    "high_quality": False,
    "logo_path": None,
    "url": None,
    "ffmpeg_url": None,
    "hide_overlay": False,
    "admin_password": None,
    "available_songs": [],
    "queue": [],
}

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=int(config.get("log_level")),
)

# Localization
@babel.localeselector
def get_locale():
    """Select the language to display the webpage in based on the Accept-Language header"""
    return request.accept_languages.best_match(LANGUAGES.keys())


# region Routes
@app.route("/")
def home():
    return render_template(
        "home.html",
        site_title=config.get("site_name"),
        title="Home",
        transpose_value=k.now_playing_transpose,
        admin=is_admin(config, request.cookies),
    )


@app.route("/auth", methods=["POST"])
def auth():
    d = request.form.to_dict()
    p = d["admin-password"]
    if p == config.get("admin_password"):
        resp = make_response(redirect("/"))
        expire_date = datetime.datetime.now()
        expire_date = expire_date + datetime.timedelta(days=90)
        resp.set_cookie("admin", config.get("admin_password"), expires=expire_date)
        # MSG: Message shown after logging in as admin successfully
        flash(_("Admin mode granted!"), "is-success")
    else:
        resp = make_response(redirect(url_for("login")))
        # MSG: Message shown after failing to login as admin
        flash(_("Incorrect admin password!"), "is-danger")
    return resp


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/logout")
def logout():
    resp = make_response(redirect("/"))
    resp.set_cookie("admin", "")
    flash("Logged out of admin mode!", "is-success")
    return resp


@app.route("/nowplaying")
def nowplaying():
    try:
        if len(config.get("queue")) >= 1:
            next_song = config.get("queue")[0]["title"]
            next_user = config.get("queue")[0]["user"]
        else:
            next_song = None
            next_user = None

        rc = {
            "now_playing": k.now_playing,
            "now_playing_user": k.now_playing_user,
            "now_playing_command": k.now_playing_command,
            "up_next": next_song,
            "next_user": next_user,
            "now_playing_url": k.now_playing_url,
            "is_paused": k.is_paused,
            "transpose_value": k.now_playing_transpose,
            "volume": k.volume,
        }
        # used to detect changes in the now playing data
        rc["hash"] = hash_dict(rc)
        return json.dumps(rc)
    except Exception as e:
        logging.error(
            "Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e)
        )
        return ""


# Call this after receiving a command in the front end
@app.route("/clear_command")
def clear_command():
    k.now_playing_command = None
    return ""


@app.route("/queue")
def queue():
    return render_template(
        "queue.html",
        queue=config.get("queue"),
        site_title=config.get("site_name"),
        title="Queue",
        admin=is_admin(config, request.cookies),
    )


@app.route("/get_queue")
def get_queue():
    if len(config.get("queue")) >= 1:
        return json.dumps(config.get("queue"))
    else:
        return json.dumps([])


@app.route("/queue/addrandom", methods=["GET"])
def add_random():
    amount = int(request.args["amount"])
    rc = k.queue_add_random(amount)
    if rc:
        flash("Added %s random tracks" % amount, "is-success")
    else:
        flash("Ran out of songs!", "is-warning")
    return redirect(url_for("queue"))


@app.route("/queue/edit", methods=["GET"])
def queue_edit():
    action = request.args["action"]
    if action == "clear":
        k.queue_clear()
        flash("Cleared the queue!", "is-warning")
        return redirect(url_for("queue"))

    song = request.args["song"]
    song = unquote(song)
    if action == "down":
        result = k.queue_edit(song, "down")
        if result:
            flash("Moved down in queue: " + song, "is-success")
        else:
            flash("Error moving down in queue: " + song, "is-danger")
    elif action == "up":
        result = k.queue_edit(song, "up")
        if result:
            flash("Moved up in queue: " + song, "is-success")
        else:
            flash("Error moving up in queue: " + song, "is-danger")
    elif action == "delete":
        result = k.queue_edit(song, "delete")
        if result:
            flash("Deleted from queue: " + song, "is-success")
        else:
            flash("Error deleting from queue: " + song, "is-danger")

    return redirect(url_for("queue"))


@app.route("/enqueue", methods=["POST", "GET"])
def enqueue():
    if "song" in request.args:
        song = request.args["song"]
    else:
        d = request.form.to_dict()
        song = d["song-to-add"]
    if "user" in request.args:
        user = request.args["user"]
    else:
        d = request.form.to_dict()
        user = d["song-added-by"]
    rc = add_song_to_queue(logging, config, song, user)
    song_title = filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})


@app.route("/skip")
def skip():
    k.skip()
    return redirect(url_for("home"))


@app.route("/pause")
def pause():
    k.pause()
    return redirect(url_for("home"))


@app.route("/transpose/<semitones>", methods=["GET"])
def transpose(semitones):
    k.transpose_current(int(semitones))
    return redirect(url_for("home"))


@app.route("/restart")
def restart():
    k.restart()
    return redirect(url_for("home"))


@app.route("/volume/<volume>")
def volume(volume):
    k.volume_change(float(volume))
    return redirect(url_for("home"))


@app.route("/vol_up")
def vol_up():
    k.vol_up()
    return redirect(url_for("home"))


@app.route("/vol_down")
def vol_down():
    k.vol_down()
    return redirect(url_for("home"))


@app.route("/search", methods=["GET"])
def search():
    if "search_string" in request.args:
        search_string = request.args["search_string"]
        if "non_karaoke" in request.args and request.args["non_karaoke"] == "true":
            search_results = get_search_results(logging, config, search_string)
        else:
            search_results = get_karaoke_search_results(logging, config, search_string)
    else:
        search_string = None
        search_results = None
    return render_template(
        "search.html",
        site_title=config.get("site_name"),
        title="Search",
        songs=k.available_songs,
        search_results=search_results,
        search_string=search_string,
    )


@app.route("/autocomplete")
def autocomplete():
    q = request.args.get("q").lower()
    result = []
    for each in config.get("available_songs"):
        if q in each.lower():
            result.append(
                {
                    "path": each,
                    "fileName": filename_from_path(each),
                    "type": "autocomplete",
                }
            )
    response = app.response_class(
        response=json.dumps(result), mimetype="application/json"
    )
    return response


@app.route("/browse", methods=["GET"])
def browse():
    search = False
    q = request.args.get("q")
    if q:
        search = True
    page = request.args.get(get_page_parameter(), type=int, default=1)

    available_songs = config.get("available_songs")

    letter = request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = filename_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = filename_from_path(song).lower()
                if f.startswith(letter.lower()):
                    result.append(song)
        available_songs = result

    if "sort" in request.args and request.args["sort"] == "date":
        songs = sorted(available_songs, key=lambda x: os.path.getctime(x))
        songs.reverse()
        sort_order = "Date"
    else:
        songs = available_songs
        sort_order = "Alphabetical"

    results_per_page = 500
    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(songs),
        search=search,
        record_name="songs",
        per_page=results_per_page,
    )
    start_index = (page - 1) * (results_per_page - 1)
    return render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        site_title=config.get("site_name"),
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(config, request.cookies),
    )


@app.route("/download", methods=["POST"])
def download():
    d = request.form.to_dict()
    song = d["song-url"]
    user = d["song-added-by"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=download_video, args=[logging, config, song, queue, user])
    t.daemon = True
    t.start()

    flash_message = (
        "Download started: '"
        + song
        + "'. This may take a couple of minutes to complete. "
    )

    if queue:
        flash_message += "Song will be added to queue."
    else:
        flash_message += 'Song will appear in the "available songs" list.'
    flash(flash_message, "is-info")
    return redirect(url_for("search"))


@app.route("/qrcode")
def qrcode():
    return send_file(config.get("qr_code_path"), mimetype="image/png")


@app.route("/logo")
def logo():
    return send_file(k.logo_path, mimetype="image/png")


@app.route("/end_song", methods=["GET"])
def end_song():
    k.end_song()
    return "ok"


@app.route("/start_song", methods=["GET"])
def start_song():
    k.start_song()
    return "ok"


@app.route("/files/delete", methods=["GET"])
def delete_file():
    if "song" in request.args:
        song_path = request.args["song"]
        if song_path in config.get("queue"):
            flash(
                "Error: Can't delete this song because it is in the current queue: "
                + song_path,
                "is-danger",
            )
        else:
            delete(logging, config, song_path)
            flash("Song deleted: " + song_path, "is-warning")
    else:
        flash("Error: No song parameter specified!", "is-danger")
    return redirect(url_for("browse"))


@app.route("/files/edit", methods=["GET", "POST"])
def edit_file():
    queue_error_msg = "Error: Can't edit this song because it is in the current queue: "
    if "song" in request.args:
        song_path = request.args["song"]
        # print "SONG_PATH" + song_path
        if song_path in config.get("queue"):
            flash(queue_error_msg + song_path, "is-danger")
            return redirect(url_for("browse"))
        else:
            return render_template(
                "edit.html",
                site_title=config.get("site_name"),
                title="Song File Edit",
                song=song_path.encode("utf-8", "ignore"),
            )
    else:
        d = request.form.to_dict()
        if "new_file_name" in d and "old_file_name" in d:
            new_name = d["new_file_name"]
            old_name = d["old_file_name"]
            if is_song_in_queue(config, old_name):
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + song_path, "is-danger")
            else:
                # check if new_name already exist
                file_extension = os.path.splitext(old_name)[1]
                if os.path.isfile(
                    os.path.join(config.get("download_path"), new_name + file_extension)
                ):
                    flash(
                        "Error Renaming file: '%s' to '%s'. Filename already exists."
                        % (old_name, new_name + file_extension),
                        "is-danger",
                    )
                else:
                    rename(logging, config, old_name, new_name)
                    flash(
                        "Renamed file: '%s' to '%s'." % (old_name, new_name),
                        "is-warning",
                    )
        else:
            flash("Error: No filename parameters were specified!", "is-danger")
        return redirect(url_for("browse"))


@app.route("/splash")
def splash():
    return render_template(
        "splash.html",
        blank_page=True,
        url=config.get("url"),
        hide_url=config.get("hide_url"),
        hide_overlay=config.get("hide_overlay"),
        screensaver_timeout=config.get("screensaver_timeout"),
    )


@app.route("/info")
def info():
    url = config.get("url")

    # cpu
    cpu = str(psutil.cpu_percent()) + "%"

    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available / 1024.0 / 1024.0, 1)
    total = round(memory.total / 1024.0 / 1024.0, 1)
    memory = (
        str(available)
        + "MB free / "
        + str(total)
        + "MB total ( "
        + str(memory.percent)
        + "% )"
    )

    # disk
    disk = psutil.disk_usage("/")
    # Divide from Bytes -> KB -> MB -> GB
    free = round(disk.free / 1024.0 / 1024.0 / 1024.0, 1)
    total = round(disk.total / 1024.0 / 1024.0 / 1024.0, 1)
    disk = (
        str(free)
        + "GB free / "
        + str(total)
        + "GB total ( "
        + str(disk.percent)
        + "% )"
    )

    return render_template(
        "info.html",
        site_title=config.get("site_name"),
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        youtubedl_version=config.get("youtubedl_version"),
        is_pi=is_raspberry_pi,
        pikaraoke_version=VERSION,
        admin=is_admin(config, request.cookies),
        admin_enabled=config.get("admin_password") != None,
    )


@app.route("/update_ytdl")
def update_ytdl():
    if is_admin(config, request.cookies):
        flash(
            "Updating youtube-dl! Should take a minute or two... ",
            "is-warning",
        )
        # th = threading.Thread(target=upgrade_youtubedl, args=[logging, config])
        # th.start()
        result = upgrade_youtubedl(logging, config)
        flash(result, "is-success")
    else:
        flash("You don't have permission to update youtube-dl", "is-danger")
    return redirect(url_for("info"))


@app.route("/refresh")
def refresh():
    if is_admin(config, request.cookies):
        # k.get_available_songs()
        config["available_songs"] = get_available_songs(logging, config.get("download_path"))
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("browse"))


@app.route("/quit")
def quit():
    if is_admin(config, request.cookies):
        flash("Quitting pikaraoke now!", "is-warning")
        th = threading.Thread(target=delayed_halt, args=[0])
        th.start()
    else:
        flash("You don't have permission to quit", "is-danger")
    return redirect(url_for("home"))


@app.route("/shutdown")
def shutdown():
    if is_admin(config, request.cookies):
        flash("Shutting down system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[1])
        th.start()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("home"))


@app.route("/reboot")
def reboot():
    if is_admin(config, request.cookies):
        flash("Rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[2])
        th.start()
    else:
        flash("You don't have permission to Reboot", "is-danger")
    return redirect(url_for("home"))


@app.route("/expand_fs")
def expand_fs():
    if is_admin(config, request.cookies) and is_raspberry_pi:
        flash("Expanding filesystem and rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[3])
        th.start()
    elif platform != "raspberry_pi":
        flash("Cannot expand fs on non-raspberry pi devices!", "is-danger")
    else:
        flash("You don't have permission to resize the filesystem", "is-danger")
    return redirect(url_for("home"))


# endregion Routes


# Delay system commands to allow redirect to render first
def delayed_halt(cmd):
    time.sleep(1.5)
    k.queue_clear()
    cherrypy.engine.stop()
    cherrypy.engine.exit()
    k.stop()
    if cmd == 0:
        sys.exit()
    if cmd == 1:
        os.system("shutdown now")
    if cmd == 2:
        os.system("reboot")
    if cmd == 3:
        process = subprocess.Popen(["raspi-config", "--expand-rootfs"])
        process.wait()
        os.system("reboot")


# Handle sigterm, apparently cherrypy won't shut down without explicit handling
signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())


def process_cli_args():

    # region CLI Arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--port",
        help=f"Desired http port (default: {config.get("port")})",
        default=config.get("port"),
        required=False,
        type=int,
    )
    parser.add_argument(
        "-f",
        "--ffmpeg-port",
        help=f"Desired ffmpeg port. This is where video stream URLs will be pointed (default: {config.get("ffmpeg_port")})",
        default=config.get("ffmpeg_port"),
        required=False,
        type=int,
    )
    parser.add_argument(
        "-d",
        "--download-path",
        # nargs="+",
        help=f"Desired path for downloaded songs. (default: {config.get("download_path")})",
        default=config.get("download_path"),
        required=False,
        type=str,
    )
    # parser.add_argument(
    #     "--window-size",
    #     help="Desired window geometry in pixels, specified as width,height",
    #     default=0,
    #     required=False,
    # )
    parser.add_argument(
        "-y",
        "--youtubedl-path",
        # nargs="+",
        help=f"Path of youtube-dl. (default: {config.get("youtubedl_path")})",
        default=config.get("youtubedl_path"),
        required=False,
        type=str,
    )
    parser.add_argument(
        "-v",
        "--volume",
        help=f"Set initial player volume. A value between 0 and 1. (default: {config.get("volume")})",
        default=config.get("volume"),
        required=False,
        type=int,
    )
    parser.add_argument(
        "-s",
        "--splash-delay",
        help=f"Delay during splash screen between songs (in secs). (default: {config.get("splash_delay")})",
        default=config.get("splash_delay"),
        required=False,
        type=int,
    )
    parser.add_argument(
        "-t",
        "--screensaver-timeout",
        help=f"Delay before the screensaver begins (in secs). (default: {config.get("screensaver_timeout")})",
        default=config.get("screensaver_timeout"),
        required=False,
        type=int,
    )
    parser.add_argument(
        "-l",
        "--log-level",
        help=f"Logging level int value (DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50). (default: {config.get("log_level")})",
        default=config.get("log_level"),
        required=False,
        type=int,
    )
    parser.add_argument(
        "--hide-url",
        action="store_true",
        help="Hide URL and QR code from the splash screen.",
        required=False,
    )
    parser.add_argument(
        "--prefer-hostname",
        action="store_true",
        help=f"Use the local hostname instead of the IP as the connection URL. Use at your discretion: mDNS is not guaranteed to work on all LAN configurations. Defaults to {config.get("prefer_hostname")}",
        required=False,
    )
    parser.add_argument(
        "--hide-raspiwifi-instructions",
        action="store_true",
        help="Hide RaspiWiFi setup instructions from the splash screen.",
        required=False,
    )
    parser.add_argument(
        "--high-quality",
        action="store_true",
        help="Download higher quality video. Note: requires ffmpeg and may cause CPU, download speed, and other performance issues",
        required=False,
    )
    parser.add_argument(
        "--logo-path",
        # nargs="+",
        help="Path to a custom logo image file for the splash screen. Recommended dimensions ~ 2048x1024px",
        default=None,
        required=False,
        type=str,
    )
    parser.add_argument(
        "-u",
        "--url",
        help="Override the displayed IP address with a supplied URL. This argument should include port, if necessary",
        default=None,
        required=False,
        type=str,
    )
    parser.add_argument(
        "-m",
        "--ffmpeg-url",
        help="Override the ffmpeg address with a supplied URL.",
        default=None,
        required=False,
        type=str,
    )
    parser.add_argument(
        "--hide-overlay",
        action="store_true",
        help="Hide overlay that shows on top of video with pikaraoke QR code and IP",
        required=False,
    )
    parser.add_argument(
        "--admin-password",
        help="Administrator password, for locking down certain features of the web UI such as queue editing, player controls, song editing, and system shutdown. If unspecified, everyone is an admin.",
        default=None,
        required=False,
        type=str,
    )
    # endregion CLI Arguments

    result = parser.parse_args().__dict__
    result["default_logo_path"] = os.path.join(
        os.path.dirname(__file__), "static", "images", "logo.png"
    )
    result["youtubedl_version"] = get_youtubedl_version(result.get("youtubedl_path"))
    result["qr_code_path"] = generate_qr_code(logging, config)
    result["ip"] = get_ip()
    return result


def run():
    global k, config

    config = {**config, **process_cli_args()}
    make_url(logging, config)
    config["available_songs"] = get_available_songs(logging, config.get("download_path"))


    # check if required binaries exist
    if not os.path.isfile(config.get("youtubedl_path")):
        logging.error(f"Youtube-dl path not found! {config.get("youtubedl_path")}")
        sys.exit(1)

    # setup/create download directory if necessary
    dl_path = os.path.expanduser(arg_path_parse(config.get("download_path")))
    if not dl_path.endswith("/"):
        dl_path += "/"
    if not os.path.exists(dl_path):
        logging.info(f"Creating download path: {dl_path}")
        os.makedirs(dl_path)

    k = karaoke.Karaoke(
        port=config.get("port"),
        ffmpeg_port=config.get("ffmpeg_port"),
        download_path=dl_path,
        youtubedl_path=arg_path_parse(config.get("youtubedl_path")),
        splash_delay=config.get("splash_delay"),
        log_level=config.get("log_level"),
        volume=float(config.get("volume", 0.85)),
        hide_url=config.get("hide_url"),
        hide_raspiwifi_instructions=config.get("hide_raspiwifi_instructions"),
        high_quality=config.get("high_quality"),
        logo_path=arg_path_parse(config.get("logo_path")),
        hide_overlay=config.get("hide_overlay"),
        screensaver_timeout=config.get("screensaver_timeout"),
        url=config.get("url"),
        ffmpeg_url=config.get("ffmpeg_url"),
        prefer_hostname=config.get("prefer_hostname"),
        config=config
    )

    # Start the CherryPy WSGI web server
    cherrypy.tree.graft(app, "/")
    # Set the configuration of the web server
    cherrypy.config.update(
        {
            "engine.autoreload.on": False,
            "log.screen": True,
            "server.socket_port": int(config.get("port")),
            "server.socket_host": "0.0.0.0",
            "server.thread_pool": 100,
        }
    )
    cherrypy.engine.start()
    k.run()

    cherrypy.engine.exit()
    sys.exit()


if __name__ == "__main__":
    run()
