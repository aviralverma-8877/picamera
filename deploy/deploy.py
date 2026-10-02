"""Copy app/ and deploy/ to the Pi, install the systemd service, restart it.

Run from the dev machine: ASTRO_PI_SSH_PASSWORD=<password> python deploy/deploy.py
"""
import os
import posixpath
import sys

import paramiko

HOST = os.environ.get("ASTRO_PI_HOST", "192.168.1.35")
USER = os.environ.get("ASTRO_PI_SSH_USER", "pi")
PASSWORD = os.environ.get("ASTRO_PI_SSH_PASSWORD")
REMOTE_ROOT = "/home/pi/astro-pi-cam"

if not PASSWORD:
    sys.exit("Set ASTRO_PI_SSH_PASSWORD in your environment before running this script.")

LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def put_dir(sftp, local_dir, remote_dir):
    try:
        sftp.mkdir(remote_dir)
    except IOError:
        pass
    for name in os.listdir(local_dir):
        if name == "captures" or name.startswith("__pycache__"):
            continue
        local_path = os.path.join(local_dir, name)
        remote_path = posixpath.join(remote_dir, name)
        if os.path.isdir(local_path):
            put_dir(sftp, local_path, remote_path)
        else:
            sftp.put(local_path, remote_path)
            print(f"  {remote_path}")


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=10)

    sftp = client.open_sftp()
    try:
        sftp.mkdir(REMOTE_ROOT)
    except IOError:
        pass

    print("Uploading app/ ...")
    put_dir(sftp, os.path.join(LOCAL_ROOT, "app"), posixpath.join(REMOTE_ROOT, "app"))
    print("Uploading deploy/ ...")
    put_dir(sftp, os.path.join(LOCAL_ROOT, "deploy"), posixpath.join(REMOTE_ROOT, "deploy"))
    sftp.close()

    print("Installing systemd service and restarting...")
    cmds = [
        f"chmod +x {REMOTE_ROOT}/deploy/*.sh",
        f"sudo -S cp {REMOTE_ROOT}/deploy/astro-pi-cam.service /etc/systemd/system/",
        "sudo -S systemctl daemon-reload",
        "sudo -S systemctl enable astro-pi-cam.service",
        "sudo -S systemctl restart astro-pi-cam.service",
        "sleep 2 && systemctl is-active astro-pi-cam.service",
    ]
    for c in cmds:
        stdin, stdout, stderr = client.exec_command(c)
        if c.startswith("sudo -S"):
            stdin.write(PASSWORD + "\n")
            stdin.flush()
        out = stdout.read().decode(errors="replace").strip()
        err = stderr.read().decode(errors="replace").strip()
        print(f"$ {c}")
        if out:
            print(out)
        if err and "password for" not in err:
            print("ERR:", err)

    client.close()
    print("Done.")


if __name__ == "__main__":
    main()
