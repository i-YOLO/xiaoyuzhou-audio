"""Synthetic fixtures only; real source checks are performed separately."""
import copy
import errno
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from test_podcast import p, PID, CHANNEL, FEED, Response, wave_data, xml, item


def identity(index):
    return f"{index:024x}"


def public_value(index, paid=False, title=None):
    return {"eid": identity(index), "pid": PID, "title": title or f"EP{index} 测试",
            "duration": 30, "pubDate": f"2026-10-{min(index, 28):02d}T08:00:00Z",
            "payType": "PAY_EPISODE" if paid else "FREE", "isPrivateMedia": paid,
            "enclosure": {"url": f"https://audio.example.com/{index}.wav"},
            "podcast": {"title": "测试频道"}}


def episode(index, paid=False, title=None):
    value = p.public_episode(public_value(index, paid, title), PID)
    value["number"] = index
    return value


def catalog(count, paid=()):
    return {"schema_version": 1, "channel_id": PID, "channel_title": "测试频道",
            "channel_url": CHANNEL, "episodes": [episode(i, i in paid) for i in range(1, count + 1)],
            "expected_count": count, "collected_count": count, "complete_history": True,
            "pay_episode_count": len(paid)}


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.settings_patch = patch.object(p, "config_path", return_value=self.root / "settings.json")
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.temp.cleanup()

    def args(self, command):
        return p.build_parser().parse_args(command)

    def listing(self, count=20, options=()):
        args = self.args(["list", CHANNEL, "--catalog", str(self.root / "list.json"), *options])
        value = catalog(count)
        with patch.object(p, "gather_catalog", return_value=value):
            return p.list_episodes(args)

    def download_args(self, value, selection=("--all",)):
        path = self.root / "catalog.json"
        p.save_new_json(path, value)
        return self.args(["download", "--catalog", str(path), "--out", str(self.root / "audio"), *selection])

    def resolve(self, url, timeout):
        number = int(p.parse_link(url)[1], 16)
        return episode(number), {"channel_id": PID}


class TestCounts(TempCase):
    def test_default_fifteen(self):
        result = self.listing()
        self.assertEqual(result["selected_count"], 15)
        self.assertEqual(result["requested_count"], 15)
        self.assertFalse(result["complete_history"])  # the returned snapshot has only 15 rows
        self.assertTrue(result["history_verified"])

    def test_custom_number(self):
        result = self.listing(options=("--limit", "3"))
        self.assertEqual(len(result["episodes"]), 3)
        self.assertTrue(result["ok"])

    def test_more_than_hundred(self):
        result = self.listing(156, ("--limit", "130"))
        self.assertEqual(len(result["episodes"]), 130)

    def test_all_is_not_truncated(self):
        result = self.listing(156, ("--all",))
        self.assertEqual(len(result["episodes"]), 156)
        self.assertTrue(result["request_satisfied"])

    def test_requested_more_than_channel_is_complete(self):
        result = self.listing(3, ("--limit", "50"))
        self.assertEqual(len(result["episodes"]), 3)
        self.assertTrue(result["ok"])

    def test_incomplete_all_reports_partial(self):
        value = catalog(15)
        value.update(expected_count=156, complete_history=False)
        args = self.args(["list", CHANNEL, "--all", "--catalog", str(self.root / "partial.json")])
        with patch.object(p, "gather_catalog", return_value=value):
            result = p.list_episodes(args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "incomplete_catalog")
        self.assertEqual(result["selected_count"], 15)

    def test_all_and_limit_conflict_before_network(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            self.args(["list", CHANNEL, "--all", "--limit", "3"])

    def test_single_link_lists_only_that_episode(self):
        args = self.args(["list", f"https://www.xiaoyuzhoufm.com/episode/{identity(1)}",
                          "--catalog", str(self.root / "one.json")])
        with patch.object(p, "resolve_episode", side_effect=self.resolve):
            result = p.list_episodes(args)
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["input_kind"], "episode")

    def test_old_two_thousand_cutoff_removed(self):
        result = p.parse_rss(xml("".join(item(uid=str(i)) for i in range(2005))), FEED, CHANNEL, PID, None)
        self.assertEqual(result["returned_by_feed"], 2005)
        self.assertEqual(len(result["episodes"]), 2005)


