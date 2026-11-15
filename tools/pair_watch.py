import re, subprocess, time
S="e3fa9e60"
def adb(*a): return subprocess.run(["adb","-s",S,*a],capture_output=True,text=True)
def log(m):
    line=f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True); open("pair_watch.log","a").write(line+"\n")
adb("logcat","-c")
log("看门狗3：已清空日志缓冲，等待配对服务")
p=subprocess.Popen(["adb","-s",S,"logcat","-v","brief"],stdout=subprocess.PIPE,text=True,bufsize=1)
buf=[]; code=None; armed=False
for line in p.stdout:
    buf.append(line.rstrip())
    m=re.search(r"updateUIPairCode:\s*(\d{6})", line)
    if m: code=m.group(1)
    m=re.search(r"Pairing server started on port (\d+)", line)
    if m and code and not armed:
        armed=True; port=m.group(1)
        log(f"服务启动 码={code} 端口={port}")
        # a Service, not an Activity: launching the UI dismisses the pairing
        # dialog and kills the server we are trying to talk to
        adb("shell","am","start-foreground-service","-n","com.phoneagent.app/.PairingService","--es","pair",f"{code}@127.0.0.1:{port}")
        time.sleep(9)
        out=adb("logcat","-d").stdout
        for l in out.splitlines():
            if any(k in l for k in ("Pairing","AdbDebugging","adb-tls","SELinux","avc")):
                log("  "+l[-150:])
        p.kill(); break
