import os
import sys
import json
import time
import socket
import threading
import contextlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import paramiko

# Local SSH Key
SSH_KEY_PATH = os.path.expanduser(r"C:\Users\dante\.ssh\id_ed25519") if os.name == 'nt' else os.path.expanduser("~/.ssh/id_ed25519")


# Infrastructure Topology
SERVERS = [
    {
        "name": "Proxy Server",
        "ip": "158.160.231.158",
        "role": "proxy",
        "user": "lcp",
        "auth": "key",
        "country": "RU",
        "services": ["vpn-route-monitor", "mtproxymax"]
    },
    {
        "name": "Gateway 1 (GW1)",
        "ip": "dns.idoctor.mom",
        "role": "gateway",
        "user": "user1",
        "auth": "key",
        "country": "RU",
        "services": ["awg-xray", "AdGuardHome"]
    },
    {
        "name": "Gateway 2 (GW2)",
        "ip": "love.idoctor.mom",
        "role": "gateway",
        "user": "root",
        "auth": "key",
        "country": "RU",
        "services": ["awg-xray"]
    },
    {
        "name": "Exit Node 1 (NL-1)",
        "ip": "144.31.224.212",
        "role": "exit",
        "country": "NL"
    },
    {
        "name": "Exit Node 2 (NL-2)",
        "ip": "144.31.157.106",
        "role": "exit",
        "country": "NL"
    },
    {
        "name": "Exit Node 3 (DE)",
        "ip": "150.241.99.63",
        "role": "exit",
        "country": "DE"
    },
    {
        "name": "Exit Node 4 (FI)",
        "ip": "109.206.243.202",
        "role": "exit",
        "country": "FI"
    },
    {
        "name": "Exit Node 5 (PL-GCP)",
        "ip": "34.158.233.138",
        "role": "exit",
        "country": "PL"
    },
    {
        "name": "Exit Node 6 (FI-GCP)",
        "ip": "34.88.71.12",
        "role": "exit",
        "country": "FI"
    }
]

# Thread-safe global state
server_status_cache = {}
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

