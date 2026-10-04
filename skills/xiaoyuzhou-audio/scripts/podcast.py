#!/usr/bin/env python3
"""List Xiaoyuzhou episodes and download explicitly selected public audio.

Python 3.10+, standard library only. No transcription, login, AI API, or scheduler.
JSON is written to stdout; progress goes to stderr. No existing file is overwritten.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import email.utils
import errno
import gzip
import hashlib
from html.parser import HTMLParser
from http.client import HTTPException
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import sys
import tempfile
import time
import unicodedata
from urllib import error, parse, request
import uuid
import xml.etree.ElementTree as ET

VERSION = "0.2.0"
DEFAULT_RSSHUB = "https://rsshub.bestblogs.dev"
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_CATALOG_BYTES = 32 * 1024 * 1024
USER_AGENT = f"xiaoyuzhou-audio-skill/{VERSION} (personal RSS reader)"
CHANNEL_RE = re.compile(r"/podcast/([0-9a-fA-F]{24})/?$")
EPISODE_RE = re.compile(r"/episode/([0-9a-fA-F]{24})/?$")
MAX_PAGE_BYTES = 8 * 1024 * 1024
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".webm", ".mp4"}
MIME_EXTENSIONS = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/aacp": ".aac",
    "audio/ogg": ".ogg", "application/ogg": ".ogg", "audio/opus": ".opus",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/webm": ".webm",
}


class SkillError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def clean_text(value: str | None, limit: int = 800) -> str:
    """Remove controls/newlines; RSS strings are data, never executable instructions."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = "".join(c if not unicodedata.category(c).startswith("C") else " " for c in text)
    return " ".join(text.split())[:limit]


def safe_url(url: str) -> str:
    """Allow public HTTP(S) URLs, no embedded credentials or literal private IPs.

    Redirects use the same check. This is a local CLI, not a hardened hosted proxy;
    it does not pin DNS results and must not be exposed as an untrusted network API.
    """
    if not isinstance(url, str) or len(url) > 8192 or re.search(r"[\x00-\x20\x7f]", url):
        raise SkillError("invalid_url", "URL 含有空白、控制字符或长度不合法。")
    try:
        parts = parse.urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        _ = parts.port
        credentials = parts.username is not None or parts.password is not None
    except ValueError as exc:
        raise SkillError("invalid_url", "URL 格式不合法。") from exc
    if parts.scheme.lower() not in {"http", "https"} or not host or credentials:
        raise SkillError("invalid_url", "仅接受不含账号密码的 HTTP(S) 地址。")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SkillError("unsafe_url", "不访问本地或内部网络域名。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or re.fullmatch(r"[0-9.]+", host) or any(p.startswith("0x") for p in host.split(".")):
            raise SkillError("unsafe_url", "不接受内部主机名或非标准 IP 地址。")
    else:
        if not address.is_global:
            raise SkillError("unsafe_url", "不访问私网、环回或链路本地 IP。")
    return url


class CheckedRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_remote(url: str, timeout: float, accept: str):
    safe_url(url)
    req = request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "identity"
    })
    # Default TLS certificate verification and the user's standard proxy settings.
    return request.build_opener(CheckedRedirect()).open(req, timeout=timeout)


def positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须填写正整数") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("必须填写正整数")
    return result


def parse_channel(url: str) -> tuple[str, str]:
    url = url.strip()
    safe_url(url)
    parts = parse.urlsplit(url)
    match = CHANNEL_RE.fullmatch(parts.path)
    if parts.hostname not in {"xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"} or not match or parts.port not in (None, 443, 80):
        raise SkillError("invalid_channel", "请输入完整频道链接：https://www.xiaoyuzhoufm.com/podcast/24位节目ID；第一版不接收单集或短链接。")
    pid = match.group(1).lower()
    return pid, f"https://www.xiaoyuzhoufm.com/podcast/{pid}"


def feed_url(base: str, pid: str) -> str:
    base = base.strip().rstrip("/")
    safe_url(base)
    parts = parse.urlsplit(base)
    if parts.query or parts.fragment:
        raise SkillError("invalid_rsshub", "RSSHub 服务地址不能含查询参数或片段。填写服务根地址，不是完整订阅链接。")
    return f"{base}/xiaoyuzhou/podcast/{pid}"


def parse_link(url: str) -> tuple[str, str, str]:
    url = url.strip()
    safe_url(url)
    parts = parse.urlsplit(url)
    if parts.hostname not in {"xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"} or parts.port not in (None, 80, 443):
        raise SkillError("invalid_link", "请输入小宇宙频道或单集的完整链接。")
    for kind, pattern in (("podcast", CHANNEL_RE), ("episode", EPISODE_RE)):
        match = pattern.fullmatch(parts.path)
        if match:
            identity = match.group(1).lower()
            return kind, identity, f"https://www.xiaoyuzhoufm.com/{kind}/{identity}"
    raise SkillError("invalid_link", "仅接收 /podcast/ 或 /episode/ 后跟 24 位 ID 的完整链接。")


def config_path() -> Path:
    override = os.environ.get("XIAOYUZHOU_AUDIO_CONFIG")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "xiaoyuzhou-audio" / "settings.json"


def default_download_dir() -> Path:
    base = Path.home() / "Downloads"
    if sys.platform == "win32":
        # Respect a Downloads folder relocated through the Windows shell.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                base = Path(os.path.expandvars(value))
        except (ImportError, OSError):
            pass
    return base / "小宇宙"


