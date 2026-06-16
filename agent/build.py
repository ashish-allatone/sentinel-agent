import subprocess
import os

agents = [
    {"ip": "192.168.1.10", "name": "agent1"},
    {"ip": "192.168.1.11", "name": "agent2"},
]

for agent in agents:
    print(f"[+] Building {agent['name']}")

    # Create config dynamically
    with open("config.py", "w") as f:
        f.write(f'SERVER = "{agent["ip"]}"\n')
        f.write(f'NAME = "{agent["name"]}"\n')

    # Build using Nuitka
    subprocess.run([
        "python", "-m", "nuitka",
        "--onefile",
        "--output-dir=dist",
        f"--output-filename={agent['name']}.exe",
        "agent.py"
    ])

# Cleanup
if os.path.exists("config.py"):
    os.remove("config.py")