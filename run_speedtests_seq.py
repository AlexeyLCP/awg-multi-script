import os
import sys
import paramiko
import time

# Portable SSH key path: check env var first, then platform-specific defaults
if os.environ.get("SSH_KEY_PATH"):
    ssh_key_path = os.environ["SSH_KEY_PATH"]
elif os.name == 'nt':
    ssh_key_path = os.path.expanduser(os.path.join(os.environ.get("USERPROFILE", "~"), ".ssh", "id_ed25519"))
else:
    ssh_key_path = os.path.expanduser("~/.ssh/id_ed25519")
test_url = "http://mirror.yandex.ru/debian/ls-lR.gz"

# Server definitions
servers = [
    {"name": "Proxy Server (Direct)", "ip": "158.160.231.158", "user": "lcp", "auth": "key", "cmd": f"curl -o /dev/null -s -w '%{{speed_download}}' {test_url}"},
    {"name": "Proxy Server (awg-main)", "ip": "158.160.231.158", "user": "lcp", "auth": "key", "cmd": f"sudo curl --interface awg-main -o /dev/null -s -w '%{{speed_download}}' {test_url}"},
    {"name": "Proxy Server (awg-backup)", "ip": "158.160.231.158", "user": "lcp", "auth": "key", "cmd": f"sudo curl --interface awg-backup -o /dev/null -s -w '%{{speed_download}}' {test_url}"},
    {"name": "dns.idoctor.mom (Gateway 1)", "ip": "dns.idoctor.mom", "user": "user1", "auth": "key", "cmd": f"sudo curl -o /dev/null -s -w '%{{speed_download}}' {test_url}"},
    {"name": "love.idoctor.mom (Gateway 2)", "ip": "love.idoctor.mom", "user": "root", "auth": "key", "cmd": f"curl -o /dev/null -s -w '%{{speed_download}}' {test_url}"}
]

results = []

def format_speed(bytes_sec_str):
    try:
        bytes_sec = float(bytes_sec_str)
        mbs = bytes_sec / (1024 * 1024)
        mbps = (bytes_sec * 8) / 1000000
        return f"{mbs:.2f} MB/s ({mbps:.2f} Mbps)"
    except Exception:
        return f"Parse Error (raw: '{bytes_sec_str}')"

def test_server(s):
    ssh = paramiko.SSHClient()
    # Load known_hosts if available; fall back to AutoAddPolicy with warning
    known_hosts = os.path.expanduser("~/.ssh/known_hosts")
    if os.path.exists(known_hosts):
        ssh.load_host_keys(known_hosts)
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    name = s["name"]
    ip = s["ip"]
    user = s["user"]
    cmd = s["cmd"]
    
    start_time = time.time()
    try:
        if s["auth"] == "key":
            ssh.connect(ip, username=user, key_filename=ssh_key_path, timeout=15)
        else:
            password = s.get("pass", "")
            if not password:
                print(f"[{name}] No password configured for auth='password'")
                results.append((name, "Config Error: no password", 0.0))
                return
            ssh.connect(ip, username=user, password=password, timeout=15)
            
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode('utf-8', errors='ignore').strip()
        err = stderr.read().decode('utf-8', errors='ignore').strip()
        
        duration = time.time() - start_time
        
        if out and out != "0":
            res_str = format_speed(out)
        else:
            res_str = f"Error / Zero Speed (stderr: {err})"
            
        print(f"[{name}] {res_str} - took {duration:.1f}s")
        results.append((name, res_str, duration))
            
    except Exception as e:
        print(f"[{name}] Connection Failed: {e}")
        results.append((name, f"Connection Failed: {e}", 0.0))
    finally:
        ssh.close()

def main():
    print(f"Starting sequential speedtests across {len(servers)} servers using Yandex Mirror...")
    for s in servers:
        test_server(s)
        time.sleep(0.5)
        
    print("\n==================== SPEEDTEST RESULTS SUMMARY ====================")
    for name, res_str, duration in sorted(results, key=lambda x: x[0]):
        print(f"{name:<35} : {res_str:<25} (test time: {duration:.1f}s)")

if __name__ == "__main__":
    main()
