import os
import socket
import threading
import signal
import sys
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

# Config (set via environment or edit here)
HOST = "0.0.0.0"
PORT = int(os.environ.get("FTP_PORT", 5001))
BUFFER = 4096
S3_BUCKET = os.environ.get("S3_BUCKET", "your-bucket-name")
AWS_REGION = os.environ.get("AWS_REGION", None)  # optional

# boto3 clients (use EC2 IAM role)
session = boto3.Session(region_name=AWS_REGION) if AWS_REGION else boto3.Session()
s3 = session.client("s3")
cloudwatch = session.client("cloudwatch")

# internal state
clients = []
clients_lock = threading.Lock()
shutdown_event = threading.Event()
server_sock = None

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
        # metric push should not crash server
        print("CloudWatch put_metric failed:", e)

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
                # loop back to check shutdown event periodically
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
                    page_iter = paginator.paginate(Bucket=S3_BUCKET)
                    found_any = False
                    for page in page_iter:
                        for obj in page.get("Contents", []):
                            found_any = True
                            name = obj["Key"]
                            size = obj["Size"]
                            last_mod = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
                            rsp_lines.append(f"{size:12} {last_mod} {name}")
                    if not found_any:
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
                    # Use streaming body to avoid loading entire file into memory
                    obj = s3.get_object(Bucket=S3_BUCKET, Key=safe)
                except ClientError:
                    conn.sendall(b"ERR File not found\n")
                    continue

                filesize = obj["ContentLength"]
                conn.sendall(f"OK {filesize}".encode())
                # wait for client ACK
                try:
                    ack = conn.recv(BUFFER)
                except Exception:
                    ack = b''
                if not ack:
                    # client didn't ACK; skip
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
                # Format: put <filename> <filesize>
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

                # ACK to client to begin upload
                conn.sendall(b"OK")
                remaining = filesize

                # stream to a temporary file to avoid large memory use
                tmp_path = f"/tmp/upload-{threading.get_ident()}-{safe}"
                try:
                    with open(tmp_path, "wb") as f:
                        while remaining > 0:
                            chunk = conn.recv(min(BUFFER, remaining))
                            if not chunk:
                                break
                            f.write(chunk)
                            remaining -= len(chunk)

                    # Upload to S3 from file
                    s3.upload_file(Filename=tmp_path, Bucket=S3_BUCKET, Key=safe)
                    put_metric("Uploads", 1)
                    print(f"[+] Uploaded to S3: {safe}")
                    conn.sendall(b"OK Uploaded\n")
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

            # ---------------- SHUTDOWN (not recommended in AWS) ----------------
            elif command == "shutdown":
                conn.sendall(b"OK Shutting down\n")
                shutdown_event.set()
                break

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


def signal_handler(signum, frame):
    print("Signal received:", signum, "shutting down")
    shutdown_event.set()
    # close server socket to break accept loop
    try:
        if server_sock:
            server_sock.close()
    except Exception:
        pass

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
        print("[*] Server shutting down: notifying clients")
        with clients_lock:
            for c in list(clients):
                try:
                    c.sendall(b"SHUTDOWN")
                except Exception:
                    pass
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

