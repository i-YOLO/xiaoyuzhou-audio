"""Offline tests. All show names, URLs and audio data in this file are synthetic."""
import argparse
import datetime as dt
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from urllib.error import URLError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "podcast.py"
spec = importlib.util.spec_from_file_location("podcast", SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
PID = "0123456789abcdef01234567"
CHANNEL = f"https://www.xiaoyuzhoufm.com/podcast/{PID}"
FEED = f"https://rss.example.com/xiaoyuzhou/podcast/{PID}"


def xml(items: str, title: str = "测试频道") -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            f'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
            f'xmlns:media="http://search.yahoo.com/mrss/"><channel><title>{title}</title>'
            f'{items}</channel></rss>').encode()


def item(title="测试单集", date="Fri, 02 Oct 2026 08:00:00 +0800", uid="a", audio=True):
    enclosure = '<enclosure url="https://audio.example.com/recording.wav" type="audio/wav" length="2044"/>' if audio else ""
    return (f"<item><title>{title}</title><guid>{uid}</guid><pubDate>{date}</pubDate>"
            f'<link>https://www.xiaoyuzhoufm.com/episode/{uid}</link>'
            f"<itunes:duration>01:02:03</itunes:duration>{enclosure}</item>")


def wave_data():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 1000)
    return buf.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, mime="audio/wav", length=None, status=200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": mime, "Content-Length": str(len(data) if length is None else length)}

    def geturl(self):
        return "https://audio.example.com/recording.wav"


class TestInput(unittest.TestCase):
    def test_valid_channel_strips_tracking(self):
        self.assertEqual(p.parse_channel(CHANNEL + "?utm_source=test#fragment"), (PID, CHANNEL))

    def test_apex_domain(self):
        self.assertEqual(p.parse_channel(CHANNEL.replace("www.", ""))[0], PID)

    def test_reject_non_channel(self):
        for url in [CHANNEL.replace("/podcast/", "/episode/"), CHANNEL.replace("xiaoyuzhoufm.com", "evil.example"), "42章经", CHANNEL + "/extra"]:
            with self.subTest(url=url), self.assertRaises(p.SkillError):
                p.parse_channel(url)

    def test_reject_unsafe_urls(self):
        for url in ["file:///etc/passwd", "http://127.0.0.1/a", "http://169.254.169.254/", "http://localhost/", "http://[::1]/", "https://me:secret@example.com/", "https://example.com/\nInject", "http://2130706433/", "http://127.1/", "http://0x7f000001/"]:
            with self.subTest(url=url), self.assertRaises(p.SkillError):
                p.safe_url(url)

    def test_feed_url(self):
        self.assertEqual(p.feed_url("https://rss.example.com/", PID), FEED)

    def test_base_query_rejected(self):
        with self.assertRaises(p.SkillError):
            p.feed_url("https://rss.example.com?key=secret", PID)

    def test_duration(self):
        for value, expected in [("1:02:03", 3723), ("90", 90), ("02:12", 132), ("3600.8", 3600), ("bad", None), ("-1", None), ("1:99", None), ("", None), ("nan", None), ("inf", None)]:
            with self.subTest(value=value):
                self.assertEqual(p.duration_seconds(value), expected)

    def test_filename_is_safe_and_small(self):
        value = p.safe_filename('../CON:测试/\\*?' + "长" * 500)
        self.assertNotIn("/", value)
        self.assertNotIn("\\", value)
        self.assertLessEqual(len(value.encode()), 165)
        self.assertEqual(p.safe_filename("CON"), "_CON")
        self.assertNotEqual(p.safe_filename(".."), "..")


