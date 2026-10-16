r"""Analyze a Frigate custom object-classification dataset on Windows.

The script reads ``<clips-dir>\<model-name>\dataset`` and the deployed
``model.tflite``/``labelmap.txt`` below ``<model-cache>\<model-name>``. Its
defaults follow the active ``FRIGATE_BASE_DIR`` layout.

Examples from the repository root in PowerShell::

    .\.venv\Scripts\python.exe testing-scripts\object_dataset.py `
      --name front_gate --top-n 25 --quality

    .\.venv\Scripts\python.exe testing-scripts\object_dataset.py `
      --name front_gate --negative 'D:\FrigateSamples\misfires' `
      --threshold 0.8

For cross-class embedding analysis, download MobileNetV2 ONNX into the model
cache and pass its absolute Windows path with ``--embedding-model``. Use
``--confuses source:target`` or ``--confuses auto`` and optionally
``--save-misclassified`` to copy review candidates.

The analysis reports class balance, confusion, misclassified and borderline
images. Review results before moving training files; small crops can be either
noise or the representative signal for a distant object.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass

import cv2
import numpy as np

from frigate.util.platform import get_media_dir, get_model_cache_dir

try:
    from tflite_runtime.interpreter import Interpreter
except ModuleNotFoundError:
    from ai_edge_litert.interpreter import Interpreter

CLASSIFIER_INPUT_SIZE = 224
IMAGE_EXTS = (".webp", ".png", ".jpg", ".jpeg")


# ---------------------------------------------------------------------------
# Replicated Frigate pipeline
# ---------------------------------------------------------------------------


def load_tflite(model_path: str) -> tuple[Interpreter, list[dict], list[dict]]:
    """Mirror CustomObjectClassificationProcessor.__build_detector."""
    interpreter = Interpreter(model_path=model_path, num_threads=2)
    interpreter.allocate_tensors()
    return (
        interpreter,
        interpreter.get_input_details(),
        interpreter.get_output_details(),
    )


def load_labelmap(path: str) -> dict[int, str]:
    """Mirror util.builtin.load_labels(prefill=0, indexed=False)."""
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    return {idx: line for idx, line in enumerate(lines)}


def preprocess_for_inference(image_bgr: np.ndarray) -> np.ndarray:
    """Mirror the inference preprocessing in process_frame.

    Frigate decodes the camera frame YUV->RGB, crops, then cv2.resize to
    224x224, and passes the uint8 array directly to the int8-quantized
    interpreter. On disk we read BGR via cv2.imread, so we must convert
    to RGB to match the channel order the model was trained on.
    """
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (CLASSIFIER_INPUT_SIZE, CLASSIFIER_INPUT_SIZE))
    return resized


class MobileNetEmbedder:
    """ImageNet-pretrained MobileNetV2 backbone via cv2.dnn.

    Used as a model-independent visual embedder for cross-class contamination
    analysis. The user's trained classifier may have memorized contaminating
    training images and place them inside the right class in its own embedding
    space — a fixed external backbone keeps the analysis honest.

    Expects the standard ONNX Model Zoo MobileNetV2-7 file (PyTorch-style
    preprocessing: ImageNet mean/std on /255 input). Output is 1000-d ImageNet
    logits; L2-normalized for cosine-similarity comparisons.
    """

    IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, model_path: str):
        if not os.path.exists(model_path):
            raise FileNotFoundError(model_path)
        self.net = cv2.dnn.readNetFromONNX(model_path)

    def embed(self, image_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (224, 224)).astype(np.float32) / 255.0
        normalized = (resized - self.IMAGENET_MEAN) / self.IMAGENET_STD
        # NHWC -> NCHW
        blob = np.transpose(normalized, (2, 0, 1))[np.newaxis, :, :, :]
        self.net.setInput(blob)
        out = self.net.forward().squeeze().astype(np.float32)
        norm = float(np.linalg.norm(out))
        return out / norm if norm > 0 else out


def classify_image(
    interpreter: Interpreter,
    input_details: list[dict],
    output_details: list[dict],
    image_bgr: np.ndarray,
) -> np.ndarray:
    """Mirror _classify_object's tensor flow.

    Returns the per-class probability vector (length = num_classes) after
    the exact `probs = res / res.sum(axis=0)` renormalization Frigate uses
    on the int8-quantized output.
    """
    resized = preprocess_for_inference(image_bgr)
    tensor = np.expand_dims(resized, axis=0)
    interpreter.set_tensor(input_details[0]["index"], tensor)
    interpreter.invoke()
    res = interpreter.get_tensor(output_details[0]["index"])[0].astype(np.float32)
    total = res.sum(axis=0)
    if total <= 0:
        # Defensive: all zeros from a degenerate quantization step.
        return np.full_like(res, 1.0 / len(res))
    return res / total


# ---------------------------------------------------------------------------
# Sample loading
# ---------------------------------------------------------------------------


@dataclass
class ImageSample:
    path: str
    true_label: str | None  # None for the unlabeled negative folder
    shape: tuple[int, int]
    probs: np.ndarray
    pred_idx: int
    pred_label: str
    pred_score: float
    true_idx: int | None
    true_score: float | None


def laplacian_variance(image_bgr: np.ndarray) -> float:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def mean_brightness(image_bgr: np.ndarray) -> float:
    return float(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).mean())


def aspect_distortion(shape: tuple[int, int]) -> float:
    """How far the crop is from square; |1 - max(w,h)/min(w,h)|.

    A wide or tall crop gets squashed to 224x224 by the inference resize,
    which can be a hidden source of misclassification.
    """
    w, h = shape
    if w <= 0 or h <= 0:
        return float("inf")
    return float(max(w, h) / min(w, h) - 1.0)


def iter_dataset(dataset_dir: str) -> list[tuple[str, str]]:
    """Yield (class_name, image_path) pairs from the dataset directory."""
    pairs: list[tuple[str, str]] = []
    if not os.path.isdir(dataset_dir):
        return pairs
    for cls in sorted(os.listdir(dataset_dir)):
        cls_dir = os.path.join(dataset_dir, cls)
        if not os.path.isdir(cls_dir) or cls.startswith("."):
            continue
        for name in sorted(os.listdir(cls_dir)):
            if name.startswith("."):
                continue
            if not name.lower().endswith(IMAGE_EXTS):
                continue
            pairs.append((cls, os.path.join(cls_dir, name)))
    return pairs


def classify_folder(
    folder: str,
    interpreter: Interpreter,
    input_details: list[dict],
    output_details: list[dict],
    labelmap: dict[int, str],
    label_to_idx: dict[str, int],
    true_label: str | None = None,
) -> list[ImageSample]:
    """Classify every image directly under `folder`. Used for the negative set."""
    samples: list[ImageSample] = []
    if not os.path.isdir(folder):
        return samples
    for name in sorted(os.listdir(folder)):
        if name.startswith(".") or not name.lower().endswith(IMAGE_EXTS):
            continue
        path = os.path.join(folder, name)
        img = cv2.imread(path)
        if img is None:
            print(f"  [skip unreadable] {name}")
            continue
        probs = classify_image(interpreter, input_details, output_details, img)
        pred_idx = int(np.argmax(probs))
        true_idx = label_to_idx.get(true_label) if true_label is not None else None
        true_score = float(probs[true_idx]) if true_idx is not None else None
        samples.append(
            ImageSample(
                path=path,
                true_label=true_label,
                shape=(img.shape[1], img.shape[0]),
                probs=probs,
                pred_idx=pred_idx,
                pred_label=labelmap[pred_idx],
                pred_score=float(probs[pred_idx]),
                true_idx=true_idx,
                true_score=true_score,
            )
        )
    return samples


def classify_dataset(
    dataset_dir: str,
    interpreter: Interpreter,
    input_details: list[dict],
    output_details: list[dict],
    labelmap: dict[int, str],
    label_to_idx: dict[str, int],
) -> list[ImageSample]:
    samples: list[ImageSample] = []
    pairs = iter_dataset(dataset_dir)
    for cls, path in pairs:
        img = cv2.imread(path)
        if img is None:
            print(f"  [skip unreadable] {cls}/{os.path.basename(path)}")
            continue
        probs = classify_image(interpreter, input_details, output_details, img)
        pred_idx = int(np.argmax(probs))
        true_idx = label_to_idx.get(cls)
        true_score = float(probs[true_idx]) if true_idx is not None else None
        samples.append(
            ImageSample(
                path=path,
                true_label=cls,
                shape=(img.shape[1], img.shape[0]),
                probs=probs,
                pred_idx=pred_idx,
                pred_label=labelmap[pred_idx],
                pred_score=float(probs[pred_idx]),
                true_idx=true_idx,
                true_score=true_score,
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Baseline analyses (always run)
# ---------------------------------------------------------------------------


def summarize_dataset(samples: list[ImageSample], labelmap: dict[int, str]) -> None:
    """Per-class counts, accuracy, mean confidence on the true class."""
    print("\n" + "=" * 78)
    print(f"DATASET OVERVIEW  ({len(samples)} images)")
    print("=" * 78)

    by_class: dict[str, list[ImageSample]] = {}
    for s in samples:
        by_class.setdefault(s.true_label or "<unknown>", []).append(s)

    print(
        f"\n{'class':<20} {'count':>6} {'acc':>6} {'mean_p_true':>12} "
        f"{'min_p_true':>10} {'mislabeled':>11}"
    )
    for cls in sorted(by_class):
        rows = by_class[cls]
        correct = sum(1 for r in rows if r.pred_label == cls)
        mean_pt = (
            np.mean([r.true_score for r in rows if r.true_score is not None])
            if any(r.true_score is not None for r in rows)
            else float("nan")
        )
        min_pt = (
            np.min([r.true_score for r in rows if r.true_score is not None])
            if any(r.true_score is not None for r in rows)
            else float("nan")
        )
        acc = correct / len(rows) if rows else 0.0
        bad = len(rows) - correct
        print(
            f"{cls:<20} {len(rows):>6} {acc:>6.2%} {mean_pt:>12.3f} "
            f"{min_pt:>10.3f} {bad:>11}"
        )

    # Class balance — large skew can hide poor minority-class accuracy in the totals.
    counts = [len(by_class[c]) for c in by_class]
    if counts:
        print(
            f"\nClass balance: min={min(counts)} max={max(counts)} "
            f"ratio={max(counts) / max(1, min(counts)):.1f}x"
        )


def confusion_matrix(samples: list[ImageSample], labelmap: dict[int, str]) -> None:
    print("\n" + "=" * 78)
    print("CONFUSION MATRIX  (rows = true class, cols = predicted class)")
    print("=" * 78)

    classes = [labelmap[i] for i in sorted(labelmap)]
    idx = {c: i for i, c in enumerate(classes)}
    mat = np.zeros((len(classes), len(classes)), dtype=int)
    for s in samples:
        if s.true_label is None or s.true_label not in idx:
            continue
        mat[idx[s.true_label], s.pred_idx] += 1

    col_w = max(8, max(len(c) for c in classes) + 1)
    header = " " * (col_w + 2) + "".join(f"{c[: col_w - 1]:>{col_w}}" for c in classes)
    print("\n" + header)
    for i, cls in enumerate(classes):
        row = "".join(f"{mat[i, j]:>{col_w}}" for j in range(len(classes)))
        print(f"  {cls[: col_w - 1]:<{col_w}}{row}")

    # Top class-pair confusions, in both directions.
    pairs: list[tuple[str, str, int]] = []
    for i, src in enumerate(classes):
        for j, dst in enumerate(classes):
            if i != j and mat[i, j] > 0:
                pairs.append((src, dst, int(mat[i, j])))
    pairs.sort(key=lambda r: -r[2])
    if pairs:
        print("\nTop class-pair confusions:")
        for src, dst, n in pairs[:10]:
            print(f"  {n:>4}  {src} -> {dst}")


def worst_offenders(
    samples: list[ImageSample],
    labelmap: dict[int, str],
    top_n: int,
    quality: bool,
) -> list[ImageSample]:
    """Print the worst-offender images grouped by class.

    Two buckets per class:
      (a) Misclassified — predicted label differs from folder. Sorted by the
          confidence in the WRONG class (highest first). These are the most
          confidently wrong images, the strongest candidates for relabeling
          or deletion.
      (b) Borderline-correct — predicted label matches but p_true is low.
          These sit near the decision boundary; they're not actively wrong
          but they make the class harder to learn cleanly.

    Returns the union of (a) lists across classes, for optional copying.
    """
    print("\n" + "=" * 78)
    print(f"WORST OFFENDERS  (top {top_n} per class)")
    print("=" * 78)

    by_class: dict[str, list[ImageSample]] = {}
    for s in samples:
        if s.true_label is None:
            continue
        by_class.setdefault(s.true_label, []).append(s)

    all_misclassified: list[ImageSample] = []
    for cls in sorted(by_class):
        rows = by_class[cls]
        miscls = [r for r in rows if r.pred_label != cls]
        miscls.sort(key=lambda r: -r.pred_score)
        all_misclassified.extend(miscls[:top_n])

        print(f"\n-- class '{cls}': {len(miscls)}/{len(rows)} misclassified --")
        if miscls:
            print(
                f"{'p_pred':>7}  {'pred':<18} {'p_true':>7}  {'shape':>11}"
                + ("  blur  bright  aspect  " if quality else "  ")
                + "name"
            )
            for r in miscls[:top_n]:
                shape = f"{r.shape[0]}x{r.shape[1]}"
                extra = ""
                if quality:
                    img = cv2.imread(r.path)
                    blur = laplacian_variance(img) if img is not None else float("nan")
                    bright = mean_brightness(img) if img is not None else float("nan")
                    aspect = aspect_distortion(r.shape)
                    extra = f"  {blur:5.0f}  {bright:6.1f}  {aspect:6.2f}  "
                pt = r.true_score if r.true_score is not None else float("nan")
                print(
                    f"{r.pred_score:7.3f}  {r.pred_label:<18} "
                    f"{pt:7.3f}  {shape:>11}{extra}{os.path.basename(r.path)}"
                )

        # Borderline-correct: labeled right but the model isn't confident.
        correct = [r for r in rows if r.pred_label == cls and r.true_score is not None]
        correct.sort(key=lambda r: r.true_score or 0.0)
        borderline = correct[: max(5, top_n // 3)]
        if borderline:
            print("\n   borderline-correct (lowest p_true while still labeled right):")
            for r in borderline:
                # Second-best class names the neighbor that's pulling on this image.
                if len(r.probs) > 1:
                    second = int(np.argsort(-r.probs)[1])
                    second_lbl = labelmap[second]
                    second_p = float(r.probs[second])
                else:
                    second_lbl = "-"
                    second_p = 0.0
                print(
                    f"    p_true={r.true_score:.3f}  "
                    f"p_2nd={second_p:.3f} ({second_lbl})  "
                    f"{os.path.basename(r.path)}"
                )

    return all_misclassified


# ---------------------------------------------------------------------------
# Optional diagnostics
# ---------------------------------------------------------------------------


def quality_summary(samples: list[ImageSample]) -> None:
    """Compare image-quality stats for correct vs misclassified images.

    Helps answer: are the worst offenders systematically blurrier / darker /
    more squashed than the rest of the class? If so, the fix is to tighten
    the data-collection criteria, not just delete individual images.
    """
    print("\n" + "=" * 78)
    print("IMAGE QUALITY — correct vs misclassified")
    print("=" * 78)

    rows: list[tuple[str, bool, float, float, float]] = []
    for s in samples:
        if s.true_label is None:
            continue
        img = cv2.imread(s.path)
        if img is None:
            continue
        blur = laplacian_variance(img)
        bright = mean_brightness(img)
        aspect = aspect_distortion(s.shape)
        rows.append((s.true_label, s.pred_label == s.true_label, blur, bright, aspect))

    if not rows:
        print("  (no readable images)")
        return

    correct = [r for r in rows if r[1]]
    wrong = [r for r in rows if not r[1]]

    def stats(name: str, getter, group: list) -> None:
        if not group:
            print(f"  {name:<14} (no samples)")
            return
        vals = np.array([getter(r) for r in group])
        print(
            f"  {name:<14} n={len(vals):>4} "
            f"mean={vals.mean():8.2f}  median={np.median(vals):8.2f}  "
            f"p10={np.percentile(vals, 10):8.2f}  p90={np.percentile(vals, 90):8.2f}"
        )

    print("\nBlur (laplacian variance — higher = sharper):")
    stats("correct", lambda r: r[2], correct)
    stats("misclassified", lambda r: r[2], wrong)
    print("\nBrightness (0..255):")
    stats("correct", lambda r: r[3], correct)
    stats("misclassified", lambda r: r[3], wrong)
    print("\nAspect distortion (0 = square; higher = more squashed by 224x224):")
    stats("correct", lambda r: r[4], correct)
    stats("misclassified", lambda r: r[4], wrong)


def summarize_negative(
    neg_samples: list[ImageSample],
    threshold: float,
    labelmap: dict[int, str],
) -> None:
    """Score an unlabeled folder of runtime crops against the model.

    Equivalent to face_dataset.py's negative-set analysis: each image is
    classified, and we print its full probability vector plus whether it
    would clear the configured threshold. High-confidence predictions on
    crops the user knows are wrong indicate the training set is leaking
    a representative image into the wrong class.
    """
    print("\n" + "=" * 78)
    print(f"NEGATIVE SET ANALYSIS  ({len(neg_samples)} images, threshold={threshold})")
    print("=" * 78)

    classes = [labelmap[i] for i in sorted(labelmap)]
    print(f"\n{'pass':>4}  {'score':>6}  {'pred':<18}  full prob vector / name")
    for s in neg_samples:
        passes = "yes" if s.pred_score >= threshold else "no"
        full = " ".join(f"{c}={float(s.probs[i]):.2f}" for i, c in enumerate(classes))
        print(
            f"{passes:>4}  {s.pred_score:6.3f}  {s.pred_label:<18}  "
            f"{full}  ::  {os.path.basename(s.path)}"
        )


def pick_worst_confusion_pair(
    samples: list[ImageSample],
    labelmap: dict[int, str],
) -> tuple[int, str | None, str | None]:
    """Return (count, source, target) for the most-confused class pair."""
    classes = [labelmap[i] for i in sorted(labelmap)]
    pairs: list[tuple[int, str, str]] = []
    for src in classes:
        for tgt in classes:
            if src == tgt:
                continue
            n = sum(1 for s in samples if s.true_label == src and s.pred_label == tgt)
            if n > 0:
                pairs.append((n, src, tgt))
    pairs.sort(reverse=True)
    return pairs[0] if pairs else (0, None, None)


def cross_class_contamination(
    samples: list[ImageSample],
    source_class: str,
    target_class: str,
    label_to_idx: dict[str, int],
    embedder: MobileNetEmbedder,
    top_n: int,
) -> None:
    """Find training images in source_class that visually look like target_class.

    Generalizes face_dataset.py's contamination_analysis to N classes. Uses a
    fixed ImageNet backbone (NOT the user's trained classifier) so that
    contaminators which the trained model has memorized into the source class
    still surface — the trained model's own embedding would hide them.

    Three sections:
      1. Source-image culprits ranked by `cos(img, target_centroid) -
         cos(img, source_centroid)`. Positive delta = the image looks more
         like the target class than its own class — prime relabeling
         candidates.
      2. For each target image, the top-3 nearest source training images.
         Shows the visual chain of confusion image-by-image.
      3. Ringleader summary: source images that appear most often as a top-3
         neighbor across the target set. These few photos are responsible
         for the bulk of the confusion.
    """
    src = [s for s in samples if s.true_label == source_class]
    tgt = [s for s in samples if s.true_label == target_class]

    if not src or not tgt:
        print(
            f"\nERROR: need both classes populated; got {len(src)} "
            f"'{source_class}' and {len(tgt)} '{target_class}'"
        )
        return

    print("\n" + "=" * 78)
    print(
        f"CROSS-CLASS CONTAMINATION  '{source_class}' leaning toward '{target_class}'"
    )
    print("  (model-independent embeddings via ImageNet MobileNetV2)")
    print("=" * 78)

    target_idx = label_to_idx.get(target_class)
    source_idx = label_to_idx.get(source_class)

    print(f"\nEmbedding {len(src) + len(tgt)} images...")
    src_embs = np.stack([embedder.embed(cv2.imread(s.path)) for s in src])
    tgt_embs = np.stack([embedder.embed(cv2.imread(s.path)) for s in tgt])

    src_centroid = src_embs.mean(axis=0)
    src_centroid /= np.linalg.norm(src_centroid) + 1e-9
    tgt_centroid = tgt_embs.mean(axis=0)
    tgt_centroid /= np.linalg.norm(tgt_centroid) + 1e-9

    src_to_src = src_embs @ src_centroid
    src_to_tgt = src_embs @ tgt_centroid
    delta = src_to_tgt - src_to_src

    print(f"\n-- '{source_class}' images sorted by '{target_class}'-likeness --")
    print(f"   positive delta = visually closer to '{target_class}' centroid")
    print(
        f"   p_{target_class} = trained model's probability for '{target_class}' "
        f"on this image\n"
    )
    delta_label = "delta"
    tgt_cos_label = f"cos_{target_class}"[:12]
    src_cos_label = f"cos_{source_class}"[:12]
    p_tgt_label = f"p_{target_class}"[:10]
    print(
        f"  {delta_label:>7}  {tgt_cos_label:>12}  {src_cos_label:>12}  "
        f"{p_tgt_label:>10}  name"
    )
    order = np.argsort(-delta)
    for i in order[:top_n]:
        s = src[i]
        p_tgt = float(s.probs[target_idx]) if target_idx is not None else float("nan")
        print(
            f"  {delta[i]:+7.3f}  {src_to_tgt[i]:12.3f}  {src_to_src[i]:12.3f}  "
            f"{p_tgt:10.3f}  {os.path.basename(s.path)}"
        )

    print(f"\n-- nearest '{source_class}' neighbors for each '{target_class}' image --")
    neighbor_counts: dict[str, int] = {}
    src_paths = [os.path.basename(s.path) for s in src]
    for i, t in enumerate(tgt):
        sims = src_embs @ tgt_embs[i]
        top3 = np.argsort(-sims)[:3]
        p_src = float(t.probs[source_idx]) if source_idx is not None else float("nan")
        marker = "  <<MISCLASSIFIED" if t.pred_label == source_class else ""
        print(
            f"\n  {os.path.basename(t.path)}  "
            f"(pred={t.pred_label}, p_{source_class}={p_src:.3f}){marker}"
        )
        for j in top3:
            print(f"    cos={sims[j]:.3f}  {src_paths[j]}")
            neighbor_counts[src_paths[j]] = neighbor_counts.get(src_paths[j], 0) + 1

    print(
        f"\n-- ringleaders: '{source_class}' images appearing most often as a "
        f"top-3 nearest neighbor of '{target_class}' images --"
    )
    print("   (these few photos are doing most of the damage)\n")
    print(f"  {'count':>5}  name")
    ranked = sorted(neighbor_counts.items(), key=lambda r: -r[1])
    for name, count in ranked[:top_n]:
        print(f"  {count:>5}  {name}")


def save_misclassified(samples: list[ImageSample], out_dir: str) -> None:
    """Copy misclassified images to <out_dir>/<true>__as__<predicted>/<name>.

    Lets you browse the worst offenders in a file manager and bulk-delete or
    relabel them without poking through the original dataset tree.
    """
    print("\n" + "=" * 78)
    print(f"SAVING MISCLASSIFIED IMAGES -> {out_dir}")
    print("=" * 78)
    count = 0
    for s in samples:
        if s.true_label is None or s.pred_label == s.true_label:
            continue
        bucket = os.path.join(out_dir, f"{s.true_label}__as__{s.pred_label}")
        os.makedirs(bucket, exist_ok=True)
        score_tag = f"{int(round(s.pred_score * 100)):03d}"
        dest = os.path.join(bucket, f"{score_tag}_{os.path.basename(s.path)}")
        try:
            shutil.copy2(s.path, dest)
            count += 1
        except OSError as err:
            print(f"  [copy failed] {s.path}: {err}")
    print(f"  copied {count} images into {out_dir}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Analyze a Frigate object-classification training dataset against its "
            "deployed TFLite model."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--name",
        required=True,
        help="Classification model name (matches the key in classification.custom.<name>)",
    )
    ap.add_argument(
        "--clips-dir",
        default=os.path.join(get_media_dir(), "clips"),
        help="Frigate clips directory; dataset is read from <clips>/<name>/dataset",
    )
    ap.add_argument(
        "--model-cache",
        default=get_model_cache_dir(),
        help="Frigate model_cache; model is read from <cache>/<name>/model.tflite",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Score threshold (matches model_config.threshold; default 0.8)",
    )
    ap.add_argument(
        "--top-n",
        type=int,
        default=15,
        help="Worst-offender images to show per class",
    )
    ap.add_argument(
        "--quality",
        action="store_true",
        help="Include blur/brightness/aspect stats for correct vs misclassified",
    )
    ap.add_argument(
        "--negative",
        default=None,
        help="Score an unlabeled folder of crops against the model",
    )
    ap.add_argument(
        "--save-misclassified",
        default=None,
        help="Copy every misclassified image into this directory for review",
    )
    ap.add_argument(
        "--confuses",
        default=None,
        help=(
            "Cross-class contamination analysis. Format '<source>:<target>', "
            "e.g. 'rex:buddy' to find Rex training images that look like "
            "Buddy. Use 'auto' to pick the worst pair from the confusion matrix. "
            "Requires --embedding-model."
        ),
    )
    ap.add_argument(
        "--embedding-model",
        default=None,
        help=(
            "Path to ONNX MobileNetV2 file for model-independent embeddings "
            "(required by --confuses). Download mobilenetv2-7.onnx from "
            "the ONNX model repository into the Windows model cache."
        ),
    )
    args = ap.parse_args()

    dataset_dir = os.path.join(args.clips_dir, args.name, "dataset")
    model_path = os.path.join(args.model_cache, args.name, "model.tflite")
    labelmap_path = os.path.join(args.model_cache, args.name, "labelmap.txt")

    for required in (dataset_dir, model_path, labelmap_path):
        if not os.path.exists(required):
            print(f"ERROR: required path not found: {required}")
            return 1

    print(f"Loading model from {model_path}")
    interpreter, input_details, output_details = load_tflite(model_path)
    labelmap = load_labelmap(labelmap_path)
    label_to_idx = {v: k for k, v in labelmap.items()}
    print(f"  labels: {sorted(labelmap.values())}")

    print(f"\nScanning dataset at {dataset_dir} ...")
    samples = classify_dataset(
        dataset_dir, interpreter, input_details, output_details, labelmap, label_to_idx
    )
    if not samples:
        print("no images found — aborting")
        return 1
    print(f"  classified {len(samples)} images")

    summarize_dataset(samples, labelmap)
    confusion_matrix(samples, labelmap)
    misclassified = worst_offenders(samples, labelmap, args.top_n, args.quality)

    if args.quality:
        quality_summary(samples)

    if args.negative:
        print(f"\nLoading negatives from {args.negative} ...")
        neg = classify_folder(
            args.negative,
            interpreter,
            input_details,
            output_details,
            labelmap,
            label_to_idx,
            true_label=None,
        )
        if neg:
            summarize_negative(neg, args.threshold, labelmap)

    if args.confuses:
        if not args.embedding_model:
            print(
                "\nERROR: --confuses requires --embedding-model (path to ONNX "
                "MobileNetV2). See --help for the download command."
            )
            return 1
        try:
            embedder = MobileNetEmbedder(args.embedding_model)
        except (FileNotFoundError, cv2.error) as err:
            print(f"\nERROR: failed to load embedding model: {err}")
            return 1

        if args.confuses == "auto":
            n, src, tgt = pick_worst_confusion_pair(samples, labelmap)
            if src is None:
                print(
                    "\nNo misclassifications in dataset — "
                    "nothing to investigate via --confuses auto"
                )
            else:
                print(f"\nAuto-picked worst confusion: {src} -> {tgt} ({n} cases)")
                cross_class_contamination(
                    samples, src, tgt, label_to_idx, embedder, args.top_n
                )
        else:
            if ":" not in args.confuses:
                print("\nERROR: --confuses expects '<source>:<target>' or 'auto'")
                return 1
            src, tgt = args.confuses.split(":", 1)
            if src not in label_to_idx or tgt not in label_to_idx:
                print(
                    f"\nERROR: class names must be in the labelmap "
                    f"({sorted(label_to_idx)})"
                )
                return 1
            cross_class_contamination(
                samples, src, tgt, label_to_idx, embedder, args.top_n
            )

    if args.save_misclassified:
        save_misclassified(misclassified, args.save_misclassified)

    return 0


if __name__ == "__main__":
    sys.exit(main())
