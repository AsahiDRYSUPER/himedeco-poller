"""鍵つきの中身（sealed/<name>.enc）を開けて、そのファイルの中身として動かす。鍵は金庫の KNOWLEDGE_KEY。"""
import secret_box


def run(name, g):
    src = secret_box.text(name)
    if src is None:
        return False
    exec(compile(src, f"<{name}>", "exec"), g)
    return True
