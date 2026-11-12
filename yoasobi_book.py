"""中身は鍵つき（sealed/yoasobi_book.enc）。"""
import sys
import sealed_code

if not sealed_code.run("yoasobi_book", globals()):
    if __name__ == "__main__":
        print("鍵つきの中身を開けないので動かさない")
        sys.exit(0)
    raise ImportError("yoasobi_book: sealed")
