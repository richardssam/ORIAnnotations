#!/usr/bin/env python3
"""Download StEM2 frame ranges from the ASWF S3 ZIP via HTTP range requests.

Usage:
    python stem_download.py <output_dir> <start:count> [<start:count> ...]

Each <start:count> pair downloads `count` consecutive frames beginning at
frame number `start`.  Files are saved with their original ZIP basename:
    STEM2_4k_ctm_ACES_239.########.exr
"""

from __future__ import annotations

import bisect
import contextlib
import os
import sys
import tempfile
import typing
import zipfile

import requests
import requests.models

STEM2_ZIP_URL = (
    "https://aswf-dpel-assets.s3.amazonaws.com"
    "/asc-stem2/EXR/mission_StEM2_EXR_239_4096x1716.zip"
)
FRAME_PREFIX = "STEM2_4k_ctm_ACES_239"
FRAME_PADDING = 8


# ---------------------------------------------------------------------------
# Lazy ZIP-over-HTTP (HTTP range requests; fetches only what is needed)
# Adapted from https://github.com/pypa/pip (Apache-2.0)
# ---------------------------------------------------------------------------

class LazyZipOverHTTP:
    def __init__(
        self,
        url: str,
        session: requests.Session,
        chunk_size: int = requests.models.CONTENT_CHUNK_SIZE,
    ) -> None:
        head = session.head(url, headers={"Accept-Encoding": "identity"})
        head.raise_for_status()
        self._session, self._url, self._chunk_size = session, url, chunk_size
        self._length = int(head.headers["Content-Length"])
        self._file = tempfile.NamedTemporaryFile()
        self.truncate(self._length)
        self._left: list[int] = []
        self._right: list[int] = []
        if "bytes" not in head.headers.get("Accept-Ranges", "none"):
            raise ValueError("Server does not support HTTP range requests")
        self._check_zip()

    @property
    def mode(self) -> str:
        return "rb"

    @property
    def name(self) -> str:
        return self._file.name

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        self._file.close()

    @property
    def closed(self) -> bool:
        return self._file.closed

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def truncate(self, size: int | None = None) -> int:
        return self._file.truncate(size)

    def read(self, size: int = -1) -> bytes:
        download_size = max(size, self._chunk_size)
        start, length = self.tell(), self._length
        stop = length if size < 0 else min(start + download_size, length)
        start = max(0, stop - download_size)
        self._download(start, stop - 1)
        return self._file.read(size)

    @contextlib.contextmanager
    def _stay(self) -> typing.Generator[None, None, None]:
        pos = self.tell()
        try:
            yield
        finally:
            self.seek(pos)

    def _check_zip(self) -> None:
        end = self._length - 1
        for start in reversed(range(0, end, self._chunk_size)):
            self._download(start, end)
            with self._stay():
                try:
                    zipfile.ZipFile(self)
                    break
                except zipfile.BadZipFile:
                    pass

    def _stream_response(self, start: int, end: int) -> requests.Response:
        headers = {
            "Accept-Encoding": "identity",
            "Range": f"bytes={start}-{end}",
            "Cache-Control": "no-cache",
        }
        return self._session.get(self._url, headers=headers, stream=True)

    def _merge(
        self, start: int, end: int, left: int, right: int
    ) -> typing.Generator[tuple[int, int], None, None]:
        lslice, rslice = self._left[left:right], self._right[left:right]
        i = start = min([start] + lslice[:1])
        end = max([end] + rslice[-1:])
        for j, k in zip(lslice, rslice):
            if j > i:
                yield i, j - 1
            i = k + 1
        if i <= end:
            yield i, end
        self._left[left:right], self._right[left:right] = [start], [end]

    def _download(self, start: int, end: int) -> None:
        with self._stay():
            left = bisect.bisect_left(self._right, start)
            right = bisect.bisect_right(self._left, end)
            for s, e in self._merge(start, end, left, right):
                resp = self._stream_response(s, e)
                resp.raise_for_status()
                self.seek(s)
                for chunk in resp.iter_content(self._chunk_size):
                    self._file.write(chunk)


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------

def download_ranges(output_dir: str, ranges: list[tuple[int, int]]) -> None:
    """Download all frames covered by (start, count) pairs into output_dir."""
    os.makedirs(output_dir, exist_ok=True)

    targets: list[str] = []
    for start, count in ranges:
        for frame in range(start, start + count):
            targets.append(f"{FRAME_PREFIX}.{frame:0{FRAME_PADDING}d}.exr")
    targets.sort()

    already = sum(
        1 for t in targets if os.path.exists(os.path.join(output_dir, t))
    )
    if already == len(targets):
        print(f"All {len(targets)} frames already present in {output_dir}, skipping.")
        return

    print(f"Opening StEM2 ZIP index via HTTP range requests …")
    session = requests.Session()
    lazy = LazyZipOverHTTP(STEM2_ZIP_URL, session)

    with zipfile.ZipFile(lazy) as zf:
        zip_names = {os.path.basename(info.filename): info for info in zf.infolist()}

        for target in targets:
            outpath = os.path.join(output_dir, target)
            if os.path.exists(outpath):
                continue
            if target not in zip_names:
                print(f"  WARNING: {target} not found in ZIP", file=sys.stderr)
                continue
            print(f"  {target}")
            with open(outpath, "wb") as f:
                f.write(zf.read(zip_names[target]))


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Download StEM2 frame ranges from the ASWF S3 ZIP."
    )
    p.add_argument("output_dir", help="Directory to write EXR files into")
    p.add_argument(
        "ranges",
        nargs="+",
        metavar="START:COUNT",
        help="Frame ranges to download (e.g. 91700:100)",
    )
    args = p.parse_args()

    parsed: list[tuple[int, int]] = []
    for r in args.ranges:
        try:
            start_s, count_s = r.split(":")
            parsed.append((int(start_s), int(count_s)))
        except ValueError:
            p.error(f"Invalid range '{r}' — expected START:COUNT (e.g. 91700:100)")

    download_ranges(args.output_dir, parsed)


if __name__ == "__main__":
    main()