def read_settings() -> dict:
    path = config_path()
    if not path.exists():
        return {"schema_version": 1, "download_dir": str(default_download_dir()),
                "directory_confirmed": False}
    try:
        with path.open("rb") as handle:
            data = handle.read(64 * 1024 + 1)
        if len(data) > 64 * 1024:
            raise ValueError("oversized settings")
        settings = json.loads(data)
        if not isinstance(settings, dict) or settings.get("schema_version") != 1:
            raise ValueError("invalid settings schema")
        if not isinstance(settings.get("download_dir"), str) or not settings["download_dir"]:
            raise ValueError("invalid download directory")
        if not isinstance(settings.get("directory_confirmed"), bool):
            raise ValueError("invalid confirmation state")
        return settings
    except (ValueError, UnicodeError) as exc:
        raise SkillError("invalid_config", "下载目录配置无效，请用 config set 重新配置。") from exc


def configure_directory(directory: str) -> dict:
    if not isinstance(directory, str) or not directory.strip():
        raise SkillError("invalid_directory", "下载目录不能为空，未写入确认状态。")
    chosen = Path(directory).expanduser().resolve()
    if chosen.exists() and not chosen.is_dir():
        raise SkillError("invalid_directory", "下载位置是文件而非目录，未写入确认状态。")
    path = config_path()
    try:
        settings = read_settings()
    except SkillError as exc:
        if exc.code != "invalid_config":
            raise
        settings = {"schema_version": 1}
    settings.update(download_dir=str(chosen),
                    directory_confirmed=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                prefix=".settings-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "action": "config", "config_path": str(path), **settings}


def configuration() -> dict:
    settings = read_settings()
    return {"ok": True, "action": "config", "config_path": str(config_path()),
            "default_download_dir": str(default_download_dir()),
            "confirmation_required": not settings["directory_confirmed"], **settings}


def resolve_output_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    settings = read_settings()
    if not settings["directory_confirmed"]:
        raise SkillError("directory_confirmation_required",
            f"首次使用请确认下载目录，推荐 {default_download_dir()}；确认后执行 config set --default 或 --download-dir。")
    return Path(settings["download_dir"]).expanduser().resolve()


def read_remote_bytes(url: str, timeout: float, accept: str, maximum: int) -> bytes:
    with open_remote(url, timeout, accept) as response:
        if response.status != 200:
            raise SkillError("http_status", f"请求返回 HTTP {response.status}。")
        data = response.read(maximum + 1)
        encoding = response.headers.get("Content-Encoding", "identity").lower()
    if len(data) > maximum:
        raise SkillError("response_too_large", "远端内容超过读取上限，未截断后假装成功。")
    if encoding == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(maximum + 1)
        except (OSError, EOFError) as exc:
            raise SkillError("content_encoding", "远端 gzip 内容无效。") from exc
        if len(data) > maximum:
            raise SkillError("response_too_large", "解压后的内容超过读取上限。")
    elif encoding not in {"", "identity"}:
        raise SkillError("content_encoding", f"暂不支持响应编码 {encoding}。")
    return data


class PageDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.collecting = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.collecting = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.collecting = False

    def handle_data(self, data):
        if self.collecting:
            self.parts.append(data)


def fetch_page(url: str, timeout: float) -> dict:
    parser = PageDataParser()
    try:
        parser.feed(read_remote_bytes(url, timeout, "text/html", MAX_PAGE_BYTES).decode("utf-8"))
        value = json.loads("".join(parser.parts))["props"]["pageProps"]
        if not isinstance(value, dict):
            raise ValueError("invalid page data")
        return value
    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise SkillError("invalid_public_page", "公开页面未返回可核对的节目数据。") from exc


def episode_identity(episode: dict) -> str | None:
    value = episode.get("episode_id") or episode.get("eid")
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{24}", value):
        return value.lower()
    link = episode.get("page_url")
    if isinstance(link, str):
        parts = parse.urlsplit(link)
        match = EPISODE_RE.fullmatch(parts.path)
        if parts.hostname in {"xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"} and match:
            return match.group(1).lower()
    return None


def public_episode(value: dict, catalog_pid: str) -> dict:
    if not isinstance(value, dict):
        raise SkillError("invalid_public_page", "公开单集对象格式发生变化，未推断音频权限。")
    identity = value.get("eid")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", identity):
        raise SkillError("invalid_public_page", "公开单集 ID 无效。")
    identity = identity.lower()
    pay_type = value.get("payType")
    private = value.get("isPrivateMedia") is True
    paid = isinstance(pay_type, str) and pay_type != "FREE"
    enclosure = value.get("enclosure") or {}
    audio = enclosure.get("url") if isinstance(enclosure, dict) and not (paid or private) else None
    if audio:
        try:
            safe_url(audio)
        except SkillError:
            audio = None
    date = parse_date(value.get("pubDate")) if isinstance(value.get("pubDate"), str) else None
    return {"id": hashlib.sha256(f"{catalog_pid}\0{identity}".encode()).hexdigest()[:20],
            "episode_id": identity, "title": clean_text(value.get("title")) or "未命名单集",
            "published_at": date.isoformat() if date else None,
            "duration_seconds": duration_seconds(str(value.get("duration", ""))),
            "page_url": f"https://www.xiaoyuzhoufm.com/episode/{identity}",
            "audio_url": audio, "audio_type": None, "length_bytes": None,
            "available": bool(audio), "access": "paid" if paid else "restricted" if private else "public" if pay_type == "FREE" else "unknown",
            "pay_type": pay_type, "is_private_media": private}


def resolve_episode(url: str, timeout: float) -> tuple[dict, dict]:
    kind, identity, canonical = parse_link(url)
    if kind != "episode":
        raise SkillError("invalid_episode", "直接下载请提供单集链接；频道请先列出再选择。")
    value = fetch_page(canonical, timeout).get("episode")
    if not isinstance(value, dict) or value.get("eid") != identity:
        raise SkillError("invalid_public_page", "页面返回的单集与所选链接不一致。")
    pid = value.get("pid")
    if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-fA-F]{24}", pid):
        raise SkillError("invalid_public_page", "单集所属频道 ID 无效。")
    episode = public_episode(value, pid.lower())
    podcast = value.get("podcast") or {}
    if not isinstance(podcast, dict):
        podcast = {}
    return episode, {"channel_id": pid.lower(),
                     "channel_title": clean_text(podcast.get("title")) or pid,
                     "channel_url": f"https://www.xiaoyuzhoufm.com/podcast/{pid.lower()}"}


