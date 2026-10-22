"""用 GitHub Git Data API 推送（绕过 github.com:443 被墙）。

用法:
    python scripts/github_push.py <token>

流程: blob -> tree -> commit -> ref(patch)
"""
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
OWNER, REPO = "bushizhanghan", "c-subset-compiler"
ROOT = r"E:\dsh\csub"


def call(method, path, payload, token):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "token " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "csub-push")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        raise SystemExit("HTTP %s %s: %s" % (e.code, path, body))


def git(*args):
    r = subprocess.run(["git"] + list(args), cwd=ROOT,
                       capture_output=True, check=True)
    return r.stdout.decode("utf-8", "replace").strip()


def main():
    token = sys.argv[1]
    remote_sha = call("GET", "/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
                      None, token)["object"]["sha"]
    print("远端 main: " + remote_sha[:7])

    # -z：NUL 分隔 + 禁用路径转义。
    # 不加的话中文文件名（使用说明.md）会被转成
    # "\344\275\277..." 的八进制形式，后面 git cat-file 找不到。
    raw = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
        cwd=ROOT, capture_output=True, check=True).stdout
    files = [p.decode("utf-8") for p in raw.split(b"\x00") if p]
    print("本地文件数: %d" % len(files))

    items = []
    for i, path in enumerate(files, 1):
        # -c core.quotePath=false 让中文路径按原样读
        raw = subprocess.run(
            ["git", "-c", "core.quotePath=false", "cat-file", "-p",
             "HEAD:" + path],
            cwd=ROOT, capture_output=True,
            check=True).stdout
        try:
            text = raw.decode("utf-8")
            blob = call("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO),
                        {"content": text, "encoding": "utf-8"}, token)
        except UnicodeDecodeError:
            blob = call("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO),
                        {"content": base64.b64encode(raw).decode(),
                         "encoding": "base64"}, token)
        items.append({"path": path, "mode": "100644",
                      "type": "blob", "sha": blob["sha"]})
        if i % 50 == 0:
            print("  %d/%d" % (i, len(files)))

    print("  blob: %d" % len(items))
    tree = call("POST", "/repos/%s/%s/git/trees" % (OWNER, REPO),
                {"tree": items}, token)
    print("  tree: " + tree["sha"][:7])

    msg = git("log", "-1", "--format=%B", "HEAD")
    commit = call("POST", "/repos/%s/%s/git/commits" % (OWNER, REPO),
                  {"message": msg, "tree": tree["sha"],
                   "parents": [remote_sha]}, token)
    print("  commit: " + commit["sha"][:7])

    call("PATCH", "/repos/%s/%s/git/refs/heads/main" % (OWNER, REPO),
         {"sha": commit["sha"]}, token)
    print("已更新 main -> " + commit["sha"][:7])

    # 同步本地所有 tag。早期硬编码只推 v1.0，打 v2.0 后
    # 远端永远看不到（实测踩到）。
    local_tags = git("tag", "-l").splitlines()
    for tag in local_tags:
        if not tag.strip():
            continue
        msg = git("tag", "-l", tag, "--format=%(contents)")
        try:
            tag_tgt = call("POST", "/repos/%s/%s/git/tags" % (OWNER, REPO),
                           {"tag": tag, "message": msg or tag,
                            "object": commit["sha"], "type": "commit"},
                           token)
        except SystemExit as e:
            if "422" not in str(e):
                print("tag %s 失败: %s" % (tag, e))
                continue
            # tag 对象已存在：拿现有引用的 sha
            existing = call("GET", "/repos/%s/%s/git/refs/tags/%s"
                            % (OWNER, REPO, tag), None, token)
            tag_tgt = {"sha": existing["object"]["sha"]}
        try:
            call("POST", "/repos/%s/%s/git/refs" % (OWNER, REPO),
                 {"ref": "refs/tags/%s" % tag, "sha": tag_tgt["sha"]}, token)
            print("已创建 tag %s" % tag)
        except SystemExit as e:
            if "422" in str(e):
                call("PATCH", "/repos/%s/%s/git/refs/tags/%s"
                     % (OWNER, REPO, tag), {"sha": tag_tgt["sha"]}, token)
                print("已更新 tag %s" % tag)
            else:
                print("tag %s 失败: %s" % (tag, e))


if __name__ == "__main__":
    main()