class TestSettings(TempCase):
    def test_invalid_directory_does_not_confirm(self):
        file = self.root / "not-a-directory"
        file.write_text("keep")
        for value in ("", "  ", str(file)):
            with self.subTest(value=value), self.assertRaises(p.SkillError):
                p.configure_directory(value)
        self.assertFalse((self.root / "settings.json").exists())
        self.assertEqual(file.read_text(), "keep")

    def test_new_user_not_confirmed_and_no_config_created(self):
        result = p.configuration()
        self.assertTrue(result["confirmation_required"])
        self.assertFalse((self.root / "settings.json").exists())

    def test_confirmation_required_before_implicit_download(self):
        with self.assertRaises(p.SkillError) as context:
            p.resolve_output_dir(None)
        self.assertEqual(context.exception.code, "directory_confirmation_required")

    def test_explicit_once_does_not_change_default(self):
        p.configure_directory(str(self.root / "default"))
        self.assertEqual(p.resolve_output_dir(str(self.root / "once")), self.root / "once")
        self.assertEqual(p.resolve_output_dir(None), self.root / "default")

    def test_confirmed_directory_survives_reload(self):
        p.configure_directory(str(self.root / "chosen"))
        self.assertFalse(p.configuration()["confirmation_required"])
        self.assertEqual(p.read_settings()["download_dir"], str(self.root / "chosen"))

    def test_atomic_update_preserves_unrelated_setting(self):
        p.configure_directory(str(self.root / "first"))
        path = self.root / "settings.json"
        value = json.loads(path.read_text())
        value["extra"] = "preserve"
        path.write_text(json.dumps(value))
        p.configure_directory(str(self.root / "second"))
        self.assertEqual(p.read_settings()["extra"], "preserve")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_invalid_settings_reported_and_explicit_set_repairs(self):
        (self.root / "settings.json").write_text("broken")
        with self.assertRaises(p.SkillError):
            p.configuration()
        p.configure_directory(str(self.root / "repaired"))
        self.assertTrue(p.read_settings()["directory_confirmed"])

    def test_macos_default(self):
        with patch.object(p.sys, "platform", "darwin"), patch.object(p.Path, "home", return_value=self.root):
            self.assertEqual(p.default_download_dir(), self.root / "Downloads" / "小宇宙")

    def test_windows_relocated_downloads_folder(self):
        relocated = self.root / "Relocated" / "Downloads"
        class Key:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=1,
                    OpenKey=lambda *args: Key(), QueryValueEx=lambda *args: (str(relocated), 2))
        with patch.object(p.sys, "platform", "win32"), patch.dict(sys.modules, {"winreg": registry}):
            self.assertEqual(p.default_download_dir(), relocated / "小宇宙")


class TestPublicSources(TempCase):
    def test_paid_public_page_does_not_retain_audio_address(self):
        value = public_value(1, True)
        result = p.public_episode(value, PID)
        self.assertEqual(result["access"], "paid")
        self.assertIsNone(result["audio_url"])
        self.assertFalse(result["available"])

    def test_restricted_free_is_not_claimed_paid_or_available(self):
        value = public_value(1)
        value["isPrivateMedia"] = True
        result = p.public_episode(value, PID)
        self.assertEqual(result["access"], "restricted")
        self.assertFalse(result["available"])

    def test_page_identity_mismatch_is_rejected(self):
        url = f"https://www.xiaoyuzhoufm.com/episode/{identity(1)}"
        with patch.object(p, "fetch_page", return_value={"episode": public_value(2)}):
            with self.assertRaises(p.SkillError):
                p.resolve_episode(url, 2)

    def test_gzip_page_read(self):
        data = b'{"data":"hello"}'
        response = Response(gzip.compress(data), mime="application/json")
        response.headers["Content-Encoding"] = "gzip"
        with patch.object(p, "open_remote", return_value=response):
            self.assertEqual(p.read_remote_bytes("https://example.com/data", 2, "application/json", 100), data)

    def test_gzip_expansion_is_bounded(self):
        response = Response(gzip.compress(b"x" * 2000))
        response.headers["Content-Encoding"] = "gzip"
        with patch.object(p, "open_remote", return_value=response), self.assertRaises(p.SkillError):
            p.read_remote_bytes("https://example.com/data", 2, "*/*", 100)

    def test_same_named_other_channel_feed_is_rejected(self):
        other = catalog(1)
        other["publisher_channel_url"] = "https://www.xiaoyuzhoufm.com/podcast/" + identity(900)
        self.assertFalse(p.feed_belongs_to_channel(other, CHANNEL, [episode(1)]))

    def test_merge_publisher_and_page_complete_and_remove_paid_url(self):
        page = {"pid": PID, "title": "测试频道", "episodeCount": 3, "payEpisodeCount": 1,
                "episodes": [public_value(2, True), public_value(3)]}
        feed = catalog(2)
        feed.update(feed_url=FEED, returned_by_feed=2, publisher_channel_url=CHANNEL)
        with patch.object(p, "fetch_page", return_value={"podcast": page}), \
             patch.object(p, "find_publisher_feeds", return_value=[FEED]), \
             patch.object(p, "read_remote_bytes", return_value=b"unused"), \
             patch.object(p, "parse_rss", return_value=feed):
            result = p.gather_catalog(CHANNEL, "https://rss.example.com", 2)
        self.assertEqual(result["collected_count"], 3)
        self.assertTrue(result["complete_history"])
        paid = next(e for e in result["episodes"] if e["episode_id"] == identity(2))
        self.assertIsNone(paid["audio_url"])

    def test_rss_address_alone_does_not_mean_free(self):
        value = catalog(1)
        value["episodes"][0]["access"] = "unknown"
        value["pay_episode_count"] = None
        with patch.object(p, "resolve_episode", side_effect=URLError("unreachable")):
            p.label_access(value, 2)
        self.assertEqual(value["episodes"][0]["access"], "unknown")
        self.assertEqual(value["unverified_access_count"], 1)


