import os
import sys
import requests
import json
import argparse
import subprocess
import signal
import csv
from datetime import datetime

CACHE_FILE_PATTERN = "cache-{server}-{data_type}.json"
DEBUG_MODE = False  # Default: Debugging is off

def debug_log(message):
    """
    Logs a debug message if debugging is enabled.
    """
    if DEBUG_MODE:
        print(f"[DEBUG] {message}")

def load_cache(server, data_type):
    """Load data from the cache file if it exists and is up-to-date."""
    debug_log(f"Attempting to load cache for {data_type} on server {server}")
    cache_file = CACHE_FILE_PATTERN.format(server=server, data_type=data_type)
    if os.path.exists(cache_file):
        # Check if the cache file was created today
        file_date = datetime.fromtimestamp(os.path.getmtime(cache_file)).date()
        if file_date == datetime.today().date():
            try:
                with open(cache_file, 'r') as file:
                    return json.load(file)
            except (OSError, IOError, json.JSONDecodeError) as e:
                print(f"Error reading cache file {cache_file}: {e}", file=sys.stderr)
    return None

def save_cache(server, data_type, data):
    """Save data to the cache file."""
    debug_log(f"Attempting to save cache for {data_type} on server {server}")
    cache_file = CACHE_FILE_PATTERN.format(server=server, data_type=data_type)
    try:
        with open(cache_file, 'w') as file:
            json.dump(data, file)
    except (OSError, IOError) as e:
        print(f"Error saving cache file {cache_file}: {e}", file=sys.stderr)

def download_data(server, user, password, endpoint, additional_params=None):
    """Download data from the Xtream IPTV server."""
    debug_log(f"Downloading data from {server}, endpoint: {endpoint}")
    url = f"http://{server}/player_api.php"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
    }
    params = {"username": user, "password": password, "action": endpoint}
    if additional_params:
        params.update(additional_params)
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        debug_log(f"Response from server ({endpoint}): {response.text[:500]}")  # Print first 500 characters
        return response.json()
    else:
        print(f"Failed to fetch {endpoint} data: {response.status_code}", file=sys.stderr)
        sys.exit(1)

def check_epg(server, user, password, stream_id):
    """Check EPG data for a specific channel."""
    debug_log(f"Checking EPG for stream ID {stream_id}")
    epg_data = download_data(server, user, password, "get_simple_data_table", {"stream_id": stream_id})

    if isinstance(epg_data, dict) and epg_data.get("epg_listings"):
        return len(epg_data["epg_listings"])  # Return the count of EPG entries

    elif isinstance(epg_data, list):
        debug_log(f"Unexpected list response for EPG data: {epg_data}")
        return len(epg_data)  # Return the length of the list

    else:
        debug_log(f"Unexpected EPG response type: {type(epg_data)}")
        return 0  # No EPG data available

def filter_data(live_categories, live_streams, group, channel):
    """Filter the live streams based on group and channel arguments."""
    filtered_streams = []
    group = group.lower() if group else None
    channel = channel.lower() if channel else None

    for stream in live_streams:
        # Filter by group if specified
        if group:
            matching_categories = [cat for cat in live_categories if group in cat["category_name"].lower()]
            if not any(cat["category_id"] == stream["category_id"] for cat in matching_categories):
                continue
        # Filter by channel if specified
        if channel and channel not in stream["name"].lower():
            continue
        # Add the stream to the filtered list
        filtered_streams.append(stream)

    return filtered_streams

def check_ffprobe():
    """
    Checks if the ffprobe command is available on the system.
    Exits the program with an error message if not found.
    """
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        debug_log("ffprobe is installed and reachable.")
    except FileNotFoundError:
        print("Error: ffprobe is not installed or not found in the system PATH. Please install ffprobe before running this program.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error: ffprobe check failed with error: {e}")
        sys.exit(1)

def check_channel(url):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
                "-of", "json",
                url
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        output = json.loads(result.stdout)

        if 'streams' in output and len(output['streams']) > 0:
            stream_info = output['streams'][0]
            codec_name = stream_info.get('codec_name', 'Unknown')[:5]
            width = stream_info.get('width', 'Unknown')
            height = stream_info.get('height', 'Unknown')
            avg_frame_rate = stream_info.get('avg_frame_rate', 'Unknown')

            if avg_frame_rate != 'Unknown' and '/' in avg_frame_rate:
                num, denom = map(int, avg_frame_rate.split('/'))
                frame_rate = round(num / denom) if denom != 0 else "Unknown"
            else:
                frame_rate = avg_frame_rate

            return {
                "status": "working",
                "codec_name": codec_name,
                "width": width,
                "height": height,
                "frame_rate": frame_rate
            }
        else:
            debug_log(f"No streams found in ffprobe output for URL: {url}")
            return {"status": "not working"}
    except Exception as e:
        debug_log(f"Error in check_channel: {e}")
        return {"status": "error", "error_message": str(e)}

