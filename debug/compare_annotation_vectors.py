"""Compare vector annotations in an ori_annotations OTIO export to the baked PNGs.

For each Review-track clip: extract the clean frame from the source .mov,
diff it against the exported PNG to get the "painted pixels" mask, rasterize
the OTIO PaintPoint strokes into a second mask, and compare.

Usage: python debug/compare_annotation_vectors.py <export_dir> <out_dir>
"""
import json, os, subprocess, sys
import cv2
import numpy as np

export_dir, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
otio = json.load(open(os.path.join(export_dir, "annotationreview.otio")))


def review_clips(o):
    # Handles both layouts: flat Media/Review tracks (exporttest) and
    # per-media Stacks with a nested review track (exporttest2).
    if isinstance(o, dict):
        md = o.get("metadata") or {}
        if o.get("OTIO_SCHEMA", "").startswith("Clip") and "annotation_commands" in md:
            yield o
        for v in o.values():
            yield from review_clips(v)
    elif isinstance(o, list):
        for v in o:
            yield from review_clips(v)


def clean_frame(mov, idx):
    raw = subprocess.run(
        ["rez-env", "ffmpeg", "--", "ffmpeg", "-v", "error", "-i", mov,
         "-vf", f"select=eq(n\\,{idx})", "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def to_px(x, y, W, H):
    # RV normalized space: origin at image centre, y up, units of image height.
    return W / 2 + x * H, H / 2 - y * H


def rasterize(strokes, W, H, width_scale=1.0, dx=0, dy=0):
    m = np.zeros((H, W), np.uint8)
    for s in strokes:
        pts = [(to_px(x, y, W, H), sz) for x, y, sz in s]
        for i, ((px, py), sz) in enumerate(pts):
            r = max(1, int(round(sz * H * width_scale / 2)))
            c = (int(round(px + dx)), int(round(py + dy)))
            cv2.circle(m, c, r, 255, -1, cv2.LINE_AA)
            if i:
                (qx, qy), _ = pts[i - 1]
                cv2.line(m, (int(round(qx + dx)), int(round(qy + dy))), c, 255,
                         2 * r, cv2.LINE_AA)
    return m > 127


def iou(a, b):
    u = (a | b).sum()
    return (a & b).sum() / u if u else float("nan")


rows = []
for clip in review_clips(otio):
    md = clip["metadata"]
    media = md.get("annotated_clip_name") or clip["name"].rsplit(".", 1)[0]
    frame = int(clip["source_range"]["start_time"]["value"])
    png = os.path.join(export_dir, f"{media}.{frame:05d}.png")
    mov = os.path.join(export_dir, f"{media}.mov")
    strokes, colors, texts = [], [], []
    for cmd in md["annotation_commands"]:
        sch = cmd["OTIO_SCHEMA"]
        if sch.startswith("PaintStart"):
            colors.append(cmd["rgba"])
        elif sch.startswith("PaintPoint"):
            p = cmd["points"]
            strokes.append(list(zip(p["x"], p["y"], p["size"])))
        elif sch.startswith("TextAnnotation"):
            texts.append(cmd)
    if not os.path.exists(png):
        print("missing", png); continue

    img = cv2.imread(png)
    H, W = img.shape[:2]
    base = clean_frame(mov, frame - 1)
    diff = np.abs(img.astype(int) - base.astype(int)).max(axis=2)
    painted = diff > 40
    painted = cv2.morphologyEx(painted.astype(np.uint8), cv2.MORPH_OPEN,
                               np.ones((3, 3), np.uint8)) > 0
    # Text renders as painted pixels but isn't vector-comparable here; mask a box
    # around each text anchor out of both masks.
    ignore = np.zeros_like(painted)
    for t in texts:
        px, py = to_px(*t["position"], W, H)
        ignore[max(0, int(py) - 80):int(py) + 40, max(0, int(px) - 20):int(px) + 400] = True
    painted &= ~ignore

    vec = rasterize(strokes, W, H) & ~ignore
    base_iou = iou(painted, vec)

    # Search a width scale and a translation to expose systematic size / offset bias.
    best = (base_iou, 1.0, 0, 0)
    for ws in (1.0, 1.4, 1.5, 1.6, 1/0.6, 1.75, 1.85, 2.0, 2.2):
        for dx in range(-2, 3, 1):
            for dy in range(-2, 3, 1):
                v = iou(painted, rasterize(strokes, W, H, ws, dx, dy) & ~ignore)
                if v > best[0]:
                    best = (v, ws, dx, dy)

    tp = (painted & vec).sum()
    rows.append(dict(media=media, frame=frame, strokes=len(strokes),
                     points=sum(map(len, strokes)), text=len(texts),
                     painted_px=int(painted.sum()), vector_px=int(vec.sum()),
                     recall=tp / max(1, painted.sum()), precision=tp / max(1, vec.sum()),
                     iou=base_iou, best_iou=best[0], best_width=best[1],
                     best_dx=best[2], best_dy=best[3]))

    # Visual: painted-only = red, vector-only = blue, both = green, over a dimmed frame.
    vis = (img * 0.35).astype(np.uint8)
    vis[painted & ~vec] = (0, 0, 255)
    vis[vec & ~painted] = (255, 80, 0)
    vis[painted & vec] = (0, 220, 0)
    for s, c in zip(strokes, colors):
        pts = np.array([to_px(x, y, W, H) for x, y, _ in s], np.int32)
        cv2.polylines(vis, [pts], False, (255, 255, 255), 1, cv2.LINE_AA)
    for t in texts:
        px, py = to_px(*t["position"], W, H)
        cv2.drawMarker(vis, (int(px), int(py)), (0, 255, 255), cv2.MARKER_CROSS, 30, 2)
    cv2.imwrite(os.path.join(out_dir, f"{media}.{frame:05d}.compare.png"),
                np.hstack([img, vis]))

hdr = ("media", "frame", "strokes", "points", "text", "painted_px", "vector_px",
       "recall", "precision", "iou", "best_iou", "best_width", "best_dx", "best_dy")
print("\t".join(hdr))
for r in rows:
    print("\t".join(f"{r[k]:.3f}" if isinstance(r[k], float) else str(r[k]) for k in hdr))
json.dump(rows, open(os.path.join(out_dir, "results.json"), "w"), indent=1, default=float)
