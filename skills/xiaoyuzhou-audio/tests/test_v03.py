"""Synthetic standalone-note fixtures. No real transcripts or personal paths."""
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from test_podcast import p, PID, CHANNEL
from test_v02 import catalog, episode, identity

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import notes as n
import transcriber as t


def full_episode(index=1, paid=False):
    row = episode(index, paid)
    row["public_transcript"] = {"complete": True, "audio_processed_seconds": 30,
                                "segments": [{"start": 0, "end": 29, "text": "完整的合成节目内容。"}]}
    return row


def long_episode(seconds=3600, text="长节目的合成内容。"):
    row = full_episode()
    row["duration_seconds"] = seconds
    row["public_transcript"] = {"complete": True, "audio_processed_seconds": seconds,
                                "segments": [{"start": 0, "end": seconds - 1, "text": text}]}
    return row


def timed_note(row, stamps, body="这一段讲了具体的内容。"):
    topics = "\n\n".join(f"### {i}. 主题{i}（{stamp}）\n\n{body}" for i, stamp in enumerate(stamps, 1))
    return (f"# {row['title']}\n\n测试频道 · 2026-10-01 · 01:00:00 · [原文]({row['page_url']})\n\n"
            f"## 核心结论\n\n一个讲清楚主问题的结论。\n\n## 内容提炼\n\n{topics}\n")


def valid_note(row):
    return f"""# {row['title']}

测试频道 · 2026-10-01 · 00:00:30 · [原文]({row['page_url']})

## 核心结论

理解一个观点的条件，并把理解放回具体情境。

## 内容提炼

### 1. 理解讨论的问题（00:00:05）

观点成立需要条件，案例用来解释机制，不能代替证据。

### 2. 从理解走向行动（00:00:20）

选择一个可以观察结果的小行动，再调整判断。
"""


class NoteCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cache = self.root / "cache"
        self.out = self.root / "下载目录 with spaces"
        self.env = patch.dict(os.environ, {"XIAOYUZHOU_AUDIO_CACHE": str(self.cache)})
        self.env.start()
        self.config = patch.object(p, "config_path", return_value=self.root / "settings.json")
        self.config.start()
        self.channel = {"channel_id": PID, "channel_title": "测试频道", "channel_url": CHANNEL}

    def tearDown(self):
        self.config.stop()
        self.env.stop()
        self.temp.cleanup()

    def args(self, *values):
        return p.build_parser().parse_args(list(values))

    def ready(self, row=None, extra=()):
        row = row or full_episode()
        args = self.args("prepare", row["page_url"], "--out", str(self.out), *extra)
        with patch.object(p, "resolve_episode", return_value=(row, self.channel)):
            return n.prepare(args, p)["results"][0]

    def complete(self, row=None, extra=()):
        row = row or full_episode()
        result = self.ready(row, extra)
        Path(result["draft_path"]).write_text(valid_note(row), encoding="utf-8")
        return n.finalize(self.args("finalize", result["draft_path"]), p)["results"][0]


