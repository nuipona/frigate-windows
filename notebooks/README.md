# Notebooks

## YOLO-NAS pretrained export

[`YOLO_NAS_Pretrained_Export.ipynb`](YOLO_NAS_Pretrained_Export.ipynb) exports a COCO-pretrained YOLO-NAS detector to ONNX, in the layout Frigate's `onnx` detector expects. Run it on [Google Colab](https://colab.research.google.com/github/nuipona/frigate-windows/blob/dev/notebooks/YOLO_NAS_Pretrained_Export.ipynb) or locally.

- Set the Colab runtime to Python 3.11 before running: `super-gradients` does not work on the current Colab default. The first cell says this too.
- The export is 320x320, which costs less CPU and infers faster with no practical accuracy loss, because Frigate crops to regions of interest before it runs detection. Change `input_image_shape` to `(640, 640)` to use the larger input.
- `Models.YOLO_NAS_S` is the default. `YOLO_NAS_M` and `YOLO_NAS_L` export the same way and are larger and slower.

> [!WARNING]
> The pre-trained YOLO-NAS weights from DeciAI are subject to their license and cannot be used commercially. See https://docs.deci.ai/super-gradients/latest/LICENSE.YOLONAS.html
