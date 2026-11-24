"""coco-ww 的在线更新后端。

coco-ww 不走 PyAppify 启动器, 但 ok 框架的「关于 → 应用更新」卡片只认 `pyappify`
模块的这几个函数 (get_version_list / update_to_version / calculate_update_notes /
is_greater_version), 所以在这里用 GitHub Release 实现它们, 界面代码不用动。

更新方式是增量的:
- 每个 Release 带一个 update-manifest.json 附件, 列出那个提交里每个 git 跟踪文件的 SHA256;
- 版本列表和这些清单, 发版 Action 也会放一份到 update-index 分支 (tools/release_index.py)。先从
  raw / jsDelivr 读这个分支, 国内不用连 GitHub 接口; 都读不到才退回 GitHub 接口 + Release 附件;
- 点更新时拿清单和本地文件逐个比哈希, 只按提交号下载变了的文件 (raw / jsDelivr 哪个连得上用哪个),
  先放进 cache/update/<版本>/files, 全部校验通过才动手;
- 程序退出后由 apply_update.py 换文件 (失败整批回滚), 再按原命令行重启。

不碰的东西:
- python/ 运行环境: 不在清单里。requirements.txt 变了就说明运行环境要换, 只能下整包;
- configs/: 用户自己的设置和队伍代码。只补这一版新加的文件, 不覆盖、不删除;
- git 开发目录: 有 .git 的目录只给看版本, 不让更新, 免得冲掉没提交的改动。
"""

import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request

from src.net_ssl import open_url

# 框架在 ok/__init__.py 里读这些名字; app_version 有值会覆盖 config 里的版本号, 所以保持 None。
# 更新后重启时, 「更新成功 a -> b」和更新说明走 PYAPPIFY_APP_STARTING_VERSION /
# PYAPPIFY_APP_VERSION / PYAPPIFY_UPDATE_NOTE 环境变量 (apply_update.py 设), 框架自己会读。
app_version = None
app_starting_version = None
app_profile = None
app_ref = None
update_note = None
pyappify_version = None
pyappify_upgradeable = False
pyappify_executable = None
logger = None

REPO = "wk321425/coco-ww"
API_RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=50"
MANIFEST_ASSET = "update-manifest.json"
MANIFEST_FORMAT = 1
# 发版 Action 推的版本列表 (tools/release_index.py)。同一份内容的几个入口, 按顺序试:
# raw 最新 (缓存 5 分钟), 国内常连不上; jsDelivr 国内能连, 分支内容缓存 12 小时, 发版后 Action 会刷新。
# 清单按 releases.json 里写的提交号取, 不受分支缓存影响。
INDEX_BRANCH = "update-index"
INDEX_FILE = "releases.json"
INDEX_FORMAT = 1
INDEX_SOURCES = (
    "https://raw.githubusercontent.com/{repo}/{ref}/{path}",
    "https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}",
    "https://fastly.jsdelivr.net/gh/{repo}@{ref}/{path}",
)
# 按顺序试; 每个文件都按清单里的 SHA256 校验, 所以备用源只管能不能下到
FILE_SOURCES = (
    "https://raw.githubusercontent.com/{repo}/{commit}/{path}",
    "https://cdn.jsdelivr.net/gh/{repo}@{commit}/{path}",
    "https://fastly.jsdelivr.net/gh/{repo}@{commit}/{path}",
)
USER_AGENT = "coco-ww-updater"
TIMEOUT = 20
META_TIMEOUT = 8  # 版本列表和清单都不大, 连不上的入口早点放弃
SOURCE_PENALTY = 600  # 连不上的源 10 分钟内排到最后, 免得每个文件都先等它超时
DOWNLOAD_WORKERS = 6
# 连不上、超时、证书问题、连接中途断开
NETWORK_ERRORS = (OSError, HTTPException)

APP_ROOT = Path(__file__).resolve().parent.parent
STAGING_ROOT = APP_ROOT / "cache" / "update"
HELPER_SCRIPT = Path(__file__).resolve().parent / "apply_update.py"

