#!/usr/bin/env python3
# Cuts ALL linked videos in an EAF, respecting TIME_ORIGIN, without merging repeats.
# For each event occurrence, it:
#  - trims tiers (EAF) to the interval (rebased to 0),
#  - cuts all linked videos using each video's TIME_ORIGIN,
#  - exports a Whisper-like per-speaker JSON (from subtitle tiers),
#  - exports a CSV of trimmed annotations from all NON-subtitle tiers.
#
# Requirements: pip install pympi-ling lxml
# Needs ffmpeg/ffprobe in PATH.

import os, json, argparse, subprocess
from pathlib import Path
from typing import List, Tuple, Dict, Optional

from lxml import etree
import pympi.Elan as elan

# ---------- FFmpeg helpers ----------

def ffprobe_duration(path: str) -> Optional[float]:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-select_streams","v:0",
             " -show_entries","format=duration",
             "-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.STDOUT
        )
        return float(out.decode().strip())
    except Exception:
        return None

def ffmpeg_cut(src: str, start_s: float, end_s: float, out_path: str):
    dur = max(0.0, end_s - start_s)
    if dur <= 0:
        return
    cmd = ["ffmpeg","-y","-ss",f"{start_s:.3f}","-i",src,"-t",f"{dur:.3f}","-c","copy",out_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# ---------- Utilities ----------

def safe_label(s: str) -> str:
    keep = "".join(c for c in s if c.isalnum() or c in "-_")
    return keep or "event"
def iter_ann(eaf, tier_name):
    """
    Yield (a0_ms, a1_ms, val) for all annotations in tier_name,
    regardless of whether pympi returns 3 or 4 items.
    """
    for rec in eaf.get_annotation_data_for_tier(tier_name):
        if len(rec) == 4:
            a0, a1, val, _ = rec
        elif len(rec) == 3:
            a0, a1, val = rec
        else:
            # Unknown shape, skip defensively
            continue
        # Cast to ints (ELAN times are ms; sometimes floats)
        try:
            a0 = int(round(float(a0)))
            a1 = int(round(float(a1)))
        except Exception:
            # If something is weird, skip this annotation
            continue
        yield a0, a1, val

def read_media_with_offsets(eaf_path: str) -> List[Dict]:
    base = Path(eaf_path).parent
    tree = etree.parse(eaf_path)
    root = tree.getroot()
    ns = root.nsmap.get(None, '')
    def t(name): return f"{{{ns}}}{name}" if ns else name

    media = []
    for tag in (t("MEDIA_DESCRIPTOR"), t("LINKED_FILE_DESCRIPTOR")):
        for el in root.findall(f".//{tag}"):
            rel = el.get("RELATIVE_MEDIA_URL") or el.get("RELATIVE_LINK_URL")
            absu = el.get("MEDIA_URL") or el.get("LINK_URL")
            toff = el.get("TIME_ORIGIN")
            try:
                time_origin = int(toff) if toff is not None else 0
            except ValueError:
                time_origin = 0
            if rel:
                path = (base / rel).as_posix()
            elif absu and absu.startswith("file:"):
                path = absu.replace("file://","").replace("file:/","/")
            else:
                path = absu or rel
            if path:
                media.append({
                    "path": path,
                    "time_origin_ms": time_origin,
                    "mimetype": el.get("MIME_TYPE") or "",
                })
    return media

def collect_event_occurrences(eaf: elan.Eaf, tier_name: str) -> List[Dict]:
    if tier_name not in eaf.get_tier_names():
        raise SystemExit(f"Tier '{tier_name}' not found.")
    out = []
    for a0, a1, val in iter_ann(eaf, tier_name):
        if not val:
            continue
        out.append({"t0_ms": int(a0), "t1_ms": int(a1), "label": str(val)})
    return out  # keep ELAN order

# ---------- Exports ----------

def trim_eaf_to_interval(original: elan.Eaf, t0_ms: int, t1_ms: int, out_path: str):
    new = elan.Eaf()
    for tier in original.get_tier_names():
        new.add_tier(tier)
    for tier in original.get_tier_names():
        for (a0,a1,val) in original.get_annotation_data_for_tier(tier):
            if a1 <= t0_ms or a0 >= t1_ms:
                continue
            c0 = max(a0, t0_ms)
            c1 = min(a1, t1_ms)
            new.add_annotation(tier, c0 - t0_ms, c1 - t0_ms, val)
    new.to_file(out_path)

def export_transcript_json(eaf: elan.Eaf, tiers: List[str], t0_ms: int, t1_ms: int, out_path: str):
    speakers = {}
    for tier in tiers:
        if tier not in eaf.get_tier_names():
            continue
        segs = []
        for a0, a1, val in iter_ann(eaf, tier):
            if not val or a1 <= t0_ms or a0 >= t1_ms:
                continue
            c0 = max(a0, t0_ms); c1 = min(a1, t1_ms)
            segs.append({"start": (c0 - t0_ms)/1000.0, "end": (c1 - t0_ms)/1000.0, "text": val})
        segs.sort(key=lambda d: d["start"])
        if segs:
            speakers[tier] = segs
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"speakers": speakers}, f, ensure_ascii=False, indent=2)

