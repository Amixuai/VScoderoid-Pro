#!/usr/bin/env python3
"""Desktop Mode (experimental): the desktop (Electron) VSCodium running in a Debian system inside this app.

Run it from the terminal of this app:

    python3 ~/.vscodroid/desktop.py setup     one time: download Debian, install the screen stack and VSCodium (needs internet)
    python3 ~/.vscodroid/desktop.py start     start the desktop and print an address to open in a browser
    python3 ~/.vscodroid/desktop.py stop      stop it
    python3 ~/.vscodroid/desktop.py status    say whether it is running
    python3 ~/.vscodroid/desktop.py shell     open a shell inside Debian
    python3 ~/.vscodroid/desktop.py repair    try other Electron flag sets if the editor window does not stay up

It needs a 64-bit ARM phone with at least 6 GB of RAM and about 8 GB of free storage. Everything it creates lives in
$HOME/desktop, and a log of each step is kept in $HOME/desktop/desktop-log.txt.
"""
import os, sys, shutil, subprocess, tarfile, urllib.request, ssl, time, hashlib, json, signal, secrets

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "desktop")
ROOT = os.path.join(BASE, "debian")
OUTF = os.path.join(BASE, "desktop-log.txt")
PIDF = os.path.join(BASE, "session.json")
RELEASE = os.environ.get("DESKTOP_DEBIAN", "trixie")
URLS = [os.environ["DESKTOP_ROOTFS_URL"]] if os.environ.get("DESKTOP_ROOTFS_URL") else [
    f"https://github.com/debuerreotype/docker-debian-artifacts/raw/dist-arm64v8/{RELEASE}/rootfs.tar.xz",
    f"https://raw.githubusercontent.com/debuerreotype/docker-debian-artifacts/dist-arm64v8/{RELEASE}/rootfs.tar.xz"]
PINNED_SHA256 = os.environ.get("DESKTOP_ROOTFS_SHA256", "")      # fill in after the first run prints it
WORKSPACE = os.environ.get("DESKTOP_WORKSPACE") or next((p for p in ("/storage/emulated/0/.VScoderoid-Pro", "/storage/emulated/0/VScoderoid-Pro") if os.path.isdir(p)), "/storage/emulated/0/.VScoderoid-Pro")
GEOMETRY = os.environ.get("DESKTOP_GEOMETRY", "1280x720")
MIN_RAM_MB = 5500

def emit(row, status, detail=""):
    os.makedirs(BASE, exist_ok=True)
    line = f"{row}|{status}|{detail}"
    print(line, flush=True)
    with open(OUTF, "a") as f: f.write(line + "\n")

def ram_mb():
    try:
        for l in open("/proc/meminfo"):
            if l.startswith("MemTotal:"): return int(l.split()[1]) // 1024
    except OSError: pass
    return 0

def host_env():
    """proot itself must not run under the app's exec interceptor: termux-exec rewrites every exec path
    to the Termux prefix, which is what made proot fail to start /usr/bin/env inside Debian."""
    e = dict(os.environ)
    for k in ("LD_PRELOAD", "TERMUX_EXEC__PROC_SELF_EXE", "TERMUX_EXEC__LD_PRELOAD", "TERMUX_EXEC__SYSTEM_LINKER_EXEC__MODE"):
        e.pop(k, None)
    return e

