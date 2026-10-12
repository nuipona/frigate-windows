"""Generate go2rtc's configuration file.

go2rtc is an external process. In the container image an s6 service stages the
config in ``/dev/shm/go2rtc.yaml`` before go2rtc starts; a native install does
the same thing from Python so ``python -m frigate`` is all a user has to run.

The logic lives here rather than in the image's ``create_config.py`` so both
paths share one implementation. That script is now a thin wrapper around
:func:`main`.
"""

import json
import logging
import os
from typing import Any

from ruamel.yaml import YAML

from frigate.config.env import apply_config_env_vars, substitute_frigate_vars
from frigate.const import LIBAVFORMAT_VERSION_MAJOR
from frigate.ffmpeg_presets import parse_preset_hardware_acceleration_encode
from frigate.util.config import find_config_file, resolve_ffmpeg_path
from frigate.util.live_streams import raw_transcode_streams
from frigate.util.platform import birdseye_pipe_source, get_tmp_dir
from frigate.util.services import (
    is_go2rtc_arbitrary_exec_allowed,
    is_restricted_go2rtc_source,
)

logger = logging.getLogger(__name__)

yaml = YAML()


def go2rtc_config_path() -> str:
    """Where the generated go2rtc config is written."""
    return os.path.join(get_tmp_dir(), "go2rtc.yaml")


def load_raw_config(config_file: str | None = None) -> dict[str, Any]:
    """Read the user's config file without validating it.

    The go2rtc config has to be staged before go2rtc starts, which is before
    Frigate's own config validation runs, so a config that Frigate would reject
    still has to produce a usable go2rtc config.
    """
    config_file = config_file or find_config_file()

    try:
        with open(config_file) as file:
            raw_config = file.read()

        if config_file.endswith((".yaml", ".yml")):
            config: dict[str, Any] = yaml.load(raw_config)
        elif config_file.endswith(".json"):
            config = json.loads(raw_config)
        else:
            config = {}
    except FileNotFoundError:
        config = {}

    return config or {}


