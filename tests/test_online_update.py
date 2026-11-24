"""在线更新 (pyappify/__init__.py + pyappify/apply_update.py) 的离线测试。

起一个本地 HTTP 服务假扮 GitHub (Release 列表、更新清单、raw 文件) 和 jsDelivr 上的 update-index 分支
(用发版 Action 同一个 tools/release_index.py 生成), 在临时目录装一份「旧版」,
真走一遍: 拉版本列表 → 比哈希 → 下载校验 → 助手进程换文件/删文件 → 重启带更新说明。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_online_update.py
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyappify  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "release_index", Path(__file__).resolve().parent.parent / "tools" / "release_index.py")
release_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_index)

OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40
ENV_COMMIT = "c" * 40
INDEX_REF = "d" * 40  # update-index 分支上放清单的那次提交

OLD_TREE = {
    "config.py": 'version = "v1.0.0"\n',
    "requirements.txt": "a==1\n",
    "src/keep.py": "keep",
    "src/change.py": "old",
    "src/gone.py": "gone",
    "src/gone_modified.py": "gone2",
    "src/pkg/only.py": "x",
    "configs/a.json": "{}",
    "configs/user_deleted.json": "{}",
    "启动 coco-ww.bat": "old bat",
}
NEW_TREE = {
    "config.py": 'version = "v1.0.1"\n',
    "requirements.txt": "a==1\n",
    "src/keep.py": "keep",
    "src/change.py": "new",
    "src/added/new.py": "added",
    "configs/a.json": '{"new": 1}',
    "configs/user_deleted.json": "{}",
    "configs/custom_teams/New__Team__X/team.json": "{}",
    "启动 coco-ww.bat": "new bat",
}
ENV_TREE = dict(NEW_TREE, **{"config.py": 'version = "v1.1.0"\n', "requirements.txt": "a==2\n"})

V101_BODY = """刷 4C 声骸的两个修复。

## 下载和启动

1. 下载下面的 `coco-ww-v1.0.1.zip`。
* 这一行在下载节里, 不算

## 修了什么