def proot_cmd(command, binds=(), cwd=None, extra_env=(), user=False):
    proot = shutil.which("proot")
    if not proot: raise SystemExit("proot was not found on PATH (is this the build with WP1?)")
    tmp = os.path.join(ROOT, "tmp"); os.makedirs(tmp, exist_ok=True)
    home = "/home/user" if user else "/root"
    ident = ["-i", "1000:1000"] if user else ["-0"]
    cmd = [proot, "--link2symlink", *ident, "--kill-on-exit", "-r", ROOT,
           "-b", "/dev", "-b", "/proc", "-b", "/sys", "-b", f"{tmp}:/dev/shm"]
    for b in binds: cmd += ["-b", b]
    cmd += ["-w", cwd or home, "/usr/bin/env", "-i", f"HOME={home}", f"USER={'user' if user else 'root'}", "LANG=C.UTF-8", "TERM=xterm-256color",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND=noninteractive", *extra_env]
    return cmd + list(command)

def run_in(command, timeout=None, binds=(), extra_env=(), host_extra=None):
    env = host_env(); env.update(host_extra or {})
    return subprocess.run(proot_cmd(command, binds, extra_env=extra_env), capture_output=True, text=True, timeout=timeout, env=env)

import re
ERR = re.compile(r"(^E: |dpkg: error|Errors were encountered|\bfailed\b|cannot|Permission denied|Operation not permitted|not implemented|Segmentation|Bad system call|Killed|No such file)", re.I)

def excerpt(text, n=6):
    lines = [l.strip()[:150] for l in text.splitlines() if ERR.search(l)]
    return " | ".join(lines[:n]) if lines else (text.strip().splitlines() or [""])[-1][:150]

def tail(path, n=12):
    try: return " | ".join(l.strip()[:140] for l in open(path, errors="replace").read().splitlines()[-n:])
    except OSError: return "(no log)"

def ssl_context():
    ctx = ssl.create_default_context()
    for var in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO"):
        p = os.environ.get(var)
        if p and os.path.isfile(p):
            ctx = ssl.create_default_context(cafile=p); break
    return ctx

def api_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "vscodroid-desktop-poc", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, context=ssl_context(), timeout=60) as r:
        return json.load(r)

def discover_rootfs():
    """Find a Debian arm64 rootfs for proot without hard-coding a path that can move.
    Returns (url, sha256-or-empty, strip_top_directory, label)."""
    import re
    want = re.compile(r"^debian-(trixie|bookworm)-aarch64-pd-v[\d.]+\.tar\.xz$")
    best = None
    try:
        for rel in api_get("https://api.github.com/repos/termux/proot-distro/releases?per_page=40"):
            for a in rel.get("assets", []):
                m = want.match(a.get("name", ""))
                if not m: continue
                rank = 0 if m.group(1) == RELEASE else 1
                dg = a.get("digest") or ""
                cand = (rank, a["browser_download_url"], dg[7:] if dg.startswith("sha256:") else "", a["name"])
                if best is None or cand[0] < best[0]: best = cand
            if best and best[0] == 0: break
        if best: return best[1], best[2], True, best[3]
    except Exception as e:
        print("  proot-distro lookup failed:", e, flush=True)
    try:
        items = api_get(f"https://api.github.com/repos/debuerreotype/docker-debian-artifacts/contents/{RELEASE}?ref=dist-arm64v8")
        for it in items:
            if it.get("name") == "rootfs.tar.xz" and it.get("download_url"): return it["download_url"], "", False, f"debuerreotype {RELEASE}"
    except Exception as e:
        print("  debuerreotype lookup failed:", e, flush=True)
    return None

def need_root():
    if not os.path.isdir(os.path.join(ROOT, "usr/bin")):
        emit("DESK-ROOTFS", "FAIL", "no Debian system yet: run  python3 desktop_poc.py install  first")
        raise SystemExit(1)

def download(urls, dest):
    part = dest + ".part"; last = None
    for url in urls:
        try:
            have = os.path.getsize(part) if os.path.exists(part) else 0
            req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
            with urllib.request.urlopen(req, context=ssl_context(), timeout=60) as r:
                if have and r.status != 206: have = 0
                mode = "ab" if have else "wb"; total = r.headers.get("Content-Length"); got = have; t0 = time.time(); shown = 0
                with open(part, mode) as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b: break
                        f.write(b); got += len(b)
                        if time.time() - shown > 5: print(f"  {got >> 20} MB downloaded", flush=True); shown = time.time()
            os.replace(part, dest); return url
        except Exception as e:
            last = f"{url}: {e}"; print("  failed:", last, flush=True)
    raise RuntimeError(last or "no URL")