def feed_belongs_to_channel(catalog: dict, channel: str, visible: list[dict]) -> bool:
    link = catalog.get("publisher_channel_url")
    if link:
        try:
            _, canonical = parse_channel(link)
        except SkillError:
            pass
        else:
            return canonical == channel
    known = {episode_identity(e) for e in visible} - {None}
    return bool(known & {episode_identity(e) for e in catalog["episodes"]})


def find_publisher_feeds(title: str, timeout: float) -> list[str]:
    query = parse.urlencode({"term": title, "entity": "podcast", "country": "cn", "limit": 25})
    data = json.loads(read_remote_bytes(f"https://itunes.apple.com/search?{query}",
                      timeout, "application/json", MAX_PAGE_BYTES))
    if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
        raise SkillError("invalid_discovery_response", "发布者 RSS 搜索结果格式无效。")
    feeds = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        url = item.get("feedUrl")
        if clean_text(item.get("collectionName")) == title and isinstance(url, str):
            try:
                safe_url(url)
            except SkillError:
                continue
            if url not in feeds:
                feeds.append(url)
    return feeds[:5]


def source_error(exc: Exception) -> dict:
    if isinstance(exc, SkillError):
        code = exc.code
    elif isinstance(exc, error.HTTPError):
        code = f"http_{exc.code}"
    elif isinstance(exc, (error.URLError, socket.timeout, TimeoutError, ssl.SSLError, HTTPException)):
        code = "network_error"
    elif isinstance(exc, OSError):
        code = "io_error"
    else:
        code = "invalid_data"
    return {"code": code, "message": clean_text(str(exc)),
            **(exc.details if isinstance(exc, SkillError) else {})}


def stops_batch(exc: Exception) -> bool:
    return (isinstance(exc, error.HTTPError) and exc.code == 429 or
            isinstance(exc, SkillError) and exc.code in {"http_429", "directory_confirmation_required"} or
            isinstance(exc, OSError) and exc.errno in {
                errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC), errno.EROFS, errno.EACCES, errno.EPERM})


def gather_catalog(channel: str, rsshub: str, timeout: float, publisher_feed: str | None = None) -> dict:
    pid, channel = parse_channel(channel)
    visible, title, expected, pay_count = [], pid, None, None
    errors, sources, feeds = [], [], []
    last_error = None
    try:
        page = fetch_page(channel, timeout).get("podcast")
        if not isinstance(page, dict) or page.get("pid") != pid:
            raise SkillError("invalid_public_page", "频道 ID 与公开页面不一致。")
        title = clean_text(page.get("title")) or pid
        count = page.get("episodeCount")
        expected = count if type(count) is int and count >= 0 else None
        pay_count = page.get("payEpisodeCount")
        visible = [public_episode(e, pid) for e in page.get("episodes", [])]
        sources.append({"kind": "public_channel_page", "url": channel, "count": len(visible)})
    except (SkillError, OSError, ValueError, HTTPException) as exc:
        errors.append(source_error(exc))
        last_error = exc
    candidates = [publisher_feed] if publisher_feed else []
    if not candidates and title != pid:
        try:
            candidates = find_publisher_feeds(title, timeout)
        except (SkillError, OSError, ValueError, HTTPException) as exc:
            errors.append(source_error(exc))
    for url in candidates:
        try:
            safe_url(url)
            catalog = parse_rss(read_remote_bytes(url, timeout, "application/rss+xml, application/xml, text/xml",
                                MAX_FEED_BYTES), url, channel, pid, None)
            if not feed_belongs_to_channel(catalog, channel, visible):
                raise SkillError("source_mismatch", "RSS 不能核对为该频道，未使用同名播客的内容。")
            feeds.append(catalog)
            sources.append({"kind": "publisher_rss", "url": url, "count": len(catalog["episodes"])})
            break
        except (SkillError, OSError, ValueError, HTTPException) as exc:
            if publisher_feed:
                raise
            errors.append(source_error(exc))
            last_error = exc
    if not feeds:
        url = feed_url(rsshub, pid)
        try:
            catalog = parse_rss(read_remote_bytes(url, timeout, "application/rss+xml, application/xml, text/xml",
                                MAX_FEED_BYTES), url, channel, pid, None)
            feeds.append(catalog)
            sources.append({"kind": "rsshub", "url": url, "count": len(catalog["episodes"])})
            if title == pid:
                title = catalog["channel_title"]
        except (SkillError, OSError, ValueError, HTTPException) as exc:
            errors.append(source_error(exc))
            last_error = exc
    merged = {}
    for catalog in feeds:
        for episode in catalog["episodes"]:
            key = episode_identity(episode) or episode["id"]
            merged[key] = {**episode, "access": "unknown"}
    for episode in visible:
        key = episode_identity(episode)
        prior = merged.get(key)
        if prior and prior["title"] != episode["title"]:
            episode = {**episode, "rss_title": prior["title"]}
        merged[key] = episode
    episodes = list(merged.values())
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    episodes.sort(key=lambda e: parse_date(e.get("published_at")) or floor, reverse=True)
    for index, episode in enumerate(episodes, 1):
        identity = episode_identity(episode)
        if identity:
            episode["episode_id"] = identity
            episode["id"] = hashlib.sha256(f"{pid}\0{identity}".encode()).hexdigest()[:20]
            episode["page_url"] = f"https://www.xiaoyuzhoufm.com/episode/{identity}"
        episode["number"] = index
    if not episodes and expected != 0:
        if last_error:
            raise last_error
        raise SkillError("empty_feed", "公开来源本次没有返回节目，不能据此判断没有历史内容。")
    return {"schema_version": 1, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "channel_id": pid, "channel_title": title, "channel_url": channel,
            "feed_url": feeds[0]["feed_url"] if feeds else None,
            "returned_by_feed": feeds[0]["returned_by_feed"] if feeds else 0,
            "collected_count": len(episodes), "expected_count": expected, "pay_episode_count": pay_count,
            "complete_history": expected is not None and len(episodes) == expected,
            "sources": sources, "source_errors": errors, "episodes": episodes}