RUNTIME_FILES = ("requirements.txt",)
USER_DATA_PREFIX = "configs/"
FORBIDDEN_PREFIXES = ("python/", ".git/", "cache/", "logs/")

# get_version_list 拉到的全部 Release (含测试版), 按 tag 存, 给 update_to_version 用
_releases = {}
# 最近连不上的源 (host -> time.monotonic()), 几个下载线程共用
_failed_hosts = {}
_failed_lock = threading.Lock()


def hide_pyappify():
    return None


def kill_pyappify():
    return None


def _log():
    return logger or logging.getLogger("pyappify")


# --- 版本号 -------------------------------------------------------------------

def is_greater_version(left, right):
    from ok.util.version import compare_versions
    try:
        return compare_versions(str(left), str(right)) > 0
    except (TypeError, ValueError):
        return False


def _version_desc(left, right):
    if is_greater_version(left["version"], right["version"]):
        return -1
    if is_greater_version(right["version"], left["version"]):
        return 1
    return 0


def is_local_checkout():
    return (APP_ROOT / ".git").exists()


def installed_version():
    """磁盘上 config.py 写的版本号: 删旧文件要按它找旧清单。"""
    try:
        text = (APP_ROOT / "config.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else ""


# --- 网络 ---------------------------------------------------------------------

def _open(url, accept=None, timeout=TIMEOUT):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return open_url(Request(url, headers=headers), timeout=timeout)


def _get_json(url, accept=None):
    try:
        with _open(url, accept) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code in (403, 429) and "api.github.com" in url:
            raise RuntimeError("GitHub 接口访问太频繁被限流了，过一小时再试") from error
        raise RuntimeError(f"访问 {url} 失败: HTTP {error.code}") from error
    except NETWORK_ERRORS as error:
        raise RuntimeError(f"连不上 GitHub: {_reason(error)}") from error


def _download_bytes(url):
    with _open(url) as response:
        return response.read()


def _host(url):
    return urlsplit(url).netloc


def _reason(error):
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, URLError):
        return str(error.reason)
    return str(error) or type(error).__name__


def _ordered(urls):
    """最近连不上的源排到最后, 其余保持原来的顺序。"""
    now = time.monotonic()
    with _failed_lock:
        down = {host for host, at in _failed_hosts.items() if now - at < SOURCE_PENALTY}
    return sorted(urls, key=lambda url: _host(url) in down)


def _note_result(url, error=None):
    """记下这个源连不连得上。HTTP 错误说明服务器是通的 (只是没这个文件), 不算连不上。"""
    with _failed_lock:
        if error is None or isinstance(error, HTTPError):
            _failed_hosts.pop(_host(url), None)
        else:
            _failed_hosts[_host(url)] = time.monotonic()


def _fetch_first(urls, parse, accept=None, timeout=META_TIMEOUT):
    """几个入口挨个试, 返回 (第一个取到且 parse 通过的结果, 它的 url)。

    parse 抛 ValueError / RuntimeError 算这个入口的内容不对, 换下一个。都不行抛 RuntimeError, 带上每个入口的错。
    """
    errors = []
    for url in _ordered(urls):
        try:
            with _open(url, accept, timeout) as response:
                raw = response.read()
        except NETWORK_ERRORS as error:
            _note_result(url, error)
            if isinstance(error, HTTPError):
                error.close()
            errors.append(f"{_host(url)} {_reason(error)}")
            continue
        _note_result(url)
        try:
            return parse(json.loads(raw.decode("utf-8"))), url
        except (ValueError, RuntimeError) as error:
            errors.append(f"{_host(url)} 内容不对: {error}")
    raise RuntimeError("；".join(errors) or "没有可用的入口")


# --- 更新说明 -----------------------------------------------------------------

_SKIP_SECTIONS = ("下载", "安装", "启动", "注意")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _plain(text):
    text = _LINK.sub(r"\1", text)
    return text.replace("**", "").replace("`", "").strip()