STANDARD = {"bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt", "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var"}

def detect_strip(archive):
    """True when every entry sits inside one wrapper directory (proot-distro tarballs do), judged from the archive itself."""
    with tarfile.open(archive, "r|xz") as tf:
        for m in tf:
            top = os.path.normpath(m.name.lstrip("/")).split("/")[0]
            if top in (".", ""): continue
            return top not in STANDARD

def unpack(archive, root, strip=False):
    """Streaming extract with no root: hard links become copies, device nodes are skipped, nothing escapes root."""
    os.makedirs(root, exist_ok=True); n = 0; skipped = 0; dirs = []
    rootreal = os.path.realpath(root)
    with tarfile.open(archive, "r|xz") as tf:
        for m in tf:
            name = os.path.normpath(m.name.lstrip("/"))
            if strip:
                parts = name.split("/")
                if len(parts) < 2: continue
                name = "/".join(parts[1:])
            if name in (".", ""): continue
            if name.startswith(".."): skipped += 1; continue
            path = os.path.join(root, name)
            if not os.path.realpath(os.path.dirname(path)).startswith(rootreal): skipped += 1; continue
            if m.isdir():
                os.makedirs(path, exist_ok=True); os.chmod(path, 0o755); dirs.append((path, m.mode & 0o7777))
            elif m.isreg():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.lexists(path): os.remove(path)
                with open(path, "wb") as f: shutil.copyfileobj(tf.extractfile(m), f)
                os.chmod(path, (m.mode & 0o777) | 0o600)
            elif m.issym():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.lexists(path): os.remove(path)
                os.symlink(m.linkname, path)
            elif m.islnk():
                ln = os.path.normpath(m.linkname.lstrip("/"))
                if strip: ln = "/".join(ln.split("/")[1:])
                src = os.path.join(root, ln)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.lexists(path): os.remove(path)
                if os.path.isfile(src): shutil.copy2(src, path)
                else: skipped += 1
            else: skipped += 1; continue
            n += 1
    for p, mode in reversed(dirs):
        try: os.chmod(p, (mode & 0o777) | 0o700)
        except OSError: pass
    return n, skipped

