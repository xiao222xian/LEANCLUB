"""Start the local API and same-origin website; stop only child processes we own."""
import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须为 1–65535。")
    return port


def ensure_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            connection.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError("本机端口 %s 已被占用；请换一个端口，现有服务不会被停止。" % port) from error


def wait_ready(process, url, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("子服务启动失败，请查看上面的错误信息。")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, OSError):
            pass
        time.sleep(0.2)
    raise RuntimeError("本地服务未在预期时间就绪，请查看上面的错误信息。")


def stop_children(children):
    for process in reversed(children):
        if process.poll() is None:
            process.terminate()
    for process in reversed(children):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=port_number, default=4180, help="网页端口（默认 4180）")
    parser.add_argument("--api-port", type=port_number, default=5381, help="API 端口（默认 5381）")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "local.sqlite3", help="本地 SQLite 数据库")
    parser.add_argument("--operations-config", type=Path, help="忽略目录中的本地运营桥接配置，仅供服务间使用")
    parser.add_argument("--member-orders-config", type=Path, help="独立会员报名桥接配置，仅供服务间使用")
    args = parser.parse_args()
    children = []
    try:
        if args.port == args.api_port:
            raise RuntimeError("网页和 API 必须使用不同端口。")
        if any(importlib.util.find_spec(module) is None for module in ("PIL", "qrcode")):
            raise RuntimeError("请先安装依赖：python -m pip install -r requirements.txt")
        ensure_available(args.port)
        ensure_available(args.api_port)
        database = args.db.expanduser().resolve()
        database.parent.mkdir(parents=True, exist_ok=True)
        media_dir = database.parent / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        origin = "http://127.0.0.1" + (":%s" % args.port if args.port != 80 else "")
        environment = os.environ.copy()
        if args.operations_config:
            settings = json.loads(args.operations_config.read_text(encoding='utf-8-sig'))
            if settings.get('enabled') is not True:
                raise RuntimeError('本地运营桥接配置未启用。')
            environment['LEANCLUB_OPERATIONS_SECRET'] = settings['secret']
            environment['LEANCLUB_OPERATIONS_TENANT'] = str(settings['tenant'])
        if args.member_orders_config:
            settings = json.loads(args.member_orders_config.read_text(encoding='utf-8-sig'))
            if settings.get('enabled') is not True:
                raise RuntimeError('本地会员报名桥接配置未启用。')
            environment['LEANCLUB_MEMBER_ORDERS_SECRET'] = settings['secret']
            environment['LEANCLUB_MEMBER_ORDERS_TENANT'] = str(settings['tenant'])
        environment.update({
            "CLUB_DB": str(database),
            "CLUB_MEDIA_DIR": str(media_dir),
            "CLUB_PORT": str(args.api_port),
            "PREVIEW_PORT": str(args.port),
            "CLUB_ORIGIN": origin,
            # Only this explicit loopback launcher enables first-admin setup.
            "CLUB_ALLOW_LOCAL_SETUP": "1",
            "PYTHONUNBUFFERED": "1",
        })
        api = subprocess.Popen([sys.executable, str(ROOT / "backend" / "server.py")], cwd=str(ROOT), env=environment)
        children.append(api)
        wait_ready(api, "http://127.0.0.1:%s/api/club/health" % args.api_port)
        frontend = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "preview.py")], cwd=str(ROOT), env=environment)
        children.append(frontend)
        wait_ready(frontend, origin + "/")
        print("\n首页：%s/\n投票：%s/city-vote\n管理：%s/admin\n按 Ctrl+C 停止本次启动的两个服务。\n" % (origin, origin, origin), flush=True)
        while all(process.poll() is None for process in children):
            time.sleep(0.5)
        raise RuntimeError("有一个本地服务已经退出，本次启动的其余服务也已停止。")
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    sys.exit(main())