def release_notes(body):
    """Release 正文里「下载和启动」「注意」以外各节的列表项; 没有列表项就取第一段。"""
    notes = []
    first_paragraph = ""
    skip = False
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            skip = any(word in title for word in _SKIP_SECTIONS)
            continue
        if not line or skip:
            continue
        if line[:2] in ("* ", "- ", "+ "):
            notes.append(_plain(line[2:]))
        elif not first_paragraph and not line.startswith(("SHA256", ">", "|")):
            first_paragraph = _plain(line)
    if not notes and first_paragraph:
        notes.append(first_paragraph)
    return notes


def calculate_update_notes(versions, current_version, target_version, language=None):
    by_version = {str(item.get("version")): item for item in versions}
    if is_greater_version(target_version, current_version):
        picked = [item for item in versions
                  if is_greater_version(item["version"], current_version)
                  and not is_greater_version(item["version"], target_version)]
    else:
        picked = [by_version[target_version]] if target_version in by_version else []
    picked.sort(key=functools.cmp_to_key(_version_desc))
    tag_each = len(picked) > 1
    notes = []
    for item in picked:
        for note in item.get("update_note") or []:
            notes.append(f"[{item['version']}] {note}" if tag_each else note)
    return notes


# --- 版本列表 -----------------------------------------------------------------

def _manifest_url(release):
    for asset in release.get("assets") or []:
        if asset.get("name") == MANIFEST_ASSET:
            return asset.get("browser_download_url")
    return None


_INDEX_MANIFEST = re.compile(r"manifests/[A-Za-z0-9][A-Za-z0-9._-]*\.json")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _is_version(tag):
    from ok.util.version import compare_versions
    try:
        compare_versions(tag, tag)
    except (TypeError, ValueError):
        return False
    return True


def _entry(tag, body, prerelease, published_at, manifest_urls):
    return {
        "version": tag,
        "kind": "tag",
        "update_note": release_notes(body),
        "prerelease": bool(prerelease),
        "published_at": published_at,
        "manifest_urls": manifest_urls,
    }


def _index_releases(index):
    """update-index 分支的 releases.json -> 版本表。格式不对抛 ValueError, 换下一个入口。"""
    if (not isinstance(index, dict) or index.get("format") != INDEX_FORMAT
            or not isinstance(index.get("releases"), list)):
        raise ValueError("版本列表格式不对")
    releases = {}
    for item in index["releases"]:
        if not isinstance(item, dict):
            continue
        tag, path, ref = item.get("version"), item.get("manifest"), item.get("manifest_ref")
        if (not isinstance(tag, str) or not _is_version(tag) or not isinstance(path, str)
                or not _INDEX_MANIFEST.fullmatch(path) or not _COMMIT.fullmatch(str(ref))):
            continue
        urls = [template.format(repo=REPO, ref=ref, path=path) for template in INDEX_SOURCES]
        if isinstance(item.get("manifest_url"), str):
            urls.append(item["manifest_url"])  # Release 附件, 最后才试
        releases[tag] = _entry(tag, item.get("body"), item.get("prerelease"), item.get("published_at"), urls)
    if not releases:
        raise ValueError("版本列表是空的")
    return releases


def _fetch_index():
    urls = [template.format(repo=REPO, ref=INDEX_BRANCH, path=INDEX_FILE) for template in INDEX_SOURCES]
    releases, url = _fetch_first(urls, _index_releases)
    _log().info("coco-ww update: version list from %s", url)
    return releases


def _fetch_releases():
    data = _get_json(API_RELEASES, accept="application/vnd.github+json")
    if not isinstance(data, list):
        raise RuntimeError("GitHub 返回的 Release 列表格式不对")
    releases = {}
    for release in data:
        tag = release.get("tag_name")
        manifest_url = _manifest_url(release)
        # 没清单的老版本 (v1.0.1 及更早) 只能手动下整包, 不列出来
        if release.get("draft") or not tag or not manifest_url or not _is_version(tag):
            continue
        releases[tag] = _entry(tag, release.get("body"), release.get("prerelease"),
                               release.get("published_at"), [manifest_url])
    return releases


