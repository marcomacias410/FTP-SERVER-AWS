CPSC 471 FTP Server
=============================

Group Members
-------------
- Marco Macias <marcomacias410@csu.fullerton.edu>
- Jordan Toledo <jordantoledo248@gmail.com>
- Jericho Salonga <jirosalonga@csu.fullerton.edu>
- Luke Makishima <LukeMakishima@csu.fullerton.edu>

Programming Language
--------------------
Python 3 (recommended 3.8 or higher)

Prerequisites
-------------
Before running this project, ensure you have:
- Python 3 installed on your computer
  - Windows: Download from https://www.python.org/downloads/
  - Mac/Linux: Usually pre-installed, or install via package manager
- A terminal/command prompt (PowerShell on Windows, Terminal on Mac/Linux)
- Basic familiarity with navigating directories in the terminal

To verify Python is installed, open a terminal and run:
```
python --version
```
or
```
python3 --version
```

How to Execute (Step-by-Step for Beginners)
--------------------------------------------
### Step 1: Start the Client
The client is the program you use to upload and download files to/from the server.

The server is already running on AWS. Simply navigate to the same folder and run:
```powershell
python client.py
```

**Server Configuration:**
- The client is pre-configured to connect to the AWS server at: `471-nlb-7668a8371294d0bf.elb.us-east-2.amazonaws.com`
- Files are stored in **Amazon S3** (cloud storage) for durability and scalability
- The server is hosted on AWS EC2 instances behind a Network Load Balancer

You should see:
```
Connected. Commands: ls, get <file>, put <file>, exit
ftp>
```

### Step 2: Use the Client
At the `ftp>` prompt, you can type commands. See the **Commands Reference** section below for details.

Example session:
```
ftp> ls
No files

ftp> put "C:\Users\YourName\Documents\report.pdf"
Uploaded: report.pdf

ftp> ls
     1234567 2025-12-04 15:30:22 report.pdf

ftp> get report.pdf downloaded_report.pdf
Downloaded: downloaded_report.pdf -> C:\...\downloaded_report.pdf (1234567 bytes)

ftp> exit
```

### Step 3: Exit the Client
When you're done, type `exit` at the `ftp>` prompt or press `Ctrl+C` to disconnect from the server.

The server continues running on AWS and remains available for other clients.


Commands Reference
==================

The client supports the following commands at the `ftp>` prompt:

### `ls`
**Purpose:** List all files on the server.

**Usage:**
```
ftp> ls
```

**Example Output:**
```
Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----         12/4/2025   3:45 PM         723968 report.pdf
-a----         12/4/2025   2:10 PM           5432 data.csv
```

**Notes:**
- Shows file size, last modified time, and filename
- If no files exist, displays "No files"

---

### `get <remote_filename> [local_filename]`
**Purpose:** Download a file from the server to your computer.

**Usage:**
```
ftp> get <remote_filename>
```
or
```
ftp> get <remote_filename> <local_filename>
```

**Examples:**
```
ftp> get report.pdf
Downloaded: report.pdf -> C:\Users\YourName\...\report.pdf (723968 bytes)

ftp> get "Project Report.pdf" my_report.pdf
Downloaded: my_report.pdf -> C:\Users\YourName\...\my_report.pdf (723968 bytes)
```

**Notes:**
- If you don't specify `local_filename`, the file is saved with its original name
- Files are downloaded to your current working directory
- Use quotes around filenames with spaces
- The client shows the full path where the file was saved and the number of bytes downloaded

---

### `put <local_path>`
**Purpose:** Upload a file from your computer to the server.

**Usage:**
```
ftp> put <local_path>
```

**Examples:**
```
ftp> put report.pdf
Uploaded: report.pdf

ftp> put "C:\Users\YourName\Documents\My Report.pdf"
Uploaded: My Report.pdf
```

**Notes:**
- You can provide a full path or a relative path to the file
- Use quotes around paths with spaces
- The server stores the file using its basename (filename without the directory path)
- The file must exist on your computer, or you'll see "File does not exist."

---

### `exit` or `quit`
**Purpose:** Disconnect from the server and close the client.

**Usage:**
```
ftp> exit
```

**Notes:**
- This closes your client connection but the server remains running on AWS
- You can also press `Ctrl+C` to exit immediately
- Other users can connect and use the server at the same time


Anything Special About This Submission
=======================================

### 1. **AWS-Powered File Storage**
This server is deployed on AWS with enterprise-grade features:
- Files are stored in **Amazon S3** (cloud storage) for durability and scalability
- Server runs on **AWS EC2 instances** behind a **Network Load Balancer** for high availability
- **CloudWatch metrics** track uploads, downloads, and active connections in real-time
- Automatic backups and disaster recovery built-in
- Supports unlimited horizontal scaling (add more instances as needed)

**Current Deployment:**
- **Load Balancer DNS:** `471-nlb-7668a8371294d0bf.elb.us-east-2.amazonaws.com`
- **Region:** us-east-2
- **Storage:** S3 bucket with versioning and encryption
- **Monitoring:** CloudWatch dashboards track all metrics

---

### 2. **Filenames with Spaces Are Fully Supported**
Unlike traditional FTP, this implementation handles filenames with spaces correctly:
- Client: Use quotes around filenames: `put "My Document.pdf"`
- Server: Properly parses and stores files with spaces
- Works seamlessly with S3 cloud storage

---

### 3. **PowerShell-Style Directory Listings**
The `ls` command output mimics Windows PowerShell's `Get-ChildItem` format:
- Shows Mode, LastWriteTime, Length (size), and Name
- Files are sorted alphabetically for easy browsing
- Timestamps include date, time, and AM/PM
- File sizes are right-aligned for readability

Example:
```
Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----         12/4/2025   3:45 PM         723968 CPSC 471 Midterm.drawio
-a----         12/4/2025   2:10 PM        5640809 Plate Tectonics Lab.pdf
```

---

### 4. **Graceful Shutdown & Connection Management**
- Server can be stopped with `Ctrl+C` without corrupting file transfers
- Uses threading to handle multiple clients simultaneously
- Tracks active connections and reports them in CloudWatch (AWS mode)
- Client automatically detects server shutdown and exits cleanly

---

### 5. **Smart Multi-Line Response Handling**
The client uses `recv_until_pause()` to handle multi-line responses (like `ls` output):
- Reads all available data with a short timeout
- Prevents truncation of directory listings
- Works reliably even with large file lists

---

### 6. **Robust Error Handling**
- Server validates all commands and provides clear error messages
- Client detects incomplete downloads and reports exact byte counts
- File size is verified before and after transfer
- Handles network interruptions gracefully

