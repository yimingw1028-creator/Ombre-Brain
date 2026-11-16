# ruff: noqa
"""测试替身：用 PyYAML 实现 python-frontmatter 的最小子集。"""
import yaml

class Post:
    def __init__(self, content="", **metadata):
        self.content = content
        self.metadata = dict(metadata)
    def __getitem__(self, k): return self.metadata[k]
    def __setitem__(self, k, v): self.metadata[k] = v
    def __contains__(self, k): return k in self.metadata
    def get(self, k, default=None): return self.metadata.get(k, default)
    def keys(self): return self.metadata.keys()

def loads(text, **_):
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end]) or {}
            body = text[end + 4:]
            if body.startswith("\n"): body = body[1:]
            return Post(body.strip("\n"), **meta)
    return Post(text)

def load(fd, **_):
    if hasattr(fd, "read"):
        return loads(fd.read())
    with open(fd, "r", encoding="utf-8") as f:
        return loads(f.read())

def dumps(post, **_):
    meta = yaml.safe_dump(post.metadata, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{meta}\n---\n\n{post.content}"

def dump(post, fd, **_):
    fd.write(dumps(post).encode("utf-8") if "b" in getattr(fd, "mode", "") else dumps(post))
