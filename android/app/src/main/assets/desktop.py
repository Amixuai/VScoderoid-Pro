#!/usr/bin/env python3
"""Desktop Mode (experimental): the desktop (Electron) VSCodium running in a Debian system inside this app.

Run it from the terminal of this app:

    python3 ~/.vscodroid/desktop.py setup     one time: download Debian, install the screen stack and VSCodium (needs internet)
    python3 ~/.vscodroid/desktop.py start     start the desktop and print an address to open in a browser
    python3 ~/.vscodroid/desktop.py stop      stop it
    python3 ~/.vscodroid/desktop.py status    say whether it is running
    python3 ~/.vscodroid/desktop.py shell     open a shell inside Debian
    python3 ~/.vscodroid/desktop.py repair    try other Electron flag sets if the editor window does not stay up

Options for start: --geometry=WxH (screen size, default 1280x720, for example --geometry=1080x1920 for a tall
phone), --remote (resize the desktop to the window instead of shrinking it), --no-open.

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
USE_BROWSER = False   # --browser: open the address in the phone's browser instead of the screen inside the app
RESIZE = "scale"      # how the viewer fits the screen: "scale" shrinks it to the window, "remote" resizes the desktop to the window
MIN_RAM_MB = 5500
# Checksums of root file systems that were tested on a device. A file named here must match; a newer
# release is accepted only when GitHub publishes its digest (see discover_rootfs).
KNOWN_SHA256 = {"debian-trixie-aarch64-pd-v4.29.0.tar.xz": "3834a11cbc6496935760bdc20cca7e2c25724d0cd8f5e4926da8fd5ca1857918"}

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

SESSION_WORDS = ("Xtigervnc", "websockify", "openbox", "start-desktop.sh", "/usr/share/codium/")

def kill_session_processes():
    """End every process of a desktop session, not only the group the session started in. A server left
    over from an earlier start keeps port 5901 and the old password, so the next start would show a
    viewer that cannot log in. Returns how many it found."""
    found = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == os.getpid(): continue
        try: cl = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError: continue
        if "desktop.py" in cl or "desktop_poc" in cl: continue
        if any(w in cl for w in SESSION_WORDS): found.append(int(d))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in found:
            try: os.kill(pid, sig)
            except OSError: pass
        if sig == signal.SIGTERM: time.sleep(1.5)
    return len(found)

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
        emit("DESK-ROOTFS", "FAIL", "no Debian system yet: run  python3 ~/.vscodroid/desktop.py setup  first (and wait for it to finish)")
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
        open(arc + ".name", "w").write(label if not os.environ.get("DESKTOP_ROOTFS_URL") else "")
        emit("DESK-DOWNLOAD", "PASS", f"{os.path.getsize(arc) >> 20} MB from {used}")
    else:
        try: strip = open(arc + ".strip").read() == "1"
        except OSError: pass
    sha = hashlib.sha256(open(arc, "rb").read()).hexdigest()
    try: known = KNOWN_SHA256.get(open(arc + ".name").read().strip(), "")
    except OSError: known = ""
    if not PINNED_SHA256 and (known or sha in KNOWN_SHA256.values()): PINNED_SHA256 = known or sha
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
# Written on every start: the address printed for this session carries this session's password, so a file
# kept from an earlier start would reject it ("Failed to connect to server" in the viewer).
echo "$VNC_PW" | vncpasswd -f > "$HOME/.vnc/passwd"
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
    left = kill_session_processes()
    if left: emit("DESK-CLEAN", "WARN", f"{left} processes of an earlier session were still running and were ended")
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

def vnc_ready(timeout=20):
    """True when the VNC server answers with its RFB banner on the local port."""
    import socket
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", 5901), timeout=3) as c:
                if c.recv(12).startswith(b"RFB "): return True
        except OSError: pass
        time.sleep(2)
    return False

def web_ready():
    """True when the noVNC page itself is served on the local port."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:6080/vnc.html", timeout=5) as r: return r.status == 200
    except Exception: return False