def cmd_install(args):
    open(OUTF, "w").close()
    mb = ram_mb(); need = "--force" not in args
    emit("DESK-DEVICE", "PASS", f"arch={os.uname().machine} ram_mb={mb} free_mb={shutil.disk_usage(HOME).free >> 20} proot={shutil.which('proot')} loader={os.environ.get('PROOT_LOADER')}")
    if need and mb < MIN_RAM_MB: emit("DESK-RAM", "FAIL", f"{mb} MB RAM is below the {MIN_RAM_MB} MB minimum (use --force to try anyway)"); return
    os.makedirs(BASE, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "root"), exist_ok=True) if os.path.isdir(ROOT) else None
    arc = os.path.join(BASE, "rootfs.tar.xz")
    global PINNED_SHA256
    strip = False
    if not os.path.exists(arc):
        if os.environ.get("DESKTOP_ROOTFS_URL"):
            urls, strip = URLS, os.environ.get("DESKTOP_ROOTFS_STRIP") == "1"
        else:
            found = discover_rootfs()
            if not found: emit("DESK-FIND", "FAIL", "no Debian arm64 rootfs found through the GitHub API (offline? rate limited?). Set DESKTOP_ROOTFS_URL"); return
            url, sha, strip, label = found; urls = [url]
            if sha and not PINNED_SHA256: PINNED_SHA256 = sha
            emit("DESK-FIND", "PASS", f"{label} {url}")
        try: used = download(urls, arc)
        except Exception as e: emit("DESK-DOWNLOAD", "FAIL", str(e)[:200]); return
        open(arc + ".strip", "w").write("1" if strip else "0")
        emit("DESK-DOWNLOAD", "PASS", f"{os.path.getsize(arc) >> 20} MB from {used}")
    else:
        try: strip = open(arc + ".strip").read() == "1"
        except OSError: pass
    sha = hashlib.sha256(open(arc, "rb").read()).hexdigest()
    if PINNED_SHA256 and sha != PINNED_SHA256: emit("DESK-CHECKSUM", "FAIL", f"sha256 {sha} does not match the pinned value"); os.remove(arc); return
    emit("DESK-CHECKSUM", "PASS" if PINNED_SHA256 else "WARN", f"sha256={sha}" + ("" if PINNED_SHA256 else " (not pinned yet: put this value in DESKTOP_ROOTFS_SHA256)"))
    if os.path.exists(ROOT) and not os.path.islink(ROOT): shutil.rmtree(ROOT)
    strip = detect_strip(arc)
    emit("DESK-LAYOUT", "PASS", "wrapper directory removed" if strip else "archive is a plain rootfs")
    try: n, sk = unpack(arc, ROOT, strip)
    except Exception as e: emit("DESK-UNPACK", "FAIL", repr(e)[:200]); return
    emit("DESK-UNPACK", "PASS", f"{n} entries, {sk} skipped")
    os.makedirs(os.path.join(ROOT, "etc"), exist_ok=True)
    with open(os.path.join(ROOT, "etc/resolv.conf"), "w") as f: f.write("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")
    with open(os.path.join(ROOT, "etc/hosts"), "w") as f: f.write("127.0.0.1 localhost\n::1 localhost\n")
    os.makedirs(os.path.join(ROOT, "etc/apt/apt.conf.d"), exist_ok=True)
    with open(os.path.join(ROOT, "etc/apt/apt.conf.d/99proot"), "w") as f:
        f.write('APT::Sandbox::User "root";\nDpkg::Options { "--force-unsafe-io"; };\nAcquire::Retries "3";\n')
    if not os.path.exists(os.path.join(ROOT, "usr/bin/env")): emit("DESK-UNPACK", "FAIL", "usr/bin/env is missing after unpacking"); return
    r = run_in(["/bin/sh", "-c", ". /etc/os-release; echo $PRETTY_NAME; uname -m"], timeout=120)
    emit("DESK-START", "PASS" if r.returncode == 0 and "Debian" in r.stdout else "FAIL", (r.stdout + r.stderr).strip().replace("\n", " ")[:200])
    if r.returncode != 0: return
    r = run_in(["apt-get", "update"], timeout=600)
    emit("DESK-APT", "PASS" if r.returncode == 0 else "FAIL", (r.stdout + r.stderr).strip().splitlines()[-1][:200] if (r.stdout + r.stderr).strip() else "")
    open(os.path.join(BASE, "debian.ready"), "w").write("1")
    emit("DESK-DONE", "PASS", "Debian is installed")

def cmd_setup(args):
    """The one-time setup: Debian, then the screen stack and the editor. Safe to run again; it skips what is done."""
    if not os.path.exists(os.path.join(BASE, "debian.ready")): cmd_install(args)
    if os.path.isdir(os.path.join(ROOT, "usr/bin")) and not os.path.exists(os.path.join(ROOT, "usr/share/codium/codium")):
        cmd_display(args)
    if os.path.exists(os.path.join(ROOT, "usr/share/codium/codium")):
        print("\nSetup is done. Start the desktop with:  python3 ~/.vscodroid/desktop.py start\n", flush=True)

def cmd_shell(args):
    need_root()
    c = proot_cmd(["/bin/bash", "-l"]); os.execvpe(c[0], c, host_env())

