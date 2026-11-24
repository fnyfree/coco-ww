"""把版本列表和每个版本的在线更新清单放到 update-index 分支, 国内连不上 GitHub 接口也能更新。

在线更新 (pyappify/__init__.py) 先从 raw / jsDelivr 读这个分支, 都不行才退回 GitHub 接口 + Release 附件。
发版 Action (.github/workflows/release.yml) 每次都跑两步, 本地也能跑:

  python tools/release_index.py manifests <releases.json> <分支目录> --repo wk321425/coco-ww
  python tools/release_index.py index <releases.json> <分支目录> --repo wk321425/coco-ww --manifest-ref <提交号>

<releases.json> 是 `gh api repos/<repo>/releases?per_page=100` 的输出。
- manifests: 分支里还没有的版本, 把 Release 附件 update-manifest.json 下到 manifests/<版本>.json。
- index: 写 releases.json。清单按 --manifest-ref (最后一次改 manifests/ 的提交) 引用: jsDelivr 对分支
  内容缓存 12 小时, 按提交号取不会拿到旧内容。releases.json 本身按分支取, Action 推完会刷新它的缓存。
内容没变时写出来的文件一字不差, 重跑不会多出提交。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST_ASSET = "update-manifest.json"
MANIFEST_FORMAT = 1
INDEX_FILE = "releases.json"
INDEX_FORMAT = 1
SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
COMMIT = re.compile(r"[0-9a-f]{40}")


def manifest_asset(release):
    for asset in release.get("assets") or []:
        if asset.get("name") == MANIFEST_ASSET:
            return asset
    return None


def published(api_releases):
    """带清单附件、不是草稿、tag 能当文件名的 Release, 顺序和接口给的一样。"""
    result = []
    for release in api_releases:
        tag = release.get("tag_name")
        if (release.get("draft") or not isinstance(tag, str) or not SAFE_TAG.fullmatch(tag)
                or manifest_asset(release) is None):
            continue
        result.append(release)
    return result


def manifest_path(tag):
    return f"manifests/{tag}.json"


def check_manifest(path, tag):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path} 读不出来: {error}") from error
    if (not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT or data.get("version") != tag
            or not COMMIT.fullmatch(str(data.get("commit", ""))) or not isinstance(data.get("files"), dict)):
        raise ValueError(f"{path} 不是 {tag} 的更新清单")


def sync_manifests(api_releases, root, download):
    """把分支目录 root 里缺的清单补上, 返回补了哪些版本。download(tag, path) 负责下载附件。"""
    root = Path(root)
    added = []
    for release in published(api_releases):
        tag = release["tag_name"]
        path = root / manifest_path(tag)
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        download(tag, path)
        try:
            check_manifest(path, tag)
        except ValueError:
            path.unlink(missing_ok=True)
            raise
        added.append(tag)
    return added


def build_index(api_releases, root, manifest_ref, repo):
    """releases.json 的内容: 分支里有清单的版本, 清单按 manifest_ref 这次提交引用。"""
    if not COMMIT.fullmatch(manifest_ref or ""):
        raise ValueError(f"manifest_ref 不是提交号: {manifest_ref!r}")
    root = Path(root)
    releases = []
    for release in published(api_releases):
        tag = release["tag_name"]
        if not (root / manifest_path(tag)).is_file():
            continue
        releases.append({
            "version": tag,
            "prerelease": bool(release.get("prerelease")),
            "published_at": release.get("published_at"),
            "body": release.get("body") or "",
            "manifest": manifest_path(tag),
            "manifest_ref": manifest_ref,
            "manifest_url": manifest_asset(release).get("browser_download_url"),
        })
    return {"format": INDEX_FORMAT, "repo": repo, "releases": releases}


def write_index(index, root):
    text = json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    (Path(root) / INDEX_FILE).write_text(text, encoding="utf-8", newline="\n")


def gh_downloader(repo):
    def download(tag, path):
        subprocess.run(["gh", "release", "download", tag, "-R", repo, "-p", MANIFEST_ASSET,
                        "-O", str(path), "--clobber"], check=True)
    return download


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=("manifests", "index"))
    parser.add_argument("releases_json")
    parser.add_argument("branch_dir")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest-ref", default="")
    args = parser.parse_args(argv)

    with open(args.releases_json, encoding="utf-8") as file:
        api_releases = json.load(file)
    if not isinstance(api_releases, list):
        sys.exit("releases.json 不是 Release 列表")

    if args.step == "manifests":
        added = sync_manifests(api_releases, args.branch_dir, gh_downloader(args.repo))
        print(f"补了 {len(added)} 个清单: {' '.join(added) or '无'}")
    else:
        index = build_index(api_releases, args.branch_dir, args.manifest_ref, args.repo)
        write_index(index, args.branch_dir)
        print(f"版本列表 {len(index['releases'])} 个版本: {' '.join(r['version'] for r in index['releases'])}")


if __name__ == "__main__":
    main()
