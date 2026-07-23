#!/usr/bin/env python3
"""Run a trained multi-head VideoMAE on clips and emit structured predictions.

Modes
-----
- ``--clip <mp4>``: predict on a single clip.
- ``--clips-dir <dir>``: predict on every ``.mp4`` in a directory.
- ``--metadata <clips_metadata.json>``: predict on every clip referenced in
  the metadata file. With ``--write-back``, predictions are written into
  each clip record under ``predicted_attributes`` / ``predicted_scores`` and
  the file is saved in place.

Output (stdout and/or JSON file) per clip:
    {
      "path": "...",
      "predictions": {head: {"label": str|None, "prob": float}},
      "top3": {head: [{"label": str, "prob": float}, ...]}
    }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from glob import glob
from typing import Dict, List, Optional

import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.videomae_dataset import load_preprocessed_clip  # noqa: E402
from models.videomae_multihead import load_checkpoint  # noqa: E402


def _decode_index_map(mapping: Dict[str, int]) -> List[str]:
    """Invert a ``{label: idx}`` dict into an index-ordered list of labels."""
    inv = sorted(mapping.items(), key=lambda kv: kv[1])
    return [k for k, _ in inv]


def predict_clip(
    model,
    clip_path: str,
    idx_to_label: Dict[str, List[str]],
    num_frames: int,
    image_size: int,
    device: torch.device,
    top_k: int = 3,
) -> Dict:
    frames = load_preprocessed_clip(clip_path, num_frames=num_frames, image_size=image_size)
    frames = frames.unsqueeze(0).to(device)
    with torch.no_grad():
        logits = model(frames)

    predictions: Dict[str, Dict] = {}
    topk: Dict[str, List[Dict]] = {}
    for head, head_logits in logits.items():
        probs = F.softmax(head_logits[0], dim=-1).cpu()
        labels = idx_to_label.get(head, [])
        if not labels or probs.numel() == 0:
            predictions[head] = {"label": None, "prob": 0.0}
            topk[head] = []
            continue
        top_idx = probs.argmax().item()
        predictions[head] = {
            "label": labels[top_idx] if top_idx < len(labels) else None,
            "prob": float(probs[top_idx].item()),
        }
        k = min(top_k, len(labels), probs.numel())
        vals, inds = torch.topk(probs, k=k)
        topk[head] = [
            {"label": labels[int(i)] if int(i) < len(labels) else None, "prob": float(v)}
            for v, i in zip(vals.tolist(), inds.tolist())
        ]

    return {"path": clip_path, "predictions": predictions, "top3": topk}


def collect_paths(args) -> List[str]:
    if args.clip:
        return [args.clip]
    if args.clips_dir:
        return sorted(glob(os.path.join(args.clips_dir, "*.mp4")))
    if args.metadata:
        run_dir = os.path.dirname(args.metadata)
        with open(args.metadata) as f:
            meta = json.load(f)
        paths = []
        for clip in meta.get("clips", []):
            fn = clip.get("filename")
            if fn:
                p = os.path.join(run_dir, fn)
                if os.path.exists(p):
                    paths.append(p)
        return paths
    return []


def write_back_metadata(metadata_path: str, results: List[Dict]) -> None:
    """Merge predictions back into sibling ``clips_metadata.json`` records by filename."""
    with open(metadata_path) as f:
        meta = json.load(f)

    by_filename = {os.path.basename(r["path"]): r for r in results}
    for clip in meta.get("clips", []):
        fn = clip.get("filename")
        if not fn or fn not in by_filename:
            continue
        r = by_filename[fn]
        clip["predicted_attributes"] = {h: v["label"] for h, v in r["predictions"].items()}
        clip["predicted_scores"] = {h: v["prob"] for h, v in r["predictions"].items()}
        clip["predicted_top3"] = r["top3"]

    with open(metadata_path, "w") as f:
        json.dump(meta, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Predict multi-head attributes for clips")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--clip", help="Path to a single clip MP4")
    src.add_argument("--clips-dir", help="Directory of clip MP4s")
    src.add_argument("--metadata", help="Path to clips_metadata.json")

    parser.add_argument("--model", default="output/videomae_model/videomae_multihead.pt")
    parser.add_argument("--checkpoint", default="MCG-NJU/videomae-base-finetuned-kinetics")
    parser.add_argument("--out", default=None, help="Optional path to write predictions JSON")
    parser.add_argument("--write-back", action="store_true", help="With --metadata, update the JSON in place")
    parser.add_argument("--num-frames", type=int, default=None)
    parser.add_argument("--image-size", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    if args.device:
        device = torch.device(args.device)
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        # VideoMAE uses Conv3d which MPS doesn't support on torch 2.2.
        device = torch.device("cpu")
    print(f"Device: {device}")

    payload = load_checkpoint(args.model, checkpoint=args.checkpoint, device=device)
    model = payload["model"].eval()
    class_maps: Dict[str, Dict[str, int]] = payload.get("class_maps", {})
    team_vocab: Dict[str, int] = payload.get("team_vocab", {})

    idx_to_label: Dict[str, List[str]] = {h: _decode_index_map(m) for h, m in class_maps.items()}
    idx_to_label["team"] = _decode_index_map(team_vocab)

    num_frames = args.num_frames or payload.get("num_frames", 16)
    image_size = args.image_size or payload.get("image_size", 224)

    paths = collect_paths(args)
    if not paths:
        print("No clips found for the given input.")
        return 1
    print(f"Predicting on {len(paths)} clip(s)")

    results: List[Dict] = []
    for p in paths:
        try:
            r = predict_clip(
                model, p, idx_to_label,
                num_frames=num_frames,
                image_size=image_size,
                device=device,
                top_k=args.top_k,
            )
        except Exception as e:
            print(f"  skip {p}: {e}")
            continue
        results.append(r)
        primary = r["predictions"].get("action_type", {}).get("label")
        subtype = r["predictions"].get("shot_subtype", {}).get("label")
        outcome = r["predictions"].get("outcome", {}).get("label")
        print(f"  {os.path.basename(p)}: action={primary} subtype={subtype} outcome={outcome}")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump({"results": results}, f, indent=2)
        print(f"\nWrote {args.out}")

    if args.write_back and args.metadata:
        write_back_metadata(args.metadata, results)
        print(f"Updated {args.metadata}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