def cmd_display(args):
    need_root()
    os.makedirs(os.path.join(ROOT, "usr/sbin"), exist_ok=True)
    with open(os.path.join(ROOT, "usr/sbin/policy-rc.d"), "w") as f: f.write("#!/bin/sh\nexit 101\n")
    os.chmod(os.path.join(ROOT, "usr/sbin/policy-rc.d"), 0o755)
    groups = [("base", "ca-certificates curl xauth x11-utils xterm fonts-dejavu-core xfonts-base"),
              ("vnc", "tigervnc-standalone-server tigervnc-common"),
              ("wm-web", "openbox novnc websockify")]
    allok = True
    for name, pk in groups:
        cmd = ["apt-get", "install", "-y", "--no-install-recommends", *pk.split()]
        r = run_in(cmd, timeout=3600); note = ""
        if r.returncode != 0:
            run_in(["dpkg", "--configure", "-a"], timeout=1800)
            r = run_in(cmd, timeout=3600); note = " (after dpkg --configure -a)"
        if r.returncode != 0:
            r2 = run_in(cmd, timeout=3600, host_extra={"PROOT_NO_SECCOMP": "1"})
            if r2.returncode == 0: r = r2; note = " (needed PROOT_NO_SECCOMP=1)"
        open(os.path.join(BASE, f"apt-{name}.log"), "w").write(r.stdout + "\n" + r.stderr)
        ok = r.returncode == 0; allok &= ok
        emit(f"DESK-PKG-{name.upper()}", "PASS" if ok else "FAIL", ("installed" + note) if ok else excerpt(r.stdout + "\n" + r.stderr, 8))
    if not allok: return
    r = run_in(["/bin/sh", "-c", "curl -s -m 30 https://api.github.com/repos/VSCodium/vscodium/releases/latest | grep -o 'https://[^\"]*_arm64\\.deb' | head -1"], timeout=60)
    url = r.stdout.strip()
    if not url: emit("DESK-EDITOR", "FAIL", "could not find the VSCodium arm64 .deb URL: " + excerpt(r.stdout + r.stderr)); return
    r = run_in(["/bin/sh", "-c", f"curl -sL -m 1800 -o /tmp/codium.deb '{url}' && apt-get install -y /tmp/codium.deb"], timeout=3600)
    ok = r.returncode == 0
    if not ok:
        r = run_in(["/bin/sh", "-c", "apt-get install -y /tmp/codium.deb"], timeout=3600, host_extra={"PROOT_NO_SECCOMP": "1"}); ok = r.returncode == 0
    emit("DESK-EDITOR", "PASS" if ok else "FAIL", url if ok else excerpt(r.stdout + "\n" + r.stderr, 8))

DEFAULT_FLAGS = "--no-sandbox --disable-gpu --disable-dev-shm-usage --no-zygote --disable-setuid-sandbox --ozone-platform=x11"
FLAGS_FILE = os.path.join(BASE, "flags.txt")

def current_flags():
    try: return open(FLAGS_FILE).read().strip() or DEFAULT_FLAGS
    except OSError: return DEFAULT_FLAGS

START_SH = """#!/bin/sh
export DISPLAY=:1 ELECTRON_ENABLE_LOGGING=1
mkdir -p "$HOME/.vnc" "$HOME/workspace"
[ -f "$HOME/.vnc/passwd" ] || echo "$VNC_PW" | vncpasswd -f > "$HOME/.vnc/passwd"
chmod 600 "$HOME/.vnc/passwd"
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 /tmp/codium.log
Xtigervnc :1 -rfbauth "$HOME/.vnc/passwd" -localhost yes -geometry %(geo)s -depth 24 -rfbport 5901 -SecurityTypes VncAuth >/tmp/xvnc.log 2>&1 &
sleep 3
openbox >/tmp/openbox.log 2>&1 &
websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5901 >/tmp/websockify.log 2>&1 &
sleep 2
xterm -geometry 90x24+10+10 -e sh -c 'echo "VSCodroid desktop display works. Close this window when done."; exec sh' >/tmp/xterm.log 2>&1 &
# Start the Electron binary itself: the "codium" command is only a launcher that opens the window in the
# background and returns at once, so "codium exited" would be reported while the window is still running.
CB=/usr/share/codium/codium
[ -x "$CB" ] || CB=codium
"$CB" %(flags)s \\
  --enable-logging=stderr --user-data-dir="$HOME/.config/VSCodium" --extensions-dir="$HOME/.vscode-oss/extensions" "$HOME/workspace" >/tmp/codium.log 2>&1
echo "codium exited with status $?" >> /tmp/codium.log
wait
"""

