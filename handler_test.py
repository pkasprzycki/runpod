import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from handler import process_job
from render import blender_script_source, extract_pack, load_pack_meta, main as render_main


def write_fixture_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("booth.glb", b"glTF" + b"\x00" * 28)
        zf.writestr(
            "cameras.json",
            json.dumps({"hero": {"position": [2, 1.6, 6], "target": [2, 1.1, 2]}}),
        )
        zf.writestr(
            "world.json",
            json.dumps({"hdri": "warehouse_fair", "strength": 0.85, "exposure": 1}),
        )
    return path


class CyclesWorkerTests(unittest.TestCase):
    def test_meta_only_reads_hero_camera(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = write_fixture_zip(Path(tmp) / "pack.zip")
            dest = Path(tmp) / "out"
            extract_pack(pack, dest)
            meta = load_pack_meta(dest)
            self.assertEqual(meta["cameras"]["hero"]["position"], [2, 1.6, 6])
            self.assertEqual(meta["world"]["hdri"], "warehouse_fair")
            code = render_main(["--pack", str(pack), "--meta-only"])
            self.assertEqual(code, 0)

    def test_process_job_downloads_renders_and_puts(self) -> None:
        calls: list[str] = []

        def download(url: str, dest: Path) -> Path:
            self.assertEqual(url, "https://s3.example/pack.zip")
            write_fixture_zip(dest)
            calls.append("download")
            return dest

        def render(pack_path: Path, out_path: Path, **kwargs) -> Path:
            self.assertTrue(pack_path.is_file())
            self.assertEqual(kwargs["samples"], 64)
            out_path.write_bytes(b"\x89PNG" + b"\x00" * 40)
            calls.append("render")
            return out_path

        def upload(url: str, png_path: Path) -> None:
            self.assertEqual(url, "https://s3.example/still.png")
            self.assertGreater(png_path.stat().st_size, 32)
            calls.append("upload")

        result = process_job(
            {
                "jobId": "job-1",
                "packUrl": "https://s3.example/pack.zip",
                "outputPutUrl": "https://s3.example/still.png",
                "samples": 64,
                "resolution": [1280, 720],
            },
            download=download,
            upload=upload,
            render=render,
        )
        self.assertEqual(calls, ["download", "render", "upload"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["jobId"], "job-1")

    def test_blender_script_uses_photographic_hall_lights(self) -> None:
        source = blender_script_source()
        self.assertIn("sensor_fit", source)
        self.assertIn("hall-spot", source)
        self.assertIn("use_dof", source)
        self.assertIn("FOG_GLOW", source)
        self.assertIn("use_adaptive_sampling", source)
        self.assertIn("ShaderNodeBevel", source)
        self.assertIn("upgrade_imported_materials", source)
        self.assertIn("aim_camera", source)
        self.assertIn('meta["cameras"].get("views")', source)

    def test_process_job_requires_presigned_urls(self) -> None:
        with self.assertRaises(ValueError):
            process_job({})


if __name__ == "__main__":
    unittest.main()
