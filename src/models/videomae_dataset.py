"""Dataset for VideoMAE multi-head fine-tuning on labeled clips.

Scans ``output/clips/*/clips_metadata.json`` for every record that has an
``attributes`` dict written by :func:`utils.labels.extract_attributes`, loads
its MP4, samples a fixed number of frames, and yields ``(pixel_values,
label_dict)`` suitable for training.

Label encoding
--------------
Each attribute head uses a deterministic string -> int mapping derived from
``ATTRIBUTE_SCHEMA``. A value of ``None`` (or a value outside the schema) maps
to ``-100``, matching ``nn.CrossEntropyLoss(ignore_index=-100)`` so only valid
fields contribute to the loss. ``team`` is handled separately: its vocabulary
is built from the data at dataset construction time.

Sampling
--------
Frames are sampled uniformly across the clip duration. Training augmentations:

- random horizontal flip (basketball is roughly symmetric; no team-side
  semantics are modeled today),
- random temporal crop within the middle 80% of the clip,
- mild color jitter,
- random spatial crop + resize to ``image_size`` (default 224).

Validation uses deterministic center-crop with uniform temporal sampling.
"""

from __future__ import annotations

import json
import os
import random
import sys
from dataclasses import dataclass
from glob import glob
from typing import Dict, List, Optional, Tuple

import av
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset

# Allow ``python src/models/*.py`` to resolve the ``utils`` package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.labels import ATTRIBUTE_SCHEMA  # noqa: E402


IGNORE_INDEX = -100

# VideoMAE was pretrained with ImageNet normalization.
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


@dataclass
class ClipRecord:
    path: str
    attributes: Dict
    duration: float


def _build_class_maps(schema: Dict[str, List[str]]) -> Dict[str, Dict[str, int]]:
    """Fixed string -> int map for every schema head (None is handled separately)."""
    return {head: {value: idx for idx, value in enumerate(values)} for head, values in schema.items()}


def _encode_value(value, mapping: Dict[str, int]) -> int:
    if value is None:
        return IGNORE_INDEX
    return mapping.get(value, IGNORE_INDEX)


def discover_clip_records(clips_glob: str = "output/clips/*/clips_metadata.json") -> List[ClipRecord]:
    """Find every clip with an ``attributes`` dict and a readable MP4 on disk."""
    records: List[ClipRecord] = []
    missing = 0
    for meta_path in sorted(glob(clips_glob)):
        run_dir = os.path.dirname(meta_path)
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            continue
        for clip in meta.get("clips", []):
            attrs = clip.get("attributes")
            if not attrs:
                continue
            filename = clip.get("filename")
            if not filename:
                continue
            clip_path = os.path.join(run_dir, filename)
            if not os.path.exists(clip_path):
                missing += 1
                continue
            records.append(
                ClipRecord(
                    path=clip_path,
                    attributes=attrs,
                    duration=float(clip.get("duration", 0.0)),
                )
            )
    if missing:
        print(f"[videomae_dataset] skipped {missing} clips with missing MP4 files")
    return records


def build_team_vocab(records: List[ClipRecord]) -> Dict[str, int]:
    """Collect all observed team codes into a stable string -> int mapping."""
    teams = sorted({r.attributes.get("team") for r in records if r.attributes.get("team")})
    return {team: idx for idx, team in enumerate(teams)}