CONFIG_FILE = os.path.join(BASE, "config.json")

def load_config():
    try:
        c = json.load(open(CONFIG_FILE)); return c["flags"], bool(c.get("user", True))
    except Exception:
        return DEFAULT_FLAGS, True

def ensure_user():
    """A normal user (uid 1000) inside Debian. Electron refuses some of its helper processes when they run as root."""
    for f, line in (("etc/passwd", "user:x:1000:1000:user:/home/user:/bin/bash"), ("etc/group", "user:x:1000:")):
        p = os.path.join(ROOT, f)
        if "user:" not in open(p).read():
            with open(p, "a") as fh: fh.write(line + "\n")
    os.makedirs(os.path.join(ROOT, "home/user/workspace"), exist_ok=True)

def write_start_script(flags):
    p = os.path.join(ROOT, "usr/local/bin"); os.makedirs(p, exist_ok=True)
    path = os.path.join(p, "start-desktop.sh")
    with open(path, "w") as f: f.write(START_SH % {"geo": GEOMETRY, "flags": flags})
    os.chmod(path, 0o755)
    return "/usr/local/bin/start-desktop.sh"

def spawn(pw, flags, user, host_extra=None):
    ensure_user(); script = write_start_script(flags)
    home = "/home/user" if user else "/root"
    binds = [f"{WORKSPACE}:{home}/workspace"] if os.path.isdir(WORKSPACE) else []
    env = host_env(); env.update(host_extra or {})
    log = open(os.path.join(BASE, "desktop.log"), "w")
    p = subprocess.Popen(proot_cmd(["/bin/sh", script], binds, extra_env=[f"VNC_PW={pw}"], user=user), stdout=log, stderr=log, start_new_session=True, env=env)
    json.dump({"pid": p.pid, "pw": pw}, open(PIDF, "w"))
    return p

def proc_summary(word="codium"):
    """Electron process types now running and whether each got --no-sandbox, read from /proc."""
    from collections import Counter
    c = Counter()
    for d in os.listdir("/proc"):
        if not d.isdigit(): continue
        try: cl = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError: continue
        if word not in cl or "desktop_poc" in cl or "--kill-on-exit" in cl: continue
        t = "browser"
        for part in cl.split():
            if part.startswith("--type="): t = part[7:]
        c[f"{t}:{'nosb' if '--no-sandbox' in cl else 'SANDBOX'}"] += 1
    return dict(c)

VARIANTS = [   # (name, flags, run as a normal user?)
    ("A-user-basic", "--no-sandbox --disable-gpu --disable-dev-shm-usage --no-zygote --disable-setuid-sandbox --ozone-platform=x11", True),
    ("B-user-with-zygote", "--no-sandbox --disable-gpu --disable-dev-shm-usage --disable-setuid-sandbox --ozone-platform=x11", True),
    ("C-user-swiftshader", "--no-sandbox --disable-dev-shm-usage --no-zygote --disable-setuid-sandbox --ozone-platform=x11 --use-gl=angle --use-angle=swiftshader --disable-gpu-sandbox --in-process-gpu", True),
    ("D-user-minimal", "--no-sandbox --disable-gpu --ozone-platform=x11", True),
    ("E-root-basic", "--no-sandbox --disable-gpu --disable-dev-shm-usage --no-zygote --disable-setuid-sandbox --ozone-platform=x11", False),
    ("F-root-in-process-gpu", "--no-sandbox --disable-gpu --disable-gpu-compositing --disable-dev-shm-usage --no-zygote --disable-setuid-sandbox --ozone-platform=x11 --in-process-gpu", False),
    ("G-user-single-process", "--no-sandbox --disable-gpu --disable-dev-shm-usage --single-process --ozone-platform=x11", True),
]