def build_go2rtc_config(config: dict[str, Any]) -> dict[str, Any]:
    """Build the go2rtc config for a raw Frigate config.

    Args:
        config: the raw (unvalidated) Frigate config

    Returns:
        The go2rtc configuration, ready to be dumped as yaml.
    """
    # No validator runs here, so install environment_vars ourselves. FRIGATE_
    # names only: anything else lands in os.environ, where the exec gate reads
    # GO2RTC_ALLOW_ARBITRARY_EXEC.
    config_env_vars = config.get("environment_vars")
    apply_config_env_vars(
        {
            key: value
            for key, value in config_env_vars.items()
            if str(key).startswith("FRIGATE_")
        }
        if isinstance(config_env_vars, dict)
        else {}
    )

    go2rtc_config: dict[str, Any] = dict(config.get("go2rtc", {}) or {})

    # Need to enable CORS for go2rtc so the frigate integration / card work automatically
    if go2rtc_config.get("api") is None:
        go2rtc_config["api"] = {"origin": "*"}
    elif go2rtc_config["api"].get("origin") is None:
        go2rtc_config["api"]["origin"] = "*"

    # Need to set default location for HA config
    if go2rtc_config.get("hass") is None:
        go2rtc_config["hass"] = {"config": "/homeassistant"}

    # we want to ensure that logs are easy to read
    if go2rtc_config.get("log") is None:
        go2rtc_config["log"] = {"format": "text"}
    elif go2rtc_config["log"].get("format") is None:
        go2rtc_config["log"]["format"] = "text"

    # ensure there is a default webrtc config
    if go2rtc_config.get("webrtc") is None:
        go2rtc_config["webrtc"] = {}

    if go2rtc_config["webrtc"].get("candidates") is None:
        default_candidates = []

        # use internal candidate if it was discovered when running through the add-on
        internal_candidate = os.environ.get("FRIGATE_GO2RTC_WEBRTC_CANDIDATE_INTERNAL")

        if internal_candidate is not None:
            default_candidates.append(internal_candidate)

        # should set default stun server so webrtc can work
        default_candidates.append("stun:8555")

        go2rtc_config["webrtc"]["candidates"] = default_candidates

    if go2rtc_config.get("rtsp", {}).get("username") is not None:
        go2rtc_config["rtsp"]["username"] = substitute_frigate_vars(
            go2rtc_config["rtsp"]["username"]
        )

    if go2rtc_config.get("rtsp", {}).get("password") is not None:
        go2rtc_config["rtsp"]["password"] = substitute_frigate_vars(
            go2rtc_config["rtsp"]["password"]
        )

    # ensure ffmpeg path is set correctly
    path = config.get("ffmpeg", {}).get("path", "default")
    ffmpeg_path = resolve_ffmpeg_path(path, "ffmpeg")

    if go2rtc_config.get("ffmpeg") is None:
        go2rtc_config["ffmpeg"] = {"bin": ffmpeg_path}
    elif go2rtc_config["ffmpeg"].get("bin") is None:
        go2rtc_config["ffmpeg"]["bin"] = ffmpeg_path

    # need to replace ffmpeg command when using ffmpeg4
    if LIBAVFORMAT_VERSION_MAJOR < 59:
        rtsp_args = "-fflags nobuffer -flags low_delay -stimeout 10000000 -user_agent go2rtc/ffmpeg -rtsp_transport tcp -i {input}"
        if go2rtc_config.get("ffmpeg") is None:
            go2rtc_config["ffmpeg"] = {"rtsp": rtsp_args}
        elif go2rtc_config["ffmpeg"].get("rtsp") is None:
            go2rtc_config["ffmpeg"]["rtsp"] = rtsp_args

    for name in list(go2rtc_config.get("streams", {})):
        stream = go2rtc_config["streams"][name]

        if isinstance(stream, str):
            formatted_stream = substitute_frigate_vars(stream)

            if is_restricted_go2rtc_source(formatted_stream):
                logger.error(
                    "Stream '%s' uses a restricted source (echo/expr/exec) which "
                    "is disabled by default for security. Set "
                    "GO2RTC_ALLOW_ARBITRARY_EXEC=true to enable arbitrary exec "
                    "sources.",
                    name,
                )
                del go2rtc_config["streams"][name]
                continue

            go2rtc_config["streams"][name] = formatted_stream

        elif isinstance(stream, list):
            filtered_streams = []

            for index, stream_item in enumerate(stream):
                formatted_stream = substitute_frigate_vars(stream_item)

                if is_restricted_go2rtc_source(formatted_stream):
                    logger.error(
                        "Stream '%s' item %s uses a restricted source "
                        "(echo/expr/exec) which is disabled by default for "
                        "security. Set GO2RTC_ALLOW_ARBITRARY_EXEC=true to "
                        "enable arbitrary exec sources.",
                        name,
                        index + 1,
                    )
                    continue

                filtered_streams.append(formatted_stream)

            if filtered_streams:
                go2rtc_config["streams"][name] = filtered_streams
            else:
                logger.error(
                    "Stream '%s' was removed because all sources were restricted "
                    "(echo/expr/exec). Set GO2RTC_ALLOW_ARBITRARY_EXEC=true to "
                    "enable arbitrary exec sources.",
                    name,
                )
                del go2rtc_config["streams"][name]

        elif isinstance(stream, dict):
            # The map form ({"url": ...}) lets go2rtc resolve the source
            # recursively, so it is effectively a dynamic way to generate the URL
            # for a stream. That can only be backed by an exec source, so it cannot
            # be allowed unless arbitrary exec is explicitly enabled. When it is
            # enabled, leave the map untouched for go2rtc to resolve.
            if not is_go2rtc_arbitrary_exec_allowed():
                logger.error(
                    "Stream '%s' uses a dynamic source format which is disabled "
                    "by default for security. Set GO2RTC_ALLOW_ARBITRARY_EXEC=true "
                    "to enable arbitrary exec sources.",
                    name,
                )
                del go2rtc_config["streams"][name]
                continue

    # add transcoded live streams; a user stream with the same name wins here and
    # fails Frigate's config validation
    transcoded_streams = raw_transcode_streams(config)

    if transcoded_streams:
        if go2rtc_config.get("streams") is None:
            go2rtc_config["streams"] = {}

        for name, source in transcoded_streams.items():
            go2rtc_config["streams"].setdefault(name, source)

    # add the birdseye restream if enabled: the output process feeds frames to a
    # fifo, or to a loopback listener on Windows, and go2rtc publishes them
    if config.get("birdseye", {}).get("restream", False):
        birdseye: dict[str, Any] = config.get("birdseye")
        input = (
            f"-f rawvideo -pix_fmt yuv420p -video_size "
            f"{birdseye.get('width', 1280)}x{birdseye.get('height', 720)} -r 10 "
            f"-i {birdseye_pipe_source()}"
        )
        ffmpeg_cmd = f"exec:{parse_preset_hardware_acceleration_encode(ffmpeg_path, config.get('ffmpeg', {}).get('hwaccel_args', ''), input, '-rtsp_transport tcp -f rtsp {output}')}"

        if go2rtc_config.get("streams"):
            go2rtc_config["streams"]["birdseye"] = ffmpeg_cmd
        else:
            go2rtc_config["streams"] = {"birdseye": ffmpeg_cmd}

    return go2rtc_config


def write_go2rtc_config(config: dict[str, Any], path: str | None = None) -> str:
    """Write the go2rtc config, returning the path it was written to."""
    path = path or go2rtc_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w") as file:
        yaml.dump(config, file)

    # the config contains camera credentials; do not leave it world-readable
    if hasattr(os, "chmod"):
        try:
            os.chmod(path, 0o640)
        except OSError as err:  # pragma: no cover - depends on the filesystem
            logger.debug("Unable to restrict permissions on %s: %s", path, err)

    return path


def main() -> None:
    """Generate the go2rtc config for the config file in use."""
    config = load_raw_config()

    try:
        go2rtc_config = build_go2rtc_config(config)
    except ValueError as err:
        logger.error("Invalid substitution found in a go2rtc stream: %s", err)
        logger.error(
            "See https://docs.frigate.video/configuration/restream"
            "#advanced-restream-configurations for more info."
        )
        raise SystemExit(err) from err

    path = write_go2rtc_config(go2rtc_config)
    logger.info("Wrote go2rtc config to %s", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    main()
