"""Recommendation of ffmpeg hwaccel presets from the hardware on the system.

Every check is a filesystem read, like the detection hardware probes, so this
is cheap enough to serve from the API process.

Presets are grouped into families because some of them only decode the codec
they name. A family hides that: callers pick the family their hardware needs
and resolve it per camera against that camera's detect stream.
"""

import logging
import os
import re

from pydantic import BaseModel, Field

from frigate.const import (
    FFMPEG_HWACCEL_NVIDIA,
    FFMPEG_HWACCEL_RKMPP,
    FFMPEG_HWACCEL_VAAPI,
)
from frigate.detectors.hardware import hardware_prober
from frigate.util.platform import (
    IS_WINDOWS,
    get_cpu_brand,
    get_display_adapter_vendors,
    get_display_adapters,
)
from frigate.util.services import enumerate_drm_devices

logger = logging.getLogger(__name__)

# roots the /proc and /sys reads use, so tests can point them at a fixture tree
PROC_ROOT = "/proc"
SYS_ROOT = "/sys"

#: hardware key for a GPU Windows itself reports being driven, which is how
#: AMD and Intel GPUs are found without /sys or a vendor runtime
WINDOWS_DISPLAY_ADAPTER = "windows:display"

ANY_CODEC = "any"

# a Raspberry Pi has no detection hardware of its own, so it gets a key here
RASPBERRY_PI = "raspberrypi"

# lighter (https://github.com/fieldwork-ai/lighter) decodes on a Mac's media
# engine through virtio-media devices, which carry its virtio vendor id "LGHT"
LIGHTER_MEDIA = "lighter"
LIGHTER_VIRTIO_VENDOR = "0x4c474854"

# ffprobe names h265 streams hevc
CODEC_ALIASES = {"hevc": "h265"}

# e.g. "13th Gen Intel(R) Core(TM) i5-13500"
INTEL_GEN_PATTERN = re.compile(r"(\d+)th Gen")
# Core Ultra dropped the generation prefix and is newer than all of them
INTEL_ULTRA_PATTERN = re.compile(r"Core\(TM\) Ultra")
INTEL_GEN_LATEST = 99

# per the hwaccel docs, gen13+ and Arc prefer qsv while older is safest on
# vaapi, and qsv is not supported at all before gen8
INTEL_QSV_MIN_GEN = 13
INTEL_QSV_SUPPORTED_GEN = 8

# decode capable detection hardware, in recommendation priority order
DECODE_HARDWARE = (
    "onnx:nvidia",
    "tensorrt",
    "rknn",
    "openvino:GPU",
    "onnx:amd",
    LIGHTER_MEDIA,
    RASPBERRY_PI,
    WINDOWS_DISPLAY_ADAPTER,
)


class HwaccelFamily(BaseModel):
    """A kind of hardware decoding, and the presets that drive it."""

    key: str = Field(
        title="Family key",
        description="Stable identifier for this kind of hardware decoding.",
    )
    presets: dict[str, str] = Field(
        title="Presets",
        description="The ffmpeg preset for each codec this family decodes, or a single 'any' preset when it decodes every codec.",
    )


class HwaccelRecommendation(BaseModel):
    """The hardware decoding this system can do."""

    recommended: str = Field(
        title="Recommended family",
        description="Key of the family that fits this system best, or an empty string when none does.",
    )
    available: list[HwaccelFamily] = Field(
        default_factory=list,
        title="Available families",
        description="Every family this system's hardware can use, best first.",
    )


FAMILY_NVIDIA = HwaccelFamily(key="nvidia", presets={ANY_CODEC: FFMPEG_HWACCEL_NVIDIA})
FAMILY_VAAPI = HwaccelFamily(key="vaapi", presets={ANY_CODEC: FFMPEG_HWACCEL_VAAPI})
FAMILY_RKMPP = HwaccelFamily(key="rkmpp", presets={ANY_CODEC: FFMPEG_HWACCEL_RKMPP})
FAMILY_QSV = HwaccelFamily(
    key="intel-qsv",
    presets={"h264": "preset-intel-qsv-h264", "h265": "preset-intel-qsv-h265"},
)
FAMILY_JETSON = HwaccelFamily(
    key="jetson",
    presets={"h264": "preset-jetson-h264", "h265": "preset-jetson-h265"},
)
FAMILY_RPI = HwaccelFamily(
    key="rpi",
    presets={"h264": "preset-rpi-64-h264", "h265": "preset-rpi-64-h265"},
)
FAMILY_APPLE = HwaccelFamily(
    key="apple-silicon",
    presets={"h264": "preset-apple-silicon-h264", "h265": "preset-apple-silicon-h265"},
)