def export_events_csv(eaf: elan.Eaf, exclude_tiers: List[str], t0_ms: int, t1_ms: int, out_csv: str):
    """
    Export all annotations from tiers NOT in exclude_tiers, trimmed to [t0,t1],
    rebased to 0, as CSV with columns: tier,start_s,end_s,text
    """
    import csv
    tiers = [t for t in eaf.get_tier_names() if t not in set(exclude_tiers)]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tier","start_s","end_s","text"])
        for tier in tiers:
            for a0, a1, val in iter_ann(eaf, tier):
                if not val or a1 <= t0_ms or a0 >= t1_ms:
                    continue
                c0 = max(a0, t0_ms); c1 = min(a1, t1_ms)
                w.writerow([tier, (c0 - t0_ms)/1000.0, (c1 - t0_ms)/1000.0, val])

# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser(description="Per-occurrence cuts from EAF with offsets; JSON transcripts; CSV of non-subtitle tiers.")
    ap.add_argument("--eaf", required=True, help="Path to .eaf")
    ap.add_argument("--event_tier", default="Game event", help="Tier defining event clips")
    ap.add_argument("--subtitle_tiers", nargs="*", default=[], help="Tiers to export as per-speaker JSON; also excluded from CSV")
    ap.add_argument("--outdir", default="cuts_per_occurrence", help="Output directory")
    ap.add_argument("--clip_label_in_name", action="store_true", help="Include raw label in filenames")
    args = ap.parse_args()

    Path(args.outdir).mkdir(parents=True, exist_ok=True)

    eaf = elan.Eaf(args.eaf)
    media = read_media_with_offsets(args.eaf)
    occurrences = collect_event_occurrences(eaf, args.event_tier)

    manifest = {
        "eaf": args.eaf,
        "event_tier": args.event_tier,
        "subtitle_tiers": args.subtitle_tiers,
        "clips": []
    }

    for idx, ev in enumerate(occurrences, start=1):
        t0 = ev["t0_ms"]; t1 = ev["t1_ms"]
        raw_label = ev["label"]; lab = safe_label(raw_label)

        basename = f"{idx:04d}"
        if args.clip_label_in_name:
            basename = f"{basename}__{lab}"

        # (1) Trim EAF (rebased)
        out_eaf = os.path.join(args.outdir, f"{basename}.eaf")
        trim_eaf_to_interval(eaf, t0, t1, out_eaf)

        # (2) Per-media cuts with TIME_ORIGIN
        video_outs = []
        for m in media:
            vpath = m["path"]; to_ms = m["time_origin_ms"]
            dur_s = ffprobe_duration(vpath) or 0.0
            ls = max(0.0, min(dur_s, (t0 - to_ms)/1000.0))
            le = max(0.0, min(dur_s, (t1 - to_ms)/1000.0))
            if le - ls <= 1e-3:
                continue
            tag = Path(vpath).stem
            out_mp4 = os.path.join(args.outdir, f"{basename}__{tag}.mp4")
            ffmpeg_cut(vpath, ls, le, out_mp4)
            video_outs.append(out_mp4)

        # (3) JSON transcript (per-speaker) from subtitle tiers
        json_path = None
        if args.subtitle_tiers:
            json_path = os.path.join(args.outdir, f"{basename}.json")
            export_transcript_json(eaf, args.subtitle_tiers, t0, t1, json_path)

        # (4) CSV of all NON-subtitle tiers (trimmed, rebased)
        csv_path = os.path.join(args.outdir, f"{basename}.csv")
        export_events_csv(eaf, exclude_tiers=args.subtitle_tiers, t0_ms=t0, t1_ms=t1, out_csv=csv_path)

        manifest["clips"].append({
            "index": idx,
            "label": raw_label,
            "t0_ms": t0,
            "t1_ms": t1,
            "trimmed_eaf": out_eaf,
            "videos": video_outs,
            "transcript_json": json_path,
            "events_csv": csv_path
        })

    man_path = os.path.join(args.outdir, "cuts_manifest.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[OK] {len(occurrences)} clips exported to {args.outdir}")
    print(f"[OK] Manifest: {man_path}")

if __name__ == "__main__":
    main()
