import os
import sys
import json
import time
import socket
import threading
import re
import shlex
import secrets
import contextlib
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
import paramiko

# Auth token for dashboard API (auto-generated on first run, stored in config)
DASHBOARD_AUTH_TOKEN = None

# Local SSH Key (portable: env var > platform default)
if os.environ.get("SSH_KEY_PATH"):
    SSH_KEY_PATH = os.environ["SSH_KEY_PATH"]
elif os.name == 'nt':
    SSH_KEY_PATH = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), ".ssh", "id_ed25519")
else:
    SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nodes_config.json")

DEFAULT_SERVERS = [
    {
        "name": "Proxy Server",
        "ip": "158.160.231.158",
        "role": "proxy",
        "user": "lcp",
        "auth": "key",
        "country": "RU",
        "services": ["vpn-route-monitor", "mtproxymax"],
        "group": "Proxy"
    },
    {
        "name": "Gateway 1 (GW1)",
        "ip": "dns.idoctor.mom",
        "role": "gateway",
        "user": "user1",
        "auth": "key",
        "country": "RU",
        "services": ["awg-xray", "AdGuardHome"],
        "group": "1(вход)"
    },
    {
        "name": "Gateway 2 (GW2)",
        "ip": "love.idoctor.mom",
        "role": "gateway",
        "user": "root",
        "auth": "key",
        "country": "RU",
        "services": ["awg-xray"],
        "group": "1(вход)"
    },
    {
        "name": "Exit Node 1 (NL-1)",
        "ip": "144.31.224.212",
        "role": "exit",
        "country": "NL",
        "exit_slug": "n1",
        "group": "выход"
    },
    {
        "name": "Exit Node 2 (NL-2)",
        "ip": "144.31.157.106",
        "role": "exit",
        "country": "NL",
        "exit_slug": "n2",
        "group": "выход"
    },
    {
        "name": "Exit Node 3 (DE)",
        "ip": "150.241.99.63",
        "role": "exit",
        "country": "DE",
        "exit_slug": "de",
        "group": "выход"
    },
    {
        "name": "Exit Node 4 (FI)",
        "ip": "109.206.243.202",
        "role": "exit",
        "country": "FI",
        "exit_slug": "n4",
        "group": "выход"
    },
    {
        "name": "Exit Node 5 (PL-GCP)",
        "ip": "34.158.233.138",
        "role": "exit",
        "country": "PL",
        "exit_slug": "plgcp",
        "group": "выход"
    },
    {
        "name": "Exit Node 6 (FI-GCP)",
        "ip": "34.88.71.12",
        "role": "exit",
        "country": "FI",
        "exit_slug": "figcp",
        "group": "выход"
    }
]

def load_config():
    default_proxy = {
        "active_daemon": "mtproxymax",
        "port": 443,
        "secret": "83b231c9ccf32ef09f48c8f63765ab4f",
        "fake_tls_domain": "disk.yandex.ru",
        "users": [
            {"name": "default", "secret": "83b231c9ccf32ef09f48c8f63765ab4f"},
            {"name": "ed", "secret": "5fcac3e493283eb667fa89c06bb1509a"},
            {"name": "Ya", "secret": "fc04d70b805fadbc22885e4f82e19b01"},
            {"name": "TEALHR", "secret": "b3ce0cad8f8e160a3d3307527221568b"},
            {"name": "UB", "secret": "9381450e03feb99b2d7dd999f92a93fe"},
            {"name": "1", "secret": "325835b278c08abb79efa977fef52366"},
            {"name": "2", "secret": "cc194282097121182431dba95d38b5fa"},
            {"name": "Katya", "secret": "72988119be81f46ffcb597fc89b7f908"},
            {"name": "USEBUS", "secret": "dbec7f505d73c6cb1ce147452b9b5209"},
            {"name": "ginger", "secret": "86e9dcea25409a514abb292104976366"}
        ],
        "failover_gateways": [
            "dns.idoctor.mom",
            "love.idoctor.mom"
        ]
    }
    
    if not os.path.exists(CONFIG_FILE):
        config = {"nodes": DEFAULT_SERVERS, "chains": [], "proxy_config": default_proxy}
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error creating default config: {e}")
        return config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            
        modified = False
        if not isinstance(config.get("nodes"), list):
            config["nodes"] = DEFAULT_SERVERS
            modified = True
        if not isinstance(config.get("chains"), list):
            config["chains"] = []
            modified = True
        if not isinstance(config.get("proxy_config"), dict):
            config["proxy_config"] = default_proxy
            modified = True
            
        if modified:
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"Error saving updated config: {e}")
        return config
    except Exception as e:
        print(f"Error loading config: {e}")
        return {"nodes": DEFAULT_SERVERS, "chains": [], "proxy_config": default_proxy}

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving config: {e}")

# Global dynamic nodes/chains
config_state = load_config()
SERVERS = config_state.get("nodes", DEFAULT_SERVERS)
CHAINS = config_state.get("chains", [])
config_lock = threading.Lock()

def reload_servers():
    global SERVERS, CHAINS, config_state
    with config_lock:
        config_state = load_config()
        SERVERS = config_state.get("nodes", DEFAULT_SERVERS)
        CHAINS = config_state.get("chains", [])
        # Ensure auth token exists
        global DASHBOARD_AUTH_TOKEN
        if config_state.get("dashboard_auth_token"):
            DASHBOARD_AUTH_TOKEN = config_state["dashboard_auth_token"]
        else:
            DASHBOARD_AUTH_TOKEN = secrets.token_hex(16)
            config_state["dashboard_auth_token"] = DASHBOARD_AUTH_TOKEN
            save_config(config_state)


def _check_auth(handler):
    """Check Bearer token or query param ?token=..."""
    if not DASHBOARD_AUTH_TOKEN:
        return True
    auth = handler.headers.get('Authorization', '')
    if auth.startswith('Bearer ') and auth[7:] == DASHBOARD_AUTH_TOKEN:
        return True
    # Also allow ?token=... query param for browser/SSE
    parsed = urllib.parse.urlparse(handler.path)
    qs = urllib.parse.parse_qs(parsed.query)
    if qs.get('token', [None])[0] == DASHBOARD_AUTH_TOKEN:
        return True
    return False


def _send_unauthorized(handler):
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(b'{"error":"unauthorized"}')


def _validate_proxy_config(data):
    """Validate and sanitize proxy_config POST body."""
    if not isinstance(data, dict):
        return None
    clean = {}
    clean["active_daemon"] = str(data.get("active_daemon", "mtproxymax"))[:20]
    port = data.get("port", 443)
    try:
        clean["port"] = int(port)
        if not (1 <= clean["port"] <= 65535):
            clean["port"] = 443
    except (ValueError, TypeError):
        clean["port"] = 443
    domain = str(data.get("fake_tls_domain", "disk.yandex.ru"))
    # Only allow valid domain chars
    if not re.match(r'^[a-zA-Z0-9._-]+$', domain):
        domain = "disk.yandex.ru"
    clean["fake_tls_domain"] = domain
    clean["failover_gateways"] = [str(ip) for ip in data.get("failover_gateways", []) if isinstance(ip, str)][:20]
    users = []
    for u in data.get("users", []):
        if not isinstance(u, dict):
            continue
        name = str(u.get("name", ""))[:32]
        secret = str(u.get("secret", ""))[:64]
        if not re.match(r'^[a-zA-Z0-9_]+$', name):
            continue
        if not re.match(r'^[a-fA-F0-9]+$', secret):
            continue
        users.append({"name": name, "secret": secret})
    clean["users"] = users
    return clean


def _toml_escape(s):
    """Escape a string for safe inclusion in TOML double-quoted values."""
    s = str(s)
    s = s.replace('\\', '\\\\')
    s = s.replace('"', '\\"')
    s = s.replace('\n', '\\n')
    s = s.replace('\r', '\\r')
    s = s.replace('\t', '\\t')
    return s