def collect_server_status(srv):
    name = srv["name"]
    ip = srv["ip"]
    role = srv["role"]
    
    status = {
        "name": name,
        "ip": ip,
        "role": role,
        "country": srv["country"],
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
        # Connect via SSH
        ssh = get_ssh_client(srv)

        # CPU Usage using vmstat (lightweight, no CPU spikes)
        vmstat_out = execute_remote(ssh, "vmstat 1 2")
        try:
            last_line = vmstat_out.splitlines()[-1].split()
            idle = float(last_line[14])
            status["cpu"] = f"{100.0 - idle:.1f}%"
        except Exception:
            status["cpu"] = "Error"

        # RAM Usage
        ram_cmd = "free -m | awk 'NR==2{printf \"%s/%s MB (%.0f%%)\", $3,$2,$3*100/$2}'"
        status["ram"] = execute_remote(ssh, ram_cmd)

        # Uptime
        status["uptime"] = execute_remote(ssh, "uptime -p")

        # Services checks
        if "services" in srv:
            for svc in srv["services"]:
                # Check systemd service status
                svc_status = execute_remote(ssh, f"systemctl is-active {svc}")
                status["services"][svc] = svc_status

        # VPN / WireGuard checks
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

    except Exception as e:
        status["online"] = False
        status["error"] = str(e)
    finally:
        if ssh is not None:
            ssh.close()

    return status

def poll_all_servers():
    global server_status_cache
    temp_cache = {}
    threads = []
    
    # Filter exit nodes out of SSH polling for security
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
        
    # Populate exit node states purely from Gateway handshakes
    for srv in exit_servers:
        name = srv["name"]
        ip = srv["ip"]
        role = srv["role"]
        country = srv["country"]
        
        # Determine exit interface ID
        exit_num = 1
        if "NL-2" in name: exit_num = 2
        elif "(DE)" in name: exit_num = 3
        elif "(FI)" in name and "GCP" not in name: exit_num = 4
        elif "PL-GCP" in name: exit_num = 5
        elif "FI-GCP" in name: exit_num = 6
        
        iface_name = f"awg-exit-n{exit_num}"
        is_online = False
        
        # Check GW1 status
        gw1 = temp_cache.get("Gateway 1 (GW1)")
        if gw1 and gw1.get("online") and gw1.get("vpn", {}).get(iface_name):
            peer = gw1["vpn"][iface_name]["peers"][0]
            if peer.get("handshake") and peer["handshake"] != "N/A":
                is_online = True
                
        # Check GW2 status
        if not is_online:
            gw2 = temp_cache.get("Gateway 2 (GW2)")
            if gw2 and gw2.get("online") and gw2.get("vpn", {}).get(iface_name):
                peer = gw2["vpn"][iface_name]["peers"][0]
                if peer.get("handshake") and peer["handshake"] != "N/A":
                    is_online = True
                    
        temp_cache[name] = {
            "name": name,
            "ip": ip,
            "role": role,
            "country": country,
            "online": is_online,
            "cpu": "N/A",
            "ram": "N/A",
            "uptime": "N/A",
            "services": {},
            "vpn": {},
            "error": None
        }
        
    with cache_lock:
        server_status_cache = temp_cache

def background_poller():
    while True:
        try:
            poll_all_servers()
        except Exception as e:
            print(f"Poller error: {e}")
        time.sleep(15)

# HTML / CSS / JS UI Content
HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>iDoctor VPN Network Control Center</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #080914;
            --bg-secondary: rgba(18, 22, 47, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --neon-cyan: #00f2fe;
            --neon-purple: #9b51e0;
            --neon-green: #10b981;
            --neon-red: #f43f5e;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
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
                radial-gradient(circle at 10% 20%, rgba(155, 81, 224, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(0, 242, 254, 0.08) 0%, transparent 40%);
        }

        header {
            padding: 24px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--card-border);
            background: rgba(8, 9, 20, 0.8);
            backdrop-filter: blur(12px);
            position: sticky;
            top: 0;
            z-index: 100;
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
            background: linear-gradient(135deg, #00f2fe, #4facfe);
            border: none;
            color: #080914;
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 0 15px rgba(0, 242, 254, 0.3);
        }

        .btn-refresh:hover {
            transform: translateY(-2px);
            box-shadow: 0 0 25px rgba(0, 242, 254, 0.5);
        }

        .btn-refresh:active {
            transform: translateY(0);
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
            padding: 40px;
        }

        /* Topology Section */
        .topology-card {
            background: var(--bg-secondary);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 40px;
            backdrop-filter: blur(16px);
            position: relative;
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

        .topo-node {
            background: rgba(8, 9, 20, 0.9);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 16px 24px;
            min-width: 220px;
            text-align: center;
            transition: all 0.3s ease;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
        }

        .topo-node.active {
            border-color: var(--neon-cyan);
            box-shadow: 0 0 15px rgba(0, 242, 254, 0.15);
        }

        .topo-node.exit-active {
            border-color: var(--neon-green);
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.15);
        }

        .topo-node.exit-idle {
            border-color: var(--text-muted);
            opacity: 0.5;
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
            background: var(--bg-secondary);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(16px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
        }

        .server-card:hover {
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.15);
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

        /* Modal / Tool section */
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
    </style>
</head>
<body>
    <header>
        <div class="logo-section">
            <h1>iDOCTOR CONTROL CENTER</h1>
            <p>VPN & MTProto Proxy Infrastructure Monitor</p>
        </div>
        <div class="controls">
            <button class="btn-toggle-ips" onclick="toggleIps()" style="margin-right: 10px;">HIDE IPs</button>
            <button class="btn-refresh" onclick="refreshData()">FORCE REFRESH</button>
        </div>
    </header>

    <div class="container">
        <!-- Topology Visualization -->
        <div class="topology-card">
            <h2>NETWORK TRANSIT TOPOLOGY</h2>
            <div class="topo-container">
                <svg class="topo-connector" id="connector-svg"></svg>
                
                <!-- Proxy Node -->
                <div class="topo-column">
                    <div class="topo-node active" id="node-proxy">
                        <h3>Proxy Server</h3>
                        <p><span class="ip-address">158.160.231.158</span></p>
                        <p style="margin-top: 4px; color: var(--neon-cyan);" id="proxy-path-text">Active path: main</p>
                    </div>
                </div>

                <!-- Gateway Nodes -->
                <div class="topo-column">
                    <div class="topo-node active" id="node-gw1" onclick="selectGateway('Gateway 1 (GW1)')" style="cursor: pointer; transition: all 0.3s;">
                        <h3><span class="domain-name">dns.idoctor.mom</span></h3>
                        <p><span class="ip-address">84.54.59.160</span></p>
                        <p style="margin-top: 4px; color: var(--neon-green);" id="gw1-load-text">Load: 0%</p>
                        <p style="margin-top: 2px; color: var(--text-muted); font-size: 11px;" id="gw1-users-text">Clients: 0 active</p>
                    </div>
                    <div class="topo-node" id="node-gw2" onclick="selectGateway('Gateway 2 (GW2)')" style="cursor: pointer; transition: all 0.3s; opacity: 0.8;">
                        <h3><span class="domain-name">love.idoctor.mom</span></h3>
                        <p><span class="ip-address">185.173.37.215</span></p>
                        <p style="margin-top: 4px; color: var(--text-muted);" id="gw2-load-text">Backup Idle</p>
                        <p style="margin-top: 2px; color: var(--text-muted); font-size: 11px;" id="gw2-users-text">Clients: 0 active</p>
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

        <!-- Server List Grid -->
        <div class="server-grid" id="servers-grid">
            <!-- Dynamic Content -->
        </div>

        <!-- Actions Panel -->
        <div class="actions-panel">
            <h2>INFRASTRUCTURE ACTIONS</h2>
            <div class="actions-buttons">
                <button class="btn-action" onclick="runAction('speedtest')">Run Speed Test</button>
                <button class="btn-action" onclick="runAction('restart-xray')">Restart Xray (GW1)</button>
                <button class="btn-action" onclick="runAction('restart-proxy-monitor')">Restart Failover Daemon (Proxy)</button>
            </div>
        </div>
    </div>

    <!-- Terminal Output Modal -->
    <div class="modal" id="terminal-modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 id="modal-title">Action Execution</h3>
                <button class="btn-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="terminal-output" id="terminal-text">Connecting...</div>
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

        let ipsHidden = false;
        let globalData = null;
        let selectedGateway = null;
        const expandedCards = new Set();

        function toggleCardVpn(srvName) {
            const safeName = srvName.replace(/\\s+/g, '-');
            const el = document.getElementById(`vpn-details-${safeName}`);
            const icon = document.getElementById(`vpn-toggle-icon-${safeName}`);
            if (expandedCards.has(srvName)) {
                expandedCards.delete(srvName);
                if (el) el.style.display = 'none';
                if (icon) {
                    icon.innerHTML = '&#9660;'; // ▼
                }
            } else {
                expandedCards.add(srvName);
                if (el) el.style.display = 'block';
                if (icon) {
                    icon.innerHTML = '&#9650;'; // ▲
                }
            }
        }

        function toggleIps() {
            ipsHidden = !ipsHidden;
            const btn = document.querySelector('.btn-toggle-ips');
            if (ipsHidden) {
                document.body.classList.add('hide-ips');
                btn.innerText = 'SHOW IPs';
                btn.style.borderColor = 'var(--neon-purple)';
                btn.style.boxShadow = '0 0 10px rgba(155, 81, 224, 0.3)';
            } else {
                document.body.classList.remove('hide-ips');
                btn.innerText = 'HIDE IPs';
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

        function drawConnectors() {
            const svg = document.getElementById('connector-svg');
            svg.innerHTML = '';
            
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
                line.setAttribute('stroke-width', active ? '2.5' : '1');
                if (active) {
                    line.setAttribute('stroke-dasharray', '5, 5');
                    line.innerHTML = `<animate attributeName="stroke-dashoffset" values="50;0" dur="2s" repeatCount="indefinite" />`;
                } else {
                    line.setAttribute('opacity', '0.2');
                }
                svg.appendChild(line);
            }

            // Draw Proxy to Gateways dynamically based on active path
            let isMainActive = true;
            if (globalData && globalData["Proxy Server"] && globalData["Proxy Server"].vpn) {
                const awgMain = globalData["Proxy Server"].vpn["awg-main"];
                isMainActive = awgMain && awgMain.peers && awgMain.peers[0] && awgMain.peers[0].handshake !== "N/A";
            }

            drawLine(proxy, gw1, isMainActive, '#00f2fe');
            drawLine(proxy, gw2, !isMainActive, '#9b51e0');

            // Draw Selected Gateway to Exits based on Xray Priority Failover rules
            const selectedGwEl = (selectedGateway === "Gateway 1 (GW1)") ? gw1 : gw2;
            const selectedGwData = globalData ? globalData[selectedGateway] : null;

            // Step 1: Check handshakes for all exit interfaces
            const exitHandshakes = {};
            for (let i = 1; i <= 6; i++) {
                const ifaceName = `awg-exit-n${i}`;
                let hasHandshake = false;
                if (selectedGwData && selectedGwData.vpn && selectedGwData.vpn[ifaceName]) {
                    const peer = selectedGwData.vpn[ifaceName].peers[0];
                    hasHandshake = peer && peer.handshake && peer.handshake !== "N/A";
                }
                exitHandshakes[i] = hasHandshake;
            }

            // Step 2: Determine which exit nodes are routing traffic
            // Primary exits are Exit 5 (PL-GCP) and Exit 6 (FI-GCP)
            const isPrimaryActive = exitHandshakes[5] || exitHandshakes[6];

            const exitRoutingActive = {};
            if (isPrimaryActive) {
                // If primary exits are up, they are active; backups are in standby (idle)
                exitRoutingActive[1] = false;
                exitRoutingActive[2] = false;
                exitRoutingActive[3] = false;
                exitRoutingActive[4] = false;
                exitRoutingActive[5] = exitHandshakes[5];
                exitRoutingActive[6] = exitHandshakes[6];
            } else {
                // If both primary exits are down, backups with handshakes are active
                exitRoutingActive[1] = exitHandshakes[1];
                exitRoutingActive[2] = exitHandshakes[2];
                exitRoutingActive[3] = exitHandshakes[3];
                exitRoutingActive[4] = exitHandshakes[4];
                exitRoutingActive[5] = false;
                exitRoutingActive[6] = false;
            }

            exitNodes.forEach((node, idx) => {
                const exitNum = idx + 1;
                const isRouting = exitRoutingActive[exitNum];

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
                globalData = data;
                renderGrid(data);
                updateTopologyUI(data);
                setTimeout(drawConnectors, 100);
            } catch (e) {
                console.error("Failed to fetch server statuses", e);
            }
        }

        function updateTopologyUI(data) {
            const proxy = data["Proxy Server"];
            let hasMain = false;
            if (proxy && proxy.vpn) {
                const awgMain = proxy.vpn["awg-main"];
                hasMain = awgMain && awgMain.peers && awgMain.peers[0] && awgMain.peers[0].handshake !== "N/A";
            }
            
            if (selectedGateway === null) {
                selectedGateway = hasMain ? "Gateway 1 (GW1)" : "Gateway 2 (GW2)";
            }

            const pathText = document.getElementById('proxy-path-text');
            if (hasMain) {
                pathText.innerHTML = "Active path: <span class='domain-name'>dns.idoctor.mom</span> (Main)";
                pathText.style.color = 'var(--neon-cyan)';
            } else {
                pathText.innerHTML = "Active path: <span class='domain-name'>love.idoctor.mom</span> (Backup)";
                pathText.style.color = 'var(--neon-purple)';
            }

            const gw1Node = document.getElementById('node-gw1');
            const gw2Node = document.getElementById('node-gw2');
            
            if (selectedGateway === "Gateway 1 (GW1)") {
                gw1Node.classList.add('active');
                gw1Node.style.borderColor = 'var(--neon-cyan)';
                gw1Node.style.opacity = '1';
                gw2Node.classList.remove('active');
                gw2Node.style.borderColor = 'var(--card-border)';
                gw2Node.style.opacity = '0.6';
            } else {
                gw2Node.classList.add('active');
                gw2Node.style.borderColor = 'var(--neon-purple)';
                gw2Node.style.opacity = '1';
                gw1Node.classList.remove('active');
                gw1Node.style.borderColor = 'var(--card-border)';
                gw1Node.style.opacity = '0.6';
            }

            const gw1Data = data["Gateway 1 (GW1)"];
            if (gw1Data && gw1Data.online) {
                document.getElementById('gw1-load-text').innerText = "Load: " + gw1Data.cpu;
            } else {
                document.getElementById('gw1-load-text').innerText = "Offline";
            }
            
            const gw2Data = data["Gateway 2 (GW2)"];
            if (gw2Data && gw2Data.online) {
                document.getElementById('gw2-load-text').innerText = "Load: " + gw2Data.cpu;
            } else {
                document.getElementById('gw2-load-text').innerText = "Offline";
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
                    const isActive = peer.handshake && peer.handshake !== "N/A";
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
            const grid = document.getElementById('servers-grid');
            grid.innerHTML = '';

            Object.values(data).forEach(srv => {
                const card = document.createElement('div');
                card.className = `server-card ${srv.online ? 'online' : 'offline'}`;

                const servicesHTML = Object.entries(srv.services || {}).map(([name, status]) => {
                    const active = status === "active";
                    return `<span class="service-badge ${active ? 'active' : 'inactive'}">${name}: ${status}</span>`;
                }).join('');

                const vpnHTML = Object.entries(srv.vpn || {}).map(([iface, info]) => {
                    const peersHTML = info.peers.map((peer, pIdx) => {
                        const isActive = peer.handshake && peer.handshake !== "N/A";
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

                card.innerHTML = `
                    <div class="card-header">
                        <div class="server-title">
                            <h3>${srv.name} <span class="flag">${countryFlags[srv.country] || ''}</span></h3>
                            <span><span class="ip-address">${srv.ip}</span></span>
                        </div>
                        <div class="status-badge ${srv.online ? 'online' : 'offline'}">
                            <span class="status-dot"></span>
                            ${srv.online ? 'ONLINE' : 'OFFLINE'}
                        </div>
                    </div>
                    
                    <div class="stats-list">
                        <div class="stat-item">
                            <span class="label">CPU Usage</span>
                            <span class="value" style="color: ${srv.cpu.includes('N/A') ? 'var(--text-muted)' : 'var(--neon-cyan)'}">${srv.cpu}</span>
                        </div>
                        <div class="stat-item">
                            <span class="label">RAM Usage</span>
                            <span class="value">${srv.ram}</span>
                        </div>
                        <div class="stat-item">
                            <span class="label">Uptime</span>
                            <span class="value" style="font-size:12px;">${srv.uptime}</span>
                        </div>
                    </div>

                    ${vpnHTML ? `
                        <div class="vpn-section">
                            <div style="display:flex; justify-content:space-between; align-items:center; cursor:pointer;" onclick="toggleCardVpn('${srv.name}')">
                                <h4 style="margin:0;">ACTIVE VPN TUNNELS</h4>
                                <span id="vpn-toggle-icon-${srv.name.replace(/\\s+/g, '-')}" style="font-size:12px; color:var(--neon-cyan); font-weight:600;">${isExpanded ? '&#9650;' : '&#9660;'}</span>
                            </div>
                            <div id="vpn-details-${srv.name.replace(/\\s+/g, '-')}" style="display:${displayStyle}; margin-top:10px;">
                                ${vpnHTML}
                            </div>
                        </div>
                    ` : ''}
                    ${servicesHTML ? `<div class="services-badges">${servicesHTML}</div>` : ''}
                    ${srv.error ? `<div style="margin-top: 12px; font-size: 11px; color: var(--neon-red); font-family: 'JetBrains Mono', monospace;">${srv.error}</div>` : ''}
                `;

                grid.appendChild(card);
            });
        }

        async function runAction(action) {
            const modal = document.getElementById('terminal-modal');
            const term = document.getElementById('terminal-text');
            const title = document.getElementById('modal-title');
            
            modal.style.display = 'flex';
            term.innerText = 'Connecting and executing action on remote hosts...\\n';
            
            if (action === 'speedtest') {
                title.innerText = 'Execution: Sequential Speed Test';
            } else if (action === 'restart-xray') {
                title.innerText = 'Execution: Restart Xray Balancer';
            } else {
                title.innerText = 'Execution: Restart Failover Daemon';
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
                term.innerText += `\\n[ERROR] Action failed: ${e}`;
            }
        }

        function closeModal() {
            document.getElementById('terminal-modal').style.display = 'none';
            fetchStatus();
        }

        function refreshData() {
            const btn = document.querySelector('.btn-refresh');
            btn.innerText = 'REFRESHING...';
            btn.disabled = true;
            fetch('/api/force-refresh', { method: 'POST' })
                .then(() => fetchStatus())
                .finally(() => {
                    btn.innerText = 'FORCE REFRESH';
                    btn.disabled = false;
                });
        }

        window.addEventListener('resize', drawConnectors);
        
        // Initial load
        fetchStatus();
        
        // Auto-refresh every 20s
        setInterval(fetchStatus, 20000);
    </script>
</body>
</html>
"""

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress logging of HTTP requests to console to keep output clean
        return
        
    def do_GET(self):
        url_parsed = urllib.parse.urlparse(self.path)
        
        if url_parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode('utf-8'))
            
        elif url_parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with cache_lock:
                # Sorted statuses by type (proxy first, then gateways, then exits)
                sorted_status = dict(sorted(
                    server_status_cache.items(),
                    key=lambda x: (
                        0 if x[1]["role"] == "proxy"
                        else (1 if x[1]["role"] == "gateway" else 2),
                        x[0]
                    )
                ))
                self.wfile.write(json.dumps(sorted_status).encode('utf-8'))
                
        else:
            self.send_response(404)
            self.end_headers()
            
    def do_POST(self):
        url_parsed = urllib.parse.urlparse(self.path)
        
        if url_parsed.path == "/api/force-refresh":
            poll_all_servers()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            
        elif url_parsed.path == "/api/action":
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
                send_msg("[SYSTEM] Starting sequential speed test on all 11 nodes...")
                # Run sequential speed test script locally
                import subprocess
                script_dir = os.path.dirname(os.path.abspath(__file__))
                p = subprocess.Popen(
                    [sys.executable, "-u", "run_speedtests_seq.py"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=script_dir
                )
                for line in p.stdout:
                    send_msg(line.strip())
                p.wait()
                send_msg("\n[SYSTEM] Speed test completed successfully!")
                
            elif action_type == "restart-xray":
                send_msg("[SYSTEM] Connecting to Gateways to restart Xray...")
                for gw in [
                    {"name": "dns.idoctor.mom", "user": "user1", "sudo": True},
                    {"name": "love.idoctor.mom", "user": "root", "sudo": False}
                ]:
                    send_msg(f"\n[SYSTEM] Connecting to {gw['name']}...")
                    ssh = None
                    try:
                        ssh = paramiko.SSHClient()
                        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        ssh.connect(gw["name"], username=gw["user"], key_filename=SSH_KEY_PATH, timeout=5)

                        cmd = "sudo systemctl restart awg-xray" if gw["sudo"] else "systemctl restart awg-xray"
                        send_msg(f"[{gw['name']}] Executing: {cmd}")
                        stdin, stdout, stderr = ssh.exec_command(cmd)
                        stdout.read()

                        cmd_status = "sudo systemctl status awg-xray -n 2 --no-pager" if gw["sudo"] else "systemctl status awg-xray -n 2 --no-pager"
                        status_out = execute_remote(ssh, cmd_status)
                        send_msg(f"[{gw['name']}] Status:\n{status_out}")
                    except Exception as e:
                        send_msg(f"[ERROR] failed on {gw['name']}: {e}")
                    finally:
                        if ssh is not None:
                            ssh.close()

            elif action_type == "restart-proxy-monitor":
                send_msg("[SYSTEM] Connecting to Proxy Server...")
                ssh = None
                try:
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect("158.160.231.158", username="lcp", key_filename=SSH_KEY_PATH, timeout=5)

                    send_msg("[Proxy] Executing: sudo systemctl restart vpn-route-monitor")
                    stdin, stdout, stderr = ssh.exec_command("sudo systemctl restart vpn-route-monitor")
                    stdout.read()

                    status_out = execute_remote(ssh, "sudo systemctl status vpn-route-monitor -n 2 --no-pager")
                    send_msg(f"[Proxy] Status:\n{status_out}")
                except Exception as e:
                    send_msg(f"[ERROR] failed: {e}")
                finally:
                    if ssh is not None:
                        ssh.close()
            else:
                send_msg("[ERROR] Unknown action.")

def run_server():
    server_address = ('0.0.0.0', 8050)
    httpd = HTTPServer(server_address, DashboardHandler)
    print(f"============================================================")
    print(f"  iDoctor Control Center Server started successfully!")
    print(f"  Open your browser and navigate to: http://localhost:8050")
    print(f"  Press Ctrl+C to terminate.")
    print(f"============================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()
        sys.exit(0)

if __name__ == "__main__":
    # Perform initial status poll in main thread so cache has data immediately
    print("Performing initial status checks on all servers...")
    poll_all_servers()
    
    # Start poller in background thread (runs every 15s)
    t = threading.Thread(target=background_poller, daemon=True)
    t.start()
    
    # Start web server
    run_server()