class ClipAttributeDataset(Dataset):
    """Yield ``(pixel_values, label_dict)`` for VideoMAE multi-head training.

    Parameters
    ----------
    records:
        List of :class:`ClipRecord` returned by :func:`discover_clip_records`.
    class_maps:
        ``{head: {value: idx}}`` for every schema head. Usually the output of
        :func:`build_class_maps` for ``ATTRIBUTE_SCHEMA``.
    team_vocab:
        ``{team_code: idx}`` for the ``team`` head.
    num_frames:
        Number of frames sampled per clip (must match the VideoMAE checkpoint,
        typically 16).
    image_size:
        Target HxW after resize/crop.
    training:
        If True, applies random augmentation. If False, deterministic sampling.
    """

    def __init__(
        self,
        records: List[ClipRecord],
        class_maps: Dict[str, Dict[str, int]],
        team_vocab: Dict[str, int],
        num_frames: int = 16,
        image_size: int = 224,
        training: bool = True,
    ):
        self.records = records
        self.class_maps = class_maps
        self.team_vocab = team_vocab
        self.num_frames = num_frames
        self.image_size = image_size
        self.training = training

    def __len__(self) -> int:
        return len(self.records)

    # ---- label encoding ----

    def encode_labels(self, attrs: Dict) -> Dict[str, int]:
        labels = {head: _encode_value(attrs.get(head), mapping) for head, mapping in self.class_maps.items()}
        team = attrs.get("team")
        labels["team"] = self.team_vocab.get(team, IGNORE_INDEX) if team else IGNORE_INDEX
        return labels

    # ---- frame loading ----

    def _load_frames(self, path: str) -> torch.Tensor:
        """Decode clip frames with PyAV and return (T, C, H, W) float tensor.

        We route through PyAV + PIL + ``torch.frombuffer`` to avoid the
        torch/numpy ABI bridge, which breaks whenever torch and NumPy are
        built against different numpy majors (see the torchvision.io issue
        with numpy 2.x on torch 2.2).
        """
        container = av.open(path)
        try:
            pil_frames = [f.to_image() for f in container.decode(video=0)]
        finally:
            container.close()

        total = len(pil_frames)
        if total == 0:
            raise RuntimeError(f"Empty video: {path}")

        if self.training:
            # Sample from the middle 80% of the clip so augmentation doesn't
            # pick up title-card artifacts right at the edges.
            lo = int(total * 0.1)
            hi = max(lo + self.num_frames, int(total * 0.9))
            hi = min(hi, total)
            if hi - lo < self.num_frames:
                lo, hi = 0, total
            indices = torch.linspace(lo, max(hi - 1, lo), steps=self.num_frames).long().tolist()
        else:
            indices = torch.linspace(0, total - 1, steps=self.num_frames).long().tolist()

        w, h = pil_frames[0].size
        tensors: List[torch.Tensor] = []
        for idx in indices:
            img = pil_frames[idx]
            if img.mode != "RGB":
                img = img.convert("RGB")
            # frombuffer requires a mutable source; bytearray() copies once.
            t = torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8).view(h, w, 3)
            tensors.append(t)
        frames = torch.stack(tensors, dim=0)  # (T, H, W, C)
        frames = frames.permute(0, 3, 1, 2).float() / 255.0  # (T, C, H, W)
        return frames

    # ---- augmentation ----

    def _augment(self, frames: torch.Tensor) -> torch.Tensor:
        if self.training and random.random() < 0.5:
            frames = torch.flip(frames, dims=[-1])
        if self.training and random.random() < 0.7:
            # mild color jitter applied consistently across all frames
            brightness = 1.0 + (random.random() - 0.5) * 0.3
            contrast = 1.0 + (random.random() - 0.5) * 0.3
            frames = frames * brightness
            mean = frames.mean(dim=[-1, -2], keepdim=True)
            frames = (frames - mean) * contrast + mean
            frames = frames.clamp(0.0, 1.0)
        return frames

    def _resize_and_crop(self, frames: torch.Tensor) -> torch.Tensor:
        # frames: (T, C, H, W)
        t, c, h, w = frames.shape
        short_side = self.image_size + 32 if self.training else self.image_size
        # Resize so the short side is short_side.
        if h < w:
            new_h = short_side
            new_w = int(w * short_side / h)
        else:
            new_w = short_side
            new_h = int(h * short_side / w)
        frames = F.interpolate(frames, size=(new_h, new_w), mode="bilinear", align_corners=False)

        if self.training:
            top = random.randint(0, new_h - self.image_size)
            left = random.randint(0, new_w - self.image_size)
        else:
            top = (new_h - self.image_size) // 2
            left = (new_w - self.image_size) // 2
        frames = frames[:, :, top:top + self.image_size, left:left + self.image_size]
        return frames

    # ---- main entrypoint ----

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, Dict[str, int]]:
        rec = self.records[idx]
        try:
            frames = self._load_frames(rec.path)
        except Exception as e:
            raise RuntimeError(f"Failed to read clip {rec.path}: {e}")

        frames = self._resize_and_crop(frames)
        frames = self._augment(frames)

        # Normalize with ImageNet stats expected by VideoMAE.
        frames = (frames - MEAN) / STD
        labels = self.encode_labels(rec.attributes)
        return frames, labels


def build_class_maps() -> Dict[str, Dict[str, int]]:
    """Convenience: build the fixed label-to-index tables for every head."""
    return _build_class_maps(ATTRIBUTE_SCHEMA)


def load_preprocessed_clip(
    path: str,
    num_frames: int = 16,
    image_size: int = 224,
) -> torch.Tensor:
    """Deterministic single-clip loader for prediction (validation-style)."""
    records = [ClipRecord(path=path, attributes={}, duration=0.0)]
    ds = ClipAttributeDataset(
        records=records,
        class_maps=build_class_maps(),
        team_vocab={},
        num_frames=num_frames,
        image_size=image_size,
        training=False,
    )
    frames, _ = ds[0]
    return frames


def collate_batch(
    batch: List[Tuple[torch.Tensor, Dict[str, int]]],
) -> Tuple[torch.Tensor, Dict[str, torch.Tensor]]:
    """Stack clip tensors and labels into a batch."""
    pixels = torch.stack([b[0] for b in batch], dim=0)
    labels: Dict[str, torch.Tensor] = {}
    keys = batch[0][1].keys()
    for k in keys:
        labels[k] = torch.tensor([b[1][k] for b in batch], dtype=torch.long)
    return pixels, labels
