import os
import socket
import threading
import signal
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

# ---------------- CONFIG ----------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("FTP_PORT", 5001))
BUFFER = 4096
S3_BUCKET = os.environ.get("S3_BUCKET", "cpsc-471-bucket")
AWS_REGION = os.environ.get("AWS_REGION")

# ---------------- AWS CLIENTS ----------------
session = boto3.Session(region_name=AWS_REGION) if AWS_REGION else boto3.Session()
s3 = session.client("s3")
cloudwatch = session.client("cloudwatch")

# ---------------- STATE ----------------
clients = []
clients_lock = threading.Lock()
shutdown_event = threading.Event()
server_sock = None


# ---------------- CLOUDWATCH METRICS ----------------
def put_metric(metric_name, value, unit="Count"):
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
            try:
                data = conn.recv(BUFFER)
            except socket.timeout:
                continue
            except OSError:
                break

            if not data:
                print(f"[-] Client disconnected: {addr}")
                break

            cmd = data.decode(errors="ignore").strip()
            if not cmd:
                continue

            parts = cmd.split()
            command = parts[0].lower()

            # ---------------- LS ----------------
            if command == "ls":
                try:
                    rsp_lines = []
                    paginator = s3.get_paginator("list_objects_v2")

                    for page in paginator.paginate(Bucket=S3_BUCKET):
                        for obj in page.get("Contents", []):
                            name = obj["Key"]
                            size = obj["Size"]
                            last_mod = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
                            rsp_lines.append(f"{size:12} {last_mod} {name}")

                    if not rsp_lines:
                        conn.sendall(b"No files in bucket\n")
                    else:
                        conn.sendall(("\n".join(rsp_lines) + "\n").encode())

                except ClientError as e:
                    conn.sendall(f"ERR S3 list error: {e}\n".encode())

            # ---------------- GET ----------------
            elif command == "get":
                if len(parts) < 2:
                    conn.sendall(b"ERR Invalid GET format\n")
                    continue

                remote = " ".join(parts[1:])
                safe = os.path.basename(remote)

                try:
                    obj = s3.get_object(Bucket=S3_BUCKET, Key=safe)
                except ClientError:
                    conn.sendall(b"ERR File not found\n")
                    continue

                filesize = obj["ContentLength"]
                conn.sendall(f"OK {filesize}\n".encode())

                try:
                    ack = conn.recv(BUFFER)
                except Exception:
                    continue

                body = obj["Body"]

                while True:
                    chunk = body.read(BUFFER)
                    if not chunk:
                        break
                    try:
                        conn.sendall(chunk)
                    except Exception:
                        break

                put_metric("Downloads", 1)
                print(f"[+] Served GET {safe} to {addr}")

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

                filename = " ".join(parts[1:-1])
                safe = os.path.basename(filename)

                conn.sendall(b"OK\n")

                remaining = filesize
                tmp_path = f"/tmp/upload-{threading.get_ident()}-{safe}"

                try:
                    with open(tmp_path, "wb") as f:
                        while remaining > 0:
                            chunk = conn.recv(min(BUFFER, remaining))
                            if not chunk:
                                break
                            f.write(chunk)
                            remaining -= len(chunk)

                    s3.upload_file(Filename=tmp_path, Bucket=S3_BUCKET, Key=safe)

                    put_metric("Uploads", 1)
                    print(f"[+] Uploaded to S3: {safe}")

                except Exception as e:
                    print("Upload error:", e)
                    try:
                        conn.sendall(f"ERR Upload failed: {e}\n".encode())
                    except Exception:
                        pass

                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass

            # ---------------- UNKNOWN ----------------
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
    print("Signal received:", signum, "shutting down")
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

    print(f"Server listening on {HOST}:{PORT} (S3 bucket: {S3_BUCKET})")

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()

    finally:
        print("[*] Server shutting down")

        with clients_lock:
            for c in list(clients):
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    c.close()
                except Exception:
                    pass

        print("[*] Shutdown complete")


if __name__ == "__main__":
    main()