* **Boss 没死就去捡声骸**：现在要再确认一次。
* 详见 [README](https://example.com/readme)

## ⚠️ 注意

* 免费开源

SHA256：`abc`
"""


def manifest(version, commit, tree):
    return {"format": 1, "version": version, "commit": commit,
            "files": {rel: {"sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "size": len(text.encode())} for rel, text in tree.items()}}


class FakeGitHub:
    def __init__(self):
        self.routes = {}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = unquote(self.path)
                owner.requests.append(path)
                body = owner.routes.get(path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add_release(self, releases, version, commit, tree, body="", prerelease=False):
        self.routes[f"/m/{version}.json"] = json.dumps(manifest(version, commit, tree)).encode()
        for rel, text in tree.items():
            self.routes[f"/raw/{commit}/{rel}"] = text.encode()
        releases.append({"tag_name": version, "draft": False, "prerelease": prerelease, "body": body,
                         "assets": [{"name": "update-manifest.json",
                                     "browser_download_url": f"{self.base}/m/{version}.json"}]})

    def publish_index(self, releases, ref=INDEX_REF):
        """像发版 Action 那样把清单和 releases.json 放到 update-index 分支, 挂在 /cdn/<分支或提交>/ 下。"""
        with tempfile.TemporaryDirectory() as branch:
            root = Path(branch)
            added = release_index.sync_manifests(
                releases, root, lambda tag, path: path.write_bytes(self.routes[f"/m/{tag}.json"]))
            index = release_index.build_index(releases, root, ref, "fake/repo")
            release_index.write_index(index, root)
            self.routes[f"/cdn/{pyappify.INDEX_BRANCH}/{release_index.INDEX_FILE}"] = (
                root / release_index.INDEX_FILE).read_bytes()
            for tag in added:
                path = release_index.manifest_path(tag)
                self.routes[f"/cdn/{ref}/{path}"] = (root / path).read_bytes()
        return index


def write_tree(root, tree):
    for rel, text in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # write_text 在 Windows 上会把 \n 写成 \r\n


def read_tree(root):
    result = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(("cache/", "logs/")):
            result[rel] = path.read_text(encoding="utf-8")
    return result


def exited_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class OnlineUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.github = FakeGitHub()
        releases = []
        cls.github.add_release(releases, "v1.0.0", OLD_COMMIT, OLD_TREE, body="* 第一版")
        cls.github.add_release(releases, "v1.0.1", NEW_COMMIT, NEW_TREE, body=V101_BODY)
        cls.github.add_release(releases, "v1.1.0", ENV_COMMIT, ENV_TREE, body="* 换了依赖", prerelease=True)
        releases.append({"tag_name": "v0.9.0", "draft": False, "prerelease": False, "body": "", "assets": []})
        cls.github.routes["/releases"] = json.dumps(releases).encode()
        cls.index = cls.github.publish_index(releases)

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="coco_update_test_"))
        self.app = self.temp / "app"
        write_tree(self.app, OLD_TREE)
        # 用户自己的改动: 改了一个设置、改了一个要被删的代码文件、删了一个设置
        (self.app / "configs/a.json").write_text('{"user": 1}', encoding="utf-8")
        (self.app / "src/gone_modified.py").write_text("my edit", encoding="utf-8")
        (self.app / "configs/user_deleted.json").unlink()
        (self.app / "src/pkg/__pycache__").mkdir()
        (self.app / "src/pkg/__pycache__/only.cpython-312.pyc").write_bytes(b"pyc")
        self.saved = {name: getattr(pyappify, name) for name in
                      ("API_RELEASES", "INDEX_SOURCES", "FILE_SOURCES", "APP_ROOT", "STAGING_ROOT", "_releases")}
        pyappify.API_RELEASES = f"{self.github.base}/releases"
        pyappify.INDEX_SOURCES = (f"{self.github.base}/cdn/{{ref}}/{{path}}",)
        pyappify.FILE_SOURCES = (f"{self.github.base}/missing/{{commit}}/{{path}}",
                                 f"{self.github.base}/raw/{{commit}}/{{path}}")
        pyappify.APP_ROOT = self.app
        pyappify.STAGING_ROOT = self.app / "cache" / "update"
        pyappify._releases = {}
        pyappify._failed_hosts.clear()

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(pyappify, name, value)
        pyappify._failed_hosts.clear()
        shutil.rmtree(self.temp, ignore_errors=True)

    def run_helper(self, helper, plan_path):
        return subprocess.run([sys.executable, "-X", "utf8", "-I", str(helper), str(plan_path)],
                              capture_output=True, text=True, timeout=60)

    def test_version_list_and_notes(self):
        versions = pyappify.get_version_list(release_only=True)
        self.assertEqual([v["version"] for v in versions], ["v1.0.1", "v1.0.0"])
        everything = pyappify.get_version_list(release_only=False)
        self.assertEqual([v["version"] for v in everything], ["v1.1.0", "v1.0.1", "v1.0.0"])
        self.assertEqual(everything[1]["update_note"],
                         ["Boss 没死就去捡声骸：现在要再确认一次。", "详见 README"])
        notes = pyappify.calculate_update_notes(everything, "v1.0.0", "v1.1.0")
        self.assertEqual(notes[0], "[v1.1.0] 换了依赖")
        self.assertEqual(len(notes), 3)
        self.assertEqual(pyappify.calculate_update_notes(everything, "v1.0.0", "v1.0.1"),
                         everything[1]["update_note"])
        self.assertTrue(pyappify.is_greater_version("v1.0.10", "v1.0.9"))
        self.assertFalse(pyappify.is_greater_version("", ""))

    def test_update_applies_and_restarts(self):
        plan, plan_path, helper = pyappify.prepare_update("v1.0.1", restart=False)
        self.assertEqual(plan["from"], "v1.0.0")
        self.assertEqual(plan["replace"], ["config.py", "configs/custom_teams/New__Team__X/team.json",
                                           "src/added/new.py", "src/change.py", "启动 coco-ww.bat"])
        self.assertEqual(plan["delete"], ["src/gone.py", "src/pkg/only.py"])

        env_file = self.temp / "restart_env.json"
        plan["pid"] = exited_pid()
        plan["restart"] = {"cmd": [sys.executable, "-c",
                                   "import os, json, sys; open(sys.argv[1], 'w', encoding='utf-8').write("
                                   "json.dumps({k: os.environ.get(k) for k in "
                                   "('PYAPPIFY_APP_STARTING_VERSION', 'PYAPPIFY_APP_VERSION', "
                                   "'PYAPPIFY_UPDATE_NOTE')}))", str(env_file)],
                           "cwd": str(self.temp)}
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        result = self.run_helper(helper, plan_path)
        self.assertEqual(result.returncode, 0, result.stderr + (self.app / "logs/update.log").read_text("utf-8"))

        expected = dict(NEW_TREE)
        expected["configs/a.json"] = '{"user": 1}'          # 用户设置不覆盖
        del expected["configs/user_deleted.json"]           # 用户删掉的设置不补回来
        expected["src/gone_modified.py"] = "my edit"        # 用户改过的旧文件不删
        self.assertEqual(read_tree(self.app), expected)
        self.assertFalse((self.app / "src/pkg").exists())   # 删空的目录 (只剩 __pycache__) 一起删
        self.assertFalse(Path(plan["files_dir"]).exists())
        self.assertEqual((Path(plan["backup_dir"]) / "src/change.py").read_text("utf-8"), "old")

        for _ in range(100):
            if env_file.exists() and env_file.stat().st_size:
                break
            time.sleep(0.1)
        env = json.loads(env_file.read_text(encoding="utf-8"))
        self.assertEqual(env["PYAPPIFY_APP_STARTING_VERSION"], "v1.0.0")
        self.assertEqual(env["PYAPPIFY_APP_VERSION"], "v1.0.1")
        self.assertEqual(json.loads(env["PYAPPIFY_UPDATE_NOTE"])[0], "Boss 没死就去捡声骸：现在要再确认一次。")

        with self.assertRaisesRegex(RuntimeError, "不用更新"):
            pyappify.prepare_update("v1.0.1", restart=False)

    def test_failed_apply_rolls_back(self):
        before = read_tree(self.app)
        plan, plan_path, helper = pyappify.prepare_update("v1.0.1", restart=False)
        plan["pid"] = exited_pid()
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        (Path(plan["files_dir"]) / plan["replace"][-1]).unlink()  # 最后一个文件换不上
        result = self.run_helper(helper, plan_path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(read_tree(self.app), before)
        self.assertIn("rolling back", (self.app / "logs/update.log").read_text("utf-8"))
        # 已经下好的文件放回暂存区, 下次重试不用重下
        self.assertEqual((Path(plan["files_dir"]) / "src/change.py").read_text("utf-8"), "new")
        self.github.requests.clear()
        pyappify.prepare_update("v1.0.1", restart=False)
        self.assertEqual([p for p in self.github.requests if p.startswith("/raw/")], ["/raw/" + NEW_COMMIT + "/启动 coco-ww.bat"])

    def test_runtime_change_needs_full_package(self):
        with self.assertRaisesRegex(RuntimeError, "运行环境"):
            pyappify.prepare_update("v1.1.0", restart=False)

    def test_git_checkout_refused(self):
        (self.app / ".git").mkdir()
        self.assertTrue(pyappify.is_local_checkout())
        with self.assertRaisesRegex(RuntimeError, "git"):
            pyappify.prepare_update("v1.0.1", restart=False)

    def test_bad_manifest_path_refused(self):
        bad = manifest("v1.0.1", NEW_COMMIT, {"../evil.py": "x"})
        self.github.routes["/m/bad.json"] = json.dumps(bad).encode()
        with self.assertRaisesRegex(RuntimeError, "不安全"):
            pyappify._load_manifest({"version": "v1.0.1", "manifest_url": f"{self.github.base}/m/bad.json"})

    # --- update-index 分支 (国内不用连 GitHub 接口) ---------------------------------------

    def test_index_used_without_github_api(self):
        self.github.requests.clear()
        pyappify.get_version_list(release_only=True)
        pyappify.prepare_update("v1.0.1", restart=False)
        self.assertNotIn("/releases", self.github.requests)
        self.assertFalse([p for p in self.github.requests if p.startswith("/m/")])
        self.assertIn(f"/cdn/{INDEX_REF}/manifests/v1.0.1.json", self.github.requests)
        # 旧版清单也从分支取: 靠它认出新版删掉了哪些文件
        self.assertIn(f"/cdn/{INDEX_REF}/manifests/v1.0.0.json", self.github.requests)

    def test_falls_back_to_github_api_without_index(self):
        pyappify.INDEX_SOURCES = (f"{self.github.base}/nothing/{{ref}}/{{path}}",)
        self.github.requests.clear()
        versions = pyappify.get_version_list(release_only=True)
        self.assertEqual([v["version"] for v in versions], ["v1.0.1", "v1.0.0"])
        plan, _plan_path, _helper = pyappify.prepare_update("v1.0.1", restart=False)
        self.assertEqual(plan["delete"], ["src/gone.py", "src/pkg/only.py"])
        self.assertIn("/releases", self.github.requests)
        self.assertIn("/m/v1.0.1.json", self.github.requests)

    def test_everything_down_says_so(self):
        pyappify.INDEX_SOURCES = (f"{self.github.base}/nothing/{{ref}}/{{path}}",)
        pyappify.API_RELEASES = f"{self.github.base}/nothing/releases"
        with self.assertRaisesRegex(RuntimeError, "jsDelivr 和 GitHub 都连不上"):
            pyappify.get_version_list()

    def test_garbage_from_one_source_skipped(self):
        # 比如被运营商劫持返回网页: 换下一个入口
        self.github.routes["/junk/update-index/releases.json"] = b"<html>blocked</html>"
        pyappify.INDEX_SOURCES = (f"{self.github.base}/junk/{{ref}}/{{path}}",
                                  f"{self.github.base}/cdn/{{ref}}/{{path}}")
        versions = pyappify.get_version_list(release_only=True)
        self.assertEqual([v["version"] for v in versions], ["v1.0.1", "v1.0.0"])

    def test_manifest_falls_back_to_release_asset(self):
        key = f"/cdn/{INDEX_REF}/manifests/v1.0.1.json"
        saved = self.github.routes.pop(key)
        try:
            self.github.requests.clear()
            pyappify.get_version_list()
            plan, _plan_path, _helper = pyappify.prepare_update("v1.0.1", restart=False)
        finally:
            self.github.routes[key] = saved
        self.assertEqual(len(plan["replace"]), 5)
        self.assertIn("/m/v1.0.1.json", self.github.requests)
        self.assertNotIn("/releases", self.github.requests)

    def test_unreachable_source_moves_to_back(self):
        dead = "http://dead.invalid"
        pyappify.INDEX_SOURCES = (dead + "/{ref}/{path}", f"{self.github.base}/cdn/{{ref}}/{{path}}")
        pyappify.FILE_SOURCES = (dead + "/{commit}/{path}", f"{self.github.base}/raw/{{commit}}/{{path}}")
        real_open = pyappify._open
        dead_hits = []

        def fake_open(url, *args, **kwargs):
            if url.startswith(dead):
                dead_hits.append(url)
                raise URLError(ConnectionRefusedError(10061, "refused"))
            return real_open(url, *args, **kwargs)

        with patch.object(pyappify, "_open", fake_open):
            pyappify.get_version_list()
            plan, _plan_path, _helper = pyappify.prepare_update("v1.0.1", restart=False)
        self.assertEqual(len(plan["replace"]), 5)
        # 拉版本列表那次就记下它连不上, 两份清单和 5 个文件都没再先等它
        self.assertEqual(len(dead_hits), 1, dead_hits)
        cdn = f"{self.github.base}/cdn/x"
        self.assertEqual(pyappify._ordered([dead + "/x", cdn]), [cdn, dead + "/x"])
        # 过了 10 分钟再给它机会
        pyappify._failed_hosts["dead.invalid"] = time.monotonic() - pyappify.SOURCE_PENALTY - 1
        self.assertEqual(pyappify._ordered([dead + "/x", cdn]), [dead + "/x", cdn])

    def test_unsafe_index_entries_dropped(self):
        good = dict(self.index["releases"][0])
        bad_path = dict(good, version="v2.0.0", manifest="manifests/../../evil.json")
        bad_ref = dict(good, version="v2.0.1", manifest_ref="main")
        releases = pyappify._index_releases({"format": 1, "releases": [good, bad_path, bad_ref, "x"]})
        self.assertEqual(list(releases), [good["version"]])
        urls = releases[good["version"]]["manifest_urls"]
        self.assertEqual(urls, [f"{self.github.base}/cdn/{INDEX_REF}/{good['manifest']}", good["manifest_url"]])
        with self.assertRaises(ValueError):
            pyappify._index_releases({"format": 2, "releases": [good]})


if __name__ == "__main__":
    unittest.main()
