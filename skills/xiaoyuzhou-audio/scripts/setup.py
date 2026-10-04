#!/usr/bin/env python3
"""Explicit, optional setup in user cache; never changes the Skill or system Python."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv

from transcriber import cache_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("renderer", "transcriber"))
    parser.add_argument("--backend", choices=("faster", "mlx"), default="faster")
    parser.add_argument("--download-model", action="store_true", help="明确允许下载 faster-whisper 模型")
    parser.add_argument("--model", default="small")
    args = parser.parse_args()
    if args.download_model and (args.component != "transcriber" or args.backend != "faster"):
        parser.error("--download-model 仅用于 faster 转写后端；MLX 请提供已有本地模型。")
    if args.backend == "mlx" and args.component == "transcriber" and sys.platform != "darwin":
        parser.error("MLX 后端仅支持 Apple Silicon macOS。")
    directory = cache_root() / (args.component + "-env")
    python = directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(directory)
    package = "markdown-it-py>=3,<5" if args.component == "renderer" else "faster-whisper>=1,<2" if args.backend == "faster" else "mlx-whisper>=0.4,<1"
    subprocess.run([str(python), "-m", "pip", "install", package], check=True)
    if args.download_model:
        subprocess.run([str(python), "-c", "from faster_whisper.utils import download_model; import sys; print(download_model(sys.argv[1]))", args.model], check=True)
    print(f"READY\t{args.component}\t{directory}")


if __name__ == "__main__":
    main()