class TestStandalone(NoteCase):
    def test_prepare_does_not_claim_completed(self):
        result = self.ready()
        self.assertEqual(result["status"], "ready_for_note")
        self.assertFalse(Path(result["markdown"]).exists())
        self.assertFalse(self.out.exists())

    def test_exact_directory_hierarchy_and_three_outputs(self):
        result = self.complete()
        self.assertEqual(result["status"], "completed")
        md = Path(result["markdown"])
        self.assertEqual(md.relative_to(self.out).parts,
                         ("测试频道", "2026-10-01 - EP1 测试", "EP1 测试.md"))
        self.assertEqual(sorted(x.name for x in md.parent.iterdir()), ["EP1 测试.html", "EP1 测试.md", "EP1 测试.srt"])
        self.assertEqual(list(self.out.rglob("library-index*")), [])
        self.assertEqual(list(self.out.rglob("*.json")), [])

    def test_original_directory_config_reused_without_new_confirmation(self):
        p.configure_directory(str(self.out))
        row = full_episode()
        with patch.object(p, "resolve_episode", return_value=(row, self.channel)):
            result = n.prepare(self.args("prepare", row["page_url"]), p)
        self.assertTrue(result["ok"])
        self.assertNotIn("knowledge_dir", p.read_settings())

    def test_first_use_does_not_create_or_confirm_directory(self):
        with self.assertRaises(p.SkillError) as context:
            n.prepare(self.args("prepare", full_episode()["page_url"]), p)
        self.assertEqual(context.exception.code, "directory_confirmation_required")
        self.assertFalse((self.root / "settings.json").exists())

    def test_windows_reserved_names_and_invalid_characters(self):
        row = full_episode()
        row["title"] = 'CON:<测试>/\\|?*'
        channel = dict(self.channel, channel_title="CON")
        md, _ = n.output_paths(self.out, row, channel, p)
        self.assertEqual(md.relative_to(self.out).parts[0], "_CON")
        for part in md.relative_to(self.out).parts:
            self.assertFalse(any(c in part for c in '<>:"/\\|?*'))

    def test_missing_date_and_separate_channel_directories(self):
        row = full_episode()
        row["published_at"] = None
        a, _ = n.output_paths(self.out, row, self.channel, p)
        b, _ = n.output_paths(self.out, row, dict(self.channel, channel_title="另一频道"), p)
        self.assertIn("日期未知 - ", a.parent.name)
        self.assertNotEqual(a, b)

    def test_paid_has_zero_audio_and_asr_requests(self):
        with patch.object(p, "download_episode") as download, patch.object(t, "Backend") as backend:
            result = self.ready(full_episode(paid=True))
        self.assertEqual(result["status"], "skipped_paid")
        download.assert_not_called()
        backend.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_complete_public_transcript_does_not_download_or_use_backend(self):
        with patch.object(p, "download_episode") as download, patch.object(t, "Backend") as backend:
            result = self.ready()
        self.assertEqual(result["status"], "ready_for_note")
        download.assert_not_called()
        backend.assert_not_called()

    def test_snippet_is_not_accepted_as_complete(self):
        row = full_episode()
        row["public_transcript"]["audio_processed_seconds"] = 15
        with patch.object(t, "Backend", side_effect=t.TranscriptionError("model_required", "模型缺失")):
            result = self.ready(row)
        self.assertEqual(result["status"], "failed")

    def test_scaffold_cannot_be_finalized(self):
        result = self.ready()
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertFalse(Path(result["markdown"]).exists())

    def test_one_short_topic_is_accepted_without_word_count_rule(self):
        result = self.ready()
        source = valid_note(full_episode()).split("### 2.")[0]
        Path(result["draft_path"]).write_text(source)
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertTrue(final["ok"])

    def test_source_link_and_metadata_line_are_required(self):
        result = self.ready()
        source = valid_note(full_episode()).replace("[原文]", "[错误标签]")
        Path(result["draft_path"]).write_text(source)
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "invalid_source_line")

    def test_heading_in_code_example_is_not_a_required_section(self):
        result = self.ready()
        source = valid_note(full_episode()) + "\n```markdown\n## 评分\n示例代码，不是笔记栏目。\n```\n"
        Path(result["draft_path"]).write_text(source)
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertTrue(final["ok"])

    def test_missing_title_is_rejected(self):
        result = self.ready()
        Path(result["draft_path"]).write_text(valid_note(full_episode()).split("\n", 1)[1])
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "missing_note_title")

    def test_new_notes_do_not_accept_legacy_extra_sections(self):
        result = self.ready()
        source = valid_note(full_episode()) + "\n## 行动建议\n\n不应成为独立栏目。\n"
        Path(result["draft_path"]).write_text(source)
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "unexpected_note_section")

    def test_html_contains_source_once_and_no_removed_components(self):
        result = self.complete()
        html = Path(result["html"]).read_text()
        self.assertEqual(html.count(full_episode()["page_url"]), 1)
        self.assertIn("测试频道", html)
        self.assertIn("2026-10-01", html)
        self.assertIn("00:00:30", html)
        for markup in ('class="rating"', 'class="tags"', 'class="concept-card"', 'class="metadata-grid"', "作者：", "未注明"):
            self.assertNotIn(markup, html)

    def test_no_hidden_episode_marker_leaks_into_reading_content(self):
        result = self.complete()
        self.assertNotIn("xiaoyuzhou-episode:", Path(result["html"]).read_text())

    def test_transcript_tampering_prevents_publication(self):
        result = self.ready()
        Path(result["draft_path"]).write_text(valid_note(full_episode()))
        Path(result["transcripts"]["text"]).write_text("被改变的内容")
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertFalse(self.out.exists())

    def test_incomplete_status_is_rejected_even_with_updated_hash(self):
        result = self.ready()
        job = Path(result["draft_path"]).parent
        manifest = json.loads((job / "prepared.json").read_text())
        path = job / manifest["transcript_status"]
        status = json.loads(path.read_text())
        status["processed_intervals"] = [{"start": 0, "end": 15}]
        n.write_json(path, status)
        manifest["status_sha256"] = t.digest(path)
        with self.assertRaises(p.SkillError):
            n.validate_material(job, manifest, p)

    def test_nan_processed_duration_is_rejected(self):
        result = self.ready()
        job = Path(result["draft_path"]).parent
        manifest = json.loads((job / "prepared.json").read_text())
        path = job / manifest["transcript_status"]
        status = json.loads(path.read_text())
        status["audio_processed_seconds"] = float("nan")
        n.write_json(path, status)
        manifest["status_sha256"] = t.digest(path)
        with self.assertRaises(p.SkillError):
            n.validate_material(job, manifest, p)

    def test_duplicate_leaves_original_files_unchanged(self):
        result = self.complete()
        md, html = Path(result["markdown"]), Path(result["html"])
        before = md.read_bytes(), html.read_bytes()
        repeated = self.ready()
        self.assertEqual(repeated["status"], "exists")
        self.assertEqual((md.read_bytes(), html.read_bytes()), before)

    def test_srt_is_full_transcript_not_condensed_note(self):
        result = self.complete()
        text = Path(result["srt"]).read_text()
        self.assertEqual(text, "1\n00:00:00,000 --> 00:00:29,000\n完整的合成节目内容。\n")
        self.assertNotIn("核心结论", text)
        self.assertEqual(result["srt_cue_count"], 1)


    def test_thin_note_is_rejected_against_srt_text(self):
        row = long_episode(60, text="字" * 3000)
        result = self.ready(row)
        Path(result["draft_path"]).write_text(timed_note(row, ["00:00:10"]))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "note_too_thin")
        self.assertFalse(Path(result["markdown"]).exists())

    def test_overlong_note_is_rejected_against_srt_text(self):
        row = long_episode(60, text="字" * 3000)
        result = self.ready(row)
        Path(result["draft_path"]).write_text(timed_note(row, ["00:00:10"], body="字" * 2000))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "note_too_long")
        self.assertFalse(Path(result["markdown"]).exists())

    def test_note_in_range_is_accepted_for_a_substantial_transcript(self):
        row = long_episode(60, text="字" * 3000)
        result = self.ready(row)
        Path(result["draft_path"]).write_text(timed_note(row, ["00:00:10"], body="字" * 750))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertTrue(final["ok"], final)

    def test_template_has_no_label_prefixes_to_copy(self):
        template = (SCRIPTS.parent / "assets" / "podcast-note.md").read_text(encoding="utf-8")
        for label in ("主张：", "支撑：", "转折与限定："):
            self.assertNotIn(label, template)

    def test_every_topic_needs_a_timestamp(self):
        result = self.ready()
        Path(result["draft_path"]).write_text(valid_note(full_episode()).replace("（00:00:05）", ""))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "missing_topic_timestamp")

    def test_long_gap_without_a_topic_is_rejected(self):
        row = long_episode()
        result = self.ready(row)
        Path(result["draft_path"]).write_text(timed_note(row, ["00:01:00", "00:50:00"]))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "note_coverage_gap")

    def test_topics_spread_across_the_episode_are_accepted(self):
        row = long_episode()
        result = self.ready(row)
        stamps = [f"00:{m:02d}:00" for m in (2, 10, 20, 30, 40, 50, 58)]
        Path(result["draft_path"]).write_text(timed_note(row, stamps))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertTrue(final["ok"], final)

    def test_prepare_offers_srt_derived_reading_view(self):
        result = self.ready()
        self.assertEqual(result["source_srt_origin"], "transcribed")
        self.assertEqual(Path(result["source_view"]).read_text(), "[00:00:00 - 00:00:29] 完整的合成节目内容。\n")

    def test_existing_srt_is_the_source_the_note_is_written_from(self):
        first = self.ready()
        srt = Path(first["srt"])
        srt.parent.mkdir(parents=True)
        corrected = "1\n00:00:00,000 --> 00:00:29,000\n用户校正后的转录内容。\n"
        srt.write_text(corrected)
        result = self.ready()
        self.assertEqual(result["source_srt_origin"], "existing")
        self.assertIn("用户校正后的转录内容。", Path(result["source_view"]).read_text())
        self.assertNotIn("完整的合成节目内容", Path(result["source_view"]).read_text())
        Path(result["draft_path"]).write_text(valid_note(full_episode()))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertTrue(final["ok"], final)
        self.assertEqual(srt.read_text(), corrected)

    def test_srt_edited_after_prepare_blocks_finalize(self):
        first = self.ready()
        srt = Path(first["srt"])
        srt.parent.mkdir(parents=True)
        srt.write_text("1\n00:00:00,000 --> 00:00:29,000\n用户校正后的转录内容。\n")
        result = self.ready()
        srt.write_text("1\n00:00:00,000 --> 00:00:29,000\n整理之后又改了。\n")
        Path(result["draft_path"]).write_text(valid_note(full_episode()))
        final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertEqual(final["results"][0]["error"]["code"], "srt_changed")
        self.assertFalse(Path(result["markdown"]).exists())

    def test_existing_srt_that_misses_most_of_the_audio_is_not_a_source(self):
        row = long_episode()
        first = self.ready(row)
        srt = Path(first["srt"])
        srt.parent.mkdir(parents=True)
        srt.write_text("1\n00:00:00,000 --> 00:10:00,000\n只有开头十分钟。\n")
        result = self.ready(row)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "srt_incomplete")
        self.assertIn("只有开头十分钟", srt.read_text())

    def test_transcript_files_carry_no_commentary_about_accuracy(self):
        result = self.ready()
        for key in ("markdown", "text"):
            self.assertNotIn("识别准确", Path(result["transcripts"][key]).read_text())

    def test_srt_failure_rolls_back_both_new_note_files(self):
        result = self.ready()
        Path(result["draft_path"]).write_text(valid_note(full_episode()))
        original = p.publish_without_overwrite
        def publish(source, target):
            if target.suffix == ".srt":
                raise OSError(errno.ENOSPC, "磁盘不足")
            original(source, target)
        with patch.object(p, "publish_without_overwrite", side_effect=publish):
            final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertFalse(final["ok"])
        for key in ("markdown", "html", "srt"):
            self.assertFalse(Path(result[key]).exists())

    def test_legacy_cache_backfills_srt_without_touching_notes(self):
        result = self.complete()
        md, html, srt = [Path(result[k]) for k in ("markdown", "html", "srt")]
        before = md.read_bytes(), html.read_bytes()
        srt.unlink()
        job = n.job_for(self.out, identity(1))
        manifest = json.loads((job / "prepared.json").read_text())
        status_path = job / manifest["transcript_status"]
        status = json.loads(status_path.read_text())
        (job / status["outputs"].pop("srt")).unlink()
        status["output_hashes"].pop("srt")
        n.write_json(status_path, status)
        manifest["status_sha256"] = t.digest(status_path)
        n.write_json(job / "prepared.json", manifest)
        with patch.object(t, "Backend") as backend, patch.object(p, "download_episode") as download:
            repeated = self.ready()
        self.assertEqual(repeated["status"], "srt_added")
        self.assertTrue(srt.is_file())
        self.assertEqual((md.read_bytes(), html.read_bytes()), before)
        backend.assert_not_called()
        download.assert_not_called()

    def test_enrich_preserves_hand_corrected_srt(self):
        result = self.complete()
        srt = Path(result["srt"])
        corrected = srt.read_text().replace("完整的合成节目内容。", "用户人工校正后的内容。")
        srt.write_text(corrected)
        ready = self.ready(extra=("--enrich",))
        final = n.finalize(self.args("finalize", ready["draft_path"]), p)
        self.assertTrue(final["ok"])
        self.assertEqual(srt.read_text(), corrected)

    def test_missing_cache_can_reprepare_srt_without_changing_notes(self):
        result = self.complete()
        md, html, srt = [Path(result[k]) for k in ("markdown", "html", "srt")]
        before = md.read_bytes(), html.read_bytes()
        srt.unlink()
        job = n.job_for(self.out, identity(1))
        manifest = json.loads((job / "prepared.json").read_text())
        (job / manifest["transcript_status"]).unlink()
        repeated = self.ready()
        self.assertEqual(repeated["status"], "srt_added")
        self.assertTrue(srt.exists())
        self.assertEqual((md.read_bytes(), html.read_bytes()), before)
        self.assertEqual(self.ready()["status"], "exists")

    def test_missing_only_cached_srt_rebuilds_from_verified_segments(self):
        result = self.complete()
        Path(result["srt"]).unlink()
        job = n.job_for(self.out, identity(1))
        manifest = json.loads((job / "prepared.json").read_text())
        status = json.loads((job / manifest["transcript_status"]).read_text())
        (job / status["outputs"]["srt"]).unlink()
        with patch.object(t, "Backend") as backend:
            repeated = self.ready()
        self.assertEqual(repeated["status"], "srt_added")
        backend.assert_not_called()
        self.assertEqual(self.ready()["status"], "exists")

    def test_existing_srt_collision_is_not_overwritten(self):
        ready = self.ready()
        srt = Path(ready["srt"])
        srt.parent.mkdir(parents=True)
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n用户已有字幕。\n")
        Path(ready["draft_path"]).write_text(valid_note(full_episode()))
        final = n.finalize(self.args("finalize", ready["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertIn("用户已有字幕", srt.read_text())
        self.assertFalse(Path(ready["markdown"]).exists())

    def test_invalid_existing_srt_is_reported_without_replacement(self):
        result = self.complete()
        srt = Path(result["srt"])
        srt.write_text("用户写的内容，不能被覆盖")
        repeated = self.ready()
        self.assertEqual(repeated["status"], "failed")
        self.assertEqual(srt.read_text(), "用户写的内容，不能被覆盖")

    def test_collision_does_not_overwrite(self):
        row = full_episode()
        md, html = n.output_paths(self.out, row, self.channel, p)
        md.parent.mkdir(parents=True)
        md.write_text("用户原文")
        html.write_text("用户页面")
        result = self.ready(row)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(md.read_text(), "用户原文")

    def test_renderer_failure_publishes_neither_file(self):
        result = self.ready()
        Path(result["draft_path"]).write_text(valid_note(full_episode()))
        with patch.object(n, "render_file", side_effect=p.SkillError("render_failed", "失败")):
            final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertFalse(Path(result["markdown"]).exists())
        self.assertFalse(Path(result["html"]).exists())

    def test_second_publish_failure_rolls_back_own_first_file(self):
        result = self.ready()
        Path(result["draft_path"]).write_text(valid_note(full_episode()))
        original = p.publish_without_overwrite
        def publish(source, target):
            if target.suffix == ".html":
                raise OSError(errno.ENOSPC, "磁盘不足")
            original(source, target)
        with patch.object(p, "publish_without_overwrite", side_effect=publish):
            final = n.finalize(self.args("finalize", result["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertFalse(Path(result["markdown"]).exists())

    def test_enrichment_preserves_feelings_and_handwritten_text(self):
        result = self.complete()
        md = Path(result["markdown"])
        source = md.read_text().replace("## 核心结论", "## 我的感受\n\n### 我的原话\n\n这是我的原话。\n\n### 整理与延伸\n\n用户提供的补充。\n\n## 核心结论")
        source += "\n手工新增的说明。\n"
        md.write_text(source)
        ready = self.ready(extra=("--enrich",))
        draft = Path(ready["draft_path"])
        self.assertEqual(draft.read_text(), source)
        draft.write_text(source.replace("观点成立需要条件", "观点需要结合情境理解"))
        final = n.finalize(self.args("finalize", str(draft)), p)
        self.assertTrue(final["ok"])
        self.assertIn("这是我的原话。", md.read_text())
        self.assertIn("手工新增的说明。", md.read_text())

    def test_enrichment_removing_user_text_is_rejected(self):
        result = self.complete()
        md = Path(result["markdown"])
        md.write_text(md.read_text() + "\n用户手工新增的一段。\n")
        ready = self.ready(extra=("--enrich",))
        Path(ready["draft_path"]).write_text(valid_note(full_episode()))
        final = n.finalize(self.args("finalize", ready["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertIn("用户手工新增的一段。", md.read_text())

    def test_concurrent_user_edit_is_preserved(self):
        result = self.complete()
        ready = self.ready(extra=("--enrich",))
        md = Path(result["markdown"])
        md.write_text(md.read_text() + "\n用户刚做了修改。\n")
        final = n.finalize(self.args("finalize", ready["draft_path"]), p)
        self.assertFalse(final["ok"])
        self.assertIn("用户刚做了修改。", md.read_text())

    def test_relative_markdown_link_survives_directory_move(self):
        result = self.complete()
        md = Path(result["markdown"])
        html = Path(result["html"])
        from urllib.parse import quote
        self.assertIn('href="' + quote(md.name) + '"', html.read_text())
        moved = self.root / "移走的单集"
        shutil.move(str(md.parent), moved)
        self.assertTrue((moved / md.name).is_file())
        self.assertTrue((moved / html.name).is_file())

    def test_render_does_not_change_markdown(self):
        result = self.complete()
        md = Path(result["markdown"])
        before = md.read_bytes()
        final = n.render(self.args("render", str(md)), p)
        self.assertTrue(final["ok"])
        self.assertEqual(md.read_bytes(), before)

    def test_render_does_not_replace_unrelated_html(self):
        md = self.root / "笔记.md"
        md.write_text("# 测试")
        md.with_suffix(".html").write_text("用户自己写的 HTML")
        result = n.render(self.args("render", str(md)), p)
        self.assertFalse(result["ok"])
        self.assertEqual(md.with_suffix(".html").read_text(), "用户自己写的 HTML")

    def test_batch_partial_failure_continues_and_paid_skips(self):
        rows = [full_episode(1), full_episode(2, paid=True), full_episode(3)]
        def resolve(url, timeout):
            index = int(p.parse_link(url)[1], 16)
            if index == 1:
                raise p.SkillError("invalid_public_page", "字段失效")
            return rows[index - 1], self.channel
        with patch.object(p, "resolve_episode", side_effect=resolve):
            result = n.prepare(self.args("prepare", *[r["page_url"] for r in rows], "--out", str(self.out)), p)
        self.assertEqual([r["status"] for r in result["results"]], ["failed", "skipped_paid", "ready_for_note"])

    def test_rate_limit_stops_remaining(self):
        rows = [full_episode(1), full_episode(2)]
        with patch.object(p, "resolve_episode", side_effect=HTTPError(rows[0]["page_url"], 429, "限流", {}, None)):
            result = n.prepare(self.args("prepare", *[r["page_url"] for r in rows], "--out", str(self.out)), p)
        self.assertEqual([r["status"] for r in result["results"]], ["failed", "not_attempted"])

    def test_catalog_numbers_keep_snapshot_identity(self):
        value = catalog(3)
        path = self.root / "catalog.json"
        p.save_new_json(path, value)
        def resolve(url, timeout):
            return full_episode(int(p.parse_link(url)[1], 16)), self.channel
        with patch.object(p, "resolve_episode", side_effect=resolve):
            result = n.prepare(self.args("prepare", "--catalog", str(path), "--numbers", "3,1,3", "--out", str(self.out)), p)
        self.assertEqual([r["episode_id"] for r in result["results"]], [identity(3), identity(1)])


class TestCheckpoints(NoteCase):
    def test_srt_millisecond_rounding_and_hour_boundary(self):
        text = t.srt_text([{"start": .1236, "end": 1.2345, "text": "你好\n\n世界"},
                           {"start": 3599.9996, "end": 3600.4004, "text": "下一段"}], 3601)
        self.assertEqual(text, "1\n00:00:00,124 --> 00:00:01,235\n你好 世界\n\n2\n01:00:00,000 --> 01:00:00,400\n下一段\n")

    def test_sub_millisecond_speech_still_has_valid_srt_interval(self):
        text = t.srt_text([{"start": 0, "end": .0002, "text": "短句"}], 1)
        self.assertIn("00:00:00,000 --> 00:00:00,001", text)

    def fake_backend(self):
        return types.SimpleNamespace(name="fake", model_key="model-a", label="合成后端",
               transcribe=lambda path: [{"start": 0, "end": 9, "text": "合成语音"}])

    def mocks(self):
        def run(command):
            Path(command[-1]).write_bytes(b"synthetic chunk")
        return patch.object(t, "run", side_effect=run)

    def test_resume_reuses_only_completed_chunks(self):
        audio = self.root / "source.wav"
        audio.write_bytes(b"original")
        args = types.SimpleNamespace(chunk_minutes=1/6, backend="auto", model=None, language="zh", prompt="")
        backend = self.fake_backend()
        calls = []
        def infer(path):
            calls.append(path.name)
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return [{"start": 0, "end": 9, "text": "合成语音"}]
        backend.transcribe = infer
        def duration(path):
            return 30 if Path(path) == audio else 10
        with patch.object(t, "probe_duration", side_effect=duration), self.mocks():
            with self.assertRaises(KeyboardInterrupt):
                t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
            backend.transcribe = self.fake_backend().transcribe
            _, status = t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
        self.assertEqual(status["resumed_chunks"], 1)
        self.assertEqual(status["audio_processed_seconds"], 30)
        self.assertEqual(status["processed_intervals"][-1]["end"], 30)

    def test_parameter_and_source_changes_invalidate_checkpoints(self):
        audio = self.root / "source.wav"
        audio.write_bytes(b"original")
        args = types.SimpleNamespace(chunk_minutes=1/6, backend="auto", model=None, language="zh", prompt="")
        backend = self.fake_backend()
        def duration(path):
            if Path(path) == audio:
                return 30
            return args.chunk_minutes * 60
        with patch.object(t, "probe_duration", side_effect=duration), self.mocks():
            t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
            _, repeated = t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
            self.assertEqual(repeated["resumed_chunks"], 3)
            for key, value in (("language", "en"), ("prompt", "专名"), ("chunk_minutes", 1/4)):
                old = getattr(args, key)
                setattr(args, key, value)
                _, changed = t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
                self.assertEqual(changed["resumed_chunks"], 0)
                setattr(args, key, old)
            backend.model_key = "model-b"
            _, changed = t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
            self.assertEqual(changed["resumed_chunks"], 0)
            audio.write_bytes(b"different original")
            _, changed = t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
            self.assertEqual(changed["resumed_chunks"], 0)

    def test_empty_transcript_is_not_complete_content(self):
        audio = self.root / "source.wav"
        audio.write_bytes(b"original")
        args = types.SimpleNamespace(chunk_minutes=1, backend="auto", model=None, language="zh", prompt="")
        backend = self.fake_backend()
        backend.transcribe = lambda path: []
        with patch.object(t, "probe_duration", return_value=30), self.mocks():
            with self.assertRaises(t.TranscriptionError) as context:
                t.transcribe(audio, self.cache / "evidence", identity(1), "测试", args, backend)
        self.assertEqual(context.exception.code, "empty_transcript")

    def test_windows_cache_path_logic(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/Example/AppData/Local"}), patch.object(t.sys, "platform", "win32"):
            saved = os.environ.pop("XIAOYUZHOU_AUDIO_CACHE")
            try:
                self.assertEqual(str(t.cache_root()).replace("\\", "/"), "C:/Users/Example/AppData/Local/xiaoyuzhou-audio")
            finally:
                os.environ["XIAOYUZHOU_AUDIO_CACHE"] = saved


if __name__ == "__main__":
    unittest.main()
