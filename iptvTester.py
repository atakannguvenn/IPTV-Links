import os
import re
import sys
import subprocess
import time
import platform
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

def get_playlist_file():
    if len(sys.argv) > 1:
        playlist_file = sys.argv[1]
    else:
        playlist_file = "combined.m3u"
    return playlist_file


def test_channel(name, url, timeout=10, grace_period=5, extended_check=10):
    """
    Tests a single stream using ffplay with emoji output and smarter connection detection.
    """
    print(f"🔍 Testing: {name}")

    try:
        process = subprocess.Popen(
            ["ffplay", "-loglevel", "info", "-autoexit", "-t", str(timeout), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )

        output_lines = []
        def read_stderr():
            for line in process.stderr:
                output_lines.append(line)

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        start_time = time.time()
        success_keywords = ("Stream #", "Video:", "Duration:")
        connecting_keywords = ("Opening", "buffer", "Cache", "Connected", "AVIO")

        success_detected = False
        connection_detected = False

        while time.time() - start_time < timeout:
            if process.poll() is not None:
                break

            current_time = time.time() - start_time
            for line in output_lines:
                if any(k in line for k in success_keywords):
                    success_detected = True
                if any(k in line for k in connecting_keywords):
                    connection_detected = True

            if success_detected and current_time >= grace_period:
                break

            time.sleep(0.2)

        # Give extra time if connecting but no video yet
        if connection_detected and not success_detected:
            print("⏳ Connecting... waiting a bit longer.")
            extra_time = time.time()
            while time.time() - extra_time < extended_check:
                if process.poll() is not None:
                    break
                for line in output_lines:
                    if any(k in line for k in success_keywords):
                        success_detected = True
                        break
                if success_detected:
                    break
                time.sleep(0.2)

        # Terminate ffplay
        if process.poll() is None:
            if platform.system() == "Windows":
                subprocess.call(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()

        if success_detected:
            print(f"✅ SUCCESS: {name}")
            return name, url, True
        elif connection_detected:
            print(f"❓ MAYBE (connected, no video): {name}")
            return name, url, False
        else:
            print(f"❌ FAIL (no connection): {name}")
            return name, url, False

    except Exception as e:
        print(f"🚫 ERROR: {name} -> {e}")
        return name, url, False

def parse_m3u_lines(lines):
    """Returns a list of (channel_name, url) pairs from the M3U file."""
    entries = []
    current_name = "Unknown"
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            match = re.search(r",(.+)", line)
            if match:
                current_name = match.group(1).strip()
        elif line.startswith("http"):
            entries.append((current_name, line))
    return entries

def check_stream_status(name, url, timeout=20, grace_period=10, extended_check=10):
    """
    Test a single stream by launching ffplay and analyzing stderr output.
    
    - Shows emojis: ✅ (success), ❌ (fail), ❓ (maybe)
    - Grace period: minimum time to let ffplay initialize.
    - Extended check: wait a bit longer if signs of connection are seen but no video yet.

    Returns: True if video stream confirmed working, False otherwise.
    """
    print(f"🔍 Testing: {name} -> {url}")

    try:
        process = subprocess.Popen(
            ["ffplay", "-loglevel", "info", "-autoexit", "-t", str(timeout), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )

        output_lines = []
        def read_stderr():
            for line in process.stderr:
                output_lines.append(line)

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        start_time = time.time()
        success_keywords = ("Stream #", "Video:", "Opening", "AVFormat", "Duration:")
        connecting_keywords = ("Opening", "buffer", "Cache", "Connected", "AVIO")

        success_detected = False
        connection_detected = False

        while time.time() - start_time < timeout:
            if process.poll() is not None:
                break

            current_time = time.time() - start_time
            # Update detection status
            for line in output_lines:
                if any(keyword in line for keyword in success_keywords):
                    success_detected = True
                if any(keyword in line for keyword in connecting_keywords):
                    connection_detected = True

            # If success is detected and grace_period has passed, we can exit early
            if success_detected and current_time >= grace_period:
                break

            time.sleep(0.2)

        # If it looks like it's still connecting but no video yet, wait more
        if connection_detected and not success_detected:
            print("⏳ Looks like it's connecting... giving it more time.")
            extra_time = time.time()
            while time.time() - extra_time < extended_check:
                if process.poll() is not None:
                    break
                for line in output_lines:
                    if any(keyword in line for keyword in success_keywords):
                        success_detected = True
                        break
                if success_detected:
                    break
                time.sleep(0.2)

        # Kill the process if still running
        if process.poll() is None:
            if platform.system() == "Windows":
                subprocess.call(['taskkill', '/F', '/T', '/PID', str(process.pid)])
            else:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()

        if success_detected:
            print(f"✅ SUCCESS: {name}")
            return True
        elif connection_detected:
            print(f"❓ MAYBE: {name} (connected, but no video)")
            return False
        else:
            print(f"❌ FAIL: {name} (no connection or video)")
            return False

    except Exception as e:
        print(f"🚫 ERROR: {name} -> {e}")
        return False

def main():
    playlist_file = get_playlist_file()
    if not os.path.exists(playlist_file):
        print(f"📁 File not found: {playlist_file}")
        return

    with open(playlist_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    entries = parse_m3u_lines(lines)

    working_channels = {}
    not_working_channels = {}

    log_file = "stream_test_log.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"🔎 Stream Test Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    print(f"⚡ Testing {len(entries)} channels with high parallelism...\n")

    max_threads = 10  # 🚀 Adjust as needed (try 256, 512, etc., based on system RAM/CPU)

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_channel = {
            executor.submit(test_channel, name, url, timeout=4, grace_period=1, extended_check=2): (name, url)
            for name, url in entries
        }

        for future in as_completed(future_to_channel):
            name, url, result = future.result()
            if result:
                working_channels[name] = url
            else:
                not_working_channels[name] = url

            log_result(name, url, result, log_file)

    print("\n✅ Working Channels:")
    for name, url in working_channels.items():
        print(f"  - {name}: {url}")

    print("\n❌ Not Working Channels:")
    for name, url in not_working_channels.items():
        print(f"  - {name}: {url}")

def log_result(name, url, result, log_file):
    status = "✅ WORKING" if result else "❌ NOT WORKING"
    log_entry = f"{status}: {name}\nURL: {url}\n{'-'*50}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_entry)

def test_single(url):
    result = check_stream_status("MyChannel", url)
    if result:
        print("All good!")
    else:
        print("Need to re-check.")

if __name__ == "__main__":
    # main()
    test_single("https://tv.ensonhaber.com/bloomberght/bloomberght.m3u8")
