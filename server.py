import json
import os
import re
import time
import logging
import psutil
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any
import pymysql
from pymysql import Error
from discord_message import DISCORD_WEBHOOK_URL, send_discord_message  # Ensure this is installed
from credentials import DB_CONFIG, DB_NAME


# Configure logging
logging.basicConfig(
    filename="script_monitor.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory state
script_status: Dict[int, Dict[str, Any]] = {}

def get_db_connection():
    connection = pymysql.connect(
        host=DB_CONFIG['host'],  # Replace with your database host
        user=DB_CONFIG['user'],  # Replace with your database username
        password=DB_CONFIG['password'],  # Replace with your database password
        database=DB_NAME  # Replace with your database name
    )
    return connection

def find_scrapy_project_root(start_dir: str) -> Optional[str]:
    """Locate Scrapy project root by searching for scrapy.cfg."""
    current_dir = start_dir
    while True:
        if os.path.exists(os.path.join(current_dir, "scrapy.cfg")):
            return current_dir
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:
            return None
        current_dir = parent_dir


def find_spider_file_by_name(spiders_dir: str, spider_name: str) -> Optional[str]:
    """Locate spider file containing the specified spider name."""
    for root, _, files in os.walk(spiders_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                if is_spider_in_file(file_path, spider_name):
                    return file_path
    return None


def is_spider_in_file(file_path: str, spider_name: str) -> bool:
    """Check if a spider with the given name exists in the file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            pattern = rf"class\s+\w+.*?scrapy\.Spider.*?:\s*[\s\S]*?name\s*=\s*[\'\"]{spider_name}[\'\"]"
            return re.search(pattern, content, re.DOTALL) is not None
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return False


def get_script_path(proc: psutil.Process) -> Optional[str]:
    """Determine script path for Python processes."""
    try:
        cmdline = proc.cmdline()
        cwd = proc.cwd()

        # Scrapy spider detection
        if "-m" in cmdline and "scrapy" in cmdline and "crawl" in cmdline:
            try:
                crawl_idx = cmdline.index("crawl")
                spider_name = cmdline[crawl_idx + 1]
                project_root = find_scrapy_project_root(cwd)

                if not project_root:
                    return f"Spider: {spider_name} (Project root not found)"

                spider_file = find_spider_file_by_name(project_root, spider_name)

                if spider_file:
                    return spider_file
                return f"Not found in {project_root}"
            except (IndexError, ValueError) as e:
                logger.warning(f"Invalid Scrapy command format: {e}")
                return None

        # Normal script detection
        for arg in cmdline:
            if arg.endswith(".py") and os.path.isabs(arg):
                return os.path.abspath(arg)
        if len(cmdline) > 1 and cmdline[0].endswith("python") and cmdline[1].endswith(".py"):
            return os.path.abspath(os.path.join(proc.cwd(), cmdline[1]))

        # Interactive session detection
        if "-i" in cmdline or not any(arg.endswith(".py") for arg in cmdline):
            return "<interactive>"

    except (psutil.AccessDenied, psutil.NoSuchProcess) as e:
        logger.warning(f"Process access denied: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in get_script_path: {e}")
    return None


def check_system_resources() -> Dict[str, Any]:
    """Collect system resource metrics."""
    try:
        ram = psutil.virtual_memory()
        cpu = psutil.cpu_times_percent()
        drives = psutil.disk_partitions()

        return {
            "ram": {
                "total_gb": round(ram.total / (1024 ** 3), 2),
                "used_gb": round(ram.used / (1024 ** 3), 2),
                "percent": ram.percent,
            },
            "cpu": {
                "usage_percent": psutil.cpu_percent(),
                "logical_cores": psutil.cpu_count(logical=True),
                "physical_cores": psutil.cpu_count(logical=False),
            },
            "drives": [
                {
                    "device": d.device,
                    "total_gb": round(psutil.disk_usage(d.mountpoint).total / (1024 ** 3), 2),
                    "used_gb": round(psutil.disk_usage(d.mountpoint).used / (1024 ** 3), 2),
                    "percent": psutil.disk_usage(d.mountpoint).percent,
                }
                for d in drives
                if not (os.name == "nt" and "cdrom" in d.opts)
            ],
        }
    except Exception as e:
        logger.error(f"Error checking system resources: {e}")
        return {}


def send_discord_alert(message: str) -> None:
    """Send notification via Discord webhook."""
    try:
        if DISCORD_WEBHOOK_URL:
            send_discord_message(message)
    except Exception as e:
        logger.error(f"Discord notification failed: {e} in {get_local_ip()}")   


def monitor_scripts() -> None:
    """Main monitoring loop."""
    logger.info("Starting script monitoring service")

    while True:
        try:
            # Get running scripts
            running_scripts = []
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                if "python" in proc.info["name"].lower():
                    script_path = get_script_path(proc)
                    if script_path and script_path != "<interactive>":
                        running_scripts.append(
                            {
                                "pid": proc.info["pid"],
                                "script_path": script_path,
                                "ip": get_local_ip(),
                            }
                        )

            # Process system metrics
            resources = check_system_resources()

            # Handle started scripts
            for script in running_scripts:
                pid = script["pid"]
                if pid not in script_status:
                    script_status[pid] = {
                        "script_path": script["script_path"],
                        "start_time": datetime.now(),
                    }
                    drives = ''
                    for drive in resources['drives']:
                        drives += f'{drive["device"]} {drive["percent"]}% '

                    send_discord_alert(
                        f"🟢 **Script Started**\n"
                        f"Path: `{script['script_path']}`\n"
                        f"Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
                        f"IP: `{script['ip']}`\n"
                        f"RAM: {resources['ram']['percent']}%\n"
                        f"CPU: {resources['cpu']['usage_percent']}%\n"
                        f"Drives: {drives}"
                    )
                    log_script_event("start",script['ip'], str(script_status[pid]), script_status[pid]["script_path"], script_status[pid]["start_time"], resources)

            # Handle stopped scripts
            current_pids = {s["pid"] for s in running_scripts}
            for pid in list(script_status.keys()):
                if pid not in current_pids:
                    duration = datetime.now() - script_status[pid]["start_time"]
                    end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    send_discord_alert(
                        f"🔴 **Script Stopped**\n"
                        f"IP: {str(get_local_ip())}\n"
                        f"Path: `{script_status[pid]['script_path']}`\n"
                        f"Duration: {str(duration).split('.')[0]}"
                    )
                    log_script_event("end",str(get_local_ip()), str(script_status[pid]), script_status[pid]["script_path"], end_time, resources)
                    del script_status[pid]

            time.sleep(4)  # Reduced frequency for production

        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
            break
        except Exception as e:
            logger.error(f"Critical monitoring error: {e}")
            send_discord_alert(f"🚨 **Monitoring Error**\n{str(e)} in {str(get_local_ip())}")
            time.sleep(60)  # Safety sleep on critical failure


def log_script_event(event_type: str,localip : str, pid: str, path: str, time: str, resources_info: dict) -> None:
    """Log script events to the database."""
    connection = get_db_connection()
    if not connection:
        return

    try:
        cursor = connection.cursor()

        # Convert resources_info dictionary to JSON string
        resources_json = json.dumps(resources_info)

        if event_type == "start":
            # Insert a new entry for the start event
            query = """
                   INSERT INTO script_event (event_type,ip, pid, script_path, start_time, resources_info)
                   VALUES (%s, %s, %s, %s, %s, %s)
               """
            cursor.execute(query, (event_type,localip, pid, path, time, resources_json))
        elif event_type == "end":
            # Update the existing entry for the end event
            query = """
                   UPDATE script_event
                   SET event_type = %s, end_time = %s, resources_info = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE pid = %s AND event_type = 'start'
               """
            cursor.execute(query, (event_type, time, resources_json, pid))

        connection.commit()
    except Error as e:
        print(f"Database logging failed: {e}")


def get_local_ip() -> str:
    """Get IPv4 address, preferring Wi-Fi over Ethernet, skipping virtual/disconnected adapters."""
    try:
        # Run ipconfig and get output
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            shell=True,
        )
        output = result.stdout.replace('\r', '')  # Normalize newlines

        # Split on adapter sections
        adapter_blocks = re.split(r"\n(?=\S.*adapter .+:)", output)

        wifi_ip = None
        ethernet_ip = None

        for block in adapter_blocks:
            # Skip unwanted/virtual/disconnected adapters
            if ("Media disconnected" in block or
                "vEthernet" in block or
                "Virtual" in block or
                "VPN" in block or
                "Loopback" in block):
                continue

            # Prefer Wi-Fi
            if "Wireless LAN adapter Wi-Fi" in block:
                match = re.search(r"IPv4 Address[ .:]+([\d.]+)", block)
                if match:
                    wifi_ip = match.group(1)

            # Fallback to Ethernet
            elif "Ethernet adapter Ethernet" in block:
                match = re.search(r"IPv4 Address[ .:]+([\d.]+)", block)
                if match:
                    ethernet_ip = match.group(1)

        return wifi_ip or ethernet_ip or "N/A"

    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    monitor_scripts()