def ws_probe(timeout=10):
    """Do what the noVNC page does: open a WebSocket to the viewer's port and read the first frame, which
    websockify relays from the VNC server. Returns (ok, detail)."""
    import socket, base64
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:6080\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n"
           "Origin: http://127.0.0.1:6080\r\n\r\n").encode()
    try:
        with socket.create_connection(("127.0.0.1", 6080), timeout=timeout) as c:
            c.settimeout(timeout); c.sendall(req); data = b""
            while b"\r\n\r\n" not in data:
                chunk = c.recv(4096)
                if not chunk: break
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            status = head.split(b"\r\n")[0].decode(errors="replace")
            if b" 101 " not in (status.encode() + b" "): return False, f"the WebSocket upgrade was refused: {status}"
            while len(rest) < 2:
                chunk = c.recv(4096)
                if not chunk: return False, "the connection closed right after the upgrade (the VNC server was not reached)"
                rest += chunk
            n = rest[1] & 0x7F; payload = rest[2:2 + n]
            while len(payload) < min(n, 12):
                chunk = c.recv(4096)
                if not chunk: break
                payload += chunk
            return (True, "WebSocket upgrade works and the VNC banner came through: " + payload[:12].decode(errors="replace").strip()) if payload.startswith(b"RFB ") else (False, f"the upgrade worked but the first frame was not a VNC banner: {payload[:20]!r}")
    except OSError as e:
        return False, f"could not open the WebSocket: {e}"

# --- A real VNC login, over the same WebSocket the viewer page uses -------------------------------------
_IP = [58,50,42,34,26,18,10,2,60,52,44,36,28,20,12,4,62,54,46,38,30,22,14,6,64,56,48,40,32,24,16,8,57,49,41,33,25,17,9,1,59,51,43,35,27,19,11,3,61,53,45,37,29,21,13,5,63,55,47,39,31,23,15,7]
_FP = [40,8,48,16,56,24,64,32,39,7,47,15,55,23,63,31,38,6,46,14,54,22,62,30,37,5,45,13,53,21,61,29,36,4,44,12,52,20,60,28,35,3,43,11,51,19,59,27,34,2,42,10,50,18,58,26,33,1,41,9,49,17,57,25]
_E = [32,1,2,3,4,5,4,5,6,7,8,9,8,9,10,11,12,13,12,13,14,15,16,17,16,17,18,19,20,21,20,21,22,23,24,25,24,25,26,27,28,29,28,29,30,31,32,1]
_P = [16,7,20,21,29,12,28,17,1,15,23,26,5,18,31,10,2,8,24,14,32,27,3,9,19,13,30,6,22,11,4,25]
_PC1 = [57,49,41,33,25,17,9,1,58,50,42,34,26,18,10,2,59,51,43,35,27,19,11,3,60,52,44,36,63,55,47,39,31,23,15,7,62,54,46,38,30,22,14,6,61,53,45,37,29,21,13,5,28,20,12,4]
_PC2 = [14,17,11,24,1,5,3,28,15,6,21,10,23,19,12,4,26,8,16,7,27,20,13,2,41,52,31,37,47,55,30,40,51,45,33,48,44,49,39,56,34,53,46,42,50,36,29,32]
_SH = [1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1]
_S = [
 [14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7,0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8,4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0,15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13],
 [15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10,3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5,0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15,13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9],
 [10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8,13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1,13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7,1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12],
 [7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15,13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9,10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4,3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14],
 [2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9,14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6,4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14,11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3],
 [12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11,10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8,9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6,4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13],
 [4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1,13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6,1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2,6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12],
 [13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7,1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2,7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8,2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]]

def _bits(b): return [(byte >> (7 - i)) & 1 for byte in b for i in range(8)]
def _bytes(bits): return bytes(sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8))

def des_encrypt_block(key8, block8):
    """Plain DES on one 8-byte block (needed for VNC password login; the standard library has no DES)."""
    k = _bits(key8); k = [k[i - 1] for i in _PC1]; c, d = k[:28], k[28:]; subkeys = []
    for sh in _SH:
        c, d = c[sh:] + c[:sh], d[sh:] + d[:sh]; cd = c + d; subkeys.append([cd[i - 1] for i in _PC2])
    m = _bits(block8); m = [m[i - 1] for i in _IP]; l, r = m[:32], m[32:]
    for sk in subkeys:
        er = [r[i - 1] for i in _E]; x = [a ^ b for a, b in zip(er, sk)]; out = []
        for n in range(8):
            blk = x[n * 6:n * 6 + 6]; row = (blk[0] << 1) | blk[5]; col = (blk[1] << 3) | (blk[2] << 2) | (blk[3] << 1) | blk[4]
            v = _S[n][row * 16 + col]; out += [(v >> 3) & 1, (v >> 2) & 1, (v >> 1) & 1, v & 1]
        f = [out[i - 1] for i in _P]; l, r = r, [a ^ b for a, b in zip(l, f)]
    pre = r + l
    return _bytes([pre[i - 1] for i in _FP])