def get_dashboard_pubkey():
    pub_key_path = SSH_KEY_PATH + ".pub"
    if os.path.exists(pub_key_path):
        with open(pub_key_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    if os.path.exists(SSH_KEY_PATH):
        try:
            key = paramiko.Ed25519Key(filename=SSH_KEY_PATH)
            return f"ssh-ed25519 {key.get_base64()}"
        except Exception:
            try:
                key = paramiko.RSAKey(filename=SSH_KEY_PATH)
                return f"ssh-rsa {key.get_base64()}"
            except Exception:
                pass
    try:
        os.makedirs(os.path.dirname(SSH_KEY_PATH), exist_ok=True)
        key = paramiko.Ed25519Key.generate()
        key.write_private_key_file(SSH_KEY_PATH)
        pub_key = f"ssh-ed25519 {key.get_base64()}"
        with open(pub_key_path, "w", encoding="utf-8") as f:
            f.write(pub_key)
        return pub_key
    except Exception as e:
        print(f"Error generating keys: {e}")
        return ""

# Thread-safe global state
server_status_cache = {}
active_gateway_cache = None
active_exits_cache = []
cache_lock = threading.Lock()

def get_ssh_client(srv):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if srv["auth"] == "key":
        ssh.connect(srv["ip"], username=srv["user"], key_filename=SSH_KEY_PATH, timeout=5)
    else:
        ssh.connect(srv["ip"], username=srv["user"], password=srv["pass"], timeout=5)
    return ssh

def execute_remote(ssh, cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    return stdout.read().decode('utf-8', errors='ignore').strip()

def get_active_proxy_gateway(config):
    proxy_node = next((n for n in config.get("nodes", []) if n.get("role") == "proxy"), None)
    if not proxy_node:
        return None
    ssh = None
    try:
        ssh = get_ssh_client(proxy_node)
        stdin, stdout, stderr = ssh.exec_command("ip route show 91.108.4.0/22")
        route_info = stdout.read().decode('utf-8').strip()

        parts = route_info.split()
        if "dev" in parts:
            dev_idx = parts.index("dev")
            if dev_idx + 1 < len(parts):
                iface = parts[dev_idx + 1]

                # Check legacy fallback
                if iface == "awg-main":
                    gw = next((n for n in config.get("nodes", []) if "GW1" in n.get("name", "")), None)
                    if gw: return {"name": gw["name"], "ip": gw["ip"]}
                elif iface == "awg-backup":
                    gw = next((n for n in config.get("nodes", []) if "GW2" in n.get("name", "")), None)
                    if gw: return {"name": gw["name"], "ip": gw["ip"]}

                # Dynamic matching
                if iface.startswith("awg-failover-"):
                    slug = iface[len("awg-failover-"):]
                    for node in config.get("nodes", []):
                        if node.get("role") == "gateway" or "вход" in node.get("group", "").lower():
                            node_slug = re.sub(r'[^a-zA-Z0-9_]', '_', node.get('name', '')).lower()[:10]
                            if node_slug == slug:
                                return {"name": node["name"], "ip": node["ip"]}
    except Exception as e:
        print(f"Error getting active proxy gateway: {e}")
    finally:
        if ssh is not None:
            ssh.close()
    return None

def get_proxy_monitor_logs(config):
    proxy_node = next((n for n in config.get("nodes", []) if n.get("role") == "proxy"), None)
    if not proxy_node:
        return "Proxy node not found in config."
    ssh = None
    try:
        ssh = get_ssh_client(proxy_node)
        # Use journalctl to get route monitor logs
        stdin, stdout, stderr = ssh.exec_command("sudo journalctl -u vpn-route-monitor.service -n 40 --no-pager")
        logs = stdout.read().decode('utf-8', errors='ignore')
        return logs
    except Exception as e:
        return f"Error fetching logs: {e}"
    finally:
        if ssh is not None:
            ssh.close()

def is_handshake_recent(handshake_str):
    if not handshake_str or handshake_str == "N/A":
        return False
    hs_lower = handshake_str.lower()
    if any(unit in hs_lower for unit in ["day", "hour", "week", "year", "month"]):
        return False
    minute_match = re.search(r'(\d+)\s*(?:min|minute)', hs_lower)
    if minute_match:
        minutes = int(minute_match.group(1))
        if minutes >= 5:
            return False
    return True

def get_active_exit_interfaces(gw_srv):
    ssh = None
    try:
        ssh = get_ssh_client(gw_srv)
        stdin, stdout, stderr = ssh.exec_command("ip route show table 202")
        route_info = stdout.read().decode('utf-8', errors='ignore').strip()

        interfaces = re.findall(r'dev\s+(awg-exit-\S+)', route_info)
        return list(set(interfaces))
    except Exception as e:
        print(f"Error getting active exit interfaces on {gw_srv.get('name')}: {e}")
        return []
    finally:
        if ssh is not None:
            ssh.close()

def collect_server_status(srv):
    name = srv.get("name", "unknown")
    ip = srv.get("ip", "")
    role = srv.get("role", "unknown")
    
    status = {
        "name": name,
        "ip": ip,
        "role": role,
        "country": srv.get("country", "RU"),
        "group": srv.get("group", "1(вход)"),
        "online": False,
        "cpu": "N/A",
        "ram": "N/A",
        "uptime": "N/A",
        "services": {},
        "vpn": {},
        "error": None
    }
    
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(1.5)
        try:
            s.connect((ip, 22))
            status["online"] = True
        except Exception as e:
            status["online"] = False
            status["error"] = f"Port 22 unreachable: {e}"
            return status

    ssh = None
    try:
        ssh = get_ssh_client(srv)

        vmstat_out = execute_remote(ssh, "vmstat 1 2")
        try:
            last_line = vmstat_out.splitlines()[-1].split()
            idle = float(last_line[14])
            status["cpu"] = f"{100.0 - idle:.1f}%"
        except Exception:
            status["cpu"] = "Error"

        ram_cmd = "free -m | awk 'NR==2{printf \"%s/%s MB (%.0f%%)\", $3,$2,$3*100/$2}'"
        status["ram"] = execute_remote(ssh, ram_cmd)

        status["uptime"] = execute_remote(ssh, "uptime -p")

        if "services" in srv:
            for svc in srv["services"]:
                if not re.match(r'^[a-zA-Z0-9@._-]+$', str(svc)):
                    status["services"][svc] = "invalid"
                    continue
                svc_status = execute_remote(ssh, f"systemctl is-active {shlex.quote(str(svc))}")
                status["services"][svc] = svc_status

        sudo_prefix = "sudo " if srv["user"] != "root" else ""
        wg_show = execute_remote(ssh, f"{sudo_prefix}awg show")
        if wg_show:
            lines = wg_show.splitlines()
            current_iface = None
            iface_info = {}
            for line in lines:
                parts = line.strip().split()
                if not parts:
                    continue
                if parts[0] == "interface:":
                    if current_iface:
                        status["vpn"][current_iface] = iface_info
                    current_iface = parts[1]
                    iface_info = {"peers": []}
                elif current_iface:
                    if parts[0] == "peer:":
                        peer_key = parts[1]
                        peer_info = {"public_key": peer_key, "handshake": "N/A", "transfer": "0"}
                        iface_info["peers"].append(peer_info)
                    elif parts[0] == "endpoint:" and iface_info["peers"]:
                        iface_info["peers"][-1]["endpoint"] = parts[1]
                    elif parts[0] == "allowed" and parts[1] == "ips:" and iface_info["peers"]:
                        iface_info["peers"][-1]["allowed_ips"] = " ".join(parts[2:])
                    elif parts[0] == "latest" and parts[1] == "handshake:" and iface_info["peers"]:
                        iface_info["peers"][-1]["handshake"] = " ".join(parts[2:])
                    elif parts[0] == "transfer:" and iface_info["peers"]:
                        iface_info["peers"][-1]["transfer"] = " ".join(parts[1:])
            if current_iface:
                status["vpn"][current_iface] = iface_info

    except Exception as ssh_err:
        status["error"] = f"SSH Collection Error: {ssh_err}"
    finally:
        if ssh is not None:
            ssh.close()

    return status

def poll_all_servers():
    global server_status_cache, active_gateway_cache, active_exits_cache
    temp_cache = {}
    threads = []
    
    config = load_config()
    reload_servers()
    
    ssh_servers = [s for s in SERVERS if s.get("role") != "exit"]
    exit_servers = [s for s in SERVERS if s.get("role") == "exit"]
    
    def worker(srv):
        res = collect_server_status(srv)
        temp_cache[srv["name"]] = res
        
    for srv in ssh_servers:
        t = threading.Thread(target=worker, args=(srv,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # 1. Determine active gateway
    active_gateway = get_active_proxy_gateway(config)
    
    # 2. Parse exit servers online status (mapping Exit Nodes sequentially to awg-exit-n1...n6)
    for idx, srv in enumerate(exit_servers, 1):
        name = srv["name"]
        ip = srv["ip"]
        role = srv["role"]
        country = srv.get("country", "NL")
        
        iface_name = f"awg-exit-n{idx}"
        is_online = False
        
        for gw_srv in ssh_servers:
            gw_name = gw_srv["name"]
            gw_data = temp_cache.get(gw_name)
            if gw_data and gw_data.get("online") and gw_data.get("vpn", {}).get(iface_name):
                peers = gw_data["vpn"][iface_name].get("peers", [])
                if peers:
                    peer = peers[0]
                    if peer.get("handshake") and is_handshake_recent(peer["handshake"]):
                        is_online = True
                        break
                        
        temp_cache[name] = {
            "name": name,
            "ip": ip,
            "role": role,
            "country": country,
            "group": srv.get("group", "выход"),
            "online": is_online,
            "cpu": "N/A",
            "ram": "N/A",
            "uptime": "N/A",
            "services": {},
            "vpn": {},
            "error": None
        }

    # 3. Determine active exit interfaces on the active gateway using Xray balancer rules
    active_exits = []
    if active_gateway:
        gw_name = active_gateway["name"]
        gw_data = temp_cache.get(gw_name)
        if gw_data and gw_data.get("online"):
            exit_handshakes = {}
            for i, srv in enumerate(exit_servers, 1):
                iface_name = f"awg-exit-n{i}"
                has_handshake = False
                if gw_data.get("vpn", {}).get(iface_name):
                    peers = gw_data["vpn"][iface_name].get("peers", [])
                    if peers:
                        peer = peers[0]
                        has_handshake = peer.get("handshake") and is_handshake_recent(peer["handshake"])
                exit_handshakes[i] = has_handshake
            
            # Xray Balancer Logic: Primary is 5 & 6, Backup is 1, 2, 3, 4
            is_primary_active = exit_handshakes.get(5) or exit_handshakes.get(6)
            if is_primary_active:
                if exit_handshakes.get(5):
                    active_exits.append("awg-exit-n5")
                if exit_handshakes.get(6):
                    active_exits.append("awg-exit-n6")
            else:
                for i in [1, 2, 3, 4]:
                    if exit_handshakes.get(i):
                        active_exits.append(f"awg-exit-n{i}")
        
    with cache_lock:
        server_status_cache = temp_cache
        active_gateway_cache = active_gateway
        active_exits_cache = active_exits

def background_poller():
    while True:
        try:
            poll_all_servers()
        except Exception as e:
            print(f"Poller error: {e}")
        time.sleep(15)

# HTML / CSS / JS UI Content
HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>iDoctor VPN — Центр управления сетью</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #030409;
            --bg-secondary: rgba(10, 11, 26, 0.45);
            --card-border: rgba(255, 255, 255, 0.05);
            --neon-cyan: #00f2fe;
            --neon-purple: #9b51e0;
            --neon-green: #10b981;
            --neon-red: #f43f5e;
            --text-main: #f3f4f6;
            --text-muted: #8a8f98;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-primary);
            color: var(--text-main);
            font-family: 'Outfit', sans-serif;
            min-height: 100vh;
            overflow-x: hidden;
            background-image: 
                linear-gradient(rgba(255, 255, 255, 0.007) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.007) 1px, transparent 1px),
                radial-gradient(circle at 10% 20%, rgba(155, 81, 224, 0.08) 0%, transparent 45%),
                radial-gradient(circle at 90% 80%, rgba(0, 242, 254, 0.08) 0%, transparent 45%);
            background-size: 32px 32px, 32px 32px, auto, auto;
        }

        header {
            padding: 20px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            background: rgba(4, 5, 14, 0.75);
            backdrop-filter: blur(20px);
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.4);
        }

        .logo-section h1 {
            font-size: 24px;
            font-weight: 800;
            background: linear-gradient(90deg, var(--neon-cyan), var(--neon-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 1px;
        }

        .logo-section p {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .controls {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .btn-refresh {
            background: linear-gradient(135deg, #00f2fe, #0072ff);
            border: none;
            color: #fff;
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            box-shadow: 0 4px 15px rgba(0, 242, 254, 0.2);
        }

        .btn-refresh:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 242, 254, 0.35);
            filter: brightness(1.08);
        }

        .btn-toggle-ips {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .btn-toggle-ips:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--neon-cyan);
        }

        /* IP & Domain Hiding CSS */
        .ip-address, .domain-name {
            transition: filter 0.3s ease;
        }

        .hide-ips .ip-address, .hide-ips .domain-name {
            filter: blur(6px);
        }

        .hide-ips .ip-address:hover, .hide-ips .domain-name:hover {
            filter: none;
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px 40px 40px 40px;
        }

        /* Topology Section */
        .topology-card {
            background: rgba(10, 12, 30, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 40px;
            backdrop-filter: blur(20px);
            position: relative;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
        }

        .topology-card h2 {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .topology-card h2::before {
            content: '';
            display: inline-block;
            width: 8px;
            height: 8px;
            background-color: var(--neon-cyan);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--neon-cyan);
        }

        .topo-container {
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: relative;
            min-height: 250px;
            padding: 0 50px;
        }

        .topo-column {
            display: flex;
            flex-direction: column;
            gap: 20px;
            z-index: 2;
        }

        @keyframes cyan-pulse {
            0% { box-shadow: 0 0 12px rgba(0, 242, 254, 0.15); border-color: rgba(0, 242, 254, 0.4); }
            50% { box-shadow: 0 0 24px rgba(0, 242, 254, 0.4); border-color: rgba(0, 242, 254, 1); }
            100% { box-shadow: 0 0 12px rgba(0, 242, 254, 0.15); border-color: rgba(0, 242, 254, 0.4); }
        }

        @keyframes green-pulse {
            0% { box-shadow: 0 0 12px rgba(16, 185, 129, 0.15); border-color: rgba(16, 185, 129, 0.4); }
            50% { box-shadow: 0 0 24px rgba(16, 185, 129, 0.4); border-color: rgba(16, 185, 129, 1); }
            100% { box-shadow: 0 0 12px rgba(16, 185, 129, 0.15); border-color: rgba(16, 185, 129, 0.4); }
        }

        .topo-node {
            background: rgba(13, 14, 33, 0.75);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 18px 24px;
            min-width: 220px;
            text-align: center;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(16px);
        }

        .topo-node:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.15);
            box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.7);
        }

        .topo-node.active {
            animation: cyan-pulse 3s infinite ease-in-out;
        }

        .topo-node.exit-active {
            animation: green-pulse 3s infinite ease-in-out;
        }

        .topo-node.exit-idle {
            border-color: rgba(255, 255, 255, 0.05);
            opacity: 0.45;
        }

        .topo-node h3 {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 6px;
        }

        .topo-node p {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: var(--text-muted);
        }

        .topo-connector {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 1;
        }

        /* Server Grid */
        .server-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 30px;
        }

        .server-card {
            background: rgba(10, 11, 26, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(24px);
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
        }

        .server-card:hover {
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.12);
            box-shadow: 0 12px 40px 0 rgba(0, 242, 254, 0.05);
        }

        .server-card.online {
            border-left: 3px solid var(--neon-green);
        }

        .server-card.offline {
            border-left: 3px solid var(--neon-red);
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 20px;
        }

        .server-title h3 {
            font-size: 18px;
            font-weight: 600;
        }

        .server-title span {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: var(--text-muted);
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }

        .status-badge.online {
            background: rgba(16, 185, 129, 0.1);
            color: var(--neon-green);
        }

        .status-badge.offline {
            background: rgba(244, 63, 94, 0.1);
            color: var(--neon-red);
        }

        .status-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
        }

        .status-badge.online .status-dot {
            background-color: var(--neon-green);
            box-shadow: 0 0 8px var(--neon-green);
            animation: pulse-green 2s infinite;
        }

        .status-badge.offline .status-dot {
            background-color: var(--neon-red);
            box-shadow: 0 0 8px var(--neon-red);
        }

        @keyframes pulse-green {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        .stats-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-bottom: 20px;
            font-size: 14px;
        }

        .stat-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px dashed rgba(255, 255, 255, 0.05);
            padding-bottom: 8px;
        }

        .stat-item .label {
            color: var(--text-muted);
        }

        .stat-item .value {
            font-family: 'JetBrains Mono', monospace;
            font-weight: 600;
        }

        .vpn-section {
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            padding-top: 16px;
        }

        .vpn-section h4 {
            font-size: 13px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
        }

        .vpn-interface {
            background: rgba(8, 9, 20, 0.5);
            border-radius: 8px;
            padding: 10px 14px;
            margin-bottom: 8px;
            border: 1px solid rgba(255, 255, 255, 0.03);
        }

        .vpn-iface-header {
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 6px;
        }

        .vpn-peer-info {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: var(--text-muted);
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .vpn-peer-info div {
            display: flex;
            justify-content: space-between;
        }

        .flag {
            display: inline-block;
            margin-left: 6px;
            font-size: 14px;
        }

        .services-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }

        .service-badge {
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        .service-badge.active {
            background: rgba(16, 185, 129, 0.1);
            border-color: rgba(16, 185, 129, 0.2);
            color: var(--neon-green);
        }
        
        .service-badge.inactive {
            background: rgba(244, 63, 94, 0.1);
            border-color: rgba(244, 63, 94, 0.2);
            color: var(--neon-red);
        }

        .actions-panel {
            margin-top: 40px;
            background: var(--bg-secondary);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
        }

        .actions-panel h2 {
            font-size: 18px;
            margin-bottom: 16px;
        }

        .actions-buttons {
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
        }

        .btn-action {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 12px 24px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s;
        }

        .btn-action:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--neon-cyan);
        }

        /* Modal styling */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(8, 9, 20, 0.85);
            backdrop-filter: blur(8px);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }

        .modal-content {
            background: #0f1126;
            border: 1px solid var(--card-border);
            border-radius: 16px;
            width: 800px;
            max-width: 90%;
            padding: 30px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.8);
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            padding-bottom: 15px;
        }

        .modal-header h3 {
            font-size: 20px;
            color: var(--neon-cyan);
        }

        .btn-close {
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 24px;
            cursor: pointer;
        }

        .terminal-output {
            background: #060710;
            border-radius: 8px;
            padding: 20px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            height: 350px;
            overflow-y: auto;
            white-space: pre-wrap;
            color: #38bdf8;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        /* New Tabs Styling */
        .tabs-nav {
            display: flex;
            gap: 12px;
            border-bottom: 1px solid var(--card-border);
            margin-bottom: 24px;
            padding-bottom: 12px;
        }

        .tab-btn {
            background: none;
            border: 1px solid transparent;
            color: var(--text-muted);
            font-family: 'Outfit', sans-serif;
            font-size: 15px;
            font-weight: 600;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
        }

        .tab-btn.active {
            color: #fff;
            background: rgba(0, 242, 254, 0.06);
            border-color: rgba(0, 242, 254, 0.25);
            box-shadow: 0 0 20px rgba(0, 242, 254, 0.1);
        }

        .tab-btn:hover:not(.active) {
            color: #fff;
            background: rgba(255, 255, 255, 0.03);
        }

        /* New Forms & Tables */
        .form-group {
            margin-bottom: 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .form-group label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .form-control {
            background: rgba(8, 9, 20, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            color: var(--text-main);
            padding: 12px 16px;
            font-family: 'Outfit', sans-serif;
            font-size: 14px;
            width: 100%;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }

        .form-control:focus {
            outline: none;
            border-color: var(--neon-cyan);
            box-shadow: 0 0 12px rgba(0, 242, 254, 0.15);
            background: rgba(8, 9, 20, 0.8);
        }

        .node-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }

        .node-table th, .node-table td {
            text-align: left;
            padding: 12px 16px;
            border-bottom: 1px solid var(--card-border);
        }

        .node-table th {
            font-weight: 600;
            color: var(--text-muted);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .node-table tr:hover {
            background: rgba(255, 255, 255, 0.02);
        }

        .btn-delete {
            background: rgba(244, 63, 94, 0.1);
            color: var(--neon-red);
            border: 1px solid rgba(244, 63, 94, 0.2);
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            transition: all 0.3s;
        }

        .btn-delete:hover {
            background: var(--neon-red);
            color: #080914;
            box-shadow: 0 0 15px rgba(244, 63, 94, 0.4);
        }

        .chain-card {
            background: rgba(18, 22, 47, 0.5);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .chain-info h3 {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 8px;
        }

        .chain-path {
            display: flex;
            align-items: center;
            gap: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: var(--text-muted);
        }

        .chain-arrow {
            color: var(--neon-cyan);
            font-weight: bold;
        }

        .btn-deploy {
            background: rgba(16, 185, 129, 0.1);
            color: var(--neon-green);
            border: 1px solid rgba(16, 185, 129, 0.2);
            padding: 8px 16px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.3s;
            margin-right: 8px;
        }

        .btn-deploy:hover {
            background: var(--neon-green);
            color: #080914;
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.4);
        }

        /* Draggable Priority Items */
        .draggable-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 8px;
        }

        .draggable-item {
            cursor: grab;
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.2s;
            font-size: 13px;
        }

        .draggable-item:hover {
            background: rgba(255, 255, 255, 0.04);
            border-color: rgba(255, 255, 255, 0.15);
        }

        .draggable-item.dragging {
            opacity: 0.4;
            background: rgba(0, 242, 254, 0.05);
            border-color: var(--neon-cyan);
            box-shadow: 0 0 10px rgba(0, 242, 254, 0.2);
        }

        .draggable-item .drag-handle {
            color: var(--text-muted);
            cursor: grab;
            font-size: 16px;
            margin-right: 12px;
            user-select: none;
        }

        .draggable-item.disabled {
            opacity: 0.5;
            background: rgba(0, 0, 0, 0.2);
            cursor: not-allowed;
        }
        
        .draggable-item.disabled .drag-handle {
            cursor: not-allowed;
        }

        /* User Secret Table */
        .user-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }

        .user-table th, .user-table td {
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 13px;
        }

        .user-table th {
            color: var(--text-muted);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }

        /* Topology Widget */
        .topo-mini-container {
            display: flex;
            align-items: center;
            justify-content: space-around;
            background: rgba(8, 9, 20, 0.5);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            position: relative;
        }

        .topo-mini-node {
            background: rgba(18, 22, 47, 0.8);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 10px 16px;
            text-align: center;
            min-width: 100px;
            z-index: 2;
            transition: all 0.3s;
        }

        .topo-mini-node.active {
            border-color: var(--neon-green);
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.25);
        }

        .topo-mini-node.standby {
            border-color: var(--neon-cyan);
            box-shadow: 0 0 12px rgba(0, 242, 254, 0.15);
        }

        .topo-mini-node.offline {
            border-color: var(--neon-red);
            opacity: 0.6;
        }

        .topo-mini-line {
            position: absolute;
            height: 2px;
            background: rgba(255, 255, 255, 0.08);
            z-index: 1;
            top: 50%;
            left: 120px;
            right: 120px;
            transform: translateY(-50%);
        }

        .topo-mini-line.active {
            background: linear-gradient(90deg, var(--neon-green), var(--neon-cyan));
            box-shadow: 0 0 8px rgba(16, 185, 129, 0.5);
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-section">
            <h1 id="header-title">iDOCTOR ЦЕНТР УПРАВЛЕНИЯ</h1>
            <p id="header-subtitle">Мониторинг инфраструктуры VPN &amp; MTProto Proxy</p>
        </div>
        <div class="controls">
            <button class="btn-lang" id="btn-lang" onclick="toggleLang()" style="background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:#f3f4f6;padding:10px 16px;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer;transition:all 0.3s;letter-spacing:1px;margin-right:6px;">EN</button>
            <button class="btn-toggle-ips" id="btn-hide-ips" onclick="toggleIps()" style="margin-right: 10px;">СКРЫТЬ IP</button>
            <button class="btn-refresh" id="btn-refresh" onclick="refreshData()">ОБНОВИТЬ</button>
        </div>
    </header>

    <div class="tabs-nav container" style="padding-bottom:0; margin-bottom: 20px;">
        <button class="tab-btn active" id="tab-btn-monitoring" onclick="switchTab('monitoring')">Мониторинг</button>
        <button class="tab-btn" id="tab-btn-nodes" onclick="switchTab('nodes')">Ноды &amp; Группы</button>
        <button class="tab-btn" id="tab-btn-chains" onclick="switchTab('chains')">Цепочки</button>
        <button class="tab-btn" id="tab-btn-configs" onclick="switchTab('configs')">Конфиги пользователей</button>
        <button class="tab-btn" id="tab-btn-proxy" onclick="switchTab('proxy')">Прокси и Отказоустойчивость</button>
    </div>

    <!-- Tab 1: Monitoring -->
    <div id="tab-monitoring" class="tab-content container">
        <!-- Topology Visualization -->
        <div class="topology-card">
            <h2 id="topology-title">ТОПОЛОГИЯ ТРАНЗИТНОЙ СЕТИ</h2>
            <div class="topo-container">
                <svg class="topo-connector" id="connector-svg"></svg>
                
                <!-- Proxy Node -->
                <div class="topo-column">
                    <div class="topo-node active" id="node-proxy">
                        <h3 id="proxy-node-label">Прокси-сервер</h3>
                        <p><span class="ip-address">158.160.231.158</span></p>
                        <p style="margin-top: 4px; color: var(--neon-cyan);" id="proxy-path-text">Активный маршрут: основной</p>
                    </div>
                </div>

                <!-- Gateway Nodes -->
                <div class="topo-column">
                    <div class="topo-node active" id="node-gw1" onclick="selectGateway('Gateway 1 (GW1)')" style="cursor: pointer; transition: all 0.3s;">
                        <h3><span class="domain-name">dns.idoctor.mom</span></h3>
                        <p><span class="ip-address">84.54.59.160</span></p>
                        <p style="margin-top: 4px; color: var(--neon-green);" id="gw1-load-text">Нагрузка: 0%</p>
                        <p style="margin-top: 2px; color: var(--text-muted); font-size: 11px;" id="gw1-users-text">Клиентов: 0 активных</p>
                    </div>
                    <div class="topo-node" id="node-gw2" onclick="selectGateway('Gateway 2 (GW2)')" style="cursor: pointer; transition: all 0.3s; opacity: 0.8;">
                        <h3><span class="domain-name">love.idoctor.mom</span></h3>
                        <p><span class="ip-address">185.173.37.215</span></p>
                        <p style="margin-top: 4px; color: var(--text-muted);" id="gw2-load-text">Backup Idle</p>
                        <p style="margin-top: 2px; color: var(--text-muted); font-size: 11px;" id="gw2-users-text">Клиентов: 0 активных</p>
                    </div>
                </div>

                <!-- Exit Nodes -->
                <div class="topo-column" style="gap: 10px;">
                    <div class="topo-node exit-idle" id="node-exit1" style="padding: 10px 20px;">
                        <h3 style="font-size: 12px;">Exit 1 (NL-1)</h3>
                        <p style="font-size: 10px;"><span class="ip-address">144.31.224.212</span></p>
                    </div>
                    <div class="topo-node exit-idle" id="node-exit2" style="padding: 10px 20px;">
                        <h3 style="font-size: 12px;">Exit 2 (NL-2)</h3>
                        <p style="font-size: 10px;"><span class="ip-address">144.31.157.106</span></p>
                    </div>
                    <div class="topo-node exit-idle" id="node-exit3" style="padding: 10px 20px;">
                        <h3 style="font-size: 12px;">Exit 3 (DE)</h3>
                        <p style="font-size: 10px;"><span class="ip-address">150.241.99.63</span></p>
                    </div>
                    <div class="topo-node exit-idle" id="node-exit4" style="padding: 10px 20px;">
                        <h3 style="font-size: 12px;">Exit 4 (FI)</h3>
                        <p style="font-size: 10px;"><span class="ip-address">109.206.243.202</span></p>
                    </div>
                    <div class="topo-node exit-active" id="node-exit5" style="padding: 10px 20px;">
                        <h3 style="font-size: 12px;">Exit 5 (PL-GCP)</h3>
                        <p style="font-size: 10px;"><span class="ip-address">34.158.233.138</span></p>
                    </div>
                    <div class="topo-node exit-active" id="node-exit6" style="padding: 10px 20px;">
                        <h3 style="font-size: 12px;">Exit 6 (FI-GCP)</h3>
                        <p style="font-size: 10px;"><span class="ip-address">34.88.71.12</span></p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Server Grid -->
        <div id="servers-grid"></div>

        <!-- Actions Panel -->
        <div class="actions-panel">
            <h2 id="actions-title">ДЕЙСТВИЯ С ИНФРАСТРУКТУРОЙ</h2>
            <div class="actions-buttons">
                <button id="btn-speedtest" class="btn-action" onclick="runAction('speedtest')">Тест скорости</button>
                <button id="btn-restart-xray" class="btn-action" onclick="runAction('restart-xray')">Перезапустить Xray (GW1)</button>
                <button id="btn-restart-failover" class="btn-action" onclick="runAction('restart-proxy-monitor')">Перезапустить Failover (Прокси)</button>
            </div>
        </div>
    </div>

    <!-- Tab 2: Nodes & Groups -->
    <div id="tab-nodes" class="tab-content container" style="display:none;">
        <div class="topology-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                <h2 id="nodes-title">СЕРВЕРНЫЕ НОДЫ И ГРУППЫ</h2>
                <button class="btn-refresh" id="btn-add-node" onclick="openAddNodeModal()">Добавить новую ноду</button>
            </div>
            
            <table class="node-table">
                <thead>
                    <tr>
                        <th id="th-node-name">Имя ноды</th>
                        <th id="th-node-ip">IP-адрес</th>
                        <th id="th-node-role">Роль в системе</th>
                        <th id="th-node-group">Категория (Группа)</th>
                        <th id="th-node-country">Страна</th>
                        <th id="th-node-delete">Удалить</th>
                    </tr>
                </thead>
                <tbody id="nodes-table-body">
                    <!-- Dynamic nodes list -->
                </tbody>
            </table>
        </div>
    </div>

    <!-- Tab 3: Chains -->
    <div id="tab-chains" class="tab-content container" style="display:none;">
        <div style="display: grid; grid-template-columns: 1fr 1.5fr; gap: 30px;">
            <!-- Chain Form -->
            <div class="topology-card">
                <h2 id="chains-create-title">СОЗДАТЬ ЦЕПОЧКУ</h2>
                <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 16px;">
                    <div class="form-group">
                        <label id="label-chain-name">Имя цепочки</label>
                        <input type="text" id="chain-name" class="form-control" placeholder="e.g. RU -> NL (Cascade)">
                    </div>
                    <div class="form-group">
                        <label id="label-chain-entrance">Входная нода (Группа 1)</label>
                        <select id="chain-entrance" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label id="label-chain-transit">Транзитные ноды (Группа 2) (опционально)</label>
                        <select id="chain-transit" class="form-control" multiple style="height: 100px;"></select>
                        <small id="small-chain-transit" style="color: var(--text-muted); font-size:11px;">Зажмите Ctrl для выбора нескольких</small>
                    </div>
                    <div class="form-group">
                        <label id="label-chain-exit">Выходная нода (Группа 3)</label>
                        <select id="chain-exit" class="form-control"></select>
                    </div>
                    <button class="btn-refresh" id="btn-create-chain" onclick="submitCreateChain()" style="width: 100%; margin-top: 10px;">Создать цепочку</button>
                </div>
            </div>
            
            <!-- Chain List -->
            <div class="topology-card">
                <h2 id="chains-list-title">НАСТРОЕННЫЕ ЦЕПОЧКИ</h2>
                <div id="chains-list-container" style="margin-top: 16px;">
                    <!-- Dynamic chains list -->
                </div>
            </div>
        </div>
    </div>

    <!-- Tab 4: Config Generator -->
    <div id="tab-configs" class="tab-content container" style="display:none;">
        <div style="display: grid; grid-template-columns: 1fr 1.5fr; gap: 30px;">
            <!-- Config Form -->
            <div class="topology-card">
                <h2 id="configs-generator-title">ГЕНЕРАТОР ПОДКЛЮЧЕНИЙ</h2>
                <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 16px;">
                    <div class="form-group">
                        <label id="label-config-target">Цель (Маршрут / Нода)</label>
                        <select id="config-target" class="form-control"></select>
                    </div>
                    <div class="form-group">
                        <label id="label-config-client">Имя пользователя</label>
                        <input type="text" id="config-client-name" class="form-control" placeholder="e.g. user_macbook">
                    </div>
                    <button class="btn-refresh" id="btn-generate-config" onclick="submitGenerateConfig()" style="width: 100%; margin-top: 10px;">Сгенерировать конфиг</button>
                </div>
            </div>
            
            <!-- Config Output -->
            <div class="topology-card" id="config-result-card" style="display: none; text-align: center;">
                <h2 id="config-result-title">ГЕНЕРАЦИЯ ЗАВЕРШЕНА</h2>
                <div style="display: flex; flex-direction: column; align-items: center; gap: 20px; margin-top: 20px;">
                    <div id="config-qrcode-container" style="background: white; padding: 15px; border-radius: 8px;">
                        <!-- Dynamic QR Code -->
                    </div>
                    <div style="width: 100%; text-align: left;">
                        <label id="label-config-output" style="font-weight: 600; color: var(--text-muted); font-size:13px; text-transform:uppercase;">Конфигурация AmneziaWG (.conf)</label>
                        <textarea id="config-text-output" class="form-control" style="font-family: 'JetBrains Mono', monospace; font-size: 12px; height: 180px; margin-top: 6px;" readonly></textarea>
                    </div>
                    <button class="btn-refresh" id="btn-download-config" onclick="downloadConfigText()">Скачать .conf файл</button>
                </div>
            </div>
        </div>
    <!-- Tab 5: Proxy & Failover -->
    <div id="tab-proxy" class="tab-content container" style="display:none; padding-bottom: 40px;">
        <div style="display: grid; grid-template-columns: 1.1fr 1.3fr; gap: 30px; align-items: start;">
            
            <!-- Left Side: Config & Failover Priority -->
            <div style="display: flex; flex-direction: column; gap: 24px;">
                <!-- Proxy config card -->
                <div class="topology-card">
                    <h2 id="proxy-config-title">НАСТРОЙКА MTPROTO ПРОКСИ</h2>
                    <div style="display: flex; flex-direction: column; gap: 14px; margin-top: 16px;">
                        <div class="form-group">
                            <label id="label-proxy-daemon">Активный демон</label>
                            <select id="proxy-daemon" class="form-control" onchange="toggleProxyWarning()">
                                <option value="mtproxymax">mtproxymax (C, Multi-User)</option>
                                <option value="mtg">mtg (Go, Single-Secret)</option>
                            </select>
                            <small id="proxy-daemon-warning" style="color: var(--neon-cyan); font-size:11px; display:none; margin-top:4px;">
                                ⚠️ Внимание: mtg v2 поддерживает только одного общего пользователя. Будет использован секрет первого пользователя в таблице.
                            </small>
                        </div>
                        <div class="form-group">
                            <label id="label-proxy-port">Порт</label>
                            <input type="number" id="proxy-port" class="form-control" value="443">
                        </div>
                        <div class="form-group">
                            <label id="label-proxy-domain">Fake TLS SNI домен</label>
                            <input type="text" id="proxy-domain" class="form-control" value="disk.yandex.ru">
                        </div>
                    </div>
                </div>

                <!-- Failover Priority card -->
                <div class="topology-card">
                    <h2 id="failover-priority-title">ПРИОРИТЕТЫ ШЛЮЗОВ</h2>
                    <p id="failover-priority-desc" style="color: var(--text-muted); font-size: 11px; margin-top: 4px; margin-bottom: 12px;">
                        Перетаскивайте шлюзы мышкой вверх/вниз для настройки порядка отказоустойчивости (Main ➔ Backup).
                    </p>
                    <div class="draggable-list" id="failover-priority-list">
                        <!-- Dynamic sortable list -->
                    </div>
                    
                    <button class="btn-refresh" id="btn-deploy-proxy" onclick="submitDeployProxyRouting()" style="width: 100%; margin-top: 20px; font-weight:700; letter-spacing:0.5px;">
                        ПРИМЕНИТЬ НАСТРОЙКИ И ДЕПЛОЙ
                    </button>
                </div>
            </div>

            <!-- Right Side: User management & Status -->
            <div style="display: flex; flex-direction: column; gap: 24px;">
                <!-- User manager card -->
                <div class="topology-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                        <h2 id="proxy-users-title">ПОЛЬЗОВАТЕЛИ</h2>
                        <button class="btn-refresh" id="btn-add-proxy-user" onclick="addNewProxyUser()" style="margin-top:0; font-size:11px; padding: 6px 12px;">Добавить</button>
                    </div>
                    <div style="overflow-x: auto; max-height: 250px;">
                        <table class="user-table">
                            <thead>
                                <tr>
                                    <th id="th-proxy-name">Имя</th>
                                    <th id="th-proxy-secret">Секрет</th>
                                    <th id="th-proxy-actions" style="width: 120px; text-align: right;">Действия</th>
                                </tr>
                            </thead>
                            <tbody id="proxy-users-table-body">
                                <!-- Dynamic user table -->
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Live Failover & Logs card -->
                <div class="topology-card">
                    <h2 id="proxy-topo-title">СОСТОЯНИЕ МАРШРУТИЗАЦИИ</h2>
                    <div class="topo-mini-container" style="margin-top: 16px;">
                        <div class="topo-mini-line active" id="topo-mini-link-line"></div>
                        <div class="topo-mini-node active" id="topo-mini-proxy">
                            <h4 style="font-size:11px; margin:0;">PROXY</h4>
                            <span style="font-size:9px; color:var(--text-muted);">158.160.231.158</span>
                        </div>
                        <div class="topo-mini-node" id="topo-mini-gate">
                            <h4 style="font-size:11px; margin:0; text-transform:uppercase;" id="topo-mini-gate-name">GW (Active)</h4>
                            <span style="font-size:9px; color:var(--text-muted);" id="topo-mini-gate-ip">Active GW IP</span>
                        </div>
                    </div>
                    
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
                        <label id="label-proxy-logs" style="font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase;">Логи vpn-route-monitor.sh</label>
                        <button class="btn-refresh" id="btn-refresh-proxy-logs" onclick="fetchRouteMonitorLogs()" style="margin-top:0; padding:2px 8px; font-size:10px; height:auto; background:rgba(255,255,255,0.03);">Обновить логи</button>
                    </div>
                    <textarea id="proxy-monitor-logs" class="form-control" style="font-family: 'JetBrains Mono', monospace; font-size: 11px; height: 120px; background: rgba(5,6,15,0.8); border: 1px solid rgba(255,255,255,0.03); color: #38bdf8; resize:none;" readonly></textarea>
                </div>
            </div>

        </div>
    </div>

    <!-- Proxy Share/QR Modal -->
    <div class="modal" id="proxy-share-modal">
        <div class="modal-content" style="max-width: 450px; text-align: center;">
            <div class="modal-header">
                <h3 id="proxy-share-title-modal">Подключение для пользователя</h3>
                <button class="btn-close" onclick="closeProxyShareModal()">&times;</button>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; gap: 16px; margin-top: 10px;">
                <div id="proxy-qrcode-container" style="background: white; padding: 12px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
                    <!-- Dynamic QR -->
                </div>
                <div style="width: 100%; text-align: left;">
                    <label id="label-proxy-share-url" style="font-weight: 600; color: var(--text-muted); font-size:11px; text-transform:uppercase;">Telegram Proxy URL</label>
                    <div style="display:flex; gap:8px; margin-top: 4px;">
                        <input type="text" id="proxy-share-url" class="form-control" style="font-family:'JetBrains Mono'; font-size:11px; padding: 8px 10px;" readonly>
                        <button class="btn-refresh" onclick="copyProxyUrlToClipboard()" style="margin-top:0; padding:0 12px; font-size:11px; white-space:nowrap;" id="btn-copy-proxy">Копировать</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Terminal Output Modal -->
    <div class="modal" id="terminal-modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 id="modal-title">Выполнение команды</h3>
                <button class="btn-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="terminal-output" id="terminal-text">Подключение...</div>
        </div>
    </div>

    <!-- Add Node Modal -->
    <div class="modal" id="add-node-modal">
        <div class="modal-content" style="max-width: 500px;">
            <div class="modal-header">
                <h3 id="add-node-title">Добавить серверную ноду</h3>
                <button class="btn-close" onclick="closeAddNodeModal()">&times;</button>
            </div>
            <div style="display: flex; flex-direction: column; gap: 12px;">
                <div class="form-group">
                    <label id="label-add-node-name">Имя ноды</label>
                    <input type="text" id="add-node-name" class="form-control" placeholder="e.g. NL-Main-Exit">
                </div>
                <div class="form-group">
                    <label id="label-add-node-ip">IP-адрес</label>
                    <input type="text" id="add-node-ip" class="form-control" placeholder="e.g. 192.168.1.100">
                </div>
                <div class="form-group">
                    <label id="label-add-node-user">SSH логин</label>
                    <input type="text" id="add-node-user" class="form-control" value="root">
                </div>
                <div class="form-group">
                    <label id="label-add-node-auth">Авторизация</label>
                    <select id="add-node-auth" class="form-control" onchange="toggleAuthFields()">
                        <option value="password" id="opt-auth-password">По паролю (установит SSH ключ автоклиента)</option>
                        <option value="key" id="opt-auth-key">По дефолтному SSH ключу</option>
                    </select>
                </div>
                <div class="form-group" id="node-password-group">
                    <label id="label-add-node-password">SSH пароль</label>
                    <input type="password" id="add-node-password" class="form-control" placeholder="Пароль">
                </div>
                <div class="form-group">
                    <label id="label-add-node-role">Роль в системе</label>
                    <select id="add-node-role" class="form-control" onchange="toggleExitSlugField()">
                        <option value="gateway" id="opt-role-gateway">1(вход) / Entrance (Gateway)</option>
                        <option value="transit" id="opt-role-transit">2(транзит) / Transit</option>
                        <option value="exit" id="opt-role-exit">3(выход) / Exit</option>
                    </select>
                </div>
                <div class="form-group" id="node-slug-group" style="display:none;">
                    <label id="label-add-node-slug">Слуг (латиница, до 6 символов)</label>
                    <input type="text" id="add-node-slug" class="form-control" placeholder="e.g. nl3">
                </div>
                <div class="form-group">
                    <label id="label-add-node-country">Страна (2-буквенный ISO-код)</label>
                    <input type="text" id="add-node-country" class="form-control" value="NL" placeholder="e.g. NL, RU, DE">
                </div>
                <button class="btn-refresh" id="btn-start-setup" onclick="submitAddNode()" style="width: 100%; margin-top: 10px;">Начать установку</button>
            </div>
        </div>
    </div>

    <script>
        const countryFlags = {
            "RU": "🇷🇺",
            "NL": "🇳🇱",
            "DE": "🇩🇪",
            "FI": "🇫🇮",
            "PL": "🇵🇱"
        };

        // ===== i18n =====
        let currentLang = localStorage.getItem('dash_lang') || 'ru';

        const TRANSLATIONS = {
            ru: {
                title: 'iDOCTOR ЦЕНТР УПРАВЛЕНИЯ',
                subtitle: 'Мониторинг инфраструктуры VPN & MTProto Proxy',
                hide_ips: 'СКРЫТЬ IP',
                show_ips: 'ПОКАЗАТЬ IP',
                refresh: 'ОБНОВИТЬ',
                topology_title: 'ТОПОЛОГИЯ ТРАНЗИТНОЙ СЕТИ',
                proxy_node: 'Прокси-сервер',
                actions_title: 'ДЕЙСТВИЯ С ИНФРАСТРУКТУРОЙ',
                speedtest: 'Тест скорости',
                restart_xray: 'Перезапустить Xray (GW1)',
                restart_failover: 'Перезапустить Failover (Прокси)',
                modal_title: 'Выполнение команды',
                connecting: 'Подключение и выполнение команды на удалённых хостах...\\n',
                active_path: 'Активный маршрут',
                path_main: 'Основной',
                path_backup: 'Резервный',
                load: 'Нагрузка',
                offline: 'Недоступен',
                group_proxy: 'Прокси-сервер',
                group_entrance: 'Входные шлюзы (Группа 1)',
                group_transit: 'Транзитные узлы (Группа 2)',
                group_exit: 'Выходные узлы (Группа 3)',
                vpn_tunnels: 'АКТИВНЫЕ VPN ТУННЕЛИ',
                online: 'ОНЛАЙН',
                offline_badge: 'ОФЛАЙН',
                cpu: 'Нагрузка CPU',
                ram: 'Использование ОЗУ',
                uptime: 'Аптайм',
                proxy_tab_title: 'Прокси и Отказоустойчивость',
                proxy_config_header: 'НАСТРОЙКА MTPROTO ПРОКСИ',
                active_daemon: 'Активный демон',
                port: 'Порт',
                fake_tls_domain: 'Fake TLS SNI домен',
                mtg_warning: '⚠️ Внимание: mtg v2 поддерживает только одного общего пользователя. Будет использован секрет первого пользователя в таблице.',
                failover_header: 'ПРИОРИТЕТЫ ШЛЮЗОВ',
                failover_desc: 'Перетаскивайте шлюзы мышкой вверх/вниз для настройки порядка отказоустойчивости (Main ➔ Backup).',
                deploy_btn: 'ПРИМЕНИТЬ НАСТРОЙКИ И ДЕПЛОЙ',
                users_header: 'ПОЛЬЗОВАТЕЛИ',
                add_user_btn: 'Добавить пользователя',
                table_name: 'Имя',
                table_secret: 'Секрет',
                table_actions: 'Действия',
                btn_share: 'Ссылка / QR',
                btn_delete: 'Удалить',
                active_gateways_header: 'СОСТОЯНИЕ МАРШРУТИЗАЦИИ',
                monitor_logs: 'Логи vpn-route-monitor.sh',
                refresh_logs: 'Обновить логи',
                proxy_share_title: 'Подключение для: ',
                copy_btn: 'Копировать',
                copied_btn: 'Скопировано!',
                gw_active: 'АКТИВЕН',
                gw_disabled: 'ОТКЛЮЧЕН',
                // Tabs
                tab_monitoring: 'Мониторинг',
                tab_nodes: 'Ноды & Группы',
                tab_chains: 'Цепочки',
                tab_configs: 'Конфиги пользователей',
                tab_proxy: 'Прокси и Отказоустойчивость',
                // Nodes & Groups tab
                nodes_title: 'СЕРВЕРНЫЕ НОДЫ И ГРУППЫ',
                btn_add_node: 'Добавить новую ноду',
                th_node_name: 'Имя ноды',
                th_node_ip: 'IP-адрес',
                th_node_role: 'Роль в системе',
                th_node_group: 'Категория (Группа)',
                th_node_country: 'Страна',
                th_node_delete: 'Удалить',
                // Chains tab
                chains_create_title: 'СОЗДАТЬ ЦЕПОЧКУ',
                label_chain_name: 'Имя цепочки',
                label_chain_entrance: 'Входная нода (Группа 1)',
                label_chain_transit: 'Транзитные ноды (Группа 2) (опционально)',
                small_chain_transit: 'Зажмите Ctrl для выбора нескольких',
                label_chain_exit: 'Выходная нода (Группа 3)',
                btn_create_chain: 'Создать цепочку',
                chains_list_title: 'НАСТРОЕННЫЕ ЦЕПОЧКИ',
                // Configs tab
                configs_generator_title: 'ГЕНЕРАТОР ПОДКЛЮЧЕНИЙ',
                label_config_target: 'Цель (Маршрут / Нода)',
                label_config_client: 'Имя пользователя',
                btn_generate_config: 'Сгенерировать конфиг',
                config_result_title: 'ГЕНЕРАЦИЯ ЗАВЕРШЕНА',
                label_config_output: 'Конфигурация AmneziaWG (.conf)',
                btn_download_config: 'Скачать .conf файл',
                // Add Node Modal
                add_node_title: 'Добавить серверную ноду',
                label_node_name: 'Имя ноды',
                label_node_ip: 'IP-адрес',
                label_node_user: 'SSH логин',
                label_node_auth: 'Авторизация',
                opt_auth_password: 'По паролю (установит SSH ключ автоклиента)',
                opt_auth_key: 'По дефолтному SSH ключу',
                label_node_password: 'SSH пароль',
                label_node_role: 'Роль в системе',
                opt_role_gateway: '1(вход) / Entrance (Gateway)',
                opt_role_transit: '2(транзит) / Transit',
                opt_role_exit: '3(выход) / Exit',
                label_node_slug: 'Слуг (латиница, до 6 символов)',
                label_node_country: 'Страна (2-буквенный ISO-код)',
                btn_start_setup: 'Начать установку',
                // Proxy Share Modal
                proxy_share_title_modal: 'Подключение для пользователя',
                label_proxy_share_url: 'Telegram Proxy URL'
            },
            en: {
                title: 'iDOCTOR CONTROL CENTER',
                subtitle: 'VPN & MTProto Proxy Infrastructure Monitor',
                hide_ips: 'HIDE IPs',
                show_ips: 'SHOW IPs',
                refresh: 'FORCE REFRESH',
                topology_title: 'NETWORK TRANSIT TOPOLOGY',
                proxy_node: 'Proxy Server',
                actions_title: 'INFRASTRUCTURE ACTIONS',
                speedtest: 'Run Speed Test',
                restart_xray: 'Restart Xray Balancer',
                restart_failover: 'Restart Failover Daemon',
                modal_title: 'Action Execution',
                connecting: 'Connecting and executing action on remote hosts...\\n',
                active_path: 'Active route',
                path_main: 'Main',
                path_backup: 'Backup',
                load: 'Load',
                offline: 'Offline',
                group_proxy: 'Proxy Server',
                group_entrance: 'Entrance Gateways (Group 1)',
                group_transit: 'Transit Nodes (Group 2)',
                group_exit: 'Exit Nodes (Group 3)',
                vpn_tunnels: 'ACTIVE VPN TUNNELS',
                online: 'ONLINE',
                offline_badge: 'OFFLINE',
                cpu: 'CPU Usage',
                ram: 'RAM Usage',
                uptime: 'Uptime',
                proxy_tab_title: 'Proxy & Failover',
                proxy_config_header: 'MTPROTO PROXY CONFIG',
                active_daemon: 'Proxy Daemon',
                port: 'Listen Port',
                fake_tls_domain: 'Fake TLS SNI Domain',
                mtg_warning: "⚠️ Warning: mtg v2 supports only a single shared secret. The first user's secret in the list will be used.",
                failover_header: 'GATEWAY FAILOVER',
                failover_desc: 'Drag gateways up/down to configure failover priority order (Main ➔ Backup).',
                deploy_btn: 'DEPLOY CONFIG',
                users_header: 'USER ACCOUNTS',
                add_user_btn: 'Add User',
                table_name: 'Name',
                table_secret: 'Secret',
                table_actions: 'Actions',
                btn_share: 'Link / QR',
                btn_delete: 'Delete',
                active_gateways_header: 'FAILOVER TOPO',
                monitor_logs: 'Monitor Logs',
                refresh_logs: 'Refresh Logs',
                proxy_share_title: 'Connection for: ',
                copy_btn: 'Copy',
                copied_btn: 'Copied!',
                gw_active: 'ACTIVE',
                gw_disabled: 'DISABLED',
                // Tabs
                tab_monitoring: 'Monitoring',
                tab_nodes: 'Nodes & Groups',
                tab_chains: 'Chains',
                tab_configs: 'Client Configs',
                tab_proxy: 'Proxy & Failover',
                // Nodes & Groups tab
                nodes_title: 'SERVER NODES & GROUPS',
                btn_add_node: 'Add Node',
                th_node_name: 'Node Name',
                th_node_ip: 'IP Address',
                th_node_role: 'System Role',
                th_node_group: 'Group',
                th_node_country: 'Country',
                th_node_delete: 'Delete',
                // Chains tab
                chains_create_title: 'CREATE CHAIN',
                label_chain_name: 'Chain Name',
                label_chain_entrance: 'Entrance Node (Group 1)',
                label_chain_transit: 'Transit Nodes (Group 2) (optional)',
                small_chain_transit: 'Hold Ctrl to select multiple',
                label_chain_exit: 'Exit Node (Group 3)',
                btn_create_chain: 'Create Chain',
                chains_list_title: 'CONFIGURED CHAINS',
                // Configs tab
                configs_generator_title: 'CONFIG GENERATOR',
                label_config_target: 'Target Route',
                label_config_client: 'Client Name',
                btn_generate_config: 'Generate Config',
                config_result_title: 'CONFIG GENERATED',
                label_config_output: 'AmneziaWG Configuration (.conf)',
                btn_download_config: 'Download .conf',
                // Add Node Modal
                add_node_title: 'Add Server Node',
                label_node_name: 'Node Name',
                label_node_ip: 'IP Address',
                label_node_user: 'SSH User',
                label_node_auth: 'Auth Method',
                opt_auth_password: 'Password (will auto-install SSH key)',
                opt_auth_key: 'Default SSH Key',
                label_node_password: 'SSH Password',
                label_node_role: 'Role',
                opt_role_gateway: '1 (Entrance Gateway)',
                opt_role_transit: '2 (Transit)',
                opt_role_exit: '3 (Exit)',
                label_node_slug: 'Exit Interface Slug',
                label_node_country: 'Country (2-letter ISO code)',
                btn_start_setup: 'Start Setup',
                // Proxy Share Modal
                proxy_share_title_modal: 'Connection for User',
                label_proxy_share_url: 'Telegram Proxy URL'
            }
        };

        function t(key) {
            return TRANSLATIONS[currentLang][key] || key;
        }

        function toggleLang() {
            const lang = currentLang === 'ru' ? 'en' : 'ru';
            currentLang = lang;
            localStorage.setItem('dash_lang', lang);
            applyStaticTranslations();
            if (globalData) {
                renderGrid(globalData);
                updateTopologyUI(globalData);
            }
            if (currentProxyConfig) {
                renderProxyUI(currentProxyConfig);
            }
        }

        function applyStaticTranslations() {
            const lang = currentLang;
            
            const setSafeText = (id, text) => {
                const el = document.getElementById(id);
                if (el) el.innerText = text;
            };
            
            const setSafeQueryText = (selector, text) => {
                const el = document.querySelector(selector);
                if (el) el.innerText = text;
            };

            setSafeText('header-title', t('title'));
            setSafeText('header-subtitle', t('subtitle'));
            
            const btnLang = document.getElementById('btn-lang');
            if (btnLang) {
                btnLang.innerText = lang === 'ru' ? 'EN' : 'RU';
                btnLang.style.borderColor = lang === 'ru' ? 'rgba(255,255,255,0.12)' : 'var(--neon-cyan)';
            }
            
            const btnHideIps = document.getElementById('btn-hide-ips');
            if (btnHideIps) btnHideIps.innerText = ipsHidden ? t('show_ips') : t('hide_ips');
            
            setSafeText('btn-refresh', t('refresh'));
            setSafeQueryText('#topology-title', t('topology_title'));
            setSafeQueryText('#proxy-node-label', t('proxy_node'));
            setSafeQueryText('#actions-title', t('actions_title'));
            setSafeQueryText('#btn-speedtest', t('speedtest'));
            setSafeQueryText('#btn-restart-xray', t('restart_xray'));
            setSafeQueryText('#btn-restart-failover', t('restart_failover'));
            setSafeText('modal-title', t('modal_title'));
            
            const termEl = document.getElementById('terminal-text');
            if (termEl) {
                if (termEl.innerText === TRANSLATIONS['ru'].connecting || termEl.innerText === TRANSLATIONS['en'].connecting) {
                    termEl.innerText = t('connecting');
                }
            }

            // Tabs
            setSafeText('tab-btn-monitoring', t('tab_monitoring'));
            setSafeText('tab-btn-nodes', t('tab_nodes'));
            setSafeText('tab-btn-chains', t('tab_chains'));
            setSafeText('tab-btn-configs', t('tab_configs'));
            setSafeText('tab-btn-proxy', t('tab_proxy'));
            
            // Nodes & Groups
            setSafeText('nodes-title', t('nodes_title'));
            setSafeText('btn-add-node', t('btn_add_node'));
            setSafeText('th-node-name', t('th_node_name'));
            setSafeText('th-node-ip', t('th_node_ip'));
            setSafeText('th-node-role', t('th_node_role'));
            setSafeText('th-node-group', t('th_node_group'));
            setSafeText('th-node-country', t('th_node_country'));
            setSafeText('th-node-delete', t('th_node_delete'));
            
            // Chains
            setSafeText('chains-create-title', t('chains_create_title'));
            setSafeText('label-chain-name', t('label_chain_name'));
            setSafeText('label-chain-entrance', t('label_chain_entrance'));
            setSafeText('label-chain-transit', t('label_chain_transit'));
            setSafeText('small-chain-transit', t('small_chain_transit'));
            setSafeText('label-chain-exit', t('label_chain_exit'));
            setSafeText('btn-create-chain', t('btn_create_chain'));
            setSafeText('chains-list-title', t('chains_list_title'));
            
            // Configs
            setSafeText('configs-generator-title', t('configs_generator_title'));
            setSafeText('label-config-target', t('label_config_target'));
            setSafeText('label-config-client', t('label_config_client'));
            setSafeText('btn-generate-config', t('btn_generate_config'));
            setSafeText('config-result-title', t('config_result_title'));
            setSafeText('label-config-output', t('label_config_output'));
            setSafeText('btn-download-config', t('btn_download_config'));
            
            // Proxy
            setSafeText('proxy-config-title', t('proxy_config_header'));
            setSafeText('label-proxy-daemon', t('active_daemon'));
            setSafeText('proxy-daemon-warning', t('mtg_warning'));
            setSafeText('label-proxy-port', t('port'));
            setSafeText('label-proxy-domain', t('fake_tls_domain'));
            setSafeText('failover-priority-title', t('failover_header'));
            setSafeText('failover-priority-desc', t('failover_desc'));
            setSafeText('btn-deploy-proxy', t('deploy_btn'));
            setSafeText('proxy-users-title', t('users_header'));
            setSafeText('btn-add-proxy-user', t('add_user_btn'));
            setSafeText('th-proxy-name', t('table_name'));
            setSafeText('th-proxy-secret', t('table_secret'));
            setSafeText('th-proxy-actions', t('table_actions'));
            setSafeText('proxy-topo-title', t('active_gateways_header'));
            setSafeText('label-proxy-logs', t('monitor_logs'));
            setSafeText('btn-refresh-proxy-logs', t('refresh_logs'));
            
            // Add Node Modal
            setSafeText('add-node-title', t('add_node_title'));
            setSafeText('label-add-node-name', t('label_node_name'));
            setSafeText('label-add-node-ip', t('label_node_ip'));
            setSafeText('label-add-node-user', t('label_node_user'));
            setSafeText('label-add-node-auth', t('label_node_auth'));
            setSafeText('opt-auth-password', t('opt_auth_password'));
            setSafeText('opt-auth-key', t('opt_auth_key'));
            setSafeText('label-add-node-password', t('label_node_password'));
            setSafeText('label-add-node-role', t('label_node_role'));
            setSafeText('opt-role-gateway', t('opt_role_gateway'));
            setSafeText('opt-role-transit', t('opt_role_transit'));
            setSafeText('opt-role-exit', t('opt_role_exit'));
            setSafeText('label-add-node-slug', t('label_node_slug'));
            setSafeText('label-add-node-country', t('label_node_country'));
            setSafeText('btn-start-setup', t('btn_start_setup'));
            
            // Proxy Share Modal
            setSafeText('proxy-share-title-modal', t('proxy_share_title_modal'));
            setSafeText('label-proxy-share-url', t('label_proxy_share_url'));
            setSafeText('btn-copy-proxy', t('copy_btn'));
        }

        let ipsHidden = false;
        let globalData = null;
        let activeGateway = null;
        let activeExits = [];
        let selectedGateway = null;
        const expandedCards = new Set();

        function toggleCardVpn(srvName) {
            const safeName = srvName.replace(/\\s+/g, '-');
            const el = document.getElementById(`vpn-details-${safeName}`);
            const icon = document.getElementById(`vpn-toggle-icon-${safeName}`);
            if (expandedCards.has(srvName)) {
                expandedCards.delete(srvName);
                if (el) el.style.display = 'none';
                if (icon) icon.innerText = currentLang === 'ru' ? '[Развернуть]' : '[Expand]';
            } else {
                expandedCards.add(srvName);
                if (el) el.style.display = 'block';
                if (icon) icon.innerText = currentLang === 'ru' ? '[Свернуть]' : '[Collapse]';
            }
        }

        // ===== Tab Navigation =====
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            
            document.getElementById(`tab-${tabId}`).style.display = 'block';
            document.getElementById(`tab-btn-${tabId}`).classList.add('active');
            
            if (tabId === 'nodes') {
                fetchNodes();
            } else if (tabId === 'chains') {
                fetchChains();
                fetchNodesForSelectors();
            } else if (tabId === 'configs') {
                fetchTargetsForConfig();
            } else if (tabId === 'proxy') {
                fetchProxyConfig();
            }
        }

        // ===== IP Hiding =====
        function toggleIps() {
            ipsHidden = !ipsHidden;
            const btn = document.getElementById('btn-hide-ips');
            if (ipsHidden) {
                document.body.classList.add('hide-ips');
                btn.innerText = t('show_ips');
                btn.style.borderColor = 'var(--neon-purple)';
                btn.style.boxShadow = '0 0 10px rgba(155, 81, 224, 0.3)';
            } else {
                document.body.classList.remove('hide-ips');
                btn.innerText = t('hide_ips');
                btn.style.borderColor = 'var(--card-border)';
                btn.style.boxShadow = 'none';
            }
        }

        function selectGateway(gwName) {
            selectedGateway = gwName;
            if (globalData) {
                updateTopologyUI(globalData);
                drawConnectors();
            }
        }

        // ===== Modal Handlers =====
        function openAddNodeModal() {
            document.getElementById('add-node-modal').style.display = 'flex';
        }
        function closeAddNodeModal() {
            document.getElementById('add-node-modal').style.display = 'none';
        }
        function toggleAuthFields() {
            const method = document.getElementById('add-node-auth').value;
            const pwGroup = document.getElementById('node-password-group');
            pwGroup.style.display = (method === 'password') ? 'flex' : 'none';
        }
        function toggleExitSlugField() {
            const role = document.getElementById('add-node-role').value;
            const slugGroup = document.getElementById('node-slug-group');
            slugGroup.style.display = (role === 'exit') ? 'flex' : 'none';
        }

        // ===== API Calls =====
        async function fetchNodes() {
            const res = await fetch('/api/nodes');
            const nodes = await res.json();
            const tbody = document.getElementById('nodes-table-body');
            tbody.innerHTML = '';
            
            nodes.forEach(node => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-weight:600;">${node.name}</td>
                    <td class="ip-address">${node.ip}</td>
                    <td><span style="font-family:'JetBrains Mono'; font-size:12px;">${node.role}</span></td>
                    <td>
                        <select onchange="changeNodeGroup('${node.ip}', this.value)" class="form-control" style="padding:4px 8px; width:auto; display:inline-block; font-size:12px;">
                            <option value="Proxy" ${node.group === 'Proxy' ? 'selected' : ''}>Proxy</option>
                            <option value="1(вход)" ${node.group === '1(вход)' ? 'selected' : ''}>1(вход) / Entrance</option>
                            <option value="2(транзит)" ${node.group === '2(транзит)' ? 'selected' : ''}>2(транзит) / Transit</option>
                            <option value="выход" ${node.group === 'выход' ? 'selected' : ''}>выход / Exit</option>
                        </select>
                    </td>
                    <td><span class="flag">${countryFlags[node.country] || node.country || ''}</span></td>
                    <td>
                        <button class="btn-delete" onclick="deleteNode('${node.ip}')">Удалить</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function changeNodeGroup(ip, group) {
            await fetch('/api/change-group', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ip, group })
            });
            fetchNodes();
        }

        async function deleteNode(ip) {
            if (confirm(currentLang === 'ru' ? `Вы действительно хотите удалить ноду ${ip}?` : `Are you sure you want to delete node ${ip}?`)) {
                await fetch('/api/nodes/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ip })
                });
                fetchNodes();
            }
        }

        async function fetchNodesForSelectors() {
            const res = await fetch('/api/nodes');
            const nodes = await res.json();
            
            const entranceSel = document.getElementById('chain-entrance');
            const transitSel = document.getElementById('chain-transit');
            const exitSel = document.getElementById('chain-exit');
            
            entranceSel.innerHTML = '';
            transitSel.innerHTML = '';
            exitSel.innerHTML = '';
            
            nodes.forEach(node => {
                const opt = `<option value="${node.ip}">${node.name} (${node.ip})</option>`;
                if (node.group === '1(вход)' || node.role === 'gateway') {
                    entranceSel.innerHTML += opt;
                } else if (node.group === '2(транзит)' || node.role === 'transit') {
                    transitSel.innerHTML += opt;
                } else if (node.group === 'выход' || node.role === 'exit') {
                    exitSel.innerHTML += opt;
                }
            });
        }

        async function submitCreateChain() {
            const name = document.getElementById('chain-name').value;
            const entrance = document.getElementById('chain-entrance').value;
            const exit = document.getElementById('chain-exit').value;
            
            const transitsOpts = document.getElementById('chain-transit').selectedOptions;
            const transit = Array.from(transitsOpts).map(o => o.value);
            
            if (!name || !entrance || !exit) {
                alert("Пожалуйста, заполните обязательные поля!");
                return;
            }
            
            const hops = [entrance, ...transit, exit];
            
            const res = await fetch('/api/chains', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, hops })
            });
            
            if (res.ok) {
                document.getElementById('chain-name').value = '';
                fetchChains();
            } else {
                alert("Ошибка создания цепочки");
            }
        }

        async function fetchChains() {
            const res = await fetch('/api/chains');
            const chains = await res.json();
            const container = document.getElementById('chains-list-container');
            container.innerHTML = '';
            
            if (chains.length === 0) {
                container.innerHTML = '<p style="color:var(--text-muted);">Цепочки еще не созданы.</p>';
                return;
            }
            
            const nRes = await fetch('/api/nodes');
            const nodes = await nRes.json();
            const nodeNames = {};
            nodes.forEach(n => nodeNames[n.ip] = n.name);
            
            chains.forEach(chain => {
                const card = document.createElement('div');
                card.className = 'chain-card';
                
                const hopsHTML = chain.hops.map((hopIp, idx) => {
                    const name = nodeNames[hopIp] || hopIp;
                    const arrow = (idx < chain.hops.length - 1) ? ' <span class="chain-arrow">→</span> ' : '';
                    return `<span>${name}</span>${arrow}`;
                }).join('');
                
                card.innerHTML = `
                    <div class="chain-info">
                        <h3>${chain.name}</h3>
                        <div class="chain-path">
                            ${hopsHTML}
                        </div>
                        <div style="margin-top:8px; font-size:11px; color:${chain.status === 'deployed' ? 'var(--neon-green)' : 'var(--text-muted)'}">
                            Статус / Status: <strong>${chain.status.toUpperCase()}</strong>
                        </div>
                    </div>
                    <div>
                        <button class="btn-deploy" onclick="deployChain('${chain.id}')">Применить / Deploy</button>
                        <button class="btn-delete" onclick="deleteChain('${chain.id}')">Удалить</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        async function deployChain(id) {
            const modal = document.getElementById('terminal-modal');
            const term = document.getElementById('terminal-text');
            const title = document.getElementById('modal-title');
            
            modal.style.display = 'flex';
            title.innerText = 'Развёртывание цепочки / Chain Deployment';
            term.innerText = 'Запуск конфигурирования туннелей на удалённых серверах...\\n';
            
            try {
                const res = await fetch(`/api/action?type=deploy-chain&chain_id=${id}`, { method: 'POST' });
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    term.innerText += decoder.decode(value);
                    term.scrollTop = term.scrollHeight;
                }
            } catch (e) {
                term.innerText += `\\n[ERROR] Deployment failed: ${e}`;
            }
        }

        async function deleteChain(id) {
            if (confirm(currentLang === 'ru' ? "Удалить цепочку?" : "Delete chain?")) {
                await fetch('/api/chains/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id })
                });
                fetchChains();
            }
        }

        async function fetchTargetsForConfig() {
            const nRes = await fetch('/api/nodes');
            const nodes = await nRes.json();
            
            const cRes = await fetch('/api/chains');
            const chains = await cRes.json();
            
            const select = document.getElementById('config-target');
            select.innerHTML = '';
            
            chains.forEach(chain => {
                if (chain.status === 'deployed') {
                    select.innerHTML += `<option value="${chain.id}">Цепочка: ${chain.name}</option>`;
                }
            });
            
            nodes.forEach(node => {
                if (node.group === '1(вход)' || node.role === 'gateway') {
                    select.innerHTML += `<option value="${node.ip}">Нода: ${node.name} (${node.ip})</option>`;
                }
            });
        }

        async function submitGenerateConfig() {
            const target = document.getElementById('config-target').value;
            const clientName = document.getElementById('config-client-name').value;
            
            if (!target || !clientName) {
                alert("Пожалуйста, заполните поля!");
                return;
            }
            
            const modal = document.getElementById('terminal-modal');
            const term = document.getElementById('terminal-text');
            const title = document.getElementById('modal-title');
            
            modal.style.display = 'flex';
            title.innerText = 'Генерация ключей / Generating Client Config';
            term.innerText = 'Подключение к входной ноде для генерации AmneziaWG ключей...\\n';
            
            try {
                const res = await fetch(`/api/action?type=generate-client-config&chain_id=${target}&client_name=${clientName}`, { method: 'POST' });
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                
                let output = '';
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    const chunk = decoder.decode(value);
                    output += chunk;
                    term.innerText += chunk;
                    term.scrollTop = term.scrollHeight;
                }
                
                if (output.includes('[CONFIG_START]')) {
                    const confStart = output.indexOf('[CONFIG_START]') + '[CONFIG_START]'.length;
                    const confEnd = output.indexOf('[CONFIG_END]');
                    const configText = output.substring(confStart, confEnd).trim();
                    
                    document.getElementById('config-text-output').value = configText;
                    
                    const qrContainer = document.getElementById('config-qrcode-container');
                    qrContainer.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(configText)}" alt="QR Code" style="display:block;"/>`;
                    
                    document.getElementById('config-result-card').style.display = 'block';
                    closeModal();
                }
            } catch (e) {
                term.innerText += `\\n[ERROR] Generation failed: ${e}`;
            }
        }

        function downloadConfigText() {
            const text = document.getElementById('config-text-output').value;
            const clientName = document.getElementById('config-client-name').value || 'client';
            const blob = new Blob([text], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${clientName}_awg2.conf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        async function submitAddNode() {
            const name = document.getElementById('add-node-name').value;
            const ip = document.getElementById('add-node-ip').value;
            const user = document.getElementById('add-node-user').value;
            const auth = document.getElementById('add-node-auth').value;
            const password = document.getElementById('add-node-password').value;
            const role = document.getElementById('add-node-role').value;
            const slug = document.getElementById('add-node-slug').value;
            const country = document.getElementById('add-node-country').value;
            
            if (!name || !ip || !user) {
                alert("Пожалуйста, заполните основные поля!");
                return;
            }
            
            closeAddNodeModal();
            
            const modal = document.getElementById('terminal-modal');
            const term = document.getElementById('terminal-text');
            const title = document.getElementById('modal-title');
            
            modal.style.display = 'flex';
            title.innerText = 'Установка ноды / Node Installation';
            term.innerText = 'Подготовка к установке ноды AmneziaWG...\\n';
            
            const params = new URLSearchParams({
                type: 'add-node',
                name,
                ip,
                user,
                auth,
                password,
                role,
                exit_slug: slug,
                country
            });
            
            try {
                const res = await fetch(`/api/action?${params.toString()}`, { method: 'POST' });
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    term.innerText += decoder.decode(value);
                    term.scrollTop = term.scrollHeight;
                }
            } catch (e) {
                term.innerText += `\\n[ERROR] Installation failed: ${e}`;
            }
        }

        function drawConnectors() {
            const svg = document.getElementById('connector-svg');
            if (!svg) return;
            
            // Keep filter definitions in SVG
            svg.innerHTML = `<defs>
                <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="3.5" result="blur" />
                    <feMerge>
                        <feMergeNode in="blur" />
                        <feMergeNode in="SourceGraphic" />
                    </feMerge>
                </filter>
            </defs>`;
            
            const proxy = document.getElementById('node-proxy');
            const gw1 = document.getElementById('node-gw1');
            const gw2 = document.getElementById('node-gw2');
            
            const exitNodes = [
                document.getElementById('node-exit1'),
                document.getElementById('node-exit2'),
                document.getElementById('node-exit3'),
                document.getElementById('node-exit4'),
                document.getElementById('node-exit5'),
                document.getElementById('node-exit6'),
            ];

            const svgRect = svg.getBoundingClientRect();

            function drawLine(el1, el2, active, color) {
                if (!el1 || !el2) return;
                const r1 = el1.getBoundingClientRect();
                const r2 = el2.getBoundingClientRect();
                
                const x1 = r1.right - svgRect.left;
                const y1 = r1.top + r1.height / 2 - svgRect.top;
                const x2 = r2.left - svgRect.left;
                const y2 = r2.top + r2.height / 2 - svgRect.top;
                
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', x1);
                line.setAttribute('y1', y1);
                line.setAttribute('x2', x2);
                line.setAttribute('y2', y2);
                line.setAttribute('stroke', color);
                line.setAttribute('stroke-width', active ? '3' : '1.5');
                if (active) {
                    line.setAttribute('stroke-dasharray', '6, 6');
                    line.setAttribute('filter', 'url(#glow)');
                    line.innerHTML = `<animate attributeName="stroke-dashoffset" values="60;0" dur="1.5s" repeatCount="indefinite" />`;
                } else {
                    line.setAttribute('opacity', '0.15');
                }
                svg.appendChild(line);
            }

            const isGw1Active = activeGateway && (activeGateway.name.includes("GW1") || activeGateway.name.includes("Gateway 1"));
            const isGw2Active = activeGateway && (activeGateway.name.includes("GW2") || activeGateway.name.includes("Gateway 2"));

            drawLine(proxy, gw1, isGw1Active, '#00f2fe');
            drawLine(proxy, gw2, isGw2Active, '#9b51e0');

            // Determine selected gateway element
            let selectedGwEl = null;
            if (selectedGateway === "Gateway 1 (GW1)") {
                selectedGwEl = gw1;
            } else if (selectedGateway === "Gateway 2 (GW2)") {
                selectedGwEl = gw2;
            } else {
                selectedGwEl = isGw1Active ? gw1 : (isGw2Active ? gw2 : gw1);
            }

            const selectedGwData = globalData ? globalData[selectedGateway || (isGw2Active ? "Gateway 2 (GW2)" : "Gateway 1 (GW1)")] : null;

            const exitHandshakes = {};
            for (let i = 1; i <= 6; i++) {
                const ifaceName = `awg-exit-n${i}`;
                let hasHandshake = false;
                if (selectedGwData && selectedGwData.vpn && selectedGwData.vpn[ifaceName]) {
                    const peers = selectedGwData.vpn[ifaceName].peers || [];
                    if (peers.length > 0) {
                        const peer = peers[0];
                        hasHandshake = peer && peer.handshake && isHandshakeRecentJS(peer.handshake);
                    }
                }
                exitHandshakes[i] = hasHandshake;
            }

            const isSelectedActive = (selectedGateway === null) || 
                                     (selectedGateway === "Gateway 1 (GW1)" && isGw1Active) || 
                                     (selectedGateway === "Gateway 2 (GW2)" && isGw2Active);

            exitNodes.forEach((node, idx) => {
                if (!node) return;
                const exitNum = idx + 1;
                const exitIface = `awg-exit-n${exitNum}`;
                
                let isRouting = false;
                if (isSelectedActive) {
                    isRouting = activeExits.includes(exitIface);
                } else {
                    isRouting = exitHandshakes[exitNum];
                }

                if (isRouting) {
                    node.classList.remove('exit-idle');
                    node.classList.add('exit-active');
                    node.style.opacity = '1';
                } else {
                    node.classList.remove('exit-active');
                    node.classList.add('exit-idle');
                    node.style.opacity = '0.5';
                }

                drawLine(selectedGwEl, node, isRouting, isRouting ? '#10b981' : '#9ca3af');
            });
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                globalData = data.statuses;
                activeGateway = data.active_gateway;
                activeExits = data.active_exits || [];
                
                renderGrid(globalData);
                updateTopologyUI(globalData);
                setTimeout(drawConnectors, 100);
            } catch (e) {
                console.error("Failed to fetch server statuses", e);
            }
        }

        function isHandshakeRecentJS(handshakeStr) {
            if (!handshakeStr || handshakeStr === 'N/A') return false;
            const hs = handshakeStr.toLowerCase();
            if (hs.includes('day') || hs.includes('hour') || hs.includes('week') || hs.includes('month') || hs.includes('year')) {
                return false;
            }
            const minuteMatch = hs.match(/(\\d+)\\s+minute/);
            if (minuteMatch) {
                const minutes = parseInt(minuteMatch[1], 10);
                if (minutes >= 5) return false;
            }
            return true;
        }

        function updateTopologyUI(data) {
            if (!data) return;
            const pathText = document.getElementById('proxy-path-text');
            if (pathText) {
                if (activeGateway) {
                    pathText.innerHTML = `${t('active_path')}: <span class='domain-name'>${activeGateway.ip}</span> (${activeGateway.name})`;
                    pathText.style.color = 'var(--neon-cyan)';
                } else {
                    pathText.innerHTML = `${t('active_path')}: <span class='domain-name'>None</span>`;
                    pathText.style.color = 'var(--neon-red)';
                }
            }

            const gw1Node = document.getElementById('node-gw1');
            const gw2Node = document.getElementById('node-gw2');
            
            const isGw1Active = activeGateway && (activeGateway.name.includes("GW1") || activeGateway.name.includes("Gateway 1"));
            const isGw2Active = activeGateway && (activeGateway.name.includes("GW2") || activeGateway.name.includes("Gateway 2"));

            if (selectedGateway === null && activeGateway) {
                selectedGateway = activeGateway.name;
            }

            if (gw1Node && gw2Node) {
                if (isGw1Active) {
                    gw1Node.classList.add('active');
                    gw1Node.style.borderColor = 'var(--neon-cyan)';
                    gw1Node.style.opacity = '1';
                    gw2Node.classList.remove('active');
                    gw2Node.style.borderColor = 'var(--card-border)';
                    gw2Node.style.opacity = '0.6';
                } else if (isGw2Active) {
                    gw2Node.classList.add('active');
                    gw2Node.style.borderColor = 'var(--neon-purple)';
                    gw2Node.style.opacity = '1';
                    gw1Node.classList.remove('active');
                    gw1Node.style.borderColor = 'var(--card-border)';
                    gw1Node.style.opacity = '0.6';
                }
            }

            const gw1Data = data["Gateway 1 (GW1)"];
            const gw1Load = document.getElementById('gw1-load-text');
            if (gw1Load) {
                if (gw1Data && gw1Data.online) {
                    gw1Load.innerText = `${t('load')}: ` + gw1Data.cpu;
                } else {
                    gw1Load.innerText = t('offline');
                }
            }
            
            const gw2Data = data["Gateway 2 (GW2)"];
            const gw2Load = document.getElementById('gw2-load-text');
            if (gw2Load) {
                if (gw2Data && gw2Data.online) {
                    gw2Load.innerText = `${t('load')}: ` + gw2Data.cpu;
                } else {
                    gw2Load.innerText = t('offline');
                }
            }

            const updateClientsText = (elementId, gwData) => {
                const el = document.getElementById(elementId);
                if (!el) return;
                if (!gwData || !gwData.online) {
                    el.innerText = "Clients: N/A";
                    return;
                }
                if (!gwData.vpn || !gwData.vpn["awg0"]) {
                    el.innerText = "Clients: 0 active";
                    return;
                }
                let usersCount = 0;
                let transportCount = 0;
                const peers = gwData.vpn["awg0"].peers || [];
                peers.forEach(peer => {
                    const isProxy = peer.allowed_ips && (peer.allowed_ips.includes("10.45.116.10") || peer.allowed_ips.includes("10.44.232.10"));
                    const isActive = peer.handshake && isHandshakeRecentJS(peer.handshake);
                    if (isActive) {
                        if (isProxy) {
                            transportCount++;
                        } else {
                            usersCount++;
                        }
                    }
                });
                
                if (transportCount > 0) {
                    el.innerText = `Clients: ${usersCount} active (+ ${transportCount} transport)`;
                } else {
                    el.innerText = `Clients: ${usersCount} active`;
                }
            };

            updateClientsText('gw1-users-text', gw1Data);
            updateClientsText('gw2-users-text', gw2Data);
        }

        function renderGrid(data) {
            const gridContainer = document.getElementById('servers-grid');
            if (!gridContainer) return;
            gridContainer.innerHTML = '';
            if (!data) return;

            let proxyNodes = [];
            let entranceNodes = [];
            let transitNodes = [];
            let exitNodes = [];

            Object.values(data).forEach(srv => {
                const role = (srv.role || '').toLowerCase();
                const group = (srv.group || '').toLowerCase();
                if (role === 'proxy' || group.includes('proxy')) {
                    proxyNodes.push(srv);
                } else if (role === 'gateway' || group.includes('вход') || group.includes('entrance') || group.includes('1')) {
                    entranceNodes.push(srv);
                } else if (role === 'transit' || group.includes('транзит') || group.includes('transit') || group.includes('2')) {
                    transitNodes.push(srv);
                } else if (role === 'exit' || group.includes('выход') || group.includes('exit') || group.includes('3')) {
                    exitNodes.push(srv);
                } else {
                    entranceNodes.push(srv);
                }
            });

            const groups = [
                { titleKey: 'group_proxy', nodes: proxyNodes, color: 'var(--neon-purple)' },
                { titleKey: 'group_entrance', nodes: entranceNodes, color: 'var(--neon-cyan)' },
                { titleKey: 'group_transit', nodes: transitNodes, color: '#f59e0b' },
                { titleKey: 'group_exit', nodes: exitNodes, color: 'var(--neon-green)' }
            ];

            groups.forEach(g => {
                if (g.nodes.length === 0) return;

                const groupSection = document.createElement('div');
                groupSection.className = 'server-group-section';
                groupSection.style.marginBottom = '40px';

                groupSection.innerHTML = `
                    <h3 class="server-group-title" style="font-size: 16px; font-weight: 700; color: #fff; margin-bottom: 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); padding-bottom: 10px; display: flex; align-items: center; gap: 10px; text-transform: uppercase; letter-spacing: 1.5px;">
                        <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${g.color}; box-shadow: 0 0 10px ${g.color};"></span>
                        ${t(g.titleKey)}
                        <span class="group-count" style="font-size: 11px; background: rgba(255,255,255,0.06); color: var(--text-muted); padding: 2px 8px; border-radius: 20px; font-weight: 600; margin-left: 5px;">${g.nodes.length}</span>
                    </h3>
                `;

                const subGrid = document.createElement('div');
                subGrid.className = 'server-grid';
                
                g.nodes.forEach(srv => {
                    const card = document.createElement('div');
                    card.className = `server-card ${srv.online ? 'online' : 'offline'}`;

                    const servicesHTML = Object.entries(srv.services || {}).map(([name, status]) => {
                        const active = status === "active";
                        return `<span class="service-badge ${active ? 'active' : 'inactive'}">${name}: ${status}</span>`;
                    }).join('');

                    const vpnHTML = Object.entries(srv.vpn || {}).map(([iface, info]) => {
                        const peersHTML = (info.peers || []).map((peer, pIdx) => {
                            const isActive = peer.handshake && isHandshakeRecentJS(peer.handshake);
                            const shortKey = peer.public_key ? `${peer.public_key.substring(0, 8)}...` : 'Unknown';
                            
                            return `
                                <div class="vpn-peer-detail" style="margin-top: 6px; padding-top: 6px; border-top: 1px dashed rgba(255,255,255,0.05); opacity: ${isActive ? '1' : '0.5'};">
                                    <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom: 2px;">
                                        <span style="color:var(--neon-cyan); font-weight:600;">Peer: ${shortKey}</span>
                                        <span style="font-size:9px; background:${isActive ? 'rgba(16,185,129,0.15)' : 'rgba(255,255,255,0.05)'}; color:${isActive ? 'var(--neon-green)' : 'var(--text-muted)'}; padding: 1px 4px; border-radius: 3px;">
                                            ${isActive ? 'ACTIVE' : 'IDLE'}
                                        </span>
                                    </div>
                                    <div class="vpn-peer-info">
                                        ${peer.allowed_ips ? `<div><span style="color:var(--text-muted)">Allowed IPs:</span> <span class="ip-address">${peer.allowed_ips}</span></div>` : ''}
                                        <div><span style="color:var(--text-muted)">Endpoint:</span> <span><span class="ip-address">${peer.endpoint || 'N/A'}</span></span></div>
                                        <div><span style="color:var(--text-muted)">Handshake:</span> <span>${peer.handshake || 'N/A'}</span></div>
                                        <div><span style="color:var(--text-muted)">Transfer:</span> <span style="color:var(--neon-cyan)">${peer.transfer || '0'}</span></div>
                                    </div>
                                </div>
                            `;
                        }).join('');

                        return `
                            <div class="vpn-interface" style="margin-bottom: 12px; background: rgba(8, 9, 20, 0.4); border-radius: 8px; padding: 10px; border: 1px solid rgba(255,255,255,0.03);">
                                <div class="vpn-iface-header" style="font-size:13px; font-weight:600; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:4px; margin-bottom:4px;">
                                    <span>Interface: ${iface}</span>
                                </div>
                                ${peersHTML}
                            </div>
                        `;
                    }).join('');

                    const isExpanded = expandedCards.has(srv.name);
                    const displayStyle = isExpanded ? 'block' : 'none';
                    const toggleText = isExpanded ? (currentLang === 'ru' ? '[Свернуть]' : '[Collapse]') : (currentLang === 'ru' ? '[Развернуть]' : '[Expand]');

                    card.innerHTML = `
                        <div class="card-header">
                            <div class="server-title">
                                <h3>${srv.name} <span class="flag">${countryFlags[srv.country] || ''}</span></h3>
                                <span><span class="ip-address">${srv.ip}</span></span>
                            </div>
                            <div class="status-badge ${srv.online ? 'online' : 'offline'}">
                                <span class="status-dot"></span>
                                ${srv.online ? t('online') : t('offline_badge')}
                            </div>
                        </div>
                        
                        <div class="stats-list">
                            <div class="stat-item">
                                <span class="label">${t('cpu')}</span>
                                <span class="value" style="color: ${srv.cpu && srv.cpu.includes('N/A') ? 'var(--text-muted)' : 'var(--neon-cyan)'}">${srv.cpu}</span>
                            </div>
                            <div class="stat-item">
                                <span class="label">${t('ram')}</span>
                                <span class="value">${srv.ram}</span>
                            </div>
                            <div class="stat-item">
                                <span class="label">${t('uptime')}</span>
                                <span class="value" style="font-size:12px;">${srv.uptime}</span>
                            </div>
                        </div>

                        ${vpnHTML ? `
                            <div class="vpn-section">
                                <div style="display:flex; justify-content:space-between; align-items:center; cursor:pointer;" onclick="toggleCardVpn('${srv.name}')">
                                    <h4>${t('vpn_tunnels')}</h4>
                                    <span id="vpn-toggle-icon-${srv.name.replace(/\\s+/g, '-')}" style="font-size:12px; color:var(--neon-cyan); font-weight:600;">${toggleText}</span>
                                </div>
                                <div id="vpn-details-${srv.name.replace(/\\s+/g, '-')}" style="display:${displayStyle}; margin-top:10px;">
                                    ${vpnHTML}
                                </div>
                            </div>
                        ` : ''}
                        ${servicesHTML ? `<div class="services-badges">${servicesHTML}</div>` : ''}
                        ${srv.error ? `<div style="margin-top: 12px; font-size: 11px; color: var(--neon-red); font-family: 'JetBrains Mono', monospace;">${srv.error}</div>` : ''}
                    `;
                    subGrid.appendChild(card);
                });

                groupSection.appendChild(subGrid);
                gridContainer.appendChild(groupSection);
            });
        }

        async function runAction(action) {
            const modal = document.getElementById('terminal-modal');
            const term = document.getElementById('terminal-text');
            const title = document.getElementById('modal-title');
            
            modal.style.display = 'flex';
            term.innerText = t('connecting');
            
            if (action === 'speedtest') {
                title.innerText = 'Выполнение: Тест скорости';
            } else if (action === 'restart-xray') {
                title.innerText = 'Выполнение: Перезапуск Xray балансировщика';
            } else {
                title.innerText = 'Выполнение: Перезапуск Failover демона';
            }

            try {
                const res = await fetch(`/api/action?type=${action}`, { method: 'POST' });
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    term.innerText += decoder.decode(value);
                    term.scrollTop = term.scrollHeight;
                }
            } catch (e) {
                term.innerText += `\\n[ОШИБКА] Действие не выполнено: ${e}`;
            }
        }

        function closeModal() {
            document.getElementById('terminal-modal').style.display = 'none';
            fetchStatus();
        }

        let currentProxyConfig = null;

        function toggleProxyWarning() {
            const daemon = document.getElementById('proxy-daemon').value;
            const warning = document.getElementById('proxy-daemon-warning');
            if (daemon === 'mtg') {
                warning.style.display = 'block';
            } else {
                warning.style.display = 'none';
            }
        }

        async function fetchProxyConfig() {
            try {
                const res = await fetch('/api/proxy-config');
                const data = await res.json();
                currentProxyConfig = data;
                renderProxyUI(data);
            } catch (e) {
                console.error("Failed to fetch proxy config", e);
            }
        }

        function renderProxyUI(data) {
            document.getElementById('proxy-daemon').value = data.config.active_daemon || 'mtproxymax';
            document.getElementById('proxy-port').value = data.config.port || 443;
            document.getElementById('proxy-domain').value = data.config.fake_tls_domain || 'disk.yandex.ru';
            toggleProxyWarning();

            // Render failover priority gateways (Drag-and-Drop)
            const listContainer = document.getElementById('failover-priority-list');
            listContainer.innerHTML = '';
            
            const savedGateways = data.config.failover_gateways || [];
            const allGateways = data.all_gateways || [];
            
            // Order allGateways: saved ones first in order, then others
            const orderedGws = [];
            savedGateways.forEach(ip => {
                const node = allGateways.find(n => n.ip === ip);
                if (node) orderedGws.push({ node, active: true });
            });
            allGateways.forEach(node => {
                if (!savedGateways.includes(node.ip)) {
                    orderedGws.push({ node, active: false });
                }
            });

            orderedGws.forEach(item => {
                const div = document.createElement('div');
                div.className = `draggable-item ${item.active ? '' : 'disabled'}`;
                div.setAttribute('draggable', item.active ? 'true' : 'false');
                div.setAttribute('data-ip', item.node.ip);
                
                div.innerHTML = `
                    <div style="display:flex; align-items:center;">
                        <span class="drag-handle">☰</span>
                        <span style="font-weight:600;">${item.node.name} <span style="color:var(--text-muted); font-size:11px;">(${item.node.ip})</span></span>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:11px; color:var(--text-muted);">${item.active ? t('gw_active') : t('gw_disabled')}</span>
                        <input type="checkbox" class="gw-active-checkbox" ${item.active ? 'checked' : ''} onchange="toggleGwItemActive(this)" style="cursor:pointer;">
                    </div>
                `;
                listContainer.appendChild(div);
            });

            initDragAndDrop();

            // Render User accounts table
            renderUsersTable(data.config.users || []);

            // Render live status / logs
            updateProxyStatusUI(data);
        }

        function toggleGwItemActive(chk) {
            const item = chk.closest('.draggable-item');
            const statusLabel = item.querySelector('div:last-child span');
            if (chk.checked) {
                item.classList.remove('disabled');
                item.setAttribute('draggable', 'true');
                statusLabel.innerText = t('gw_active');
            } else {
                item.classList.add('disabled');
                item.setAttribute('draggable', 'false');
                statusLabel.innerText = t('gw_disabled');
            }
        }

        function initDragAndDrop() {
            const list = document.getElementById('failover-priority-list');
            if (!list) return;
            let draggingItem = null;

            list.addEventListener('dragstart', (e) => {
                const item = e.target.closest('.draggable-item');
                if (!item || item.classList.contains('disabled')) {
                    e.preventDefault();
                    return;
                }
                draggingItem = item;
                draggingItem.classList.add('dragging');
            });

            list.addEventListener('dragend', (e) => {
                if (draggingItem) {
                    draggingItem.classList.remove('dragging');
                }
                draggingItem = null;
            });

            list.addEventListener('dragover', (e) => {
                e.preventDefault();
                const afterElement = getDragAfterElement(list, e.clientY);
                if (draggingItem) {
                    if (afterElement == null) {
                        list.appendChild(draggingItem);
                    } else {
                        list.insertBefore(draggingItem, afterElement);
                    }
                }
            });
        }

        function getDragAfterElement(container, y) {
            const draggableElements = [...container.querySelectorAll('.draggable-item:not(.dragging):not(.disabled)')];
            return draggableElements.reduce((closest, child) => {
                const box = child.getBoundingClientRect();
                const offset = y - box.top - box.height / 2;
                if (offset < 0 && offset > closest.offset) {
                    return { offset: offset, element: child };
                } else {
                    return closest;
                }
            }, { offset: Number.NEGATIVE_INFINITY }).element;
        }

        function renderUsersTable(users) {
            const tbody = document.getElementById('proxy-users-table-body');
            tbody.innerHTML = '';
            
            users.forEach((user, idx) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="font-weight:600; font-family:'JetBrains Mono';">${user.name}</td>
                    <td style="font-family:'JetBrains Mono'; font-size:12px; color:var(--text-muted);">${user.secret.substring(0,8)}...</td>
                    <td style="text-align: right; display:flex; justify-content:flex-end; gap:8px;">
                        <button class="btn-deploy" onclick="showProxyShare('${user.name}', '${user.secret}')" style="padding:4px 8px; font-size:11px; margin-right:0;">${t('btn_share')}</button>
                        <button class="btn-delete" onclick="deleteProxyUser('${user.name}')" style="padding:4px 8px; font-size:11px;">${t('btn_delete')}</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        function generateRandomSecret() {
            let chars = '0123456789abcdef';
            let secret = '';
            for (let i = 0; i < 32; i++) {
                secret += chars[Math.floor(Math.random() * 16)];
            }
            return secret;
        }

        function addNewProxyUser() {
            const name = prompt(currentLang === 'ru' ? "Введите имя пользователя:" : "Enter username:");
            if (!name) return;
            const cleanName = name.trim().replace(/\\s+/g, '_');
            
            if (currentProxyConfig.config.users.some(u => u.name === cleanName)) {
                alert(currentLang === 'ru' ? "Пользователь с таким именем уже существует!" : "User with this name already exists!");
                return;
            }
            
            const secret = generateRandomSecret();
            currentProxyConfig.config.users.push({ name: cleanName, secret: secret });
            renderUsersTable(currentProxyConfig.config.users);
        }

        function deleteProxyUser(name) {
            if (confirm(currentLang === 'ru' ? `Удалить пользователя ${name}?` : `Delete user ${name}?`)) {
                currentProxyConfig.config.users = currentProxyConfig.config.users.filter(u => u.name !== name);
                renderUsersTable(currentProxyConfig.config.users);
            }
        }

        let activeShareUrl = '';
        function showProxyShare(userName, secret) {
            const daemon = document.getElementById('proxy-daemon').value;
            const port = document.getElementById('proxy-port').value;
            const domain = document.getElementById('proxy-domain').value;
            
            // Hex-encode domain for Fake TLS secret suffix
            const encoder = new TextEncoder();
            const view = encoder.encode(domain);
            const domainHex = Array.from(view).map(b => b.toString(16).padStart(2, '0')).join('');
            
            const fullSecret = 'ee' + secret + domainHex;
            const host = "158.160.231.158"; // Proxy server static IP
            
            activeShareUrl = `tg://proxy?server=${host}&port=${port}&secret=${fullSecret}`;
            
            document.getElementById('proxy-share-title-modal').innerText = t('proxy_share_title') + userName;
            document.getElementById('proxy-share-url').value = activeShareUrl;
            
            const qrContainer = document.getElementById('proxy-qrcode-container');
            qrContainer.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(activeShareUrl)}" alt="QR Code" style="display:block;"/>`;
            
            document.getElementById('proxy-share-modal').style.display = 'flex';
            document.getElementById('btn-copy-proxy').innerText = t('copy_btn');
        }

        function closeProxyShareModal() {
            document.getElementById('proxy-share-modal').style.display = 'none';
        }

        function copyProxyUrlToClipboard() {
            const urlInput = document.getElementById('proxy-share-url');
            urlInput.select();
            urlInput.setSelectionRange(0, 99999);
            navigator.clipboard.writeText(urlInput.value).then(() => {
                document.getElementById('btn-copy-proxy').innerText = 'Скопировано!';
            });
        }

        function updateProxyStatusUI(data) {
            // Check active routing status from API
            const statusNode = document.getElementById('topo-mini-gate');
            const statusLine = document.getElementById('topo-mini-link-line');
            const statusGateName = document.getElementById('topo-mini-gate-name');
            const statusGateIp = document.getElementById('topo-mini-gate-ip');

            if (data.active_gateway) {
                statusNode.className = 'topo-mini-node active';
                statusLine.className = 'topo-mini-line active';
                statusGateName.innerText = data.active_gateway.name || 'GW (Active)';
                statusGateIp.innerText = data.active_gateway.ip || 'N/A';
            } else {
                statusNode.className = 'topo-mini-node offline';
                statusLine.className = 'topo-mini-line';
                statusGateName.innerText = 'GW (DOWN)';
                statusGateIp.innerText = 'N/A';
            }

            // Populate monitor log
            fetchRouteMonitorLogs();
        }

        async function fetchRouteMonitorLogs() {
            try {
                const res = await fetch('/api/proxy-monitor-logs');
                const text = await res.text();
                document.getElementById('proxy-monitor-logs').value = text;
            } catch (e) {
                console.error("Failed to fetch route monitor logs", e);
            }
        }

        async function submitDeployProxyRouting() {
            const daemon = document.getElementById('proxy-daemon').value;
            const port = parseInt(document.getElementById('proxy-port').value) || 443;
            const domain = document.getElementById('proxy-domain').value.trim();
            
            // Collect order from drag-and-drop list
            const priorityList = document.getElementById('failover-priority-list');
            const items = [...priorityList.querySelectorAll('.draggable-item:not(.disabled)')];
            const failover_gateways = items.map(item => item.getAttribute('data-ip'));
            
            if (failover_gateways.length === 0) {
                alert("Пожалуйста, активируйте хотя бы один шлюз для отказоустойчивости!");
                return;
            }
            
            const config = {
                active_daemon: daemon,
                port: port,
                fake_tls_domain: domain,
                users: currentProxyConfig.config.users,
                failover_gateways: failover_gateways
            };
            
            // 1. Save config to backend
            const saveRes = await fetch('/api/proxy-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
            
            if (!saveRes.ok) {
                alert("Ошибка при сохранении конфигурации!");
                return;
            }
            
            // 2. Open terminal modal and run deploy script
            const modal = document.getElementById('terminal-modal');
            const term = document.getElementById('terminal-text');
            const title = document.getElementById('modal-title');
            
            modal.style.display = 'flex';
            term.innerText = 'Инициализация развертывания прокси и отказоустойчивости...\\n';
            title.innerText = 'Развёртывание Прокси и Маршрутизации / Deploy Proxy & Routing';
            
            try {
                const res = await fetch('/api/action?type=deploy-proxy-routing', { method: 'POST' });
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    term.innerText += decoder.decode(value);
                    term.scrollTop = term.scrollHeight;
                }
            } catch (e) {
                term.innerText += `\\n[ERROR] Deployment failed: ${e}`;
            }
        }

        function refreshData() {
            const btn = document.querySelector('.btn-refresh');
            btn.innerText = 'REFRESHING...';
            btn.disabled = true;
            fetch('/api/force-refresh', { method: 'POST' })
                .then(() => fetchStatus())
                .finally(() => {
                    btn.innerText = t('refresh');
                    btn.disabled = false;
                });
        }

        window.addEventListener('resize', drawConnectors);
        
        applyStaticTranslations();
        fetchStatus();
        setInterval(fetchStatus, 20000);
    </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return
        
    def read_post_json(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            return {}
        if content_length > 1048576:  # 1MB limit
            return {}
        post_data = self.rfile.read(content_length)
        try:
            return json.loads(post_data.decode('utf-8'))
        except Exception:
            return {}

    def do_GET(self):
        url_parsed = urllib.parse.urlparse(self.path)
        
        if url_parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode('utf-8'))
            
        elif url_parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with cache_lock:
                sorted_status = {}
                for k, v in sorted(
                    server_status_cache.items(),
                    key=lambda x: (
                        0 if x[1]["role"] == "proxy"
                        else (1 if x[1]["role"] == "gateway" else (2 if x[1]["role"] == "transit" else 3)),
                        x[0]
                    )
                ):
                    sorted_status[k] = v
                response_data = {
                    "statuses": sorted_status,
                    "active_gateway": active_gateway_cache,
                    "active_exits": active_exits_cache
                }
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                
        elif url_parsed.path == "/api/nodes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            config = load_config()
            self.wfile.write(json.dumps(config.get("nodes", [])).encode('utf-8'))
            
        elif url_parsed.path == "/api/chains":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            config = load_config()
            self.wfile.write(json.dumps(config.get("chains", [])).encode('utf-8'))
            
        elif url_parsed.path == "/api/proxy-config":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            config = load_config()
            active_gateway = get_active_proxy_gateway(config)
            all_gateways = [
                {"name": n.get("name", ""), "ip": n.get("ip", "")}
                for n in config.get("nodes", [])
                if n.get("role") == "gateway" or "вход" in n.get("group", "").lower()
            ]
            res_data = {
                "config": config.get("proxy_config", {}),
                "all_gateways": all_gateways,
                "active_gateway": active_gateway
            }
            self.wfile.write(json.dumps(res_data).encode('utf-8'))
            
        elif url_parsed.path == "/api/proxy-monitor-logs":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            config = load_config()
            logs = get_proxy_monitor_logs(config)
            self.wfile.write(logs.encode('utf-8'))
            
        else:
            self.send_response(404)
            self.end_headers()
            
    def do_POST(self):
        url_parsed = urllib.parse.urlparse(self.path)
        
        if url_parsed.path == "/api/force-refresh":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            poll_all_servers()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            
        elif url_parsed.path == "/api/nodes/delete":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            data = self.read_post_json()
            node_ip = data.get("ip", "")
            if node_ip:
                with config_lock:
                    config = load_config()
                    config["nodes"] = [n for n in config["nodes"] if n.get("ip") != node_ip]
                    save_config(config)
                reload_servers()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(400)
                self.end_headers()
                
        elif url_parsed.path == "/api/change-group":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            data = self.read_post_json()
            node_ip = data.get("ip", "")
            new_group = data.get("group", "")
            if node_ip and new_group:
                with config_lock:
                    config = load_config()
                    for n in config["nodes"]:
                        if n.get("ip") == node_ip:
                            n["group"] = new_group
                            if "вход" in new_group.lower() or "gateway" in new_group.lower() or "entrance" in new_group.lower():
                                n["role"] = "gateway"
                            elif "выход" in new_group.lower() or "exit" in new_group.lower():
                                n["role"] = "exit"
                            elif "транзит" in new_group.lower() or "transit" in new_group.lower():
                                n["role"] = "transit"
                    save_config(config)
                reload_servers()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(400)
                self.end_headers()
                
        elif url_parsed.path == "/api/chains":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            data = self.read_post_json()
            chain_name = str(data.get("name", ""))[:64]
            hops = data.get("hops", [])
            if chain_name and isinstance(hops, list) and len(hops) >= 2:
                with config_lock:
                    config = load_config()
                    chain_id = "chain_" + str(int(time.time()))
                    new_chain = {
                        "id": chain_id,
                        "name": chain_name,
                        "hops": [str(h) for h in hops[:10]],
                        "status": "configured"
                    }
                    config["chains"] = config.get("chains", [])
                    config["chains"].append(new_chain)
                    save_config(config)
                reload_servers()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(new_chain).encode('utf-8'))
            else:
                self.send_response(400)
                self.end_headers()
                
        elif url_parsed.path == "/api/chains/delete":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            data = self.read_post_json()
            chain_id = str(data.get("id", ""))[:64]
            if chain_id:
                with config_lock:
                    config = load_config()
                    config["chains"] = [c for c in config.get("chains", []) if c.get("id") != chain_id]
                    save_config(config)
                reload_servers()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(400)
                self.end_headers()
                
        elif url_parsed.path == "/api/proxy-config":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            data = self.read_post_json()
            clean_data = _validate_proxy_config(data)
            if clean_data:
                with config_lock:
                    config = load_config()
                    config["proxy_config"] = clean_data
                    save_config(config)
                reload_servers()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(400)
                self.end_headers()

        elif url_parsed.path == "/api/action":
            if not _check_auth(self):
                _send_unauthorized(self)
                return
            params = urllib.parse.parse_qs(url_parsed.query)
            action_type = params.get("type", [""])[0]
            
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            
            def send_msg(msg):
                self.wfile.write(f"{msg}\n".encode('utf-8'))
                self.wfile.flush()
                
            if action_type == "speedtest":
                send_msg("[SYSTEM] Starting sequential speed test on all nodes...")
                import subprocess
                import select as _select_mod
                script_dir = os.path.dirname(os.path.abspath(__file__))
                p = subprocess.Popen(
                    [sys.executable, "-u", "run_speedtests_seq.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=script_dir
                )
                deadline = time.time() + 300
                try:
                    while True:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(p.args, 300)
                        ready, _, _ = _select_mod.select([p.stdout], [], [], min(remaining, 5))
                        if ready:
                            line = p.stdout.readline()
                            if not line:
                                break
                            send_msg(line.strip())
                        elif p.poll() is not None:
                            break
                    p.wait(timeout=10)
                    send_msg("\n[SYSTEM] Speed test completed successfully!")
                except subprocess.TimeoutExpired:
                    p.kill()
                    try:
                        p.wait(timeout=5)
                    except Exception:
                        pass
                    send_msg("\n[SYSTEM] Speed test timed out (5 min limit).")
                except Exception as e:
                    send_msg(f"\n[SYSTEM] Speed test error: {e}")
                    try:
                        p.kill()
                        p.wait(timeout=5)
                    except Exception:
                        pass
                
            elif action_type == "restart-xray":
                send_msg("[SYSTEM] Connecting to Gateways to restart Xray...")
                # Dynamically get gateway nodes from config
                config = load_config()
                gateways = [n for n in config.get("nodes", []) if n.get("role") == "gateway" or "вход" in n.get("group", "").lower()]
                for gw in gateways:
                    gw_name = gw.get("name", "unknown")
                    gw_ip = gw.get("ip", "")
                    gw_user = gw.get("user", "root")
                    send_msg(f"\n[SYSTEM] Connecting to {gw_name} ({gw_ip})...")
                    ssh = None
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(gw_ip, username=gw_user, key_filename=SSH_KEY_PATH, timeout=5)

                        sudo_cmd = "sudo " if gw_user != "root" else ""
                        cmd = f"{sudo_cmd}systemctl restart awg-xray"
                        send_msg(f"[{gw_name}] Executing: {cmd}")
                        stdin, stdout, stderr = ssh.exec_command(cmd)
                        stdout.read()

                        cmd_status = f"{sudo_cmd}systemctl status awg-xray -n 2 --no-pager"
                        status_out = execute_remote(ssh, cmd_status)
                        send_msg(f"[{gw_name}] Status:\n{status_out}")
                    except Exception as e:
                        send_msg(f"[ERROR] failed on {gw_name}: {e}")
                    finally:
                        if ssh is not None:
                            ssh.close()

            elif action_type == "restart-proxy-monitor":
                send_msg("[SYSTEM] Connecting to Proxy Server...")
                # Dynamically get proxy node from config
                config = load_config()
                proxy_node = next((n for n in config.get("nodes", []) if n.get("role") == "proxy"), None)
                if not proxy_node:
                    send_msg("[ERROR] Proxy node not found in config.")
                else:
                    proxy_ip = proxy_node.get("ip", "")
                    proxy_user = proxy_node.get("user", "root")
                    ssh = None
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(proxy_ip, username=proxy_user, key_filename=SSH_KEY_PATH, timeout=5)

                        sudo_cmd = "sudo " if proxy_user != "root" else ""
                        send_msg(f"[Proxy] Executing: {sudo_cmd}systemctl restart vpn-route-monitor")
                        stdin, stdout, stderr = ssh.exec_command(f"{sudo_cmd}systemctl restart vpn-route-monitor")
                        stdout.read()

                        status_out = execute_remote(ssh, f"{sudo_cmd}systemctl status vpn-route-monitor -n 2 --no-pager")
                        send_msg(f"[Proxy] Status:\n{status_out}")
                    except Exception as e:
                        send_msg(f"[ERROR] failed: {e}")
                    finally:
                        if ssh is not None:
                            ssh.close()

            elif action_type == "add-node":
                node_name = params.get("name", [""])[0]
                node_ip = params.get("ip", [""])[0]
                node_user = params.get("user", ["root"])[0]
                node_auth = params.get("auth", ["password"])[0]
                node_pass = params.get("password", [""])[0]
                node_role = params.get("role", ["gateway"])[0]
                node_country = params.get("country", ["RU"])[0]
                exit_slug = params.get("exit_slug", [""])[0]
                
                send_msg(f"[SYSTEM] Starting installation on {node_name} ({node_ip})...")

                ssh = None
                try:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    
                    send_msg(f"[SYSTEM] Connecting via SSH to {node_user}@{node_ip}...")
                    if node_auth == "password":
                        ssh.connect(node_ip, username=node_user, password=node_pass, timeout=10)
                    else:
                        ssh.connect(node_ip, username=node_user, key_filename=SSH_KEY_PATH, timeout=10)
                    
                    send_msg("[SYSTEM] SSH connection established.")
                    
                    pub_key = get_dashboard_pubkey()
                    if pub_key:
                        send_msg("[SYSTEM] Deploying dashboard public SSH key to authorized_keys...")
                        ssh.exec_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
                        # Use single-quotes for shell safety; validate key format
                        if re.match(r'^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+$', pub_key):
                            ssh.exec_command(f"echo '{pub_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")
                            send_msg("[SYSTEM] Public key deployed successfully.")
                        else:
                            send_msg("[WARNING] Invalid public key format, skipping deployment.")
                    
                    send_msg("[SYSTEM] Enabling BBR TCP congestion control...")
                    sudo_prefix = "sudo " if node_user != "root" else ""
                    
                    ssh.exec_command(f"{sudo_prefix}sysctl -w net.core.default_qdisc=fq")
                    ssh.exec_command(f"{sudo_prefix}sysctl -w net.ipv4.tcp_congestion_control=bbr")
                    ssh.exec_command(f'echo "net.core.default_qdisc=fq" | {sudo_prefix}tee -a /etc/sysctl.conf')
                    ssh.exec_command(f'echo "net.ipv4.tcp_congestion_control=bbr" | {sudo_prefix}tee -a /etc/sysctl.conf')
                    ssh.exec_command(f"{sudo_prefix}sysctl -p")
                    send_msg("[SYSTEM] BBR enabled and configured.")
                    
                    send_msg("[SYSTEM] Uploading awg2.sh auto-installer...")
                    sftp = ssh.open_sftp()
                    local_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "awg2.sh")
                    sftp.put(local_script, "/tmp/awg2.sh")
                    sftp.chmod("/tmp/awg2.sh", 0o755)
                    sftp.close()
                    send_msg("[SYSTEM] Upload complete.")
                    
                    send_msg("[SYSTEM] Running auto-installer. This will take 1-3 minutes. Reading output...")
                    stdin, stdout, stderr = ssh.exec_command(f"AUTOINSTALL=1 {sudo_prefix}bash /tmp/awg2.sh --auto")
                    
                    for line in stdout:
                        clean_line = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', line.strip())
                        send_msg(clean_line)
                        
                    stdout.channel.recv_exit_status()
                    
                    send_msg("[SYSTEM] Fetching generated client config (/root/client1_awg2.conf)...")
                    stdin, stdout, stderr = ssh.exec_command(f"{sudo_prefix}cat /root/client1_awg2.conf")
                    conf_content = stdout.read().decode('utf-8', errors='ignore')
                    
                    if not conf_content or "[Interface]" not in conf_content:
                        send_msg("[WARNING] Could not read /root/client1_awg2.conf directly. Let's try server config.")
                        stdin, stdout, stderr = ssh.exec_command(f"{sudo_prefix}cat /etc/amnezia/amneziawg/awg0.conf")
                        conf_content = stdout.read().decode('utf-8', errors='ignore')
                    
                    config = load_config()
                    config["nodes"] = [n for n in config["nodes"] if n["ip"] != node_ip]
                    
                    node_group = "выход" if node_role == "exit" else ("2(транзит)" if node_role == "transit" else "1(вход)")
                    
                    new_node = {
                        "name": node_name,
                        "ip": node_ip,
                        "role": node_role,
                        "user": node_user,
                        "auth": "key",
                        "country": node_country,
                        "group": node_group,
                        "client_conf": conf_content
                    }
                    if exit_slug:
                        new_node["exit_slug"] = exit_slug
                    if node_role == "gateway":
                        new_node["services"] = ["awg-xray"]
                    elif node_role == "proxy":
                        new_node["services"] = ["vpn-route-monitor", "mtproxymax"]
                    
                    config["nodes"].append(new_node)
                    save_config(config)
                    reload_servers()
                    
                    send_msg(f"\n[SYSTEM] Node {node_name} successfully added and configured!")
                except Exception as e:
                    send_msg(f"\n[ERROR] Installation failed: {e}")
                finally:
                    if ssh is not None:
                        ssh.close()

            elif action_type == "deploy-chain":
                chain_id = params.get("chain_id", [""])[0]
                config = load_config()
                chains = config.get("chains", [])
                chain = next((c for c in chains if c["id"] == chain_id), None)
                if not chain:
                    send_msg("[ERROR] Chain not found.")
                    return
                
                send_msg(f"[SYSTEM] Deploying chain: {chain['name']}...")
                
                hops = []
                for hop_ip in chain.get("hops", []):
                    node = next((n for n in config["nodes"] if n.get("ip") == hop_ip), None)
                    if not node:
                        send_msg(f"[ERROR] Node with IP {hop_ip} not found in config.")
                        return
                    hops.append(node)

                if len(hops) < 2:
                    send_msg("[ERROR] Chain must have at least 2 hops to deploy.")
                    return
                
                try:
                    for i in range(len(hops) - 1, 0, -1):
                        client_node = hops[i - 1]
                        server_node = hops[i]

                        # Sanitize names for use as peer/iface identifiers
                        client_slug = re.sub(r'[^a-zA-Z0-9_]', '_', client_node['name']).lower()[:15]
                        server_slug = re.sub(r'[^a-zA-Z0-9_]', '_', server_node['name']).lower()[:10]
                        client_peer_name = f"peer_{client_slug}"
                        server_iface_name = f"nd_{server_slug}"

                        send_msg(f"\n[SYSTEM] Hops Connection: {client_node['name']} -> {server_node['name']}")
                        send_msg(f"[SYSTEM] SSH connecting to Server Node {server_node['name']} ({server_node['ip']})...")

                        ssh_srv = None
                        try:
                            ssh_srv = paramiko.SSHClient()
                            ssh_srv.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh_srv.connect(server_node["ip"], username=server_node["user"], key_filename=SSH_KEY_PATH, timeout=10)

                            sudo_srv = "sudo " if server_node["user"] != "root" else ""

                            send_msg(f"[{server_node['name']}] Creating peer '{client_peer_name}'...")
                            ssh_srv.exec_command(f"{sudo_srv}awg2 --add-client {shlex.quote(client_peer_name)}")
                            time.sleep(2)

                            stdin, stdout, stderr = ssh_srv.exec_command(f"{sudo_srv}cat /root/{shlex.quote(client_peer_name)}_awg2.conf")
                            client_conf = stdout.read().decode('utf-8', errors='ignore')
                        finally:
                            if ssh_srv is not None:
                                ssh_srv.close()
                        
                        if not client_conf or "[Interface]" not in client_conf:
                            raise Exception(f"Failed to generate client config on {server_node['name']}")
                            
                        lines = client_conf.split('\n')
                        new_lines = []
                        in_interface = False
                        for line in lines:
                            if line.strip().lower() == "[interface]":
                                in_interface = True
                                new_lines.append(line)
                                new_lines.append("Table = off")
                                continue
                            if in_interface and line.strip().startswith("["):
                                in_interface = False
                            if in_interface and line.strip().lower().startswith("table"):
                                continue
                            new_lines.append(line)
                        clean_client_conf = '\n'.join(new_lines)
                        
                        send_msg(f"[SYSTEM] SSH connecting to Client Node {client_node['name']} ({client_node['ip']})...")
                        ssh_cli = None
                        try:
                            ssh_cli = paramiko.SSHClient()
                            ssh_cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh_cli.connect(client_node["ip"], username=client_node["user"], key_filename=SSH_KEY_PATH, timeout=10)

                            sudo_cli = "sudo " if client_node["user"] != "root" else ""
                            conf_path = f"/etc/amnezia/amneziawg/awg-exit-{server_iface_name}.conf"

                            send_msg(f"[{client_node['name']}] Saving tunnel config to {conf_path}...")
                            stdin, stdout, stderr = ssh_cli.exec_command(f"{sudo_cli}tee {shlex.quote(conf_path)} > /dev/null")
                            stdin.write(clean_client_conf.encode('utf-8'))
                            stdin.flush()
                            stdin.channel.shutdown_write()
                            stdout.read()

                            send_msg(f"[{client_node['name']}] Enabling and starting interface awg-exit-{server_iface_name}...")
                            ssh_cli.exec_command(f"{sudo_cli}systemctl daemon-reload")
                            ssh_cli.exec_command(f"{sudo_cli}systemctl enable awg-quick@awg-exit-{shlex.quote(server_iface_name)}")
                            stdin, stdout, stderr = ssh_cli.exec_command(f"{sudo_cli}systemctl restart awg-quick@awg-exit-{shlex.quote(server_iface_name)}")
                            stdout.read()

                            send_msg(f"[{client_node['name']}] Configuring routing table 202 and exits_state...")
                            exits_state_content = f"active\nmode=all\nbalancer=single\nsingle_exit={server_iface_name}\n"
                            stdin, stdout, stderr = ssh_cli.exec_command(f"{sudo_cli}tee /etc/amnezia/amneziawg/exits_state > /dev/null")
                            stdin.write(exits_state_content.encode('utf-8'))
                            stdin.flush()
                            stdin.channel.shutdown_write()
                            stdout.read()

                            send_msg(f"[{client_node['name']}] Activating exits-routing manager...")
                            ssh_cli.exec_command(f"{sudo_cli}bash -c 'source /usr/local/bin/awg2 && _exits_install_routing_files'")
                            ssh_cli.exec_command(f"{sudo_cli}systemctl daemon-reload")
                            ssh_cli.exec_command(f"{sudo_cli}systemctl enable awg-exits-routing.service")
                            stdin, stdout, stderr = ssh_cli.exec_command(f"{sudo_cli}systemctl restart awg-exits-routing.service")
                            stdout.read()

                            send_msg(f"[SYSTEM] Hop {client_node['name']} -> {server_node['name']} deployed successfully!")
                        finally:
                            if ssh_cli is not None:
                                ssh_cli.close()
                        
                    config = load_config()
                    for c in config.get("chains", []):
                        if c["id"] == chain_id:
                            c["status"] = "deployed"
                    save_config(config)
                    reload_servers()
                    
                    send_msg("\n[SYSTEM] Chain deployed successfully! All tunnel connections and policy routings are active.")
                except Exception as e:
                    send_msg(f"\n[ERROR] Chain deployment failed: {e}")

            elif action_type == "deploy-proxy-routing":
                config = load_config()
                p_conf = config.get("proxy_config", {})
                if not p_conf:
                    send_msg("[ERROR] Proxy configuration not found.")
                    return
                
                proxy_node = next((n for n in config.get("nodes", []) if n.get("role") == "proxy"), None)
                if not proxy_node:
                    send_msg("[ERROR] Proxy server node not found in configuration.")
                    return
                
                send_msg(f"[SYSTEM] Starting deployment for Proxy & Failover Routing...")

                ssh_proxy = None
                try:
                    # 1. Gather all gateways
                    failover_gateways = p_conf.get("failover_gateways", [])
                    gateways = []
                    for gw_ip in failover_gateways:
                        node = next((n for n in config.get("nodes", []) if n.get("ip") == gw_ip), None)
                        if node:
                            gateways.append(node)
                            
                    if not gateways:
                        raise Exception("No active gateways selected.")
                        
                    # For each gateway: connect and create a client config for the proxy
                    # Interface on proxy will be named: awg-failover-{slug}
                    iface_names = []
                    
                    for node in gateways:
                        slug = re.sub(r'[^a-zA-Z0-9_]', '_', node['name']).lower()[:10]
                        iface_name = f"awg-failover-{slug}"
                        iface_names.append(iface_name)

                        send_msg(f"\n[SYSTEM] Gateway {node['name']} ({node['ip']}) ➔ Creating AmneziaWG peer for proxy...")

                        ssh_gw = None
                        try:
                            ssh_gw = paramiko.SSHClient()
                            ssh_gw.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                            ssh_gw.connect(node["ip"], username=node["user"], key_filename=SSH_KEY_PATH, timeout=10)

                            sudo_gw = "sudo " if node["user"] != "root" else ""

                            # Add client peer 'peer_proxy'
                            ssh_gw.exec_command(f"{sudo_gw}awg2 --add-client peer_proxy")
                            time.sleep(2)

                            # Fetch the client conf
                            stdin, stdout, stderr = ssh_gw.exec_command(f"{sudo_gw}cat /root/peer_proxy_awg2.conf")
                            client_conf = stdout.read().decode('utf-8', errors='ignore')
                        finally:
                            if ssh_gw is not None:
                                ssh_gw.close()
                        
                        if not client_conf or "[Interface]" not in client_conf:
                            raise Exception(f"Failed to generate proxy peer config on gateway {node['name']}")
                            
                        # Modify the client config to turn off default routing table
                        lines = client_conf.split('\n')
                        new_lines = []
                        in_interface = False
                        for line in lines:
                            if line.strip().lower() == "[interface]":
                                in_interface = True
                                new_lines.append(line)
                                new_lines.append("Table = off")
                                continue
                            if in_interface and line.strip().startswith("["):
                                in_interface = False
                            if in_interface and line.strip().lower().startswith("table"):
                                continue
                            new_lines.append(line)
                        clean_client_conf = '\n'.join(new_lines)
                        
                        # Deploy config to Proxy Server
                        send_msg(f"[SYSTEM] Proxy Server ({proxy_node['ip']}) ➔ Saving interface {iface_name}...")
                        ssh_proxy = paramiko.SSHClient()
                        ssh_proxy.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh_proxy.connect(proxy_node["ip"], username=proxy_node["user"], key_filename=SSH_KEY_PATH, timeout=10)

                        sudo_proxy = "sudo " if proxy_node["user"] != "root" else ""
                        conf_path = f"/etc/amnezia/amneziawg/{iface_name}.conf"

                        # Write conf file
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}tee {shlex.quote(conf_path)} > /dev/null")
                        stdin.write(clean_client_conf.encode('utf-8'))
                        stdin.flush()
                        stdin.channel.shutdown_write()
                        stdout.read()

                        # Start interface
                        send_msg(f"[SYSTEM] Proxy Server ➔ Starting interface {iface_name}...")
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl daemon-reload")
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl enable awg-quick@{shlex.quote(iface_name)}")
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}systemctl restart awg-quick@{shlex.quote(iface_name)}")
                        stdout.read()
                        ssh_proxy.close()
                        send_msg(f"[SYSTEM] Gateway {node['name']} connection configured successfully!")

                    # 2. Deploy vpn-route-monitor.sh to Proxy Server
                    send_msg(f"\n[SYSTEM] Proxy Server ➔ Generating dynamic failover routing script...")
                    ssh_proxy = paramiko.SSHClient()
                    ssh_proxy.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh_proxy.connect(proxy_node["ip"], username=proxy_node["user"], key_filename=SSH_KEY_PATH, timeout=10)
                    
                    sudo_proxy = "sudo " if proxy_node["user"] != "root" else ""
                    
                    # Generate the interfaces list string for bash
                    interfaces_list_str = ' '.join([f'"{name}"' for name in iface_names])
                    
                    monitor_script_content = f"""#!/bin/bash
# Generated by AwgDashboard
set -e

TG_CIDRS=(
  "91.108.56.0/22"
  "91.108.4.0/22"
  "91.108.8.0/22"
  "91.108.16.0/22"
  "91.108.12.0/22"
  "149.154.160.0/20"
  "91.105.192.0/23"
  "91.108.20.0/22"
  "185.76.151.0/24"
)

# List of interfaces in priority order
IFACES=( {interfaces_list_str} )

# Ensure interfaces are up
for iface in "${{IFACES[@]}}"; do
    ip link show "$iface" >/dev/null 2>&1 || awg-quick up "$iface"
done

# Initialize routes
echo "Initializing TG routes..."
for cidr in "${{TG_CIDRS[@]}}"; do
    for i in "${{!IFACES[@]}}"; do
        iface="${{IFACES[$i]}}"
        metric=$(( (i + 1) * 10 ))
        ip route del "$cidr" dev "$iface" 2>/dev/null || true
        ip route add "$cidr" dev "$iface" metric "$metric"
    done
done

# Status tracking
STATES=()
for iface in "${{IFACES[@]}}"; do
    STATES+=( 1 )
done

check_tunnel() {{
    local iface="$1"
    curl -s --interface "$iface" --max-time 3 https://1.1.1.1 >/dev/null 2>&1 || \\
    curl -s --interface "$iface" --max-time 3 https://8.8.8.8 >/dev/null 2>&1
}}

echo "Monitoring active. Loop started."

while true; do
    for i in "${{!IFACES[@]}}"; do
        iface="${{IFACES[$i]}}"
        metric=$(( (i + 1) * 10 ))
        
        if check_tunnel "$iface"; then
            if [ "${{STATES[$i]}}" -eq 0 ]; then
                echo "$(date): $iface recovered! Restoring route."
                for cidr in "${{TG_CIDRS[@]}}"; do
                    ip route del "$cidr" dev "$iface" 2>/dev/null || true
                    ip route add "$cidr" dev "$iface" metric "$metric"
                done
                STATES[$i]=1
            fi
        else
            if [ "${{STATES[$i]}}" -eq 1 ]; then
                echo "$(date): $iface failed! Removing route."
                for cidr in "${{TG_CIDRS[@]}}"; do
                    ip route del "$cidr" dev "$iface" 2>/dev/null || true
                done
                STATES[$i]=0
            fi
        fi
    done
    sleep 15
done
"""
                    
                    # Write the script
                    script_path = "/usr/local/bin/vpn-route-monitor.sh"
                    stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}tee {shlex.quote(script_path)} > /dev/null")
                    stdin.write(monitor_script_content.encode('utf-8'))
                    stdin.flush()
                    stdin.channel.shutdown_write()
                    stdout.read()

                    ssh_proxy.exec_command(f"{sudo_proxy}chmod +x {shlex.quote(script_path)}")

                    # Restart route monitor service
                    send_msg("[SYSTEM] Proxy Server ➔ Restarting vpn-route-monitor service...")
                    ssh_proxy.exec_command(f"{sudo_proxy}systemctl daemon-reload")
                    ssh_proxy.exec_command(f"{sudo_proxy}systemctl enable vpn-route-monitor.service")
                    stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}systemctl restart vpn-route-monitor.service")
                    stdout.read()

                    # 3. Deploy MTProto Proxy daemon
                    active_daemon = p_conf.get("active_daemon", "mtproxymax")
                    port = p_conf.get("port", 443)
                    domain = p_conf.get("fake_tls_domain", "disk.yandex.ru")
                    users = p_conf.get("users", [])
                    
                    if active_daemon == "mtg":
                        send_msg(f"\n[SYSTEM] Proxy Server ➔ Deploying MTG (Go MTProto) on port {port} with SNI {domain}...")
                        
                        # Generate MTG config
                        # MTG uses the first user's secret
                        main_secret = users[0]["secret"] if users else "83b231c9ccf32ef09f48c8f63765ab4f"
                        # encode domain in hex
                        domain_hex = domain.encode('utf-8').hex()
                        ee_secret = "ee" + main_secret + domain_hex

                        mtg_toml = f"""secret = "{_toml_escape(ee_secret)}"
bind-to = "0.0.0.0:{int(port)}"
"""
                        # Write mtg config
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}tee /etc/mtg.toml > /dev/null")
                        stdin.write(mtg_toml.encode('utf-8'))
                        stdin.flush()
                        stdin.channel.shutdown_write()
                        stdout.read()
                        
                        # Write mtg systemd service wrapper
                        mtg_service = f"""[Unit]
Description=MTG Telegram Go Proxy (Docker)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
TimeoutStartSec=0
Restart=always
ExecStartPre=-/usr/bin/docker kill mtg
ExecStartPre=-/usr/bin/docker rm mtg
ExecStart=/usr/bin/docker run --name mtg --network host -v /etc/mtg.toml:/config.toml nineseconds/mtg:2 run /config.toml
ExecStop=/usr/bin/docker stop mtg

[Install]
WantedBy=multi-user.target
"""
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}tee /etc/systemd/system/mtg.service > /dev/null")
                        stdin.write(mtg_service.encode('utf-8'))
                        stdin.flush()
                        stdin.channel.shutdown_write()
                        stdout.read()
                        
                        # Enable and start mtg, disable mtproxymax
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl daemon-reload")
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl stop mtproxymax 2>/dev/null || true")
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl disable mtproxymax 2>/dev/null || true")
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl enable mtg.service")
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}systemctl restart mtg.service")
                        stdout.read()
                        
                        # Cleanup any dangling mtproxymax containers
                        ssh_proxy.exec_command(f"{sudo_proxy}docker stop mtproxymax 2>/dev/null || true")
                        ssh_proxy.exec_command(f"{sudo_proxy}docker rm mtproxymax 2>/dev/null || true")
                        
                        send_msg("[SYSTEM] MTG Proxy deployed and started successfully!")
                        
                    else: # mtproxymax
                        send_msg(f"\n[SYSTEM] Proxy Server ➔ Deploying MTProxyMax (C MTProto) on port {int(port)} with SNI {domain}...")

                        # Generate users TOML block (escaped)
                        users_toml = ""
                        for u in users:
                            users_toml += f'{_toml_escape(u["name"])} = "{_toml_escape(u["secret"])}"\n'

                        mtproxy_toml = f"""# MTProxyMax — telemt configuration
# Generated by AwgDashboard

[general]
prefer_ipv6 = false
fast_mode = true
use_middle_proxy = true
log_level = "normal"

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = ["default"]

[server]
port = {int(port)}
listen_addr_ipv4 = "0.0.0.0"
listen_addr_ipv6 = "::"
proxy_protocol = false

metrics_listen = "127.0.0.1:9090"
metrics_whitelist = ["127.0.0.1", "::1"]

[timeouts]
client_handshake = 30
tg_connect = 10
client_keepalive = 15
client_ack = 90

[censorship]
tls_domain = "{_toml_escape(domain)}"
unknown_sni_action = "mask"
mask = true
mask_port = {int(port)}
mask_host = "{_toml_escape(domain)}"
fake_cert_len = 2048

[access]
replay_check_len = 65536
replay_window_secs = 1800
ignore_time_skew = false

[access.users]
{users_toml}

[[upstreams]]
type = "direct"
weight = 10
"""
                        # Write config
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}tee /opt/mtproxymax/mtproxy/config.toml > /dev/null")
                        stdin.write(mtproxy_toml.encode('utf-8'))
                        stdin.flush()
                        stdin.channel.shutdown_write()
                        stdout.read()
                        
                        # Stop mtg, start mtproxymax
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl stop mtg 2>/dev/null || true")
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl disable mtg 2>/dev/null || true")
                        ssh_proxy.exec_command(f"{sudo_proxy}docker stop mtg 2>/dev/null || true")
                        ssh_proxy.exec_command(f"{sudo_proxy}docker rm mtg 2>/dev/null || true")
                        
                        ssh_proxy.exec_command(f"{sudo_proxy}systemctl enable mtproxymax")
                        stdin, stdout, stderr = ssh_proxy.exec_command(f"{sudo_proxy}systemctl restart mtproxymax")
                        stdout.read()
                        
                        send_msg("[SYSTEM] MTProxyMax deployed and restarted successfully!")

                    send_msg("\n[SYSTEM] Proxy and Failover Routing deployed successfully!")

                except Exception as e:
                    send_msg(f"\n[ERROR] Deployment failed: {e}")
                finally:
                    if ssh_proxy is not None:
                        ssh_proxy.close()

            elif action_type == "generate-client-config":
                chain_id = params.get("chain_id", [""])[0]
                client_name = params.get("client_name", ["client_user"])[0]
                
                config = load_config()
                chains = config.get("chains", [])
                chain = next((c for c in chains if c.get("id") == chain_id), None)
                if not chain:
                    node_name = chain_id
                    node = next((n for n in config["nodes"] if n.get("name") == node_name or n.get("ip") == node_name), None)
                else:
                    hops_list = chain.get("hops", [])
                    entrance_ip = hops_list[0] if hops_list else ""
                    node = next((n for n in config["nodes"] if n.get("ip") == entrance_ip), None)
                    
                if not node:
                    send_msg("[ERROR] Entrance node not found.")
                    return
                    
                send_msg(f"[SYSTEM] Connecting to entrance node {node['name']} ({node['ip']}) to generate user configuration...")
                ssh = None
                try:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(node["ip"], username=node["user"], key_filename=SSH_KEY_PATH, timeout=10)

                    sudo_prefix = "sudo " if node["user"] != "root" else ""
                    clean_client_name = re.sub(r'[^a-zA-Z0-9_]', '_', client_name).lower()

                    send_msg(f"[{node['name']}] Executing: {sudo_prefix}awg2 --add-client {clean_client_name}")
                    ssh.exec_command(f"{sudo_prefix}awg2 --add-client {shlex.quote(clean_client_name)}")
                    time.sleep(2)

                    stdin, stdout, stderr = ssh.exec_command(f"{sudo_prefix}cat /root/{shlex.quote(clean_client_name)}_awg2.conf")
                    conf_content = stdout.read().decode('utf-8', errors='ignore')

                    if not conf_content or "[Interface]" not in conf_content:
                        send_msg(f"[ERROR] Failed to read generated config /root/{clean_client_name}_awg2.conf")
                        return

                    send_msg("\n[CONFIG_START]")
                    send_msg(conf_content)
                    send_msg("[CONFIG_END]")
                    send_msg("[SYSTEM] User configuration generated successfully!")
                except Exception as e:
                    send_msg(f"[ERROR] Failed to generate configuration: {e}")
                finally:
                    if ssh is not None:
                        ssh.close()
            else:
                send_msg("[ERROR] Unknown action.")

def run_server():
    server_address = ('0.0.0.0', 8060)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    print(f"============================================================")
    print(f"  iDoctor Control Center Server started successfully!")
    if DASHBOARD_AUTH_TOKEN:
        print(f"  Auth token: {DASHBOARD_AUTH_TOKEN}")
        print(f"  (pass as ?token=... or Authorization: Bearer ...)")
    else:
        print(f"  WARNING: No auth token configured!")
    print(f"  Open your browser and navigate to: http://localhost:8060")
    print(f"  Press Ctrl+C to terminate.")
    print(f"============================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()
        sys.exit(0)

if __name__ == "__main__":
    print("Performing initial status checks on all servers...")
    poll_all_servers()
    
    t = threading.Thread(target=background_poller, daemon=True)
    t.start()
    
    run_server()
