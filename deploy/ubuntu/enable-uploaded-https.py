#!/usr/bin/env python3
"""One-time HTTPS cutover using an existing, locally uploaded Nginx ZIP.

Run as root on the production host. Does not touch application code or data.
Private key bytes never leave the server or appear in output.
"""
from pathlib import Path
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

DOMAIN = "boji.tensorbot.xyz"
ARCHIVE = Path("/data/boji.tensorbot.xyz_nginx.zip")
CONF = Path("/etc/nginx/sites-available/bojizhuye.conf")
ENV = Path("/etc/bojizhuye/bojizhuye.env")
TLS = Path("/etc/nginx/ssl") / DOMAIN


def run(*args, input=None):
    return subprocess.run(args, input=input, check=True, capture_output=True).stdout


def write(path, data, mode):
    tmp = path.with_name(path.name + ".https-new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    old_conf, old_env = CONF.read_bytes(), ENV.read_bytes()
    assert b"listen 443" not in old_conf, "HTTPS already present; inspect manually"
    assert old_conf.count(b"server_name boji.tensorbot.xyz;") == 1
    assert old_conf.count(b"listen 80;") == 2
    assert old_env.count(b"CLUB_ORIGIN=http://boji.tensorbot.xyz") == 1
    with zipfile.ZipFile(ARCHIVE) as z, tempfile.TemporaryDirectory() as td:
        files = [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
        certs = [(n, b) for n, b in files if b"-----BEGIN CERTIFICATE-----" in b]
        keys = [(n, b) for n, b in files if re.search(rb"-----BEGIN (?:RSA |EC |ENCRYPTED )?PRIVATE KEY-----", b)]
        assert certs and len(keys) == 1, "Missing/ambiguous certificate or key"
        cert_name, cert = max(certs, key=lambda x: x[1].count(b"-----BEGIN CERTIFICATE-----"))
        blocks = re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", cert, re.S)
        assert len(blocks) >= 2, "A complete chain is required"
        cert = b"\n".join(blocks) + b"\n"
        leaf, chain, key = [Path(td) / n for n in ("leaf.pem", "chain.pem", "key.pem")]
        leaf.write_bytes(blocks[0] + b"\n")
        chain.write_bytes(b"\n".join(blocks[1:]) + b"\n")
        key.write_bytes(keys[0][1]); key.chmod(0o600)
        run("openssl", "x509", "-in", str(leaf), "-noout", "-checkhost", DOMAIN)
        run("openssl", "x509", "-in", str(leaf), "-noout", "-checkend", "0")
        run("openssl", "verify", "-purpose", "sslserver", "-verify_hostname", DOMAIN,
            "-untrusted", str(chain), str(leaf))
        pub = run("openssl", "x509", "-in", str(leaf), "-pubkey", "-noout")
        cert_pub = run("openssl", "pkey", "-pubin", "-outform", "DER", input=pub)
        key_pub = run("openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER")
        assert hashlib.sha256(cert_pub).digest() == hashlib.sha256(key_pub).digest(), "Key mismatch"
        print("CERTIFICATE_VALIDATED", cert_name, "chain_length", len(blocks), flush=True)
        print(run("openssl", "x509", "-in", str(leaf), "-noout", "-issuer", "-dates").decode(), flush=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = Path("/var/backups/bojizhuye-https") / stamp
        backup.mkdir(parents=True, mode=0o700)
        backup.parent.chmod(0o700)
        for src in (CONF, ENV):
            shutil.copy2(src, backup / src.name)
            (backup / src.name).chmod(0o600)
        TLS.mkdir(parents=True, exist_ok=True, mode=0o700); TLS.chmod(0o700)
        assert not (TLS / "fullchain.pem").exists() and not (TLS / "privkey.pem").exists()
        write(TLS / "fullchain.pem", cert, 0o644)
        write(TLS / "privkey.pem", keys[0][1], 0o600)
    text = old_conf.decode()
    text = text.replace("# Managed by bojizhuye deploy/ubuntu.", "# Managed by bojizhuye HTTPS configuration.", 1)
    text = text.replace("listen 80;", "listen 443 ssl;\n    ssl_certificate " + str(TLS / "fullchain.pem") + ";\n    ssl_certificate_key " + str(TLS / "privkey.pem") + ";\n    ssl_protocols TLSv1.2 TLSv1.3;", 1)
    text = text.replace("return 301 http://boji.tensorbot.xyz$request_uri;", "return 301 https://boji.tensorbot.xyz$request_uri;")
    text += "\nserver {\n    listen 80;\n    server_name boji.tensorbot.xyz;\n    return 301 https://boji.tensorbot.xyz$request_uri;\n}\n"
    env = old_env.replace(b"# Managed by bojizhuye deploy/ubuntu.", b"# Managed by bojizhuye HTTPS configuration.", 1)
    env = env.replace(b"CLUB_ORIGIN=http://boji.tensorbot.xyz", b"CLUB_ORIGIN=https://boji.tensorbot.xyz", 1)
    try:
        write(CONF, text.encode(), 0o644)
        write(ENV, env, 0o600)
        run("nginx", "-t")
        run("systemctl", "restart", "bojizhuye")
        for attempt in range(15):
            try:
                run("curl", "--fail", "--silent", "--max-time", "2", "http://127.0.0.1:5381/api/admin/session")
                break
            except subprocess.CalledProcessError:
                if attempt == 14: raise
                time.sleep(1)
        run("systemctl", "reload", "nginx")
        for route in ("/", "/city-vote", "/admin", "/api/admin/session", "/api/campaign"):
            code = run("curl", "--silent", "--show-error", "--max-time", "8", "--resolve", DOMAIN + ":443:127.0.0.1", "-o", "/dev/null", "-w", "%{http_code}", "https://" + DOMAIN + route).decode()
            assert code == "200", (route, code)
            print("HTTPS_OK", route, code, flush=True)
        print("CUTOVER_COMPLETE", "backup", backup, flush=True)
        print("LIVE_RELEASE", Path("/opt/bojizhuye/current").resolve().name, flush=True)
    except BaseException:
        write(CONF, old_conf, 0o644); write(ENV, old_env, 0o600)
        run("systemctl", "restart", "bojizhuye")
        run("nginx", "-t"); run("systemctl", "reload", "nginx")
        print("CONFIG_ROLLED_BACK", backup, flush=True)
        raise


if __name__ == "__main__":
    main()
