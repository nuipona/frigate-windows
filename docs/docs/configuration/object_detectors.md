---
id: object_detectors
title: Object Detectors
---

# Object detectors on Windows

Frigate runs object detection locally. A model declares one or more Windows detector devices using the `models[].devices` list. Each value is either a detector name or `<detector>:<device>`.

## Supported detectors

| Detector | Device examples | Runtime |
| --- | --- | --- |
| CPU TFLite | `cpu` | `ai-edge-litert` |
| ONNX Runtime | `onnx`, `onnx:0`, `onnx:CPU` | CPU, CUDA, or DirectML provider exposed by the installed ONNX Runtime package |
| OpenVINO | `openvino:CPU`, `openvino:GPU`, `openvino:NPU` | Intel CPU, GPU, and NPU through OpenVINO |
| Remote ZMQ | `zmq:tcp://host:5555` | A compatible remote inference service |

Coral Edge TPU, Hailo, TensorRT, RKNN, MemryX, DeepX, Synaptics, AXEngine, and other Linux or appliance-specific detectors are not part of the native Windows application.

## CPU TFLite

The default configuration uses the CPU detector:

```yaml
models:
  - scene: default
    devices:
      - cpu
```

On first use Frigate downloads its default TFLite model into `%LOCALAPPDATA%\Frigate\config\model_cache`. Set `path` to use another compatible model.

## ONNX Runtime

```yaml
models:
  - scene: default
    path: C:\Models\yolo.onnx
    model_type: yolonas
    width: 320
    height: 320
    input_tensor: nchw
    input_pixel_format: rgb
    devices:
      - onnx:0
```

The standard installer selects `onnxruntime-directml` by default. DirectML supports modern AMD, Intel, and NVIDIA display adapters. Run `bootstrap_windows.py --no-directml` for CPU-only ONNX Runtime. A separately installed CUDA-capable ONNX Runtime can expose NVIDIA CUDA as well.

The numeric suffix selects the provider's adapter index. Use the System page and startup logs to confirm which provider and adapter loaded.

## OpenVINO

```yaml
models:
  - scene: default
    path: C:\Models\detector.xml
    devices:
      - openvino:GPU
```

Valid device values are those reported by OpenVINO on the machine, commonly `CPU`, `GPU`, `GPU.0`, and `NPU`. Current Intel graphics and NPU drivers must be installed.

## Remote ZMQ

```yaml
models:
  - scene: default
    path: C:\Models\detector.onnx
    devices:
      - zmq:tcp://192.168.1.20:5555
```

The remote endpoint is responsible for loading a compatible model and returning detections using Frigate's ZMQ detector protocol. Keep the endpoint on a trusted network; ZMQ transport does not add authentication by itself.

## Multiple devices and scenes

Listing multiple devices runs inference workers for the same model:

```yaml
models:
  - scene: outdoor
    path: C:\Models\outdoor.onnx
    devices:
      - onnx:0
      - onnx:0
  - scene: indoor
    path: C:\Models\indoor.xml
    devices:
      - openvino:NPU
```

Repeated shareable devices receive unique worker names automatically. Cameras select a model with `detect.scene`. Every configured camera scene must have a matching model.

## Model compatibility

The model format, dimensions, tensor layout, pixel format, and label map must match the selected detector. Frigate+ metadata supplies these values for a `plus://` model. For local files, configure them explicitly when they differ from the defaults.
