import os
import socket
import threading
import signal
from datetime import datetime

# ---------------- SAFE AWS IMPORT ----------------
try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception


# ---------------- CONFIG ----------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("FTP_PORT", 5001))
BUFFER = 4096
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION")

AWS_ENABLED = bool(AWS_REGION)


# ---------------- LOCAL STORAGE ----------------
LOCAL_DIR = "./uploads"
os.makedirs(LOCAL_DIR, exist_ok=True)

# ---------------- AWS CLIENTS ----------------
if AWS_ENABLED and boto3 is not None:
    session = boto3.Session(region_name=AWS_REGION)
    s3 = session.client("s3")
    cloudwatch = session.client("cloudwatch")
    print("[*] AWS MODE ENABLED")
else:
    session = None
    s3 = None
    cloudwatch = None
    print("[*] LOCAL MODE ENABLED (no AWS)")

# ---------------- STATE ----------------
clients = []
clients_lock = threading.Lock()
shutdown_event = threading.Event()
server_sock = None


# ---------------- CLOUDWATCH METRICS ----------------
def put_metric(metric_name, value, unit="Count"):
    if not AWS_ENABLED:
        return
    try:
        cloudwatch.put_metric_data(
            Namespace="MyFTPServer",
            MetricData=[{
                "MetricName": metric_name,
                "Value": float(value),
                "Unit": unit
            }]
        )
    except Exception as e:
        print("CloudWatch put_metric failed:", e)


# ---------------- CLIENT HANDLER ----------------
def handle_client(conn, addr):
    with clients_lock:
        clients.append(conn)
        put_metric("ActiveClients", len(clients))

    print(f"[+] Connected: {addr}")

    try:
        conn.settimeout(10.0)

        while not shutdown_event.is_set():
            data = conn.recv(BUFFER)
            if not data:
                break

            cmd = data.decode(errors="ignore").strip()
            parts = cmd.split()
            command = parts[0].lower() if parts else ""

            # ---------------- LS ----------------
            if command == "ls":
                rsp_lines = []

                if not AWS_ENABLED:
                    for name in os.listdir(LOCAL_DIR):
                        path = os.path.join(LOCAL_DIR, name)
                        size = os.path.getsize(path)
                        mod = datetime.fromtimestamp(
                            os.path.getmtime(path)
                        ).strftime("%Y-%m-%d %H:%M:%S")
                        rsp_lines.append(f"{size:12} {mod} {name}")
                else:
                    try:
                        paginator = s3.get_paginator("list_objects_v2")
                        for page in paginator.paginate(Bucket=S3_BUCKET):
                            for obj in page.get("Contents", []):
                                name = obj["Key"]
                                size = obj["Size"]
                                last_mod = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
                                rsp_lines.append(f"{size:12} {last_mod} {name}")
                    except ClientError as e:
                        conn.sendall(f"ERR S3 list error: {e}\n".encode())
                        continue

                if not rsp_lines:
                    conn.sendall(b"No files\n")
                else:
                    conn.sendall(("\n".join(rsp_lines) + "\n").encode())

            # ---------------- GET ----------------
            elif command == "get":
                if len(parts) < 2:
                    conn.sendall(b"ERR Invalid GET format\n")
                    continue

                safe = os.path.basename(" ".join(parts[1:]))

                if not AWS_ENABLED:
                    path = os.path.join(LOCAL_DIR, safe)
                    if not os.path.exists(path):
                        conn.sendall(b"ERR File not found\n")
                        continue

                    filesize = os.path.getsize(path)
                    conn.sendall(f"OK {filesize}\n".encode())
                    conn.recv(BUFFER)

                    with open(path, "rb") as f:
                        while True:
                            chunk = f.read(BUFFER)
                            if not chunk:
                                break
                            conn.sendall(chunk)
                else:
                    try:
                        obj = s3.get_object(Bucket=S3_BUCKET, Key=safe)
                    except ClientError:
                        conn.sendall(b"ERR File not found\n")
                        continue

                    filesize = obj["ContentLength"]
                    conn.sendall(f"OK {filesize}\n".encode())
                    conn.recv(BUFFER)

                    body = obj["Body"]
                    while True:
                        chunk = body.read(BUFFER)
                        if not chunk:
                            break
                        conn.sendall(chunk)

                put_metric("Downloads", 1)

            # ---------------- PUT ----------------
            elif command == "put":
                if len(parts) < 3:
                    conn.sendall(b"ERR Invalid PUT format\n")
                    continue

                try:
                    filesize = int(parts[-1])
                except ValueError:
                    conn.sendall(b"ERR Invalid filesize\n")
                    continue

                safe = os.path.basename(" ".join(parts[1:-1]))
                conn.sendall(b"OK\n")

                remaining = filesize
                tmp_path = os.path.join(LOCAL_DIR, safe)

                with open(tmp_path, "wb") as f:
                    while remaining > 0:
                        chunk = conn.recv(min(BUFFER, remaining))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)

                if AWS_ENABLED:
                    s3.upload_file(tmp_path, S3_BUCKET, safe)

                put_metric("Uploads", 1)
                print(f"[+] Stored: {safe}")

            else:
                conn.sendall(b"ERR Unknown command\n")

    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
            put_metric("ActiveClients", len(clients))

        try:
            conn.close()
        except Exception:
            pass
        print(f"[-] Connection closed: {addr}")


# ---------------- SIGNAL HANDLER ----------------
def signal_handler(signum, frame):
    shutdown_event.set()
    try:
        if server_sock:
            server_sock.close()
    except Exception:
        pass


# ---------------- MAIN ----------------
def main():
    global server_sock

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(128)
    server_sock.settimeout(1.0)

    mode = "AWS (S3)" if AWS_ENABLED else "LOCAL FILESYSTEM"
    print(f"Server listening on {HOST}:{PORT} [{mode}]")

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            threading.Thread(
                target=handle_client,
                args=(conn, addr),
                daemon=True
            ).start()

    finally:
        with clients_lock:
            for c in list(clients):
                try:
                    c.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