def label_access(catalog: dict, timeout: float) -> None:
    """Use the platform's paid count only after all paid identities are accounted for."""
    paid_count = catalog.get("pay_episode_count")
    known_paid = catalog.get("known_paid_count", 0)
    for episode in catalog["episodes"]:
        if episode.get("access") != "unknown":
            continue
        if type(paid_count) is int and known_paid == paid_count:
            episode.update(access="public", payment_verification="channel_paid_count")
            continue
        identity = episode_identity(episode)
        if not identity:
            continue
        try:
            fresh, _ = resolve_episode(f"https://www.xiaoyuzhoufm.com/episode/{identity}", timeout)
            episode.update(access=fresh["access"], pay_type=fresh["pay_type"],
                           is_private_media=fresh["is_private_media"],
                           available=fresh["available"], audio_url=fresh["audio_url"])
            if fresh["access"] == "paid":
                known_paid += 1
        except (SkillError, OSError, ValueError, HTTPException) as exc:
            episode["access_check_error"] = source_error(exc)
            if stops_batch(exc):
                catalog["access_checks_stopped"] = source_error(exc)
                break
    catalog["unverified_access_count"] = sum(e.get("access") == "unknown" for e in catalog["episodes"])


def local_name(tag: str) -> str:
    return tag.split("}")[-1].lower()


def child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def parse_date(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        result = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        try:
            result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=dt.timezone.utc)
    return result


def duration_seconds(value: str) -> int | None:
    try:
        parts = value.strip().split(":")
        if not 1 <= len(parts) <= 3:
            return None
        numbers = [float(part) for part in parts]
        if any(part < 0 for part in numbers):
            return None
        if len(parts) > 1 and any(part >= 60 for part in numbers[1:]):
            return None
        result = 0.0
        for part in numbers:
            result = result * 60 + part
        return int(result)
    except (ValueError, OverflowError):
        return None


def audio_enclosure(item: ET.Element, base: str) -> tuple[str | None, str | None, int | None]:
    candidates = []
    for node in item.iter():
        name = local_name(node.tag)
        if name not in {"enclosure", "content"}:
            continue
        raw_url = node.get("url")
        if not raw_url:
            continue
        kind = (node.get("type") or "").lower().split(";")[0].strip()
        url = parse.urljoin(base, raw_url)
        try:
            safe_url(url)
        except SkillError:
            continue
        extension = Path(parse.urlsplit(url).path).suffix.lower()
        if kind.startswith("audio/") or node.get("medium") == "audio" or (name == "enclosure" and extension in AUDIO_EXTENSIONS):
            size = node.get("length", "")
            candidates.append((url, kind or None, int(size) if size.isdigit() and int(size) > 0 else None))
    return candidates[0] if candidates else (None, None, None)