# Windows decodes through the platform APIs rather than libva, so it needs its
# own families. Every Windows GPU supports D3D11VA, which is why it is the
# fallback the other two are ordered ahead of.
FAMILY_WINDOWS_NVENC = HwaccelFamily(
    key="windows-nvenc", presets={ANY_CODEC: "preset-windows-nvenc"}
)
FAMILY_WINDOWS_QSV = HwaccelFamily(
    key="windows-qsv",
    presets={"h264": "preset-windows-qsv-h264", "h265": "preset-windows-qsv-h265"},
)
FAMILY_WINDOWS_D3D11VA = HwaccelFamily(
    key="windows-d3d11va", presets={ANY_CODEC: "preset-windows-d3d11va"}
)


def _read(path: str) -> str | None:
    """Read a small file, returning None if it cannot be read."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _intel_generation() -> int | None:
    """The Intel platform generation, or None when it cannot be determined."""
    if IS_WINDOWS:
        # no /proc/cpuinfo and no DRM devices: the registry reports the same
        # marketing name, and a Windows GPU driver is never the Linux one that
        # the xe check looks for
        return _generation_of(get_cpu_brand())

    # the xe driver only binds to the newest platforms (Arc and later iGPUs)
    if "xe" in enumerate_drm_devices().values():
        return INTEL_GEN_LATEST

    cpuinfo = _read(f"{PROC_ROOT}/cpuinfo") or ""

    for line in cpuinfo.splitlines():
        if not line.startswith("model name"):
            continue

        return _generation_of(line)

    return None


def _generation_of(name: str) -> int | None:
    """The Intel generation a processor marketing name implies."""
    if not name:
        return None

    match = INTEL_GEN_PATTERN.search(name)

    if match:
        return int(match.group(1))

    if INTEL_ULTRA_PATTERN.search(name):
        return INTEL_GEN_LATEST

    return None


def _is_raspberry_pi() -> bool:
    if IS_WINDOWS:
        return False

    compatible = _read(f"{PROC_ROOT}/device-tree/compatible") or ""
    return "raspberrypi" in compatible


def _has_lighter_media() -> bool:
    if IS_WINDOWS:
        return False

    video = f"{SYS_ROOT}/class/video4linux"

    try:
        devices = os.listdir(video)
    except OSError:
        return False

    return any(
        _read(f"{video}/{device}/device/vendor") == LIGHTER_VIRTIO_VENDOR
        for device in devices
    )


def _intel_families(generation: int | None) -> list[HwaccelFamily]:
    """vaapi drives every Intel GPU, qsv only those from gen8 on."""
    if generation is not None and generation < INTEL_QSV_SUPPORTED_GEN:
        return [FAMILY_VAAPI]

    if generation is not None and generation >= INTEL_QSV_MIN_GEN:
        return [FAMILY_QSV, FAMILY_VAAPI]

    return [FAMILY_VAAPI, FAMILY_QSV]


def _windows_intel_families(generation: int | None) -> list[HwaccelFamily]:
    """Intel families on Windows, where decode goes through QSV or D3D11VA.

    QSV needs a driver and an ffmpeg build that both support it, so it is only
    offered ahead of D3D11VA when the installed hardware is new enough for it
    to be certain. D3D11VA decodes on every Intel GPU.
    """
    if generation is not None and generation >= INTEL_QSV_SUPPORTED_GEN:
        return [FAMILY_WINDOWS_QSV, FAMILY_WINDOWS_D3D11VA]

    return [FAMILY_WINDOWS_D3D11VA, FAMILY_WINDOWS_QSV]


def _windows_families(key: str) -> list[HwaccelFamily]:
    """Every Windows family that can decode on this hardware, best first."""
    if key == "onnx:nvidia":
        return [FAMILY_WINDOWS_NVENC, FAMILY_WINDOWS_D3D11VA]

    if key == "openvino:GPU":
        return _windows_intel_families(_intel_generation())

    if key == WINDOWS_DISPLAY_ADAPTER:
        return [FAMILY_WINDOWS_D3D11VA]

    return []


def _families(key: str, generation: int | None) -> list[HwaccelFamily]:
    """Every family that can decode on this hardware, best first."""
    if IS_WINDOWS:
        return _windows_families(key)

    if key == "onnx:nvidia":
        return [FAMILY_NVIDIA]

    if key == "tensorrt":
        return [FAMILY_JETSON]

    if key == "rknn":
        return [FAMILY_RKMPP]

    if key == "onnx:amd":
        return [FAMILY_VAAPI]

    if key == LIGHTER_MEDIA:
        return [FAMILY_APPLE]

    if key == RASPBERRY_PI:
        return [FAMILY_RPI]

    if key == "openvino:GPU":
        return _intel_families(generation)

    return []


def _decodes(family: HwaccelFamily, codecs: set[str]) -> bool:
    """Whether a family can decode every codec that is in use."""
    if ANY_CODEC in family.presets:
        return True

    return all(codec in family.presets for codec in codecs)


def _decode_hardware(detector_key: str | None) -> list[str]:
    """Decode capable hardware on this system, best first.

    Args:
        detector_key: Hardware key of the detection hardware in use, whose GPU
            is preferred over any other

    Returns:
        The hardware keys that can decode video, in recommendation order
    """
    present = {found.key for found in hardware_prober.probe()}

    if IS_WINDOWS:
        # the prober finds Nvidia through NVML and Intel through OpenVINO, but
        # an AMD or an Intel GPU whose OpenVINO runtime is missing would go
        # unseen, and every GPU Windows drives can decode through D3D11VA
        if get_display_adapters():
            present.add(WINDOWS_DISPLAY_ADAPTER)
    else:
        if _has_lighter_media():
            present.add(LIGHTER_MEDIA)

        if _is_raspberry_pi():
            present.add(RASPBERRY_PI)

    # an Intel NPU decodes through the iGPU next to it
    if detector_key == "openvino:NPU":
        detector_key = "openvino:GPU"

    ordered = [key for key in DECODE_HARDWARE if key in present]

    if detector_key in ordered:
        ordered.remove(detector_key)
        ordered.insert(0, detector_key)

    return ordered


def hwaccel_options(
    detector_key: str | None = None, codecs: set[str] | None = None
) -> tuple[str, list[HwaccelFamily]]:
    """Get the hardware decoding this system can do.

    Args:
        detector_key: Hardware key of the detection hardware in use, which
            biases the recommendation toward that hardware's GPU
        codecs: Codecs of the streams that will be decoded, used to drop
            families that cannot decode one of them

    Returns:
        The recommended family key (empty when none fits) and every usable
        family, best first
    """
    wanted = {CODEC_ALIASES.get(codec, codec) for codec in codecs or set()}
    hardware = _decode_hardware(detector_key)
    generation = _intel_generation() if "openvino:GPU" in hardware else None

    available: list[HwaccelFamily] = []
    recommended = ""

    for key in hardware:
        usable = [
            family for family in _families(key, generation) if _decodes(family, wanted)
        ]

        if usable and not recommended:
            recommended = _recommend(usable, bool(wanted))

        for family in usable:
            if family.key not in {entry.key for entry in available}:
                available.append(family)

    return recommended, available


def _recommend(families: list[HwaccelFamily], codecs_known: bool) -> str:
    """Pick the family to default to out of the ones this hardware can use."""
    if not codecs_known:
        # a codec specific family would have to guess a codec for cameras
        # that do not exist yet
        for family in families:
            if ANY_CODEC in family.presets:
                return family.key

    return families[0].key


def recommend_hwaccel(
    detector_key: str | None = None, codecs: set[str] | None = None
) -> str:
    """Recommend a hardware decoding family for this system.

    Args:
        detector_key: Hardware key of the detection hardware in use
        codecs: Codecs of the streams that will be decoded

    Returns:
        The key of the family that fits, or an empty string when none does
    """
    return hwaccel_options(detector_key, codecs)[0]


# ---------------------------------------------------------------------------
# auto detection
# ---------------------------------------------------------------------------


def windows_hwaccel_preset(
    go2rtc_sources: list[dict[str, str]] | None = None,
) -> str:
    """The Windows decode preset to use for ``ffmpeg.hwaccel_args: auto``.

    go2rtc reports which accelerators its own ffmpeg build can open, which is
    the best evidence available when it is running. On a native Windows start
    it never is: Frigate resolves ``auto`` while loading the config, and starts
    go2rtc afterwards. The GPUs Windows reports are therefore used as the
    fallback, which is what makes ``auto`` work on a fresh install instead of
    always falling back to software decoding.

    Args:
        go2rtc_sources: The ``sources`` list from go2rtc's
            ``/api/ffmpeg/hardware``, when it could be reached

    Returns:
        A ``preset-windows-*`` name, or an empty string when nothing was found
    """
    # go2rtc only reports the accelerators it managed to open, so trust it
    # first and in order of how specific each one is
    accelerators: set[str] = set()

    for source in go2rtc_sources or []:
        if source.get("name") != "OK":
            continue

        url = source.get("url", "")

        for accelerator in ("cuda", "qsv", "d3d11va", "dxva2"):
            if accelerator in url:
                accelerators.add(accelerator)

    if "cuda" in accelerators:
        return "preset-windows-nvenc"

    if "qsv" in accelerators:
        return "preset-windows-qsv"

    if "d3d11va" in accelerators:
        return "preset-windows-d3d11va"

    if "dxva2" in accelerators:
        return "preset-windows-dxva2"

    adapters = get_display_adapters()

    if not adapters:
        return ""

    vendors = get_display_adapter_vendors()

    if "nvidia" in vendors:
        return "preset-windows-nvenc"

    if "intel" in vendors:
        return "preset-windows-qsv"

    # every Windows GPU decodes through D3D11VA, including the AMD and Intel
    # ones no vendor runtime reported
    return "preset-windows-d3d11va"