def window_sizes():
    r = run_in(["/bin/sh", "-c", "DISPLAY=:1 xwininfo -root -tree 2>&1"], timeout=30)
    out = []
    for l in r.stdout.splitlines():
        if re.search(r"codium|visual studio|welcome", l, re.I):
            m = re.search(r"(\d+)x(\d+)\+", l)
            if m: out.append((int(m.group(1)), int(m.group(2)), l.strip()[:90]))
    return out

BAD = re.compile(r"(FATAL|renderer process gone|crashed|SIGSEGV|SIGTRAP|Bad system call|Check failed|ERROR:(?!dbus))", re.I)

def watch(p, seconds=80):
    """Wait for a stable big editor window. Returns (window-or-None, crashed?)."""
    clog = os.path.join(ROOT, "tmp", "codium.log"); big = None; crashed = False; t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(5)
        try: txt = open(clog, errors="replace").read()
        except OSError: txt = ""
        if "renderer process gone" in txt or "FATAL" in txt: crashed = True; break
        if p.poll() is not None: break
        if time.time() - t0 > 12: big = next((w for w in window_sizes() if w[0] >= 400 and w[1] >= 300), big)
    return big, crashed

def show_url(pw):
    url = f"http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale&password={pw}"
    emit("DESK-URL", "PASS", url)
    print(f"\nOpen this in the phone browser:\n  {url}\n(stop later with: python3 desktop_poc.py stop)\n", flush=True)

def cmd_go(args):
    """One command: try the flag sets with a strict check, keep the first stable one, leave the desktop running."""
    need_root(); pw = secrets.token_hex(4); open(OUTF, "a").close()
    for name, flags, user in VARIANTS:
        if os.path.exists(PIDF): cmd_stop([])
        time.sleep(2)
        p = spawn(pw, flags, user)
        big, crashed = watch(p)
        clog = os.path.join(ROOT, "tmp", "codium.log")
        try: txt = open(clog, errors="replace").read()
        except OSError: txt = ""
        bad = " | ".join(l.strip()[:150] for l in txt.splitlines() if BAD.search(l))[:700]
        ok = big is not None and not crashed and p.poll() is None
        emit("DESK-TRIAL-" + name, "PASS" if ok else "FAIL", f"user={user} window={big[2] if big else None} crashed={crashed} procs={proc_summary()}" + ("" if ok else " problems: " + (bad or tail(clog, 4))))
        if ok:
            json.dump({"flags": flags, "user": user}, open(CONFIG_FILE, "w")); show_url(pw); return
    cmd_stop([]); emit("DESK-TRIAL-DONE", "FAIL", "no flag set gave a stable editor window; send this output")

def cmd_start(args):
    need_root()
    if os.path.exists(PIDF): cmd_stop([])
    flags, user = load_config(); pw = secrets.token_hex(4); open(OUTF, "a").close()
    p = spawn(pw, flags, user); big, crashed = watch(p, 60)
    emit("DESK-START-SESSION", "PASS" if (big and not crashed and p.poll() is None) else "FAIL", f"user={user} window={big[2] if big else None} crashed={crashed} procs={proc_summary()}")
    if p.poll() is None: show_url(pw)

cmd_trial = cmd_go

def cmd_stop(args):
    try: d = json.load(open(PIDF))
    except Exception: print("no session"); return
    try: os.killpg(d["pid"], signal.SIGTERM)
    except Exception: pass
    time.sleep(1)
    try: os.killpg(d["pid"], signal.SIGKILL)
    except Exception: pass
    os.remove(PIDF); emit("DESK-STOP", "PASS", "session stopped")

def cmd_status(args):
    try: d = json.load(open(PIDF)); os.kill(d["pid"], 0); print("running, pid", d["pid"])
    except Exception: print("not running")

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "help"
    c = {"-h": "help", "--help": "help"}.get(c, c)
    {"install": cmd_install, "shell": cmd_shell, "display": cmd_display, "start": cmd_start, "stop": cmd_stop, "status": cmd_status, "trial": cmd_go, "go": cmd_go, "repair": cmd_go, "setup": cmd_setup}.get(c, lambda a: print(__doc__))(sys.argv[2:])
