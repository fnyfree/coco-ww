"""发版 Action 生成 update-index 分支用的 tools/release_index.py。

客户端读不读得懂它的输出, 在 test_online_update.py 里用同一个脚本生成假分支一起测。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_release_index.py
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "release_index", Path(__file__).resolve().parent.parent / "tools" / "release_index.py")
release_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_index)

REF = "e" * 40


def release(tag, assets=True, draft=False, prerelease=False, body="* 改了点东西"):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "body": body,
            "published_at": "2026-11-24T00:00:00Z",
            "assets": [{"name": "update-manifest.json",
                        "browser_download_url": f"https://example.invalid/{tag}/update-manifest.json"}]
            if assets else []}


def manifest(tag):
    return {"format": 1, "version": tag, "commit": "f" * 40,
            "files": {"config.py": {"sha256": "0" * 64, "size": 1}}}


class ReleaseIndexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.downloads = []

    def tearDown(self):
        self.temp.cleanup()

    def download(self, tag, path):
        self.downloads.append(tag)
        path.write_text(json.dumps(manifest(tag)), encoding="utf-8")

    def test_only_published_releases_with_manifest(self):
        releases = [release("v1.0.2"), release("v1.0.1", draft=True), release("v1.0.0", assets=False),
                    release("../evil"), {"tag_name": None, "assets": []}]
        self.assertEqual([r["tag_name"] for r in release_index.published(releases)], ["v1.0.2"])

    def test_sync_downloads_missing_manifests_once(self):
        releases = [release("v1.0.2"), release("v1.0.1")]
        self.assertEqual(release_index.sync_manifests(releases, self.root, self.download), ["v1.0.2", "v1.0.1"])
        self.assertEqual(release_index.sync_manifests(releases, self.root, self.download), [])
        self.assertEqual(self.downloads, ["v1.0.2", "v1.0.1"])

    def test_sync_rejects_wrong_manifest(self):
        def wrong(_tag, path):
            path.write_text(json.dumps(manifest("v9.9.9")), encoding="utf-8")

        with self.assertRaises(ValueError):
            release_index.sync_manifests([release("v1.0.2")], self.root, wrong)
        self.assertFalse((self.root / "manifests" / "v1.0.2.json").exists())

    def test_index_lists_versions_that_have_manifests(self):
        releases = [release("v1.0.2", prerelease=True), release("v1.0.1"), release("v1.0.0")]
        release_index.sync_manifests(releases[:2], self.root, self.download)
        index = release_index.build_index(releases, self.root, REF, "owner/repo")
        self.assertEqual(index["format"], 1)
        # v1.0.0 分支里没有清单, 不列
        self.assertEqual([r["version"] for r in index["releases"]], ["v1.0.2", "v1.0.1"])
        first = index["releases"][0]
        self.assertTrue(first["prerelease"])
        self.assertEqual(first["manifest"], "manifests/v1.0.2.json")
        self.assertEqual(first["manifest_ref"], REF)
        self.assertEqual(first["manifest_url"], "https://example.invalid/v1.0.2/update-manifest.json")
        self.assertEqual(first["body"], "* 改了点东西")

    def test_index_file_is_stable(self):
        releases = [release("v1.0.1")]
        release_index.sync_manifests(releases, self.root, self.download)
        release_index.write_index(release_index.build_index(releases, self.root, REF, "owner/repo"), self.root)
        first = (self.root / "releases.json").read_bytes()
        release_index.write_index(release_index.build_index(releases, self.root, REF, "owner/repo"), self.root)
        # 一字不差: Action 里 git diff 看不出变化就不提交
        self.assertEqual((self.root / "releases.json").read_bytes(), first)
        self.assertNotIn(b"\r\n", first)

    def test_manifest_ref_must_be_commit(self):
        with self.assertRaises(ValueError):
            release_index.build_index([], self.root, "update-index", "owner/repo")

    def test_cli_index_step(self):
        releases = [release("v1.0.1")]
        release_index.sync_manifests(releases, self.root, self.download)
        api_json = self.root / "api.json"
        api_json.write_text(json.dumps(releases), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            release_index.main(["index", str(api_json), str(self.root), "--repo", "owner/repo",
                                "--manifest-ref", REF])
        written = json.loads((self.root / "releases.json").read_text(encoding="utf-8"))
        self.assertEqual([r["version"] for r in written["releases"]], ["v1.0.1"])


if __name__ == "__main__":
    unittest.main()
