"""Optional local transcription; never installs dependencies or downloads models."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


class TranscriptionError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def cache_root():
    if os.environ.get("XIAOYUZHOU_AUDIO_CACHE"):
        return Path(os.environ["XIAOYUZHOU_AUDIO_CACHE"]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "xiaoyuzhou-audio"


def run(command):
    try:
        return subprocess.run(command, check=True, text=True, encoding="utf-8",
                              errors="replace", capture_output=True)
    except FileNotFoundError as exc:
        raise TranscriptionError("missing_dependency", f"未安装本地工具：{command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise TranscriptionError("transcription_failed", (exc.stderr or str(exc))[-2500:]) from exc


def probe_duration(path):
    result = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    value = float(result.stdout.strip())
    if not math.isfinite(value) or value <= 0:
        raise TranscriptionError("invalid_audio", "音频时长无效。")
    return value


def format_time(value):
    seconds = max(0, int(value))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def srt_timestamp(milliseconds):
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def srt_text(segments, duration):
    """Export complete timed speech, never the condensed Markdown note."""
    segments = normalize_segments(segments, duration)
    if not segments:
        raise TranscriptionError("empty_transcript", "完整转写没有文本，不能生成 SRT。")
    limit = max(1, math.floor(duration * 1000 + .5))
    cues = []
    for index, row in enumerate(segments, 1):
        start = min(math.floor(row["start"] * 1000 + .5), limit - 1)
        end = min(max(math.floor(row["end"] * 1000 + .5), start + 1), limit)
        text = " ".join(line.strip() for line in row["text"].splitlines() if line.strip())
        cues.append(f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n")
    return "\n".join(cues)


def normalize_segments(segments, duration):
    result = []
    previous = -1.0
    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        text = str(segment.get("text", "")).strip()
        if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
            raise TranscriptionError("invalid_transcript", "转写时间戳无效。")
        if start < previous or end > duration + 2 or start >= duration:
            raise TranscriptionError("invalid_transcript", "转写时间戳乱序或超过音频范围。")
        previous = start
        if text:
            result.append({"start": start, "end": min(end, duration), "text": text})
    return result


class Backend:
    def __init__(self, name="auto", model=None, language="zh", prompt=""):
        self.language, self.prompt = language, prompt
        self.model = None
        self.remote = None
        model = model or os.environ.get("XIAOYUZHOU_AUDIO_MODEL")
        if name == "auto":
            if model and Path(model).expanduser().is_file() and shutil.which("whisper-cli"):
                name = "cpp"
            else:
                name = "faster"
        self.name = name
        try:
            env_python = cache_root() / "transcriber-env" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            package = "faster_whisper" if name == "faster" else "mlx_whisper" if name == "mlx" else None
            if package and importlib.util.find_spec(package) is None and env_python.is_file():
                self.remote = env_python
                self.remote_config = {"backend": name, "model": model, "language": language, "prompt": prompt}
                value = self.call_remote("describe")
                self.model_key, self.label = value["model_key"], value["label"]
                return
            if name == "cpp":
                if not model or not Path(model).expanduser().is_file():
                    raise TranscriptionError("model_required", "whisper.cpp 需要通过 --model 指定已存在的模型文件。")
                if not shutil.which("whisper-cli"):
                    raise TranscriptionError("missing_dependency", "未安装 whisper-cli。")
                self.model_path = Path(model).expanduser().resolve()
                self.model_key = digest(self.model_path)
                self.label = "whisper.cpp / " + self.model_path.name
            elif name == "faster":
                from faster_whisper import WhisperModel
                from faster_whisper.utils import download_model
                model_dir = Path(model).expanduser() if model and Path(model).expanduser().is_dir() else Path(download_model(model or "small", local_files_only=True))
                self.model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", local_files_only=True)
                self.model_key = hashlib.sha256("".join(digest(path) for path in sorted(model_dir.iterdir())
                                                       if path.is_file()).encode()).hexdigest()
                self.label = "faster-whisper / CPU int8 / " + str(model or "small")
            else:
                if name != "mlx" or not model or not Path(model).expanduser().exists():
                    raise TranscriptionError("model_required", "MLX 需要已下载的本地模型目录。")
                import mlx_whisper
                self.mlx = mlx_whisper
                self.model_path = Path(model).expanduser().resolve()
                self.model_key = hashlib.sha256("".join(
                    digest(p) for p in sorted(self.model_path.rglob("*.safetensors"))).encode()).hexdigest()
                self.label = "mlx-whisper / " + self.model_path.name
        except (ImportError, OSError, RuntimeError) as exc:
            if isinstance(exc, TranscriptionError):
                raise
            raise TranscriptionError("transcriber_unavailable",
                "本地转写后端或模型未准备好；不会自动安装或下载。请按收录说明配置。" + str(exc)) from exc

    def call_remote(self, action, audio=None):
        payload = {**self.remote_config, "action": action, "audio": str(audio) if audio else None}
        result = run([str(self.remote), str(Path(__file__).resolve()), "--worker", json.dumps(payload)])
        return json.loads(result.stdout)

    def transcribe(self, audio):
        if self.remote:
            return self.call_remote("transcribe", audio)["segments"]
        if self.name == "cpp":
            output = audio.with_suffix(".asr")
            command = ["whisper-cli", "-m", str(self.model_path), "-f", str(audio),
                       "-l", self.language, "-oj", "-of", str(output), "-bs", "5"]
            if self.prompt:
                command += ["--prompt", self.prompt]
            run(command)
            data = json.loads(output.with_suffix(".asr.json").read_text(encoding="utf-8"))
            return [{"start": row["offsets"]["from"] / 1000,
                     "end": row["offsets"]["to"] / 1000, "text": row["text"]}
                    for row in data["transcription"]]
        if self.name == "mlx":
            value = self.mlx.transcribe(str(audio), path_or_hf_repo=str(self.model_path),
                        language=self.language, initial_prompt=self.prompt or None, verbose=False)
            return value.get("segments", [])
        segments, _ = self.model.transcribe(str(audio), language=self.language, vad_filter=True,
                         beam_size=5, initial_prompt=self.prompt or None, condition_on_previous_text=True)
        return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


def write_outputs(workspace, episode_id, title, segments, status):
    directory = Path(workspace) / "assets" / "transcripts"
    paths = {"markdown": directory / f"{episode_id}-transcript.md",
             "text": directory / f"{episode_id}-transcript.txt",
             "segments_jsonl": directory / f"{episode_id}-segments.jsonl",
             "srt": directory / f"{episode_id}-transcript.srt"}
    lines = [f"[{format_time(s['start'])} - {format_time(s['end'])}] {s['text']}" for s in segments]
    atomic_text(paths["markdown"], f"# {title} 转写稿\n\n- 转写方式：{status['model']}\n\n" + "\n\n".join(lines) + "\n")
    atomic_text(paths["text"], "\n".join(lines) + "\n")
    atomic_text(paths["segments_jsonl"], "".join(json.dumps({"index": i, **s}, ensure_ascii=False) + "\n"
                                                   for i, s in enumerate(segments, 1)))
    atomic_text(paths["srt"], srt_text(segments, status["audio_duration_seconds"]))
    status.update(schema_version=1, episode_id=episode_id, complete=True,
                  segment_count=len(segments), last_speech_end_seconds=segments[-1]["end"],
                  outputs={key: path.relative_to(workspace).as_posix() for key, path in paths.items()},
                  output_hashes={key: digest(path) for key, path in paths.items()})
    status_path = directory / f"{episode_id}-status.json"
    atomic_text(status_path, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    return status_path, status


def transcribe(audio, workspace, episode_id, title, args, backend=None):
    if not math.isfinite(args.chunk_minutes) or args.chunk_minutes <= 0:
        raise TranscriptionError("invalid_chunk_size", "分段分钟数必须为有限正数。")
    audio = Path(audio).expanduser().resolve()
    duration = probe_duration(audio)
    backend = backend or Backend(args.backend, args.model, args.language, args.prompt)
    chunk_seconds = args.chunk_minutes * 60
    signature = {"schema_version": 1, "audio_sha256": digest(audio),
                 "audio_duration_seconds": duration, "backend": backend.name,
                 "model": backend.model_key, "language": args.language,
                 "chunk_seconds": chunk_seconds, "prompt": args.prompt, "beam_size": 5}
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    job = cache_root() / "jobs" / episode_id / key
    job.mkdir(parents=True, exist_ok=True)
    segments, intervals, resumed = [], [], 0
    for index in range(math.ceil(duration / chunk_seconds)):
        start, length = index * chunk_seconds, min(chunk_seconds, duration - index * chunk_seconds)
        checkpoint = job / f"chunk-{index:04d}.json"
        row = None
        if checkpoint.exists():
            try:
                candidate = json.loads(checkpoint.read_text(encoding="utf-8"))
                if (candidate.get("signature") == signature and candidate.get("complete") is True
                        and candidate.get("start") == start and candidate.get("duration") == length):
                    normalize_segments(candidate["segments"], length)
                    row = candidate
                    resumed += 1
            except (ValueError, KeyError, TranscriptionError):
                pass
        if row is None:
            chunk = job / f"chunk-{index:04d}.wav"
            valid = False
            if chunk.exists():
                try:
                    valid = abs(probe_duration(chunk) - length) < .1
                except (ValueError, TranscriptionError):
                    pass
            if not valid:
                temporary = chunk.with_suffix(".tmp.wav")
                try:
                    run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(length),
                         "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(temporary)])
                    if abs(probe_duration(temporary) - length) >= .1:
                        raise TranscriptionError("incomplete_chunk", "解码分段时长不完整。")
                    os.replace(temporary, chunk)
                finally:
                    temporary.unlink(missing_ok=True)
            local = normalize_segments(backend.transcribe(chunk), length)
            row = {"signature": signature, "complete": True, "start": start,
                   "duration": length, "segments": local}
            atomic_text(checkpoint, json.dumps(row, ensure_ascii=False) + "\n")
        intervals.append({"start": start, "end": start + length})
        segments.extend({"start": start + s["start"], "end": start + s["end"], "text": s["text"]}
                        for s in row["segments"])
        print(f"转写 {index + 1}/{math.ceil(duration / chunk_seconds)}：{format_time(start + length)}", file=sys.stderr)
    if not segments:
        raise TranscriptionError("empty_transcript", "音频已处理但未识别到内容，不能生成正式知识笔记。")
    return write_outputs(workspace, episode_id, title, segments,
        {"source_kind": "local_asr", "model": backend.label, "language": args.language,
         "audio_sha256": signature["audio_sha256"], "signature": signature,
         "audio_duration_seconds": duration, "audio_processed_seconds": duration,
         "processed_intervals": intervals, "chunk_count": len(intervals), "resumed_chunks": resumed})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True)
    config = json.loads(parser.parse_args().worker)
    backend = Backend(config["backend"], config["model"], config["language"], config["prompt"])
    result = {"model_key": backend.model_key, "label": backend.label}
    if config["action"] == "transcribe":
        result["segments"] = backend.transcribe(Path(config["audio"]))
    print(json.dumps(result, ensure_ascii=False))
