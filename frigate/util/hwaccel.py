"""Recommend FFmpeg hardware acceleration exposed by native Windows APIs."""

import re

from pydantic import BaseModel, Field

from frigate.util.platform import (
    get_cpu_brand,
    get_display_adapter_vendors,
    get_display_adapters,
)

ANY_CODEC = "any"
CODEC_ALIASES = {"hevc": "h265"}
WINDOWS_DISPLAY_ADAPTER = "windows:display"

INTEL_GEN_PATTERN = re.compile(r"(\d+)th Gen")
INTEL_ULTRA_PATTERN = re.compile(r"Core\(TM\) Ultra")
INTEL_GEN_LATEST = 99
INTEL_QSV_SUPPORTED_GEN = 8


class HwaccelFamily(BaseModel):
    """A Windows decoder family and the presets that drive it."""

    key: str = Field(title="Family key")
    presets: dict[str, str] = Field(
        title="Presets",
        description="Preset by codec, or one 'any' preset for codec-independent APIs.",
    )


class HwaccelRecommendation(BaseModel):
    """Hardware decoding available on this Windows machine."""

    recommended: str = ""
    available: list[HwaccelFamily] = Field(default_factory=list)


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


def _generation_of(name: str) -> int | None:
    """Infer an Intel generation from its Windows processor marketing name."""
    if not name:
        return None

    match = INTEL_GEN_PATTERN.search(name)
    if match:
        return int(match.group(1))
    if INTEL_ULTRA_PATTERN.search(name):
        return INTEL_GEN_LATEST
    return None


def _intel_generation() -> int | None:
    return _generation_of(get_cpu_brand())


def _windows_intel_families(generation: int | None) -> list[HwaccelFamily]:
    """Prefer QSV when the processor generation is known to support it."""
    if generation is not None and generation >= INTEL_QSV_SUPPORTED_GEN:
        return [FAMILY_WINDOWS_QSV, FAMILY_WINDOWS_D3D11VA]
    return [FAMILY_WINDOWS_D3D11VA, FAMILY_WINDOWS_QSV]


def _decodes(family: HwaccelFamily, codecs: set[str]) -> bool:
    if ANY_CODEC in family.presets:
        return True
    return all(codec in family.presets for codec in codecs)


def _family_candidates(detector_key: str | None) -> list[HwaccelFamily]:
    """Build decoder candidates from adapters driven by Windows."""
    if not get_display_adapters():
        return []

    vendors = get_display_adapter_vendors()
    candidates: list[HwaccelFamily] = []

    if "nvidia" in vendors:
        candidates.append(FAMILY_WINDOWS_NVENC)
    if "intel" in vendors:
        candidates.extend(_windows_intel_families(_intel_generation()))

    # D3D11VA is the vendor-neutral Windows fallback. It is also how AMD
    # adapters decode, and it remains available if a vendor could not be read.
    candidates.append(FAMILY_WINDOWS_D3D11VA)

    preferred_key = None
    if detector_key == "onnx:nvidia":
        preferred_key = FAMILY_WINDOWS_NVENC.key
    elif detector_key in ("openvino:GPU", "openvino:NPU"):
        preferred_key = FAMILY_WINDOWS_QSV.key

    if preferred_key:
        candidates.sort(key=lambda family: family.key != preferred_key)

    unique: list[HwaccelFamily] = []
    for family in candidates:
        if family.key not in {item.key for item in unique}:
            unique.append(family)
    return unique


def _recommend(families: list[HwaccelFamily], codecs_known: bool) -> str:
    if not codecs_known:
        for family in families:
            if ANY_CODEC in family.presets:
                return family.key
    return families[0].key if families else ""


def hwaccel_options(
    detector_key: str | None = None, codecs: set[str] | None = None
) -> tuple[str, list[HwaccelFamily]]:
    """Return the recommended and available Windows decode families."""
    wanted = {CODEC_ALIASES.get(codec, codec) for codec in codecs or set()}
    available = [
        family
        for family in _family_candidates(detector_key)
        if _decodes(family, wanted)
    ]
    return _recommend(available, bool(wanted)), available


def recommend_hwaccel(
    detector_key: str | None = None, codecs: set[str] | None = None
) -> str:
    return hwaccel_options(detector_key, codecs)[0]


def windows_hwaccel_preset(
    go2rtc_sources: list[dict[str, str]] | None = None,
) -> str:
    """Resolve ``ffmpeg.hwaccel_args: auto`` to a native Windows preset."""
    accelerators: set[str] = set()

    for source in go2rtc_sources or []:
        if source.get("name") != "OK":
            continue
        url = source.get("url", "").lower()
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
    return "preset-windows-d3d11va"
