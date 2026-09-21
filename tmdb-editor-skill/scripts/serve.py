#!/usr/bin/env python3
"""带 CORS 头的本地静态文件服务器：把处理好的图片中转给 TMDB 页面 fetch 上传。

用法：
  python3 serve.py [目录] [端口]           # 默认当前目录、端口 8899，只监听 127.0.0.1
  后台运行: python3 serve.py ./images 8899 > /tmp/tmdb-editor-serve.log 2>&1 &

在 TMDB 页面里用 fetch('http://127.0.0.1:8899/xxx.jpg', {mode:'cors'}) 取图。
（browser.py 的 upload op 走 base64 直传，不经此服务；这是浏览器控制台手工执行时的备选。）
"""

import http.server
import os
import sys


def main() -> None:
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8899
    os.chdir(directory)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            super().end_headers()

        def log_message(self, *args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"serving {os.getcwd()} on http://127.0.0.1:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