def parse_rss(data: bytes, source: str, channel: str, pid: str, limit: int | None) -> dict:
    if len(data) > MAX_FEED_BYTES:
        raise SkillError("feed_too_large", "RSS 超过 8 MiB 上限。")
    scan = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in scan or b"<!ENTITY" in scan:
        raise SkillError("unsafe_xml", "拒绝含 DTD 或实体声明的 RSS。")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SkillError("invalid_rss", "返回内容不是有效 RSS XML，可能是错误页、登录页或反爬页面。") from exc
    if local_name(root.tag) not in {"rss", "rdf"}:
        raise SkillError("invalid_rss", "返回内容不是 RSS，不能把错误页当作空频道。")
    feed = next((child for child in root if local_name(child.tag) == "channel"), None)
    if feed is None:
        raise SkillError("invalid_rss", "RSS 中缺少 channel。")
    items = [child for child in feed if local_name(child.tag) == "item"]
    if not items and local_name(root.tag) == "rdf":
        items = [child for child in root if local_name(child.tag) == "item"]
    episodes, seen = [], set()
    for item in items:
        title = clean_text(child_text(item, "title")) or "未命名单集"
        link = parse.urljoin(source, child_text(item, "link")) if child_text(item, "link") else None
        if link:
            try:
                safe_url(link)
            except SkillError:
                link = None
        audio_url, audio_type, length = audio_enclosure(item, source)
        raw_date = child_text(item, "pubdate") or child_text(item, "date")
        date = parse_date(raw_date)
        guid = clean_text(child_text(item, "guid"), 4096)
        key = guid or link or audio_url or f"{title}|{raw_date}"
        eid = hashlib.sha256(f"{pid}\0{key}".encode()).hexdigest()[:20]
        if eid in seen:
            continue
        seen.add(eid)
        episodes.append({
            "id": eid, "title": title,
            "published_at": date.isoformat() if date else None,
            "published_raw": clean_text(raw_date),
            "duration_seconds": duration_seconds(child_text(item, "duration")),
            "page_url": link, "audio_url": audio_url,
            "audio_type": audio_type, "length_bytes": length,
            "available": audio_url is not None,
        })
    floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    episodes.sort(key=lambda episode: parse_date(episode["published_at"]) or floor, reverse=True)
    total = len(episodes)
    selected = episodes if limit is None else episodes[:limit]
    for index, episode in enumerate(selected, 1):
        episode["number"] = index
    return {
        "schema_version": 1,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "channel_id": pid, "channel_title": clean_text(child_text(feed, "title")) or pid,
        "channel_url": channel, "feed_url": source,
        "publisher_channel_url": child_text(feed, "link"),
        "returned_by_feed": total, "episodes": selected,
    }


def save_new_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise SkillError("file_exists", f"文件已存在，未覆盖：{path}") from exc


def list_episodes(args) -> dict:
    kind, identity, canonical = parse_link(args.channel)
    all_requested = getattr(args, "all", False)
    limit = None if all_requested else (getattr(args, "limit", None) or 15)
    if kind == "episode":
        episode, channel = resolve_episode(canonical, args.timeout)
        episode["number"] = 1
        catalog = {"schema_version": 1, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                   **channel, "expected_count": 1, "collected_count": 1,
                   "complete_history": False, "input_kind": "episode", "episodes": [episode]}
        satisfied = True
    else:
        catalog = gather_catalog(canonical, args.rsshub, args.timeout, getattr(args, "feed", None))
        catalog["known_paid_count"] = sum(e.get("access") == "paid" for e in catalog["episodes"])
        catalog["history_verified"] = catalog["complete_history"]
        needed = min(limit, catalog["expected_count"]) if limit is not None and catalog["expected_count"] is not None else limit
        satisfied = catalog["complete_history"] if all_requested else len(catalog["episodes"]) >= needed
        catalog["episodes"] = catalog["episodes"] if limit is None else catalog["episodes"][:limit]
        catalog["complete_history"] = catalog["history_verified"] and len(catalog["episodes"]) == catalog["expected_count"]
        catalog["input_kind"] = "podcast"
        label_access(catalog, args.timeout)
    path = Path(args.catalog).expanduser() if args.catalog else config_path().parent / "catalogs" / f"{identity}-{uuid.uuid4().hex[:12]}.json"
    path = path.absolute()
    catalog.update(requested_count=limit, all_requested=all_requested,
                   selected_count=len(catalog["episodes"]), request_satisfied=satisfied)
    save_new_json(path, catalog)
    result = {"ok": satisfied, "action": "list", "catalog_path": str(path), **catalog}
    if not satisfied:
        result["error"] = {"code": "incomplete_catalog",
                           "message": f"已取得 {catalog['collected_count']} 条，频道总数为 {catalog.get('expected_count') if catalog.get('expected_count') is not None else '未知'}；不能将这份列表称为全部或足量。"}
    return result


def load_catalog(path: str) -> dict:
    file = Path(path).expanduser()
    with file.open("rb") as handle:
        data = handle.read(MAX_CATALOG_BYTES + 1)
    if len(data) > MAX_CATALOG_BYTES:
        raise SkillError("invalid_catalog", "节目列表文件过大。")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise SkillError("invalid_catalog", "节目列表文件不是有效 JSON。") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("episodes"), list):
        raise SkillError("invalid_catalog", "节目列表格式不匹配，请重新列出频道。")
    return value


def select_episode(catalog: dict, number: int | None, eid: str | None, allow_unavailable: bool = False) -> dict:
    for episode in catalog["episodes"]:
        if not isinstance(episode, dict):
            continue
        if (number is not None and episode.get("number") == number) or (eid is not None and eid in {episode.get("id"), episode_identity(episode)}):
            if not allow_unavailable and (episode.get("access") == "paid" or episode.get("is_private_media") is True):
                raise SkillError("paid_content", "该单集为付费或受限内容，不下载其完整音频。")
            if not allow_unavailable and not episode.get("audio_url"):
                raise SkillError("no_audio", "该单集没有公开音频附件地址，不能下载；可打开原文查看。")
            if episode.get("audio_url"):
                safe_url(episode["audio_url"])
            return episode
    raise SkillError("not_found", "所选单集不在这份列表中。请使用用户看到的那份列表，不要刷新后沿用旧编号。")


def safe_filename(value: str, byte_limit: int = 165) -> str:
    value = clean_text(value, 2000)
    value = re.sub(r'[<>:"/\\|?*]', "_", value).strip(" .")
    if value in {"", ".", ".."}:
        value = "未命名单集"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I):
        value = "_" + value
    while len(value.encode("utf-8")) > byte_limit:
        value = value[:-1]
    return value.rstrip(" .") or "音频"


