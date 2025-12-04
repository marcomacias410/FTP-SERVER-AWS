import socket
import os
import shlex
import select

HOST = ""
PORT = 5001
BUFFER = 4096

def recv_until_pause(sock, timeout=0.3):
    """
    Read everything available on the socket until there's a short pause.
    Used for multi-line `ls` output.
    """
    sock.setblocking(False)
    chunks = []

    try:
        while True:
            ready, _, _ = select.select([sock], [], [], timeout)
            if not ready:
                break

            data = sock.recv(BUFFER)
            if not data:
                break

            chunks.append(data)
    finally:
        sock.setblocking(True)

    if not chunks:
        return ""
    return b"".join(chunks).decode().strip()


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))

    print("Connected. Commands: ls, get <file>, put <file>, exit")

    while True:
        try:
            cmd = input("ftp> ").strip()
        except KeyboardInterrupt:
            print("\nExiting.")
            break

        if cmd == "exit":
            break

        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            print(f"Invalid command syntax: {e}")
            continue

        if not parts:
            continue

        command = parts[0].lower()

        # ---------------- LS ----------------
        if command == "ls":
            s.sendall(b"ls")
            response = recv_until_pause(s)
            print(response)

        # ---------------- GET ----------------
        elif command == "get":
            if len(parts) < 2:
                print("Usage: get <remote_filename> [local_filename]")
                continue

            if len(parts) >= 3:
                remote = " ".join(parts[1:-1])
                local = parts[-1]
            else:
                remote = " ".join(parts[1:])
                local = os.path.basename(remote)

            s.sendall(f"get {remote}".encode())
            response = s.recv(BUFFER).decode()

            if response.startswith("ERR"):
                print(response)
                continue

            tokens = response.split()
            if len(tokens) != 2 or tokens[0] != "OK":
                print("Invalid response from server:", response)
                continue

            filesize = int(tokens[1])
            s.sendall(b"OK")

            remaining = filesize
            with open(local, "wb") as f:
                while remaining > 0:
                    data = s.recv(min(BUFFER, remaining))
                    if not data:
                        print("Connection closed unexpectedly during download")
                        break
                    f.write(data)
                    remaining -= len(data)

            received = filesize - remaining

            if remaining == 0:
                abs_path = os.path.abspath(local)
                print(f"Downloaded: {local} -> {abs_path} ({received} bytes)")
            else:
                print(f"Download incomplete: {local} (received {received} of {filesize} bytes)")

        # ---------------- PUT ----------------
        elif command == "put":
            if len(parts) != 2:
                print("Usage: put <local_path>")
                continue

            local_path = parts[1]

            if not os.path.exists(local_path):
                print("File does not exist.")
                continue

            filesize = os.path.getsize(local_path)
            remote_name = os.path.basename(local_path)

            s.sendall(f"put {remote_name} {filesize}".encode())

            resp = s.recv(BUFFER).decode()

            if not resp.startswith("OK"):
                print(resp)
                continue

            with open(local_path, "rb") as f:
                while True:
                    chunk = f.read(BUFFER)
                    if not chunk:
                        break
                    s.sendall(chunk)

            print(f"Uploaded: {remote_name}")

        else:
            print("Unknown command.")

    s.close()


if __name__ == "__main__":
    main()
