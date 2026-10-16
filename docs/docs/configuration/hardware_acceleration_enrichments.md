---
id: hardware_acceleration_enrichments
title: Enrichment Acceleration
---

# Enrichment acceleration on Windows

Semantic search, face recognition, license-plate recognition, classification, and other enrichment models use ONNX Runtime or OpenVINO when the model supports them.

## Device values

Common values are:

- `CPU` — force CPU inference;
- `AUTO` — choose an available provider automatically;
- `GPU` or `GPU.0` — use an OpenVINO Intel GPU;
- `NPU` — use an OpenVINO Intel NPU;
- a numeric DirectML adapter index where the relevant setting accepts one.

The System page reports the provider selected by each loaded enrichment model.

## DirectML

The default bootstrap swaps CPU ONNX Runtime for `onnxruntime-directml`. DirectML can use AMD, Intel, and NVIDIA display adapters without a CUDA-specific Python package. Use `bootstrap_windows.py --no-directml` when CPU-only behavior is preferred.

## OpenVINO

OpenVINO is included in the Windows requirements. Intel GPU and NPU devices appear only when the matching Windows driver exposes them to OpenVINO. `CPU` remains available as a fallback.

## NVIDIA CUDA

Advanced users may replace the standard ONNX Runtime package with a CUDA-capable build that supports CPython 3.12 on Windows. The CUDA runtime, cuDNN, driver, and ONNX Runtime versions must match. DirectML is the supported default because it avoids that additional native dependency chain.

## Memory considerations

A model loaded by more than one process consumes memory per process. Start with `AUTO`, inspect runtime-device and memory statistics, and pin a device only when automatic selection is unsuitable. If a GPU runs out of memory, move some enrichments to `CPU` or reduce the number of concurrently enabled models.