def detect_audio_extension(prefix: bytes, mime: str, url: str) -> tuple[str, bool]:
    probe = prefix.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    if probe.startswith((b"<", b"{", b"[")) or mime.startswith("text/") or mime in {"application/json", "application/xml", "application/rss+xml", "text/html"}:
        raise SkillError("not_audio", "服务器返回了文本、JSON 或网页，不会将其保存成音频。")
    if prefix.startswith(b"ID3"):
        return ".mp3", True
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
        return ".wav", True
    if prefix.startswith(b"fLaC"):
        return ".flac", True
    if prefix.startswith(b"OggS"):
        return (".opus" if mime == "audio/opus" or Path(parse.urlsplit(url).path).suffix.lower() == ".opus" else ".ogg"), True
    if len(prefix) > 12 and prefix[4:8] == b"ftyp":
        return ".m4a", True
    if prefix.startswith(b"\x1aE\xdf\xa3"):
        return ".webm", True
    if len(prefix) >= 2 and prefix[0] == 0xFF:
        if prefix[1] & 0xF6 == 0xF0:
            return ".aac", True
        if prefix[1] & 0xE0 == 0xE0 and prefix[1] & 0x06:
            return ".mp3", True
    if mime in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[mime], False
    extension = Path(parse.urlsplit(url).path).suffix.lower()
    if extension in AUDIO_EXTENSIONS and mime in {"", "application/octet-stream", "binary/octet-stream"}:
        return extension, False
    raise SkillError("unknown_audio", "无法确认返回文件为受支持的音频格式，已停止保存。")


def publish_without_overwrite(temporary: Path, target: Path) -> None:
    """Publish a completed download exclusively; never clobber an existing file."""
    try:
        os.link(temporary, target)
    except FileExistsError as exc:
        raise SkillError("file_exists", f"文件已存在，未覆盖：{target}", {"path": str(target)}) from exc
    except OSError as exc:
        if exc.errno not in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}:
            raise
        created = False
        try:
            with target.open("xb") as dest:
                created = True
                with temporary.open("rb") as src:
                    shutil.copyfileobj(src, dest, 256 * 1024)
                dest.flush()
                os.fsync(dest.fileno())
        except FileExistsError as err:
            raise SkillError("file_exists", f"文件已存在，未覆盖：{target}", {"path": str(target)}) from err
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise


def download_episode(args, episode_override: dict | None = None) -> dict:
    episode = episode_override
    if episode is None:
        catalog = load_catalog(args.catalog)
        episode = select_episode(catalog, args.number, args.id)
    if episode.get("access") == "paid" or episode.get("is_private_media") is True:
        raise SkillError("paid_content", "该单集为付费或受限内容，不下载其完整音频。")
    url = episode["audio_url"]
    directory = resolve_output_dir(getattr(args, "out", None))
    directory.mkdir(parents=True, exist_ok=True)
    maximum = args.max_mb * 1024 * 1024
    started = time.monotonic()
    temporary = None
    try:
        with open_remote(url, args.timeout, "audio/*, application/octet-stream;q=0.8, */*;q=0.1") as response:
            if response.status != 200:
                raise SkillError("http_status", f"音频请求返回 HTTP {response.status}；未请求分段，不能将分段响应当作完整文件。")
            if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise SkillError("content_encoding", "音频响应被压缩，无法可靠校验文件长度。")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            length_header = response.headers.get("Content-Length", "")
            expected = int(length_header) if length_header.isdigit() else None
            if expected is not None and expected > maximum:
                raise SkillError("too_large", f"音频超过 {args.max_mb} MiB 上限；确认磁盘空间后再调整 --max-mb。")
            prefix = response.read(4096)
            if not prefix:
                raise SkillError("empty_audio", "音频响应为空。")
            extension, recognized = detect_audio_extension(prefix, mime, response.geturl())
            date = parse_date(episode.get("published_at"))
            date_string = date.date().isoformat() if date else "日期未知"
            stem = safe_filename(args.name if args.name else f"{date_string} - {episode.get('title', '音频')}")
            target = directory / f"{stem}{extension}"
            if target.exists() or target.is_symlink():
                raise SkillError("file_exists", f"文件已存在，未覆盖：{target}；明确选择新文件名后用 --name。", {"path": str(target)})
            total = 0
            digest = hashlib.sha256()
            last_progress = started
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".xiaoyuzhou-", suffix=".part", dir=directory, delete=False) as output:
                temporary = Path(output.name)
                chunk = prefix
                while chunk:
                    total += len(chunk)
                    if total > maximum:
                        raise SkillError("too_large", f"下载超过 {args.max_mb} MiB 上限，已停止。")
                    if time.monotonic() - started > args.deadline:
                        raise SkillError("deadline", "下载超过总时长限制；未生成完整文件。")
                    output.write(chunk)
                    digest.update(chunk)
                    if time.monotonic() - last_progress >= 3:
                        suffix = f" / {expected / 1024 / 1024:.1f} MiB" if expected else ""
                        print(f"已下载 {total / 1024 / 1024:.1f} MiB{suffix}", file=sys.stderr)
                        last_progress = time.monotonic()
                    chunk = response.read(256 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if expected is not None and total != expected:
                raise SkillError("incomplete_download", f"文件长度不完整：预期 {expected} 字节，实际 {total} 字节。")
            publish_without_overwrite(temporary, target)
            return {
                "ok": True, "action": "download", "title": episode.get("title"),
                "path": str(target), "bytes": total, "sha256": digest.hexdigest(),
                "audio_url": url, "page_url": episode.get("page_url"),
                "format_header_recognized": recognized,
                "length_verified": expected is not None,
                "playback_verified": False,
            }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def number_selection(value: str, maximum: int = 10000) -> list[int]:
    result = []
    for entry in value.split(","):
        entry = entry.strip()
        match = re.fullmatch(r"([1-9]\d*)(?:-([1-9]\d*))?", entry)
        if not match:
            raise SkillError("invalid_selection", "编号用逗号或连续范围，例如 1,3,5-8。")
        start, end = int(match[1]), int(match[2] or match[1])
        if end < start or end - start + 1 > maximum:
            raise SkillError("invalid_selection", "编号范围倒序或超过列表规模。")
        for number in range(start, end + 1):
            if number not in result:
                result.append(number)
        if len(result) > maximum:
            raise SkillError("invalid_selection", "所选数量超过列表规模。")
    return result


def episode_label_selection(catalog: dict, value: str) -> list[dict]:
    selected = []
    for label in value.split(","):
        label = label.strip()
        wanted = re.fullmatch(r"(?:EP|E)?0*(\d+)(#)?", label, re.I)
        if not wanted:
            raise SkillError("invalid_selection", "节目期号使用 EP88、141 或 3#；列表编号使用 --numbers。")
        matches = []
        for episode in catalog["episodes"]:
            found = re.match(r"^\s*(?:EP|E)?0*(\d+)(#)?(?=\D|$)", episode.get("title", ""), re.I)
            if found and int(found[1]) == int(wanted[1]) and bool(found[2]) == bool(wanted[2]):
                matches.append(episode)
        if len(matches) != 1:
            code = "ambiguous_episode" if matches else "not_found"
            raise SkillError(code, f"期号 {label} 未能唯一匹配；请提供标题、单集链接或明确的列表编号。")
        if matches[0] not in selected:
            selected.append(matches[0])
    return selected


def download_targets(args) -> list[dict]:
    urls = getattr(args, "urls", [])
    if urls:
        if args.catalog or any(getattr(args, key, None) for key in ("number", "id", "numbers", "ids", "episodes", "all")):
            raise SkillError("invalid_selection", "单集链接下载不能与列表选择混用。")
        targets, seen = [], set()
        for url in urls:
            kind, identity, canonical = parse_link(url)
            if kind != "episode":
                raise SkillError("invalid_episode", "频道请先 list，再明确选择要下载的节目。")
            if identity not in seen:
                seen.add(identity)
                targets.append({"episode_id": identity, "page_url": canonical,
                                "title": identity, "number": len(targets) + 1})
        return targets
    if not args.catalog:
        raise SkillError("invalid_selection", "请提供单集链接，或 --catalog 加编号、ID、期号。")
    catalog = load_catalog(args.catalog)
    if getattr(args, "all", False):
        selected = list(catalog["episodes"])
    elif getattr(args, "numbers", None):
        selected = [select_episode(catalog, n, None, True)
                    for n in number_selection(args.numbers, max(len(catalog["episodes"]), 1))]
    elif getattr(args, "ids", None):
        identities = list(dict.fromkeys(item.strip() for item in args.ids.split(",")))
        if not all(identities):
            raise SkillError("invalid_selection", "单集 ID 不能为空。")
        selected = [select_episode(catalog, None, identity, True) for identity in identities]
    elif getattr(args, "episodes", None):
        selected = episode_label_selection(catalog, args.episodes)
    elif args.number is not None or args.id is not None:
        selected = [select_episode(catalog, args.number, args.id, True)]
    else:
        raise SkillError("invalid_selection", "请明确选择编号、ID、节目期号或 --all。")
    targets, seen = [], set()
    for episode in selected:
        identity = episode_identity(episode)
        if not identity:
            raise SkillError("invalid_catalog", "列表中的单集身份无法核对，请重新列出。")
        if identity not in seen:
            seen.add(identity)
            targets.append(dict(episode))
    return targets


def download_command(args) -> dict:
    targets = download_targets(args)
    if not targets:
        raise SkillError("empty_selection", "没有选中任何节目。")
    if len(targets) > 1 and args.name:
        raise SkillError("invalid_selection", "--name 仅用于一集；多集不能使用同一个新文件名。")
    directory = resolve_output_dir(args.out)
    local_args = copy.copy(args)
    local_args.out = str(directory)
    results, stop_reason = [], None
    for original in targets:
        base = {"number": original["number"], "title": original["title"],
                "page_url": original["page_url"]}
        if stop_reason:
            results.append({**base, "ok": False, "status": "not_attempted", "reason": stop_reason})
            continue
        try:
            fresh, _ = resolve_episode(original["page_url"], args.timeout)
            fresh.update(number=original["number"])
            if original.get("id"):
                fresh["id"] = original["id"]
            base.update(title=fresh["title"])
            if fresh["access"] == "paid":
                results.append({**base, "ok": False, "status": "skipped_paid",
                                "error": {"code": "paid_content", "message": "付费节目已跳过，未请求音频。"}})
                continue
            if fresh["access"] != "public" or not fresh.get("audio_url"):
                raise SkillError("public_audio_unverified", "无法核对免费公开音频，未请求音频。")
            if len(targets) > 1:
                print(f"开始下载 {fresh['number']}：{fresh['title']}", file=sys.stderr)
            result = download_episode(local_args, fresh)
            results.append({**result, "number": original["number"], "status": "downloaded"})
        except (SkillError, OSError, ValueError, HTTPException) as exc:
            details = source_error(exc)
            exists = isinstance(exc, SkillError) and exc.code == "file_exists"
            result = {**base, "ok": False, "status": "exists" if exists else "failed", "error": details}
            if exists and exc.details.get("path"):
                result["path"] = exc.details["path"]
            results.append(result)
            if stops_batch(exc):
                stop_reason = details
        except KeyboardInterrupt:
            stop_reason = {"code": "cancelled", "message": "用户中止，剩余节目未执行。"}
            results.append({**base, "ok": False, "status": "failed", "error": stop_reason})
    if len(targets) == 1:
        return {"action": "download", **results[0]}
    counts = {status: sum(r["status"] == status for r in results)
              for status in ("downloaded", "skipped_paid", "exists", "failed", "not_attempted")}
    return {"ok": counts["failed"] == 0 and counts["not_attempted"] == 0,
            "action": "download_batch", "requested_count": len(targets),
            "completed_count": counts["downloaded"], "skipped_paid_count": counts["skipped_paid"],
            "existing_count": counts["exists"], "failed_count": counts["failed"],
            "not_attempted_count": counts["not_attempted"], "stop_reason": stop_reason,
            "output_dir": str(directory), "results": results}


def doctor() -> dict:
    return {"ok": True, "version": VERSION, "python": sys.version.split()[0],
            "default_rsshub": DEFAULT_RSSHUB, "network_tested": False,
            "requirements": "Python 3.10+，联网权限，目标目录写入权限；无第三方 Python 依赖，无模型 API Key。"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="本地条件检查，不发起网络请求")
    config = sub.add_parser("config", help="查看或记住已由用户确认的下载目录")
    config_sub = config.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("show", help="查看目录和是否需要首次确认")
    setting = config_sub.add_parser("set", help="用户确认后持久化下载目录")
    directory = setting.add_mutually_exclusive_group(required=True)
    directory.add_argument("--default", action="store_true", help="使用系统 Downloads/小宇宙")
    directory.add_argument("--download-dir", help="用户已确认的下载目录")
    listing = sub.add_parser("list", help="列出频道或单集，不下载音频")
    listing.add_argument("channel", help="小宇宙频道或单集完整链接")
    count = listing.add_mutually_exclusive_group()
    count.add_argument("--limit", "--count", type=positive_int, default=None, help="默认最近15条；指定数量不限制为100条")
    count.add_argument("--all", action="store_true", help="获取全部，并核对频道总数")
    listing.add_argument("--rsshub", default=os.environ.get("RSSHUB_BASE_URL", DEFAULT_RSSHUB))
    listing.add_argument("--feed", help="用户给出的发布者 RSS；必须核对为该频道")
    listing.add_argument("--catalog", help="指定新快照位置，绝不覆盖；默认用户配置目录下 catalogs/")
    listing.add_argument("--timeout", type=positive_int, default=30)
    download = sub.add_parser("download", help="下载明确选择的一集或多集；自动跳过付费")
    download.add_argument("urls", nargs="*", help="一个或多个单集链接")
    download.add_argument("--catalog", help="沿用用户看到的列表快照")
    selected = download.add_mutually_exclusive_group()
    selected.add_argument("--number", type=positive_int, help="列表编号")
    selected.add_argument("--numbers", help="列表编号，如 1,3,5-8")
    selected.add_argument("--id", help="快照稳定 ID 或单集 ID")
    selected.add_argument("--ids", help="逗号分隔的多个稳定 ID")
    selected.add_argument("--episodes", help="标题中的节目期号，如 EP88,EP87 或 141")
    selected.add_argument("--all", action="store_true", help="下载该快照中的全部选择；不是自动扩大到整个频道")
    download.add_argument("--out", help="仅本次覆盖下载目录；否则沿用用户确认的配置")
    download.add_argument("--name", help="用户明确的新文件名，不含扩展名，仅用于单集")
    download.add_argument("--timeout", type=positive_int, default=30)
    download.add_argument("--deadline", type=positive_int, default=1800)
    download.add_argument("--max-mb", type=positive_int, default=2048)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "config":
            result = configuration() if args.config_action == "show" else configure_directory(
                str(default_download_dir()) if args.default else args.download_dir)
        elif args.command == "list":
            result = list_episodes(args)
        else:
            result = download_command(args)
        emit(result)
        return 0 if result["ok"] else 1
    except SkillError as exc:
        code, message = exc.code, str(exc)
    except error.HTTPError as exc:
        code = f"http_{exc.code}"
        hints = {401: "实例要求鉴权；默认 Key 留空不保证所有实例无需鉴权。", 403: "访问被拒绝，可能是实例策略或来源限制；不要绕过。", 404: "路径、频道或资源不存在。", 429: "请求过于频繁；请稍后手动重试。", 502: "实例无法正常访问上游。", 503: "实例暂不可用。"}
        message = f"HTTP {exc.code}：{hints.get(exc.code, '远端请求失败。')}"
    except (error.URLError, socket.timeout, TimeoutError, ssl.SSLError, HTTPException) as exc:
        code, message = "network_error", f"网络、DNS、TLS 或代理错误：{exc}。不能据此判断频道没有节目。"
    except (OSError, ValueError, TypeError) as exc:
        code, message = "io_error", f"本地文件、参数或传输错误：{exc}"
    except KeyboardInterrupt:
        code, message = "cancelled", "用户中止，未报告下载成功。"
    emit({"ok": False, "error": {"code": code, "message": message}})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