def save_to_csv(file_name, data, fieldnames):
    """
    Save data to a CSV file, ensuring all fields are enclosed in double quotes.
    
    :param file_name: The name of the CSV file to save.
    :param data: A list of dictionaries containing the data to write.
    :param fieldnames: A list of field names for the CSV header.
    """
    try:
        with open(file_name, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(data)
        print(f"Output saved to {file_name}")
    except Exception as e:
        print(f"Error saving to CSV: {e}")

def handle_sigint(signal, frame):
    """Handle Ctrl+C gracefully."""
    print("\nProgram interrupted by user. Exiting...")
    sys.exit(0)

def main():
    global DEBUG_MODE

    # Set up the signal handler for Ctrl+C
    signal.signal(signal.SIGINT, handle_sigint)

    parser = argparse.ArgumentParser(description="Xtream IPTV Downloader and Filter")
    parser.add_argument("--server", required=True, help="The Xtream server to connect to.")
    parser.add_argument("--user", required=True, help="The username to use.")
    parser.add_argument("--pw", required=True, help="The password to use.")
    parser.add_argument("--nocache", action="store_true", help="Force download and ignore cache.")
    parser.add_argument("--channel", help="Filter by channel name.")
    parser.add_argument("--category", help="Filter by category name.")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode.")  # Debug flag
    parser.add_argument("--epgcheck", action="store_true", help="Check if channels provide EPG data and count entries.")
    parser.add_argument("--check", action="store_true", help="Check stream resolution and frame rate using ffprobe.")
    parser.add_argument("--save", action="store_true", help="Save the output to a CSV file.")
    parser.add_argument("--delay", action="store_true", help="Adds a 5s delay to prevent returning 456 err, resulting in no stream being tested.")
    parser.add_argument("--formatname", action="store_true", help="Formats the name to include 4K, FHD, HD, SD for 3280, 1080, 720, <720 respectively.")
    args = parser.parse_args()

    # Print the date and time when the program is run
    masked_server = f"{'.'.join(['xxxxx'] + args.server.split('.')[1:])}"
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n\nfind-iptv-channels-details - Running for server {masked_server} on {run_time}\n")

    # Enable debug mode if the --debug flag is present
    DEBUG_MODE = args.debug
    debug_log("Debug mode enabled")  # Will only print if debug mode is set, else ignored

    # Check ffprobe if --check is enabled
    if args.check:
        check_ffprobe()

    # Check cache or download data
    live_categories = load_cache(args.server, "live_categories") if not args.nocache else None
    live_streams = load_cache(args.server, "live_streams") if not args.nocache else None

    if not live_categories or not live_streams:
        live_categories = download_data(args.server, args.user, args.pw, "get_live_categories")
        live_streams = download_data(args.server, args.user, args.pw, "get_live_streams")
        save_cache(args.server, "live_categories", live_categories)
        save_cache(args.server, "live_streams", live_streams)

    # Filter data
    filtered_streams = filter_data(live_categories, live_streams, args.category, args.channel)

    # Prepare CSV headers
    fieldnames = ["Stream ID", "Name", "Category", "Archive", "EPG", "Codec", "Resolution", "Frame Rate"]

    # Open CSV for live writing if --save is provided
    if args.save:
        csv_file = None
        csv_writer = None
        currentTime = datetime.now()
        formattedTime = currentTime.strftime("%H_%M_%S")
        if args.category is not None:
            fileName = args.category + "_" + formattedTime + ".csv"
        elif args.channel is not None:
            fileName = args.channel + "_" + formattedTime + ".csv"
        else:
            fileName = "Full_" + formattedTime + ".csv"
        csv_file = open(fileName, mode="w", newline='', encoding="utf-8")
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        csv_writer.writeheader()

    try:
        # Print table headers
        print(f"{'ID':<10}{'Name':<60} {'Category':<40}{'Archive':<8}{'EPG':<5}{'Codec':<8}{'Resolution':<15}{'Frame':<10}")
        print("=" * 152)

        # Create a category map
        category_map = {cat["category_id"]: cat["category_name"] for cat in live_categories}
        for stream in filtered_streams:
            category_name = category_map.get(stream["category_id"], "Unknown")
            epg_count = (
                check_epg(args.server, args.user, args.pw, stream["stream_id"]) if args.epgcheck else ""
            )
            
            if args.delay:
                t.sleep(5)
            
            # Generate stream URL
            stream_url = f"http://{args.server}/{args.user}/{args.pw}/{stream['stream_id']}"
            stream_info = (
                check_channel(stream_url)
                if args.check
                else {"codec_name": "", "width": "", "height": "", "frame_rate": ""}
            )
            resolution = f"{stream_info.get('width', 'N/A')}x{stream_info.get('height', 'N/A')}" if args.check else "N/A"
            
            resolutionTag = None
            if args.formatname:
                _, splitResolution = resolution.split('x')
                if len(splitResolution) == 4 and splitResolution != "1080":
                    resolutionTag = "UHD"
                elif "720" in splitResolution:
                    resolutionTag = "HD"
                elif "1080" in splitResolution:
                    resolutionTag = "FHD"
                else:
                    resolutionTag = "SD"

                # Combine name and resolution with ' | ' separator
                name_and_resolution = f"{stream['name'][:60]} | {resolutionTag}"
            else:
                name_and_resolution = f"{stream['name'][:60]}"

            # Print to console
            print(
                f"{stream['stream_id']:<10}{name_and_resolution:<60} {category_name[:40]:<40}"
                f"{stream.get('tv_archive_duration', 'N/A'):<8}{epg_count:<5}{stream_info.get('codec_name', 'N/A'):<8}"
                f"{resolution:<15}{stream_info.get('frame_rate', 'N/A'):<10}"
            )

            # Write to CSV file live if enabled
            if csv_writer:
                csv_writer.writerow({
                    "Stream ID": stream["stream_id"],
                    "Name": name_and_resolution,
                    "Category": category_name[:40],
                    "Archive": stream.get("tv_archive_duration", "N/A"),
                    "EPG": epg_count,
                    "Codec": stream_info.get("codec_name", "N/A"),
                    "Resolution": resolution,
                    "Frame Rate": stream_info.get("frame_rate", "N/A"),
                })

    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user. Closing the CSV file safely...\n")
    finally:
        # Close the CSV file if opened
        if csv_file:
            csv_file.close()

    print("\n\n")

if __name__ == "__main__":
    main()