class TestSelectionAndBatch(TempCase):
    def test_numbers_ranges_and_dedup(self):
        self.assertEqual(p.number_selection("1,3,5-7,3"), [1, 3, 5, 6, 7])
        for value in ("0", "4-2", "1,", "a", "1-1000000"):
            with self.subTest(value=value), self.assertRaises(p.SkillError):
                p.number_selection(value, 100)

    def test_episode_number_is_not_list_position(self):
        value = catalog(2)
        value["episodes"][0]["title"] = "EP88 测试"
        value["episodes"][1]["title"] = "141 测试"
        self.assertEqual(p.episode_label_selection(value, "141")[0]["number"], 2)
        self.assertEqual(p.episode_label_selection(value, "EP88")[0]["number"], 1)

    def test_ambiguous_program_label_does_not_guess(self):
        value = catalog(2)
        for e in value["episodes"]: e["title"] = "EP88 同名"
        with self.assertRaises(p.SkillError) as context:
            p.episode_label_selection(value, "EP88")
        self.assertEqual(context.exception.code, "ambiguous_episode")

    def test_hash_series_is_explicit(self):
        value = catalog(2)
        value["episodes"][0]["title"] = "3# 番外"
        value["episodes"][1]["title"] = "03 正片"
        self.assertEqual(p.episode_label_selection(value, "3#")[0]["number"], 1)
        self.assertEqual(p.episode_label_selection(value, "3")[0]["number"], 2)

    def test_invalid_list_number_prevents_partial_download(self):
        args = self.download_args(catalog(2), ("--numbers", "1,9"))
        with patch.object(p, "resolve_episode") as resolve, self.assertRaises(p.SkillError):
            p.download_command(args)
        resolve.assert_not_called()

    def test_multiple_direct_urls_dedup(self):
        url = f"https://www.xiaoyuzhoufm.com/episode/{identity(1)}"
        args = self.args(["download", url, url, "--out", str(self.root / "audio")])
        self.assertEqual(len(p.download_targets(args)), 1)

    def test_mixed_batch_skips_paid_without_audio_request(self):
        args = self.download_args(catalog(3))
        def resolve(url, timeout):
            i = int(p.parse_link(url)[1], 16)
            return episode(i, paid=i == 2), {}
        with patch.object(p, "resolve_episode", side_effect=resolve), \
             patch.object(p, "open_remote", side_effect=lambda *args: Response(wave_data())) as remote:
            result = p.download_command(args)
        self.assertTrue(result["ok"])
        self.assertEqual(result["completed_count"], 2)
        self.assertEqual(result["skipped_paid_count"], 1)
        self.assertEqual(remote.call_count, 2)
        self.assertNotIn("https://audio.example.com/2.wav", [call.args[0] for call in remote.call_args_list])
        for saved in result["results"]:
            if saved["status"] == "downloaded":
                self.assertEqual(Path(saved["path"]).read_bytes(), wave_data())

    def test_paid_after_old_free_snapshot_is_still_skipped(self):
        args = self.download_args(catalog(1), ("--number", "1"))
        with patch.object(p, "resolve_episode", return_value=(episode(1, True), {})), \
             patch.object(p, "open_remote") as remote:
            result = p.download_command(args)
        self.assertEqual(result["status"], "skipped_paid")
        remote.assert_not_called()
        self.assertFalse((self.root / "audio").exists())

    def test_all_paid_has_no_audio_requests(self):
        args = self.download_args(catalog(2, (1, 2)))
        def resolve(url, timeout):
            return episode(int(p.parse_link(url)[1], 16), True), {}
        with patch.object(p, "resolve_episode", side_effect=resolve), patch.object(p, "open_remote") as remote:
            result = p.download_command(args)
        self.assertEqual(result["completed_count"], 0)
        self.assertEqual(result["skipped_paid_count"], 2)
        remote.assert_not_called()

    def test_metadata_failure_continues_next_free_episode(self):
        args = self.download_args(catalog(2))
        with patch.object(p, "resolve_episode", side_effect=[URLError("down"), (episode(2), {})]), \
             patch.object(p, "open_remote", return_value=Response(wave_data())):
            result = p.download_command(args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["completed_count"], 1)

    def test_media_failure_continues_next_free_episode(self):
        args = self.download_args(catalog(2))
        with patch.object(p, "resolve_episode", side_effect=self.resolve), \
             patch.object(p, "open_remote", side_effect=[URLError("down"), Response(wave_data())]):
            result = p.download_command(args)
        self.assertEqual([r["status"] for r in result["results"]], ["failed", "downloaded"])

    def test_rate_limit_stops_remaining(self):
        args = self.download_args(catalog(3))
        limited = HTTPError("https://example.com", 429, "rate limited", {}, None)
        self.addCleanup(limited.close)
        with patch.object(p, "resolve_episode", side_effect=limited) as resolve, patch.object(p, "open_remote") as remote:
            result = p.download_command(args)
        self.assertEqual(resolve.call_count, 1)
        remote.assert_not_called()
        self.assertEqual(result["not_attempted_count"], 2)

    def test_disk_full_stops_remaining(self):
        args = self.download_args(catalog(2))
        with patch.object(p, "resolve_episode", side_effect=self.resolve), \
             patch.object(p, "download_episode", side_effect=OSError(errno.ENOSPC, "disk full")) as download:
            result = p.download_command(args)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(result["not_attempted_count"], 1)

    def test_existing_file_preserved_and_batch_continues(self):
        args = self.download_args(catalog(2))
        one = copy.copy(args)
        with patch.object(p, "open_remote", return_value=Response(wave_data())):
            first = p.download_episode(one, episode(1))
        original = Path(first["path"]).read_bytes()
        with patch.object(p, "resolve_episode", side_effect=self.resolve), \
             patch.object(p, "open_remote", side_effect=lambda *args: Response(wave_data())):
            result = p.download_command(args)
        self.assertEqual(result["existing_count"], 1)
        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(Path(first["path"]).read_bytes(), original)

    def test_snapshot_selection_stays_on_same_id_when_title_changes(self):
        value = catalog(1)
        path = self.root / "catalog.json"
        args = self.download_args(value, ("--number", "1"))
        before = path.read_bytes()
        fresh = episode(1, title="EP1 新标题")
        with patch.object(p, "resolve_episode", return_value=(fresh, {})) as resolve, \
             patch.object(p, "open_remote", return_value=Response(wave_data())):
            result = p.download_command(args)
        self.assertEqual(result["title"], "EP1 新标题")
        self.assertEqual(resolve.call_args.args[0], value["episodes"][0]["page_url"])
        self.assertEqual(path.read_bytes(), before)

    def test_no_conversion_and_native_opus_extension(self):
        self.assertEqual(p.detect_audio_extension(b"OggS" + b"data", "audio/opus", "https://example.com/file.opus"), (".opus", True))

    def test_user_cancellation_keeps_remaining_unattempted(self):
        args = self.download_args(catalog(2))
        with patch.object(p, "resolve_episode", side_effect=self.resolve), \
             patch.object(p, "download_episode", side_effect=KeyboardInterrupt()):
            result = p.download_command(args)
        self.assertEqual(result["not_attempted_count"], 1)
        self.assertEqual(result["stop_reason"]["code"], "cancelled")


class TestPlatformConfigPaths(unittest.TestCase):
    def test_explicit_isolated_config_override(self):
        with patch.dict(os.environ, {"XIAOYUZHOU_AUDIO_CONFIG": "/tmp/xiao-test/settings.json"}):
            self.assertEqual(p.config_path(), Path("/tmp/xiao-test/settings.json"))

    def test_macos_application_support(self):
        env = {key: value for key, value in os.environ.items() if key != "XIAOYUZHOU_AUDIO_CONFIG"}
        with patch.object(p.sys, "platform", "darwin"), patch.object(p.os, "environ", env):
            self.assertEqual(p.config_path(), Path.home() / "Library" / "Application Support" / "xiaoyuzhou-audio" / "settings.json")

    def test_windows_appdata(self):
        env = {key: value for key, value in os.environ.items() if key != "XIAOYUZHOU_AUDIO_CONFIG"}
        env["APPDATA"] = "/tmp/windows-appdata"
        with patch.object(p.sys, "platform", "win32"), patch.object(p.os, "environ", env):
            self.assertEqual(p.config_path(), Path("/tmp/windows-appdata/xiaoyuzhou-audio/settings.json"))


if __name__ == "__main__":
    unittest.main()
