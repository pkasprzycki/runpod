#!/usr/bin/env python3
"""Runpod serverless handler: download pack, render Cycles PNG, PUT to presigned URL."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from render import render_pack

DEFAULT_SAMPLES = 128
DEFAULT_RESOLUTION = (1920, 1080)
RENDER_TIMEOUT_S = 480


def download_url(url: str, dest: Path) -> Path:
    req = Request(url, method="GET")
    with urlopen(req, timeout=120) as res, dest.open("wb") as fh:
        fh.write(res.read())
    if dest.stat().st_size < 32:
        raise RuntimeError("Downloaded pack is empty")
    return dest


def upload_png(url: str, png_path: Path) -> None:
    data = png_path.read_bytes()
    if len(data) < 32:
        raise RuntimeError("Render produced an empty PNG")
    req = Request(
        url,
        data=data,
        method="PUT",
        headers={"Content-Type": "image/png", "Content-Length": str(len(data))},
    )
    with urlopen(req, timeout=120) as res:
        res.read()


def process_job(
    inp: dict[str, Any],
    *,
    download: Callable[[str, Path], Path] = download_url,
    upload: Callable[[str, Path], None] = upload_png,
    render: Callable[..., Path] = render_pack,
) -> dict[str, Any]:
    pack_url = inp.get("packUrl")
    output_put = inp.get("outputPutUrl")
    if not pack_url or not output_put:
        raise ValueError("packUrl and outputPutUrl are required")

    samples = int(inp.get("samples") or DEFAULT_SAMPLES)
    resolution = inp.get("resolution") or list(DEFAULT_RESOLUTION)
    if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
        raise ValueError("resolution must be [width, height]")

    work = Path(tempfile.mkdtemp(prefix="cycles-job-"))
    pack_path = work / "pack.zip"
    png_path = work / "still.png"
    download(str(pack_url), pack_path)
    render(
        pack_path,
        png_path,
        samples=samples,
        resolution=(int(resolution[0]), int(resolution[1])),
    )
    upload(str(output_put), png_path)
    return {
        "ok": True,
        "jobId": inp.get("jobId"),
        "samples": samples,
        "resolution": [int(resolution[0]), int(resolution[1])],
    }


def handler(event: dict[str, Any]) -> dict[str, Any]:
    return process_job(event.get("input") or {})


if __name__ == "__main__":
    if os.environ.get("CYCLES_LOCAL") == "1":
        raise SystemExit("Use render.py --pack for local smoke")
    import runpod

    runpod.serverless.start({"handler": handler})