class TestFeed(unittest.TestCase):
    def parse(self, data, limit=10):
        return p.parse_rss(data, FEED, CHANNEL, PID, limit)

    def test_parse_fields(self):
        result = self.parse(xml(item()))
        row = result["episodes"][0]
        self.assertEqual(result["channel_title"], "测试频道")
        self.assertEqual(row["duration_seconds"], 3723)
        self.assertEqual(row["published_at"], "2026-10-02T08:00:00+08:00")
        self.assertEqual(row["audio_url"], "https://audio.example.com/recording.wav")
        self.assertEqual(row["number"], 1)
        self.assertTrue(row["available"])

    def test_sort_dedup_and_limit(self):
        data = xml(item("早", "Thu, 01 Oct 2026 00:00:00 +0000", "a") + item("晚", "Fri, 02 Oct 2026 00:00:00 +0000", "b") + item("晚重复", "Fri, 02 Oct 2026 00:00:00 +0000", "b"))
        result = self.parse(data, 1)
        self.assertEqual(result["returned_by_feed"], 2)
        self.assertEqual(len(result["episodes"]), 1)
        self.assertEqual(result["episodes"][0]["title"], "晚")

    def test_missing_fields(self):
        row = self.parse(xml("<item><title>只有标题</title></item>"))["episodes"][0]
        self.assertIsNone(row["audio_url"])
        self.assertIsNone(row["duration_seconds"])
        self.assertIsNone(row["published_at"])
        self.assertFalse(row["available"])

    def test_media_content(self):
        data = xml('<item><title>媒体</title><media:content medium="audio" type="audio/mp4" url="https://cdn.example.com/a.m4a"/></item>')
        self.assertTrue(self.parse(data)["episodes"][0]["audio_url"].endswith(".m4a"))

    def test_extension_fallback(self):
        data = xml('<item><title>媒体</title><enclosure url="https://cdn.example.com/a.mp3"/></item>')
        self.assertTrue(self.parse(data)["episodes"][0]["available"])

    def test_private_enclosure_is_ignored(self):
        data = xml('<item><title>媒体</title><enclosure url="http://127.0.0.1/secrets" type="audio/mp3"/></item>')
        self.assertFalse(self.parse(data)["episodes"][0]["available"])

    def test_reject_error_html(self):
        with self.assertRaisesRegex(p.SkillError, "不是 RSS"):
            self.parse(b"<html><title>Forbidden</title></html>")

    def test_reject_invalid_xml(self):
        with self.assertRaises(p.SkillError):
            self.parse(b"not xml")

    def test_dtd_blocked_in_utf8_and_utf16(self):
        for encoding in ["utf-8", "utf-16"]:
            with self.subTest(encoding=encoding), self.assertRaises(p.SkillError):
                self.parse('<!DOCTYPE rss [<!ENTITY x "x">]><rss><channel/></rss>'.encode(encoding))

    def test_stable_id(self):
        a = self.parse(xml(item()))["episodes"][0]["id"]
        b = self.parse(xml(item(title="更正标题")))["episodes"][0]["id"]
        self.assertEqual(a, b)

    def test_empty_is_distinct(self):
        self.assertEqual(self.parse(xml(""))["episodes"], [])


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = p.parse_rss(xml(item()), FEED, CHANNEL, PID, 10)
        self.catalog_path = self.root / "catalog.json"
        p.save_new_json(self.catalog_path, self.catalog)
        self.args = argparse.Namespace(catalog=str(self.catalog_path), number=1, id=None,
            out=str(self.root / "audio"), max_mb=1, timeout=2, deadline=60, name=None)

    def tearDown(self):
        self.temp.cleanup()

    def download(self, response):
        with patch.object(p, "open_remote", return_value=response):
            return p.download_episode(self.args)

    def test_download_byte_exact_and_wave_decodable(self):
        data = wave_data()
        result = self.download(Response(data))
        saved = Path(result["path"])
        self.assertEqual(saved.read_bytes(), data)
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(result["length_verified"])
        self.assertFalse(result["playback_verified"])
        with wave.open(str(saved), "rb") as audio:
            self.assertEqual(audio.getnframes(), 1000)
        self.assertFalse(list(saved.parent.glob("*.part")))

    def test_no_overwrite(self):
        first = self.download(Response(wave_data()))
        with self.assertRaisesRegex(p.SkillError, "已存在"):
            self.download(Response(wave_data()))
        self.assertEqual(Path(first["path"]).read_bytes(), wave_data())

    def test_incomplete_removes_partial(self):
        with self.assertRaisesRegex(p.SkillError, "不完整"):
            self.download(Response(wave_data(), length=len(wave_data()) + 4))
        self.assertEqual(list((self.root / "audio").iterdir()), [])

    def test_html_with_audio_mime_rejected(self):
        with self.assertRaisesRegex(p.SkillError, "文本"):
            self.download(Response(b"<html>access denied</html>", mime="audio/mpeg"))
        self.assertEqual(list((self.root / "audio").iterdir()), [])

    def test_partial_status_rejected(self):
        with self.assertRaisesRegex(p.SkillError, "206"):
            self.download(Response(wave_data(), status=206))

    def test_unknown_length_not_claimed_verified(self):
        response = Response(wave_data())
        del response.headers["Content-Length"]
        result = self.download(response)
        self.assertFalse(result["length_verified"])

    def test_size_limit(self):
        with self.assertRaisesRegex(p.SkillError, "上限"):
            self.download(Response(wave_data(), length=2 * 1024 * 1024))

    def test_body_limit_when_no_content_length(self):
        response = Response(wave_data() + b"\0" * (2 * 1024 * 1024))
        del response.headers["Content-Length"]
        with self.assertRaisesRegex(p.SkillError, "上限"):
            self.download(response)
        self.assertEqual(list((self.root / "audio").iterdir()), [])

    def test_signature_overrides_rsshub_mime(self):
        result = self.download(Response(wave_data(), mime="audio/mpeg"))
        self.assertTrue(result["path"].endswith(".wav"))

    def test_explicit_filename(self):
        self.args.name = "用户指定名字"
        result = self.download(Response(wave_data()))
        self.assertTrue(Path(result["path"]).name == "用户指定名字.wav")

    def test_out_of_range(self):
        self.args.number = 100
        with self.assertRaisesRegex(p.SkillError, "不在"):
            self.download(Response(wave_data()))

    def test_selection_without_audio(self):
        catalog = p.parse_rss(xml(item(audio=False)), FEED, CHANNEL, PID, 10)
        with self.assertRaisesRegex(p.SkillError, "没有公开音频"):
            p.select_episode(catalog, 1, None)

    def test_id_selection(self):
        self.args.id = self.catalog["episodes"][0]["id"]
        self.args.number = None
        self.assertTrue(self.download(Response(wave_data()))["ok"])

    def test_link_unsupported_fallback(self):
        with patch.object(p.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")):
            self.assertTrue(self.download(Response(wave_data()))["ok"])

    def test_publish_race_preserves_existing(self):
        tmp = self.root / "temp"
        target = self.root / "existing"
        tmp.write_bytes(b"new")
        target.write_bytes(b"old")
        with self.assertRaises(p.SkillError):
            p.publish_without_overwrite(tmp, target)
        self.assertEqual(target.read_bytes(), b"old")

    def test_snapshot_save_never_overwrites(self):
        before = self.catalog_path.read_bytes()
        with self.assertRaises(p.SkillError):
            p.save_new_json(self.catalog_path, {"broken": True})
        self.assertEqual(self.catalog_path.read_bytes(), before)


class TestWorkflow(unittest.TestCase):
    def test_list_then_download_same_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = argparse.Namespace(channel=CHANNEL, rsshub="https://rss.example.com", timeout=2, limit=10, catalog=str(root / "list.json"))
            feed = xml(item("早期", "Thu, 01 Oct 2026 00:00:00 +0000", "a") + item("最新", "Fri, 02 Oct 2026 00:00:00 +0000", "b"))
            catalog = p.parse_rss(feed, FEED, CHANNEL, PID, None)
            catalog.update(expected_count=2, collected_count=2, complete_history=True, pay_episode_count=0)
            for episode in catalog["episodes"]:
                episode["access"] = "public"
            with patch.object(p, "gather_catalog", return_value=catalog) as gathered, patch.object(p, "open_remote") as remote:
                listed = p.list_episodes(args)
                self.assertEqual(gathered.call_count, 1)
                remote.assert_not_called()  # listing never downloads audio
            self.assertEqual(listed["episodes"][0]["title"], "最新")
            self.assertTrue(Path(listed["catalog_path"]).exists())
            args = argparse.Namespace(catalog=listed["catalog_path"], number=2, id=None, out=str(root / "audio"), max_mb=1, timeout=2, deadline=30, name=None)
            with patch.object(p, "open_remote", return_value=Response(wave_data())):
                downloaded = p.download_episode(args)
            self.assertEqual(downloaded["title"], "早期")
            self.assertEqual(Path(downloaded["path"]).read_bytes(), wave_data())

    def test_cli_doctor(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "doctor"], capture_output=True, text=True)
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(data["network_tested"])

    def test_cli_invalid_channel_has_json_error(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "list", "bad input"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_no_success_on_network_failure(self):
        args = ["podcast.py", "list", CHANNEL]
        with patch.object(sys, "argv", args), patch.object(p, "open_remote", side_effect=URLError("DNS unavailable")), patch("sys.stdout", new_callable=io.StringIO) as output:
            result = p.main()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "network_error")


if __name__ == "__main__":
    unittest.main()
