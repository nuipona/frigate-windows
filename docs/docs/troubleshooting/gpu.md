---
id: gpu
title: GPU Troubleshooting
---

# GPU troubleshooting on Windows

Frigate discovers display adapters with Windows APIs, OpenVINO devices with the OpenVINO runtime, DirectML through ONNX Runtime, and NVIDIA telemetry through NVML. A GPU visible in Task Manager is not automatically available to every runtime.

## Basic checks

1. Install the current Intel, AMD, or NVIDIA driver from the hardware vendor.
2. Reboot after changing the driver.
3. Confirm Device Manager shows the adapter without an error.
4. Open **System > Hardware** and refresh the probe.
5. Test software decoding or CPU inference before enabling acceleration.

Remote Desktop, virtual machines, and Windows Sandbox can expose a synthetic adapter or suppress hardware APIs. Validate from the same account and session type used by the Frigate service.

## Video decoding

Use only native presets listed in [Video Hardware Acceleration](/configuration/hardware_acceleration_video). If FFmpeg exits:

- copy the generated command from the log;
- run it in PowerShell as the service account where possible;
- inspect `ffmpeg -hwaccels` and `ffmpeg -decoders` from Frigate's bundled build;
- check whether the camera's codec profile, bit depth, and chroma format are supported by the GPU;
- try `preset-windows-d3d11va` before a vendor-specific path.

An initialization error often causes the camera process to restart repeatedly. Frigate does not silently translate a non-Windows preset.

## Intel QSV and OpenVINO

QSV video decoding and OpenVINO inference are separate APIs. One can work while the other fails. Use current Intel graphics and NPU drivers, and confirm OpenVINO reports `GPU` or `NPU` in the hardware probe. Older Intel CPUs may decode with D3D11VA but not support the selected QSV codec.

## DirectML

DirectML requires the `onnxruntime-directml` package instead of standard `onnxruntime` and a compatible ONNX model:

```powershell
py -3.12 bootstrap_windows.py --directml
```

Only one ONNX Runtime package should be installed in the environment. Confirm `DmlExecutionProvider` appears:

```powershell
.\.venv\Scripts\python.exe -c "import onnxruntime as o; print(o.get_available_providers())"
```

## NVIDIA CUDA and telemetry

CUDA inference requires a CUDA-enabled ONNX Runtime build compatible with the installed driver. Video decode uses Frigate's FFmpeg build and does not require ONNX CUDA. NVML statistics require the NVIDIA driver and may be unavailable even when video decoding works.

If multiple NVIDIA adapters exist, keep detector, FFmpeg, and enrichment device indices explicit and confirm each tool uses the intended adapter.

## AMD

Use D3D11VA or DXVA2 for decoding and AMF where encoding is needed. ONNX GPU inference on AMD/Intel uses DirectML in this Windows build; ROCm providers are not supported.

## Reporting a failure

Include the GPU name, driver version, Windows build, selected preset/device, model format, `ffmpeg -hwaccels`, ONNX provider list, and the first initialization error. Redact stream URLs and credentials.
