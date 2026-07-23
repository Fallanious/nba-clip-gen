#!/usr/bin/env python3
"""Fine-tune a multi-head VideoMAE on labeled clips.

Architecture:
    Clip MP4 -> 16-frame sampler (ClipAttributeDataset)
             -> VideoMAE backbone (pretrained, Kinetics-finetuned)
             -> per-attribute linear heads
             -> masked cross-entropy loss (sum across heads)

Training schedule:
    Phase 1 (``--freeze-epochs``): backbone frozen, heads-only.
    Phase 2: backbone unfrozen, LR scaled down 10x via parameter groups.

Imbalance handling:
    WeightedRandomSampler on ``action_type`` (inverse frequency) so rare
    classes (free_throw, steal, turnover) get oversampled per epoch.
    Each per-head CrossEntropyLoss also uses inverse-frequency class weights.

Checkpointing:
    Best model by mean validation macro-F1 across heads. Report JSON
    includes per-head confusion matrices and class counts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from typing import Dict, List

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, WeightedRandomSampler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.videomae_dataset import (  # noqa: E402
    ClipAttributeDataset,
    IGNORE_INDEX,
    build_class_maps,
    build_team_vocab,
    collate_batch,
    discover_clip_records,
)
from models.videomae_multihead import VideoMAEMultiHead, save_checkpoint  # noqa: E402


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def build_class_weights(labels: List[int], num_classes: int) -> torch.Tensor:
    """Inverse-frequency weights, ignoring IGNORE_INDEX entries."""
    counts = torch.zeros(num_classes, dtype=torch.float)
    for l in labels:
        if l == IGNORE_INDEX:
            continue
        if 0 <= l < num_classes:
            counts[l] += 1
    counts[counts == 0] = 1.0
    weights = counts.sum() / (num_classes * counts)
    return weights


def build_sample_weights(action_labels: List[int]) -> List[float]:
    """Inverse-frequency per-sample weight for WeightedRandomSampler.

    Samples with IGNORE_INDEX on ``action_type`` get weight 1.0 since we don't
    want to drop them (they may still supervise other heads).
    """
    counts = Counter(a for a in action_labels if a != IGNORE_INDEX)
    total = sum(counts.values())
    weights = []
    for a in action_labels:
        if a == IGNORE_INDEX or counts.get(a, 0) == 0:
            weights.append(1.0)
        else:
            weights.append(total / (len(counts) * counts[a]))
    return weights


def per_head_metrics(
    preds: Dict[str, torch.Tensor],
    targets: Dict[str, torch.Tensor],
    head_names: Dict[str, List[str]],
) -> Dict[str, Dict]:
    """Macro-F1 and confusion matrix per head on validation set."""
    out: Dict[str, Dict] = {}
    for head, names in head_names.items():
        p = preds[head].cpu()
        t = targets[head].cpu()
        mask = t != IGNORE_INDEX
        p = p[mask]
        t = t[mask]
        n = len(names)
        if n == 0 or p.numel() == 0:
            out[head] = {"macro_f1": 0.0, "per_class": {}, "confusion_matrix": [], "support": 0}
            continue
        cm = torch.zeros((n, n), dtype=torch.long)
        for yt, yp in zip(t.tolist(), p.tolist()):
            if 0 <= yt < n and 0 <= yp < n:
                cm[yt, yp] += 1
        per_class = {}
        f1s = []
        for i, lbl in enumerate(names):
            tp = int(cm[i, i])
            fp = int(cm[:, i].sum().item() - tp)
            fn = int(cm[i, :].sum().item() - tp)
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
            per_class[lbl] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": int(cm[i, :].sum().item()),
            }
            f1s.append(f1)
        out[head] = {
            "macro_f1": float(sum(f1s) / len(f1s)) if f1s else 0.0,
            "per_class": per_class,
            "confusion_matrix": cm.tolist(),
            "support": int(mask.sum().item()),
        }
    return out


def run_epoch(
    model: VideoMAEMultiHead,
    loader: DataLoader,
    criteria: Dict[str, nn.Module],
    device: torch.device,
    optimizer: torch.optim.Optimizer = None,
) -> Dict:
    """Forward pass over a loader. If ``optimizer`` is set, also backward+step."""
    is_train = optimizer is not None
    model.train(is_train)

    total_loss = 0.0
    total_batches = 0
    head_losses: Dict[str, float] = {h: 0.0 for h in model.heads}
    all_preds: Dict[str, List[int]] = {h: [] for h in model.heads}
    all_targets: Dict[str, List[int]] = {h: [] for h in model.heads}

    for pixels, labels in loader:
        pixels = pixels.to(device, non_blocking=True)
        labels = {k: v.to(device, non_blocking=True) for k, v in labels.items()}

        with torch.set_grad_enabled(is_train):
            logits = model(pixels)
            loss = 0.0
            for head in model.heads:
                l = criteria[head](logits[head], labels[head])
                head_losses[head] += float(l.item())
                loss = loss + l

            if is_train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        total_loss += float(loss.item())
        total_batches += 1
        for head in model.heads:
            all_preds[head].extend(logits[head].argmax(dim=-1).cpu().tolist())
            all_targets[head].extend(labels[head].cpu().tolist())

    return {
        "loss": total_loss / max(total_batches, 1),
        "head_losses": {h: v / max(total_batches, 1) for h, v in head_losses.items()},
        "preds": {h: torch.tensor(v) for h, v in all_preds.items()},
        "targets": {h: torch.tensor(v) for h, v in all_targets.items()},
    }


def main():
    parser = argparse.ArgumentParser(description="Train multi-head VideoMAE on labeled clips")
    parser.add_argument("--clips-glob", default="output/clips/*/clips_metadata.json")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--freeze-epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-3, help="LR for heads; backbone uses lr/10 after unfreeze")
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-frames", type=int, default=16)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--out-dir", default="output/videomae_model")
    parser.add_argument("--checkpoint", default="MCG-NJU/videomae-base-finetuned-kinetics")
    parser.add_argument("--device", default=None, help="cpu|cuda|mps (default: auto)")
    parser.add_argument("--max-records", type=int, default=0, help="Cap dataset size for quick experiments")
    args = parser.parse_args()

    set_seed(args.seed)

    if args.device:
        device = torch.device(args.device)
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        # VideoMAE's patch embedding uses Conv3d, which MPS does not support
        # until torch >= 2.3. Fall back to CPU on macOS by default; users on
        # a newer torch can still force mps with --device mps.
        device = torch.device("cpu")
    print(f"Device: {device}")

    # ---- data ----
    records = discover_clip_records(args.clips_glob)
    if args.max_records:
        records = records[: args.max_records]
    print(f"Discovered {len(records)} labeled clips")
    if len(records) < 8:
        print("Too few records to train meaningfully.")
        return 1

    class_maps = build_class_maps()
    team_vocab = build_team_vocab(records)

    # Shuffle & split.
    random.shuffle(records)
    val_n = max(1, int(len(records) * args.val_split))
    val_records = records[:val_n]
    train_records = records[val_n:]

    train_ds = ClipAttributeDataset(
        train_records, class_maps, team_vocab,
        num_frames=args.num_frames, image_size=args.image_size, training=True,
    )
    val_ds = ClipAttributeDataset(
        val_records, class_maps, team_vocab,
        num_frames=args.num_frames, image_size=args.image_size, training=False,
    )

    # Weighted sampler balances action_type across an epoch so rare classes
    # (free_throw, steal, turnover) get trained on roughly equally.
    train_action_labels = [
        train_ds.encode_labels(r.attributes)["action_type"] for r in train_records
    ]
    sample_weights = build_sample_weights(train_action_labels)
    sampler = WeightedRandomSampler(
        sample_weights,
        num_samples=len(sample_weights),
        replacement=True,
    )

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        sampler=sampler,
        num_workers=args.num_workers,
        collate_fn=collate_batch,
        pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        collate_fn=collate_batch,
        pin_memory=(device.type == "cuda"),
    )

    # ---- model ----
    model = VideoMAEMultiHead(
        team_vocab_size=len(team_vocab),
        checkpoint=args.checkpoint,
    ).to(device)

    # ---- per-head loss with inverse-frequency class weights ----
    head_names: Dict[str, List[str]] = {
        head: list(mapping.keys()) for head, mapping in class_maps.items()
    }
    team_names = sorted(team_vocab.keys(), key=lambda t: team_vocab[t])
    head_names["team"] = team_names

    criteria: Dict[str, nn.Module] = {}
    for head in model.heads:
        n_classes = model.class_counts[head]
        train_labels = [
            train_ds.encode_labels(r.attributes)[head] for r in train_records
        ]
        weights = build_class_weights(train_labels, n_classes).to(device)
        criteria[head] = nn.CrossEntropyLoss(weight=weights, ignore_index=IGNORE_INDEX)

    # ---- optimizer (phase 1: heads only) ----
    model.freeze_backbone()
    optimizer = torch.optim.AdamW(
        [p for p in model.head_parameters()],
        lr=args.lr,
        weight_decay=args.weight_decay,
    )

    # ---- training loop ----
    os.makedirs(args.out_dir, exist_ok=True)
    best = {"epoch": 0, "mean_macro_f1": -1.0, "metrics": None}
    epoch_history = []

    for epoch in range(1, args.epochs + 1):
        if epoch == args.freeze_epochs + 1:
            print(f"Unfreezing backbone at epoch {epoch} (LR/10 on backbone group).")
            model.unfreeze_backbone()
            optimizer = torch.optim.AdamW(
                [
                    {"params": list(model.backbone_parameters()), "lr": args.lr / 10.0},
                    {"params": list(model.head_parameters()), "lr": args.lr},
                ],
                weight_decay=args.weight_decay,
            )

        tr = run_epoch(model, train_loader, criteria, device, optimizer=optimizer)
        vl = run_epoch(model, val_loader, criteria, device, optimizer=None)
        val_metrics = per_head_metrics(vl["preds"], vl["targets"], head_names)
        mean_f1 = sum(m["macro_f1"] for m in val_metrics.values()) / max(len(val_metrics), 1)

        print(
            f"epoch={epoch:3d} train_loss={tr['loss']:.4f} val_loss={vl['loss']:.4f} "
            f"val_mean_macro_f1={mean_f1:.3f} "
            + " ".join(f"{h}_f1={m['macro_f1']:.2f}" for h, m in val_metrics.items())
        )
        epoch_history.append({
            "epoch": epoch,
            "train_loss": tr["loss"],
            "val_loss": vl["loss"],
            "val_mean_macro_f1": mean_f1,
            "val_head_f1": {h: m["macro_f1"] for h, m in val_metrics.items()},
        })

        if mean_f1 > best["mean_macro_f1"]:
            best["epoch"] = epoch
            best["mean_macro_f1"] = mean_f1
            best["metrics"] = val_metrics
            save_checkpoint(
                model,
                os.path.join(args.out_dir, "videomae_multihead.pt"),
                team_vocab=team_vocab,
                class_maps=class_maps,
                extra={
                    "best_epoch": epoch,
                    "best_mean_macro_f1": mean_f1,
                    "num_frames": args.num_frames,
                    "image_size": args.image_size,
                },
            )

    # ---- final report ----
    report = {
        "sample_count": len(records),
        "train_count": len(train_records),
        "val_count": len(val_records),
        "team_vocab_size": len(team_vocab),
        "class_maps": class_maps,
        "team_names": team_names,
        "best_epoch": best["epoch"],
        "best_mean_macro_f1": best["mean_macro_f1"],
        "best_metrics": best["metrics"],
        "epoch_history": epoch_history,
        "args": vars(args),
    }
    report_path = os.path.join(args.out_dir, "train_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nSaved model: {os.path.join(args.out_dir, 'videomae_multihead.pt')}")
    print(f"Saved report: {report_path}")
    print(f"Best epoch {best['epoch']} mean macro-F1 {best['mean_macro_f1']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
