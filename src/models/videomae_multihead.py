"""VideoMAE backbone with one linear head per attribute.

Wraps a pretrained VideoMAE encoder from HuggingFace and attaches a
classification head for every entry in ``ATTRIBUTE_SCHEMA`` plus a team head
whose vocabulary is supplied at construction time (since team codes are
data-dependent, not fixed).

Why one backbone + multiple heads
---------------------------------
With ~500 labeled clips, training one classifier per attribute would either
overfit or fail to converge for the rare heads (e.g. free_throw,
putback_dunk). Sharing a backbone means every labeled clip supervises up to
six heads, so the representation sees 5-6x more gradient signal per batch
than the single-label baseline.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

import torch
import torch.nn as nn
from transformers import VideoMAEConfig, VideoMAEModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.labels import ATTRIBUTE_SCHEMA  # noqa: E402


DEFAULT_CHECKPOINT = "MCG-NJU/videomae-base-finetuned-kinetics"


def _head_class_counts(team_vocab_size: int) -> Dict[str, int]:
    """Per-head class count including the team head."""
    counts = {head: len(values) for head, values in ATTRIBUTE_SCHEMA.items()}
    counts["team"] = max(team_vocab_size, 1)
    return counts


class VideoMAEMultiHead(nn.Module):
    """VideoMAE backbone with per-attribute classification heads."""

    def __init__(
        self,
        team_vocab_size: int,
        checkpoint: str = DEFAULT_CHECKPOINT,
        dropout: float = 0.1,
        pretrained: bool = True,
    ):
        super().__init__()
        if pretrained:
            self.backbone = VideoMAEModel.from_pretrained(checkpoint)
        else:
            # Cheap path for unit-style tests that don't need downloaded weights.
            self.backbone = VideoMAEModel(VideoMAEConfig())

        hidden = self.backbone.config.hidden_size
        self.dropout = nn.Dropout(dropout)

        self.class_counts: Dict[str, int] = _head_class_counts(team_vocab_size)
        self.heads = nn.ModuleDict({name: nn.Linear(hidden, count) for name, count in self.class_counts.items()})

    # ---- freezing ----

    def freeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = False

    def unfreeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = True

    def backbone_parameters(self):
        return self.backbone.parameters()

    def head_parameters(self):
        return self.heads.parameters()

    # ---- forward ----

    def forward(self, pixel_values: torch.Tensor) -> Dict[str, torch.Tensor]:
        """Return ``{head_name: logits}``.

        ``pixel_values`` has shape ``(B, T, C, H, W)`` as produced by
        :class:`ClipAttributeDataset`.
        """
        outputs = self.backbone(pixel_values=pixel_values)
        # Mean-pool the token embeddings to get a single clip embedding.
        # VideoMAE does not use a [CLS] token by default, so pooling is the
        # standard downstream choice.
        embedding = outputs.last_hidden_state.mean(dim=1)
        embedding = self.dropout(embedding)
        return {name: head(embedding) for name, head in self.heads.items()}


def save_checkpoint(
    model: VideoMAEMultiHead,
    out_path: str,
    team_vocab: Dict[str, int],
    class_maps: Dict[str, Dict[str, int]],
    extra: Optional[Dict] = None,
) -> None:
    """Persist model weights alongside the exact label vocabularies used at train time."""
    payload = {
        "model_state_dict": {k: v.cpu() for k, v in model.state_dict().items()},
        "team_vocab": team_vocab,
        "class_maps": class_maps,
        "class_counts": model.class_counts,
        "checkpoint": getattr(model.backbone.config, "_name_or_path", None),
    }
    if extra:
        payload.update(extra)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    torch.save(payload, out_path)


def load_checkpoint(
    path: str,
    checkpoint: str = DEFAULT_CHECKPOINT,
    device: Optional[torch.device] = None,
) -> Dict:
    """Load a saved checkpoint dict along with a ready-to-use model."""
    payload = torch.load(path, map_location=device or "cpu")
    team_vocab: Dict[str, int] = payload["team_vocab"]
    model = VideoMAEMultiHead(team_vocab_size=len(team_vocab), checkpoint=checkpoint)
    model.load_state_dict(payload["model_state_dict"], strict=True)
    if device is not None:
        model = model.to(device)
    payload["model"] = model
    return payload