def _fetch_versions():
    try:
        return _fetch_index()
    except RuntimeError as index_error:
        _log().warning("coco-ww update: update index unavailable (%s), trying GitHub API", index_error)
        try:
            return _fetch_releases()
        except RuntimeError as api_error:
            raise RuntimeError(
                "取不到版本列表，jsDelivr 和 GitHub 都连不上，过几分钟再试，还不行就挂代理。"
                f"（{index_error}；{api_error}）") from api_error


def get_version_list(release_only=True, exit_event=None, **_kwargs):
    global _releases
    _releases = _fetch_versions()
    versions = [item for item in _releases.values() if not (release_only and item["prerelease"])]
    versions.sort(key=functools.cmp_to_key(_version_desc))
    _log().info("coco-ww update: %d versions %s", len(versions), [v["version"] for v in versions])
    return versions


# --- 更新计划 -----------------------------------------------------------------

def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_sha(path):
    try:
        return _sha256_file(path) if path.is_file() else None
    except OSError:
        return None


def _check_path(rel):
    pure = PurePosixPath(rel)
    if (not rel or rel != str(pure) or pure.is_absolute() or ".." in pure.parts
            or ":" in rel or "\\" in rel or rel.startswith(FORBIDDEN_PREFIXES)):
        raise RuntimeError(f"更新清单里有不安全的路径: {rel!r}")


def _check_manifest(manifest, version):
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{version} 的更新清单格式不对")
    files = manifest.get("files")
    if (manifest.get("format") != MANIFEST_FORMAT or manifest.get("version") != version
            or not _COMMIT.fullmatch(str(manifest.get("commit", ""))) or not isinstance(files, dict)):
        raise RuntimeError(f"{version} 的更新清单格式不对")
    for rel, info in files.items():
        _check_path(rel)
        if not isinstance(info, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(info.get("sha256", ""))):
            raise RuntimeError(f"更新清单里 {rel} 的哈希不对")
    return manifest


def _load_manifest(entry):
    version = entry["version"]
    urls = entry.get("manifest_urls") or [entry["manifest_url"]]
    try:
        manifest, _url = _fetch_first(urls, lambda data: _check_manifest(data, version))
    except RuntimeError as error:
        raise RuntimeError(f"取不到 {version} 的更新清单: {error}") from error
    return manifest


def plan_update(target, old=None, root=None):
    """比本地文件和清单, 返回 (要下载的路径, 要删的路径)。

    old 是本地这个版本的清单 (拿不到就是 None): 用来认出新版本删掉了哪些文件,
    以及 configs/ 里哪些是这一版新加的。只删和旧清单哈希一致 (用户没改过) 的文件。
    """
    root = Path(root or APP_ROOT)
    target_files = target["files"]
    old_files = (old or {}).get("files") or {}
    for rel in RUNTIME_FILES:
        want = (target_files.get(rel) or {}).get("sha256")
        if want and _local_sha(root / rel) != want:
            raise RuntimeError(
                f"{target['version']} 换了运行环境（{rel} 变了），在线更新补不了，"
                "请到 Release 页下载完整包")

    download = []
    for rel, info in target_files.items():
        local = root / rel
        if rel.startswith(USER_DATA_PREFIX):
            if rel in old_files or local.exists():
                continue
        elif _local_sha(local) == info["sha256"]:
            continue
        download.append(rel)

    delete = []
    for rel, info in old_files.items():
        if rel in target_files or rel.startswith(USER_DATA_PREFIX):
            continue
        if _local_sha(root / rel) == info.get("sha256"):
            delete.append(rel)
    return sorted(download), sorted(delete)


