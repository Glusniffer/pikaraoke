from .utils import filename_from_path, get_available_songs
import contextlib
import os

def is_song_in_queue(config, song_path):
    for each in config.get("queue"):
        if each["file"] == song_path:
            return True
    return False

def enqueue(logger, config, song_path, user="Pikaraoke", semitones=0, add_to_front=False):
    if is_song_in_queue(config, song_path):
        logger.warn(f"Song is already in queue, will not add: {song_path}")
        return False

    queue_item = {
        "user": user,
        "file": song_path,
        "title": filename_from_path(song_path),
        "semitones": semitones,
    }
    if add_to_front:
        logger.info(
            f"'{user}' is adding song to front of queue: {song_path}"
        )
        config.get("queue").insert(0, queue_item)
    else:
        logger.info(f"'{user}' is adding song to queue: {song_path}")
        config.get("queue").append(queue_item)
    return True


def delete(logger, config, song_path):
    logger.info(f"Deleting song: {song_path}")
    with contextlib.suppress(FileNotFoundError):
        os.remove(song_path)

    ext = os.path.splitext(song_path)
    # if we have an associated cdg file, delete that too
    cdg_file = song_path.replace(ext[1], ".cdg")
    if os.path.exists(cdg_file):
        os.remove(cdg_file)

    config["available_songs"] = get_available_songs(logger, config.get("download_path"))

def rename(logger, config, song_path, new_name):
    logger.info(f"Renaming song: '{song_path}' to: {new_name}")
    ext = os.path.splitext(song_path)
    if len(ext) == 2:
        new_file_name = new_name + ext[1]
    os.rename(song_path, config.get("download_path") + new_file_name)
    # if we have an associated cdg file, rename that too
    cdg_file = song_path.replace(ext[1], ".cdg")
    if os.path.exists(cdg_file):
        os.rename(cdg_file, config.get("download_path") + new_name + ".cdg")
    config["available_songs"] = get_available_songs(logger, config.get("download_path"))