def vnc_response(password, challenge):
    """The 16-byte answer to a VNC password challenge: DES with the password's bytes bit-reversed as the key."""
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in (password.encode()[:8].ljust(8, b"\0")))
    return des_encrypt_block(key, challenge[:8]) + des_encrypt_block(key, challenge[8:16])

class _WS:
    def __init__(self, sock, leftover): self.s = sock; self.raw = leftover; self.buf = b""
    def _raw(self, n):
        while len(self.raw) < n:
            chunk = self.s.recv(4096)
            if not chunk: raise EOFError("the connection closed")
            self.raw += chunk
        out, self.raw = self.raw[:n], self.raw[n:]; return out
    def read(self, n):
        import struct
        while len(self.buf) < n:
            h = self._raw(2); op = h[0] & 0x0F; ln = h[1] & 0x7F
            if ln == 126: ln = struct.unpack(">H", self._raw(2))[0]
            elif ln == 127: ln = struct.unpack(">Q", self._raw(8))[0]
            data = self._raw(ln)
            if op == 8: raise EOFError("the server closed the WebSocket")
            if op in (0, 1, 2): self.buf += data
        out, self.buf = self.buf[:n], self.buf[n:]; return out
    def send(self, data):
        import struct
        mask = os.urandom(4); n = len(data)
        hdr = bytes([0x82]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)) + mask
        self.s.sendall(hdr + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def vnc_login(password, timeout=20):
    """Log in the way the viewer page does, over a WebSocket, and wait for the first screen update.
    Returns (ok, detail); detail names the step that failed."""
    import socket, base64, struct
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:6080\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n"
           "Origin: http://127.0.0.1:6080\r\n\r\n").encode()
    step = "connecting"
    try:
        with socket.create_connection(("127.0.0.1", 6080), timeout=timeout) as c:
            c.settimeout(timeout); c.sendall(req); data = b""
            while b"\r\n\r\n" not in data:
                chunk = c.recv(4096)
                if not chunk: return False, "the connection closed during the WebSocket upgrade"
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            if b" 101 " not in head.split(b"\r\n")[0] + b" ": return False, "WebSocket upgrade refused: " + head.split(b"\r\n")[0].decode(errors="replace")
            ws = _WS(c, rest)
            step = "reading the server's version"; banner = ws.read(12)
            if not banner.startswith(b"RFB "): return False, f"not a VNC server: {banner!r}"
            ws.send(b"RFB 003.008\n")
            step = "reading the security types"; n = ws.read(1)[0]
            if n == 0:
                ln = struct.unpack(">I", ws.read(4))[0]; return False, "the server refused the connection: " + ws.read(ln).decode(errors="replace")
            types = list(ws.read(n))
            if 2 not in types: return False, f"the server offers security types {types}, not a password login"
            ws.send(bytes([2]))
            step = "waiting for the password challenge"; chal = ws.read(16)
            ws.send(vnc_response(password, chal))
            step = "waiting for the login result"
            if struct.unpack(">I", ws.read(4))[0] != 0: return False, "the server refused the password"
            ws.send(b"\x01")
            step = "reading the screen description"; init = ws.read(24)
            w, h = struct.unpack(">HH", init[:4]); nl = struct.unpack(">I", init[20:24])[0]; name = ws.read(nl).decode(errors="replace")
            ws.send(struct.pack(">BBHHHH", 3, 0, 0, 0, min(w, 64), min(h, 64)))
            step = "waiting for the first screen update"; kind = ws.read(1)[0]
            if kind != 0: return False, f"unexpected first message {kind}"
            return True, f"logged in; screen {w}x{h} named {name!r}; the first screen update arrived"
    except (OSError, EOFError) as e:
        return False, f"stopped while {step}: {e}"

def mem_available_mb():
    try:
        for l in open("/proc/meminfo"):
            if l.startswith("MemAvailable:"): return int(l.split()[1]) // 1024
    except OSError: pass
    return 0

def rss_mb(word="codium"):
    pages = 0
    for d in os.listdir("/proc"):
        if not d.isdigit(): continue
        try:
            cl = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
            if word in cl and "desktop" not in cl and "--kill-on-exit" not in cl: pages += int(open(f"/proc/{d}/statm").read().split()[1])
        except OSError: continue
    return pages * 4096 // (1 << 20)

