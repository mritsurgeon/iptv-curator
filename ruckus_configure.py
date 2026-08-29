#!/usr/bin/env python3
import subprocess

RUCKUS_IP = "10.10.0.1"
USERNAME = "super"
PASSWORD = "sp-admin"

def send_commands(commands):
    cmd_sequence = ""
    for c in commands:
        cmd_sequence += f'send "{c}\\r"\n'
        cmd_sequence += 'expect -re "ruckus#|ruckus\\(config\\)#|ruckus\\(config-wlan\\)#|ruckus>"\n'

    expect_script = f"""
set timeout 15
spawn ssh -o StrictHostKeyChecking=no -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa {USERNAME}@{RUCKUS_IP}

expect {{
    -re "Please login:|login:|Login:" {{
        send "{USERNAME}\\r"
        exp_continue
    }}
    -re "password:|Password:" {{
        send "{PASSWORD}\\r"
    }}
    timeout {{
        exit 1
    }}
}}

expect {{
    -re "ruckus>|ruckus#" {{
        send "enable\\r"
    }}
    timeout {{
        exit 1
    }}
}}

expect {{
    "Password:" {{
        send "{PASSWORD}\\r"
        exp_continue
    }}
    "ruckus#" {{
        {cmd_sequence}
        send "exit\\r"
    }}
}}
expect eof
"""
    cmd = ["expect", "-c", expect_script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout

if __name__ == "__main__":
    out = send_commands([
        "show wlan name Surgeons_WIFI"
    ])
    print(out)
