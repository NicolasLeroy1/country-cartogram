"""Rebuild the bundled Three.js engine; normal users do not need Node or npm."""

from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/country_cartogram/web"
VERSION = "0.186.1"
EXPORTS = """export {WebGLRenderer, Scene, Color, OrthographicCamera, HemisphereLight,
DirectionalLight, BufferGeometry, BufferAttribute, Mesh, MeshStandardMaterial,
DoubleSide, GridHelper, Raycaster, Vector2, Vector3, SRGBColorSpace, MOUSE, TOUCH,
WebGLRenderTarget, HalfFloatType, ShaderMaterial, PlaneGeometry,
CustomBlending, NormalBlending, AddEquation, OneFactor, ZeroFactor, OneMinusSrcAlphaFactor} from 'three';
export {OrbitControls} from 'three/addons/controls/OrbitControls.js';
"""


def main():
    with tempfile.TemporaryDirectory(prefix="cartogram-viewer-") as tmp:
        build = Path(tmp)
        subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                tmp,
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                f"three@{VERSION}",
                "esbuild@0.25.12",
            ],
            check=True,
        )
        (build / "entry.js").write_text(EXPORTS)
        subprocess.run(
            [
                str(build / "node_modules/.bin/esbuild"),
                str(build / "entry.js"),
                "--bundle",
                "--minify",
                "--format=iife",
                "--global-name=CartogramThree",
                f"--outfile={WEB}/three.min.js",
            ],
            check=True,
        )
        shutil.copyfile(build / "node_modules/three/LICENSE", WEB / "THREE-LICENSE.txt")


if __name__ == "__main__":
    main()