def open_in_browser(url):
    """Hand the address to the phone's browser through the app's own xdg-open, when it is there."""
    opener = shutil.which("xdg-open")
    if not opener: return False
    try: return subprocess.run([opener, url], capture_output=True, timeout=20).returncode == 0
    except Exception: return False

def show_url(pw):
    v, w = vnc_ready(), web_ready()
    emit("DESK-VNC", "PASS" if v else "FAIL", "the VNC server answers on port 5901" if v else "the VNC server does not answer on port 5901: " + tail(os.path.join(ROOT, "tmp", "xvnc.log"), 4))
    emit("DESK-WEB", "PASS" if w else "FAIL", "the viewer page is served on port 6080" if w else "the viewer page is not served on port 6080: " + tail(os.path.join(ROOT, "tmp", "websockify.log"), 4))
    ok, why = ws_probe()
    emit("DESK-WS", "PASS" if ok else "FAIL", why if ok else why + " | " + tail(os.path.join(ROOT, "tmp", "websockify.log"), 5))
    ok2, why2 = vnc_login(pw)
    emit("DESK-LOGIN", "PASS" if ok2 else "FAIL", why2 if ok2 else why2 + " | " + tail(os.path.join(ROOT, "tmp", "xvnc.log"), 5))
    # "localhost" is not the address the in-app screen takes, so --browser reaches the browser.
    url = f"http://{'localhost' if USE_BROWSER else '127.0.0.1'}:6080/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&resize={RESIZE}&password={pw}"
    emit("DESK-URL", "PASS", url)
    opened = "--no-open" not in sys.argv and open_in_browser(url)
    print("\nThe desktop is running." + (" It was opened in your browser." if opened else " Open this address in the phone's browser:"), flush=True)
    print(f"  {url}\n(stop it later with: python3 ~/.vscodroid/desktop.py stop)\n", flush=True)

def apply_options(args):
    """Options for one run of start/repair: --geometry=WxH, --remote, --scale. Anything else is ignored."""
    global GEOMETRY, RESIZE, USE_BROWSER
    for a in args:
        if a.startswith("--geometry="):
            g = a.split("=", 1)[1]
            if re.fullmatch(r"\d{3,4}x\d{3,4}", g): GEOMETRY = g
            else: print("ignored --geometry: use WIDTHxHEIGHT, for example --geometry=1080x1920", flush=True)
        elif a == "--browser": USE_BROWSER = True
        elif a == "--remote": RESIZE = "remote"
        elif a == "--scale": RESIZE = "scale"

def cmd_go(args):
    """One command: try the flag sets with a strict check, keep the first stable one, leave the desktop running."""
    apply_options(args)
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

def check_memory(args):
    free = mem_available_mb()
    if free and free < 1000 and "--force" not in args:
        emit("DESK-MEMORY", "FAIL", f"only {free} MB of memory is free; close other apps first (or add --force to try anyway)"); return False
    if free and free < 1800: emit("DESK-MEMORY", "WARN", f"{free} MB of memory is free; the desktop needs about 2 GB, so close other apps if it is slow")
    return True

def cmd_start(args):
    apply_options(args)
    if not check_memory(args): return
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
    left = kill_session_processes()
    os.remove(PIDF); emit("DESK-STOP", "PASS", "session stopped" + (f" ({left} leftover processes ended)" if left else ""))

def cmd_status(args):
    try: d = json.load(open(PIDF)); os.kill(d["pid"], 0); print(f"running, pid {d['pid']}, the editor uses about {rss_mb()} MB of memory, {mem_available_mb()} MB is free")
    except Exception: print("not running")

def acquire_lock():
    """One mutating command at a time: two at once (a setup in one terminal, a start in another) leave a
    half-unpacked Debian behind, which is how a system ends up with no /usr/bin/env."""
    import fcntl
    os.makedirs(BASE, exist_ok=True)
    f = open(os.path.join(BASE, "desktop.lock"), "w")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another desktop.py command is still running. Wait for it to finish, then run this again.", flush=True); sys.exit(1)
    return f

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "help"
    LOCK = acquire_lock() if c in ("setup", "install", "display", "start", "go", "trial", "repair", "stop") else None
    c = {"-h": "help", "--help": "help"}.get(c, c)
    {"install": cmd_install, "shell": cmd_shell, "display": cmd_display, "start": cmd_start, "stop": cmd_stop, "status": cmd_status, "trial": cmd_go, "go": cmd_go, "repair": cmd_go, "setup": cmd_setup}.get(c, lambda a: print(__doc__))(sys.argv[2:])