def _download_file(commit, rel, sha256, dest, exit_event=None):
    if _local_sha(dest) == sha256:
        return  # 上次下到一半留下的, 直接用
    urls = [template.format(repo=REPO, commit=commit, path=quote(rel, safe="/")) for template in FILE_SOURCES]
    last_error = None
    for _round in range(2):  # 每个源先各试一次, 都不行再来一轮
        for url in _ordered(urls):
            if exit_event is not None and exit_event.is_set():
                raise RuntimeError("已取消")
            try:
                data = _download_bytes(url)
            except NETWORK_ERRORS as error:
                _note_result(url, error)
                if isinstance(error, HTTPError):
                    error.close()
                last_error = error
                continue
            _note_result(url)
            if hashlib.sha256(data).hexdigest() != sha256:
                last_error = RuntimeError(f"{url} 下到的内容校验不过")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp = dest.with_name(dest.name + ".part")
            temp.write_bytes(data)
            os.replace(temp, dest)
            return
    raise RuntimeError(f"下载 {rel} 失败: {last_error}")


def _clean_other_staging(keep):
    if not STAGING_ROOT.is_dir():
        return
    for child in STAGING_ROOT.iterdir():
        if child.is_dir() and child.name != keep:
            shutil.rmtree(child, ignore_errors=True)


def _restart_command():
    executable = Path(sys.executable)
    args = list(getattr(sys, "orig_argv", [sys.executable] + sys.argv))[1:]
    return {"cmd": [str(executable)] + args, "cwd": os.getcwd()}


def _helper_python():
    """用 pythonw 跑助手, 调试启动 (python.exe) 时也不弹黑框。"""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw if pythonw.is_file() else sys.executable)


def _spawn_helper(plan_path, helper):
    cmd = [_helper_python(), "-X", "utf8", "-I", str(helper), str(plan_path)]
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(cmd, cwd=str(APP_ROOT), close_fds=True,
                         creationflags=flags | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(cmd, cwd=str(APP_ROOT), close_fds=True, creationflags=flags)


def prepare_update(version, exit_event=None, restart=True):
    """下载并校验 version 要用的文件, 写好 plan.json。返回 (plan, plan 路径)。"""
    if is_local_checkout():
        raise RuntimeError("这里是 git 开发目录，不走在线更新，请用 git pull")
    entry = _releases.get(version)
    if entry is None:
        get_version_list(release_only=False, exit_event=exit_event)
        entry = _releases.get(version)
    if entry is None:
        raise RuntimeError(f"Release 里没有 {version} 的更新清单")

    current = installed_version()
    target = _load_manifest(entry)
    old = None
    if current in _releases:
        try:
            old = _load_manifest(_releases[current])
        except RuntimeError as error:
            _log().warning("coco-ww update: old manifest %s unavailable: %s", current, error)
    else:
        _log().warning("coco-ww update: no manifest for installed %s, stale files stay", current)

    download, delete = plan_update(target, old)
    _log().info("coco-ww update %s -> %s: download %d, delete %d", current, version, len(download), len(delete))
    if not download and not delete:
        raise RuntimeError(f"本地文件已经和 {version} 一样，不用更新")

    stage = STAGING_ROOT / version
    _clean_other_staging(version)
    files_dir = stage / "files"
    commit = target["commit"]
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(_download_file, commit, rel, target["files"][rel]["sha256"],
                               files_dir / rel, exit_event) for rel in download]
        for future in futures:
            future.result()

    # 用当前版本的助手: plan.json 是当前代码写的, 格式对得上; 拷出去跑, 换掉 pyappify/ 也不影响它
    helper = stage / "apply_update.py"
    shutil.copyfile(HELPER_SCRIPT, helper)
    plan = {
        "app_root": str(APP_ROOT),
        "pid": os.getpid(),
        "from": current,
        "to": version,
        "files_dir": str(files_dir),
        "backup_dir": str(stage / "backup"),
        "replace": download,
        "delete": delete,
        "notes": calculate_update_notes(list(_releases.values()), current, version),
        "restart": _restart_command() if restart else None,
    }
    plan_path = stage / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan, plan_path, helper


def update_to_version(version, exit_event=None, language=None, **_kwargs):
    _plan, plan_path, helper = prepare_update(version, exit_event=exit_event)
    _spawn_helper(plan_path, helper)
    # UpdateCard 看到这两个标记就让程序退出, 助手等进程退了再换文件
    return {"updated": True, "restart_required": True}
