from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.config import config, ConfigSubsection, ConfigSelection, ConfigYesNo, ConfigClock, ConfigText, ConfigInteger, configfile
from Components.ConfigList import ConfigListScreen
from Components.Sources.StaticText import StaticText
from Components.config import getConfigListEntry
from enigma import eTimer
import re
import os
import datetime
import subprocess

# Define the paths for the plugin and the output file
PLUGIN_PATH = "/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer"

# A list of URLs for scraping. We'll use curl to handle the SSL issues.
# The URL must be a valid source of free CCcam servers.
# The `parser_curl_regex` will handle the parsing.
# For dynamic URLs, use a dictionary with a 'url_generator' key.
SCRAPE_URLS = [
    ("https://cccam-premium.pro/free-cccam/", "parser_curl_regex"),
    ("https://cccamsate.com/free", "parser_curl_regex"),
    ("https://cccamiptv.tv/cccamfree/#page-content", "parser_curl_regex"),
    ("https://cccam.net/freecccam", "parser_curl_regex"),
    ("https://cccamia.com/cccam-free/", "parser_curl_regex"),
    ("https://cccamhub.com/cccamfree/", "parser_curl_regex"),
    ("https://cccamgalaxy.com/", "parser_curl_regex"),
    ("https://cccamfree48h.yolasite.com/server-2.php", "parser_curl_regex"),
    ("https://cccamx.com/free-cccam", "parser_curl_regex"),
    ("https://bosscccam.co/Test.php", "parser_curl_regex"),
    ("https://iptv-15days.blogspot.com/", "parser_curl_regex"),            
    ("https://raw.githubusercontent.com/levi-45/free-cccam/main/servers.txt", "parser_curl_regex"),
    # This URL is dynamic and will use a new dedicated parser.
    # It fetches the previous day's servers.
    {"url_generator": lambda: "https://testious.com/old-free-cccam-servers/{}/".format((datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')),
     "parser_name": "parser_testious_servers"},
    # New entry to also scrape for the current day's servers from testious.com
    {"url_generator": lambda: "https://testious.com/old-free-cccam-servers/{}/".format(datetime.date.today().strftime('%Y-%m-%d')),
     "parser_name": "parser_testious_servers"}
]

# Changed the log file location to /tmp, which is cleared on reboot.
LOG_FILE = "/tmp/downloader.log"

# Configuration setup
config.plugins.Levi45FreeServer = ConfigSubsection()
config.plugins.Levi45FreeServer.downloadtime = ConfigClock(default=0)  # 00:00
config.plugins.Levi45FreeServer.softcam = ConfigSelection(
    choices=[
        ("cccam", "CCcam"),
        ("oscam", "OSCam"),
        ("ncam", "NCam")
    ],
    default="cccam"
)
config.plugins.Levi45FreeServer.outputfile = ConfigText(default="/etc/CCcam.cfg", fixed_size=False)
config.plugins.Levi45FreeServer.downloadinterval = ConfigInteger(default=1, limits=(1, 168))  # hours
config.plugins.Levi45FreeServer.enablecron = ConfigSelection(choices=[("0", "Disabled"), ("1", "Enabled")], default="0")

def log(message):
    """
    A simple logging function to help with debugging.
    """
    try:
        with open(LOG_FILE, "a") as f:
            f.write("[{}] {}\n".format(datetime.datetime.now(), message))
    except Exception as e:
        print("Failed to write to log file: {}".format(e))

# ============================================================================
# Helper Functions for Parsing Web Pages
#
# This new parser uses the output of a shell command (curl).
# ============================================================================

def parse_servers_curl_regex(html_content):
    """
    Parses an HTML page using a more flexible regex pattern.
    Assumes the format is like: 'C: host port user pass'
    This version is more flexible to handle variations in whitespace.
    Returns a list of parsed server lines.
    """
    servers = []
    pattern = re.compile(r'C:\s*([\w\d\.-]+)\s*(\d+)\s*([\w\d\.-]+)\s*([\w\d\.-]+)', re.IGNORECASE)
    for match in pattern.finditer(html_content):
        host, port, user, password = match.groups()
        servers.append("C: {} {} {} {}".format(host, port, user, password))
    return servers

def parse_testious_servers(html_content):
    """
    Parses HTML content from testious.com using a more precise regex.
    This version is more robust and less prone to false positives.
    """
    servers = []
    
    pattern_c = re.compile(r'C:\s*([\w\d\.-]+)\s*(\d+)\s*([\w\d\.-]+)\s*([\w\d\.-]+)', re.IGNORECASE)
    
    pattern_n = re.compile(
        r'N:\s*([\w\d\.-]+)\s*(\d+)\s*([\w\d\.-]+)\s*([\w\d\.-]+)\s*([0-9a-fA-F\s]+?)\s*(?:#.*)?$',
        re.MULTILINE | re.IGNORECASE
    )

    for match in pattern_n.finditer(html_content):
        host, port, user, password, key_str = match.groups()
        servers.append("N: {} {} {} {} {}".format(host, port, user, password, key_str.strip().replace(" ", "")))
        
    for match in pattern_c.finditer(html_content):
        host, port, user, password = match.groups()
        servers.append("C: {} {} {} {}".format(host, port, user, password))

    if not servers:
        log("No servers found with a comprehensive regex. The format on testious.com may have changed.")
    else:
        log("Successfully found servers using the updated testious parser.")

    return servers

# ============================================================================
# Conversion Function for OSCam and NCam
# ============================================================================

def convert_to_oscam_reader(server_line):
    """
    Converts a CCcam or Newcamd line to an OSCam or NCam reader configuration.
    Returns the formatted string.
    """
    parts = server_line.split()
    reader_config = ""
    
    if not parts or len(parts) < 5:
        return ""
        
    protocol_type = parts[0].strip(':').lower()
    
    if protocol_type == "c":
        host, port, user, password = parts[1], parts[2], parts[3], parts[4]
        reader_config = """
[reader]
label = {}_{}
protocol = cccam
device = {},{}
user = {}
password = {}
group = 1
cccversion = 2.1.2
inactivitytimeout = 1
reconnecttimeout = 30
disablelog = 1
""".format(host, port, host, port, user, password)
    elif protocol_type == "n" and len(parts) >= 6:
        host, port, user, password = parts[1], parts[2], parts[3], parts[4]
        key = "".join(parts[5:])
        
        reader_config = """
[reader]
label={}_{}
enable=1
protocol=newcamd
key={}
device={},{}
user={}
password={}
group=1
inactivitytimeout=1
reconnecttimeout=30
lb_weight=100
cccversion=2.1.2
cccmaxhops=10
cccwantemu=1
ccckeepalive=1
""".format(host, port, key, host, port, user, password)
    
    return reader_config.strip()


PARSERS = {
    "parser_curl_regex": parse_servers_curl_regex,
    "parser_testious_servers": parse_testious_servers,
}

# Global variable to track last download time
last_download_time = None

# Simple file-based configuration
AUTODOWNLOAD_FILE = "/etc/levi45_autodownload.txt"

def is_autodownload_enabled():
    """Check if auto-download is enabled using simple file-based storage"""
    try:
        if os.path.exists(AUTODOWNLOAD_FILE):
            with open(AUTODOWNLOAD_FILE, "r") as f:
                content = f.read().strip()
                return content == "True"
        return False
    except:
        return False

def set_autodownload_enabled(enabled):
    """Set auto-download status using simple file-based storage"""
    try:
        with open(AUTODOWNLOAD_FILE, "w") as f:
            f.write("True" if enabled else "False")
        return True
    except:
        return False

def get_last_download_time():
    """Get the last download time from a file to persist across restarts"""
    global last_download_time
    try:
        time_file = "/tmp/levi45_last_download.txt"
        if os.path.exists(time_file):
            with open(time_file, "r") as f:
                timestamp = float(f.read().strip())
                last_download_time = datetime.datetime.fromtimestamp(timestamp)
                log("Loaded last download time: {}".format(last_download_time))
    except:
        last_download_time = None

def save_last_download_time():
    """Save the last download time to a file"""
    global last_download_time
    try:
        time_file = "/tmp/levi45_last_download.txt"
        with open(time_file, "w") as f:
            f.write(str(last_download_time.timestamp()))
    except:
        pass

def create_cron_script():
    """Create a simple standalone cron script that doesn't import Enigma2 modules"""
    cron_script = """#!/bin/sh
# Simple cron script for Levi45FreeServer
LOG_FILE="/tmp/downloader.log"
AUTODOWNLOAD_FILE="/etc/levi45_autodownload.txt"

# Check if auto-download is enabled
if [ ! -f "$AUTODOWNLOAD_FILE" ] || [ "$(cat $AUTODOWNLOAD_FILE)" != "True" ]; then
    echo "$(date '+[%Y-%m-%d %H:%M:%S]') Auto-download disabled, skipping" >> "$LOG_FILE"
    exit 0
fi

echo "$(date '+[%Y-%m-%d %H:%M:%S]') Cron job started" >> "$LOG_FILE"

# Get the output file from settings
OUTPUT_FILE="/etc/CCcam.cfg"
if [ -f "/etc/enigma2/settings" ]; then
    OUTPUT_FILE=$(grep "config.plugins.Levi45FreeServer.outputfile" /etc/enigma2/settings | cut -d= -f2)
    if [ -z "$OUTPUT_FILE" ]; then
        OUTPUT_FILE="/etc/CCcam.cfg"
    fi
fi

# Get softcam type from settings
SOFTCAM_TYPE="cccam"
if [ -f "/etc/enigma2/settings" ]; then
    SOFTCAM_TYPE=$(grep "config.plugins.Levi45FreeServer.softcam" /etc/enigma2/settings | cut -d= -f2)
    if [ -z "$SOFTCAM_TYPE" ]; then
        SOFTCAM_TYPE="cccam"
    fi
fi

echo "$(date '+[%Y-%m-%d %H:%M:%S]') Softcam type: $SOFTCAM_TYPE, Output file: $OUTPUT_FILE" >> "$LOG_FILE"

# List of URLs to scrape (same as in plugin.py)
URLS=(
    "https://cccam-premium.pro/free-cccam/"
    "https://cccamsate.com/free"
    "https://cccamiptv.tv/cccamfree/#page-content"
    "https://cccam.net/freecccam"
    "https://cccamia.com/cccam-free/"
    "https://cccamhub.com/cccamfree/"
    "https://cccamgalaxy.com/"
    "https://cccamfree48h.yolasite.com/server-2.php"
    "https://cccamx.com/free-cccam"
    "https://bosscccam.co/Test.php"
    "https://iptv-15days.blogspot.com/    
    "https://raw.githubusercontent.com/levi-45/free-cccam/main/servers.txt"
)

# Add dynamic URLs for testious.com - FIXED DATE FORMAT
# Use Python to get dates reliably (since busybox date might not support -d flag)
YESTERDAY=$(python -c "from datetime import datetime, timedelta; print((datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))")
TODAY=$(python -c "from datetime import datetime; print(datetime.now().strftime('%Y-%m-%d'))")

echo "$(date '+[%Y-%m-%d %H:%M:%S]') Yesterday: $YESTERDAY, Today: $TODAY" >> "$LOG_FILE"

URLS+=("https://testious.com/old-free-cccam-servers/$YESTERDAY/")
URLS+=("https://testious.com/old-free-cccam-servers/$TODAY/")

TEMP_FILE="/tmp/servers_temp.txt"
FINAL_FILE="/tmp/servers_final.txt"
CONVERTED_FILE="/tmp/servers_converted.txt"

# Clear temp files
> "$FINAL_FILE"
> "$TEMP_FILE"
> "$CONVERTED_FILE"

SERVER_COUNT=0

# Function to convert C-line to OSCam reader
convert_to_oscam() {
    local line="$1"
    local parts=($line)
    
    if [ "${#parts[@]}" -lt 5 ]; then
        return
    fi
    
    local protocol_type=$(echo "${parts[0]}" | tr -d ':')
    local host="${parts[1]}"
    local port="${parts[2]}"
    local user="${parts[3]}"
    local password="${parts[4]}"
    
    if [ "$protocol_type" = "C" ]; then
        echo "[reader]"
        echo "label = ${host}_${port}"
        echo "protocol = cccam"
        echo "device = ${host},${port}"
        echo "user = ${user}"
        echo "password = ${password}"
        echo "group = 1"
        echo "cccversion = 2.1.2"
        echo "inactivitytimeout = 1"
        echo "reconnecttimeout = 30"
        echo "disablelog = 1"
        echo ""
    elif [ "$protocol_type" = "N" ] && [ "${#parts[@]}" -ge 6 ]; then
        local key="${parts[5]}"
        echo "[reader]"
        echo "label=${host}_${port}"
        echo "enable=1"
        echo "protocol=newcamd"
        echo "key=${key}"
        echo "device=${host},${port}"
        echo "user=${user}"
        echo "password=${password}"
        echo "group=1"
        echo "inactivitytimeout=1"
        echo "reconnecttimeout=30"
        echo "lb_weight=100"
        echo "cccversion=2.1.2"
        echo "cccmaxhops=10"
        echo "cccwantemu=1"
        echo "ccckeepalive=1"
        echo ""
    fi
}

# Function to parse servers using regex (improved)
parse_servers() {
    local content="$1"
    local url="$2"
    
    # Clean the content - remove HTML tags and keep only text
    local clean_content=$(echo "$content" | sed 's/<[^>]*>//g' | tr -d '\r' | sed 's/&nbsp;/ /g' | sed 's/&amp;/\&/g')
    
    # Extract CCcam servers with various patterns
    echo "$clean_content" | grep -oE 'C:[[:space:]]*[[:alnum:].-]+[[:space:]]+[0-9]+[[:space:]]+[[:alnum:].-]+[[:space:]]+[[:alnum:].-]+' >> "$TEMP_FILE"
    echo "$clean_content" | grep -oE 'C:[[:space:]]*[[:alnum:].-]+[[:space:]]+[0-9]+[[:space:]]+[[:alnum:].-]+[[:space:]]+[[:alnum:].-]+[[:space:]]*#' >> "$TEMP_FILE"
    
    # Extract Newcamd servers
    echo "$clean_content" | grep -oE 'N:[[:space:]]*[[:alnum:].-]+[[:space:]]+[0-9]+[[:space:]]+[[:alnum:].-]+[[:space:]]+[[:alnum:].-]+[[:space:]]+[0-9a-fA-F]+' >> "$TEMP_FILE"
    
    # Count servers found in this URL
    local count=$(grep -cE '^[CN]:' "$TEMP_FILE" 2>/dev/null || echo "0")
    if [ "$count" -gt 0 ]; then
        echo "$(date '+[%Y-%m-%d %H:%M:%S]') Found $count servers from $url" >> "$LOG_FILE"
    else
        echo "$(date '+[%Y-%m-%d %H:%M:%S]') No servers found in $url" >> "$LOG_FILE"
    fi
}

# Download and parse from each URL
for URL in "${URLS[@]}"; do
    echo "$(date '+[%Y-%m-%d %H:%M:%S]') Downloading from $URL" >> "$LOG_FILE"
    
    # Download using curl with timeout and follow redirects
    if curl -k -s -L --max-time 30 -A "Mozilla/5.0" "$URL" -o "$TEMP_FILE".download; then
        CONTENT=$(cat "$TEMP_FILE".download)
        parse_servers "$CONTENT" "$URL"
        rm -f "$TEMP_FILE".download
    else
        echo "$(date '+[%Y-%m-%d %H:%M:%S]') Failed to download from $URL" >> "$LOG_FILE"
        rm -f "$TEMP_FILE".download
    fi
done

# Process found servers
if [ -s "$TEMP_FILE" ]; then
    # Clean and deduplicate servers
    grep -E '^[CN]:' "$TEMP_FILE" | sed 's/#.*//' | sed 's/[[:space:]]*$//' | sort | uniq > "$FINAL_FILE"
    FINAL_COUNT=$(wc -l < "$FINAL_FILE")
    
    echo "$(date '+[%Y-%m-%d %H:%M:%S]') Found $FINAL_COUNT unique servers after processing" >> "$LOG_FILE"
    
    if [ "$FINAL_COUNT" -gt 0 ]; then
        START_MARKER="# >>>>>> BEGIN AUTO-GENERATED BY Levi45FreeServer <<<<<<"
        END_MARKER="# >>>>>> END AUTO-GENERATED BY Levi45FreeServer <<<<<<"
        
        # Backup original file
        if [ -f "$OUTPUT_FILE" ]; then
            cp "$OUTPUT_FILE" "$OUTPUT_FILE.backup"
            # Remove previous content between markers
            sed -i "/$START_MARKER/,/$END_MARKER/d" "$OUTPUT_FILE"
            sed -i '/^$/N;/^\\n$/D' "$OUTPUT_FILE"
        else
            touch "$OUTPUT_FILE"
        fi
        
        echo "" >> "$OUTPUT_FILE"
        echo "$START_MARKER" >> "$OUTPUT_FILE"
        echo "# Generated on $(date '+%Y-%m-%d %H:%M:%S')" >> "$OUTPUT_FILE"
        echo "# Total servers: $FINAL_COUNT" >> "$OUTPUT_FILE"
        
        # Convert to OSCam format if needed
        if [ "$SOFTCAM_TYPE" = "oscam" ] || [ "$SOFTCAM_TYPE" = "ncam" ]; then
            echo "$(date '+[%Y-%m-%d %H:%M:%S]') Converting servers to OSCam format" >> "$LOG_FILE"
            while IFS= read -r line; do
                convert_to_oscam "$line" >> "$CONVERTED_FILE"
            done < "$FINAL_FILE"
            cat "$CONVERTED_FILE" >> "$OUTPUT_FILE"
        else
            # For CCcam, just add the raw servers
            cat "$FINAL_FILE" >> "$OUTPUT_FILE"
        fi
        
        echo "$END_MARKER" >> "$OUTPUT_FILE"
        
        echo "$(date '+[%Y-%m-%d %H:%M:%S]') Success! Added $FINAL_COUNT servers to $OUTPUT_FILE" >> "$LOG_FILE"
        
        # Verify the file was written
        if [ -f "$OUTPUT_FILE" ]; then
            if [ "$SOFTCAM_TYPE" = "oscam" ] || [ "$SOFTCAM_TYPE" = "ncam" ]; then
                WRITTEN_COUNT=$(grep -c '^\[reader\]' "$OUTPUT_FILE")
            else
                WRITTEN_COUNT=$(grep -cE '^[CN]:' "$OUTPUT_FILE")
            fi
            echo "$(date '+[%Y-%m-%d %H:%M:%S]') Verification: $WRITTEN_COUNT readers/servers in output file" >> "$LOG_FILE"
        fi
    else
        echo "$(date '+[%Y-%m-%d %H:%M:%S]') No valid servers found after processing" >> "$LOG_FILE"
    fi
else
    echo "$(date '+[%Y-%m-%d %H:%M:%S]') No servers found from any source" >> "$LOG_FILE"
fi

# Clean up
rm -f "$TEMP_FILE" "$FINAL_FILE" "$CONVERTED_FILE" "$TEMP_FILE.download" 2>/dev/null

echo "$(date '+[%Y-%m-%d %H:%M:%S]') Cron job completed" >> "$LOG_FILE"
"""
    try:
        with open("/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer/cron_download.sh", "w") as f:
            f.write(cron_script)
        os.chmod("/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer/cron_download.sh", 0o755)
        log("Cron download script created")
    except Exception as e:
        log("Error creating cron script: {}".format(e))

def setup_cron_job():
    """Setup or remove cron job based on settings"""
    try:
        os.system("crontab -l | grep -v 'Levi45FreeServer' | crontab -")
        
        if (is_autodownload_enabled() and 
            config.plugins.Levi45FreeServer.enablecron.value == "1"):
            
            download_time = config.plugins.Levi45FreeServer.downloadtime.value
            hour = download_time[0]
            minute = download_time[1]
            
            cron_cmd = "{} {} * * * /bin/sh {}/cron_download.sh\n".format(
                minute, hour, PLUGIN_PATH)
            
            os.system("(crontab -l; echo \"{}\") | crontab -".format(cron_cmd))
            log("Cron job setup for {}:{} daily".format(hour, minute))
        else:
            log("Cron job disabled or removed")
            
    except Exception as e:
        log("Error setting up cron job: {}".format(e))

# Initialize last download time
get_last_download_time()

# ============================================================================
# Settings Screen Class
# ============================================================================

class Levi45FreeServerSettings(ConfigListScreen, Screen):
    skin = """
        <screen position="center,center" size="600,500" title="Levi45FreeServer Settings">
            <widget name="config" position="10,10" size="580,400" scrollbarMode="showOnDemand" />
            <eLabel text="Press OK to save, Exit to cancel" position="10,420" size="580,30" font="Regular;18" />
            <ePixmap pixmap="/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer/images/kofi.png" position="150,250" size="100,100" zPosition="5" />
            <ePixmap pixmap="/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer/images/paypal.png" position="350,250" size="100,100" zPosition="5" />                        
        </screen>
    """
    
    def __init__(self, session):
        Screen.__init__(self, session)
        # ... existing code ...
        
    
    def __init__(self, session):
        Screen.__init__(self, session)
        self.setup_title = "Levi45FreeServer Settings"
        
        self.list = []
        ConfigListScreen.__init__(self, self.list, session=session)
        
        self.current_softcam = config.plugins.Levi45FreeServer.softcam.value
        self.autodownload_enabled = is_autodownload_enabled()
        
        self.autodownload_value = 1 if self.autodownload_enabled else 0
        self.autodownload = getConfigListEntry("Auto Download", ConfigSelection(choices=[("0", "Disabled"), ("1", "Enabled")], default=str(self.autodownload_value)))
        self.downloadtime = getConfigListEntry("Download Time", config.plugins.Levi45FreeServer.downloadtime)
        self.softcam = getConfigListEntry("Softcam Type", config.plugins.Levi45FreeServer.softcam)
        self.outputfile = getConfigListEntry("Output File", config.plugins.Levi45FreeServer.outputfile)
        self.downloadinterval = getConfigListEntry("Download Interval (hours)", config.plugins.Levi45FreeServer.downloadinterval)
        self.enablecron = getConfigListEntry("Enable Background Cron", config.plugins.Levi45FreeServer.enablecron)
        
        self.list.append(self.autodownload)
        self.list.append(self.downloadtime)
        self.list.append(self.softcam)
        self.list.append(self.outputfile)
        self.list.append(self.downloadinterval)
        self.list.append(self.enablecron)
        
        self["config"].list = self.list
        self["config"].l.setList(self.list)
        
        self["actions"] = ActionMap(["SetupActions", "ColorActions"],
        {
            "ok": self.save,
            "cancel": self.cancel,
            "green": self.save,
            "red": self.cancel,
        }, -2)
        
        self.update_output_file()
        log("Settings screen opened - current autodownload: {}".format(self.autodownload_enabled))
    
    def keyLeft(self):
        ConfigListScreen.keyLeft(self)
        self.checkSoftcamChange()
        self.update_autodownload_value()
    
    def keyRight(self):
        ConfigListScreen.keyRight(self)
        self.checkSoftcamChange()
        self.update_autodownload_value()
    
    def update_autodownload_value(self):
        current = self["config"].getCurrent()
        if current and current[0] == "Auto Download":
            self.autodownload_value = int(current[1].value)
            log("Updated autodownload value: {}".format(self.autodownload_value))
    
    def checkSoftcamChange(self):
        if self.current_softcam != config.plugins.Levi45FreeServer.softcam.value:
            self.current_softcam = config.plugins.Levi45FreeServer.softcam.value
            self.update_output_file()
    
    def update_output_file(self):
        if self.current_softcam == "cccam":
            config.plugins.Levi45FreeServer.outputfile.value = "/etc/CCcam.cfg"
        elif self.current_softcam == "oscam":
            config.plugins.Levi45FreeServer.outputfile.value = "/etc/tuxbox/config/oscam.server"
        elif self.current_softcam == "ncam":
            config.plugins.Levi45FreeServer.outputfile.value = "/etc/tuxbox/config/ncam.server"
        
        for i, entry in enumerate(self.list):
            if entry[0] == "Output File":
                self.list[i] = getConfigListEntry("Output File", config.plugins.Levi45FreeServer.outputfile)
                break
        
        self["config"].setList(self.list)
    
    def save(self):
        for x in self["config"].list:
            x[1].save()
        
        enabled = (self.autodownload_value == 1)
        log("Saving autodownload setting: {}".format(enabled))
        
        success = set_autodownload_enabled(enabled)
        log("Save autodownload result: {}".format(success))
        
        setup_cron_job()
        configfile.save()
        
        log("All settings saved - autodownload: {}".format(enabled))
        self.close(True)
    
    def cancel(self):
        for x in self["config"].list:
            x[1].cancel()
        self.close(False)

# ============================================================================
# Main Plugin Screen Class
# ============================================================================

class Levi45FreeServerScreen(Screen):
    skin = """
        <screen name="Levi45FreeServerScreen" position="center,center" size="550,630" title="Satellite-Forum.Com V 2.2">
            <widget name="status_label" position="10,10" size="580,200" font="Regular;20" />
            <widget name="info_label" position="10,220" size="580,100" font="Regular;16" />
            <eLabel text="\c0000ffffPress Blue to save as CCcam" position="10,330" size="580,30" font="Regular;18" />
            <eLabel text="\c0000ff00Press Green to save as OSCam" position="10,360" size="580,30" font="Regular;18" />
            <eLabel text="\c00ffff00Press Yellow to save as NCam" position="10,390" size="580,30" font="Regular;18" />
            <eLabel text="\c00ff0000Press Red to force download" position="10,420" size="580,30" font="Regular;18" />
            <eLabel text="Press Menu for settings" position="10,450" size="580,30" font="Regular;18" />
            <widget name="support" position="60,480" size="480,25" font="Regular; 24" valign="center" halign="left" transparent="1" foregroundColor="#ffcc00" zPosition="10" /> 
            <ePixmap pixmap="/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer/images/kofi.png" position="120,520" size="100,100" zPosition="5" />
            <ePixmap pixmap="/usr/lib/enigma2/python/Plugins/Extensions/Levi45FreeServer/images/paypal.png" position="320,520" size="100,100" zPosition="5" />              
        </screen>
    """
    
    def __init__(self, session, args=None):
        Screen.__init__(self, session)
        
        # Initialize the widgets
        self["status_label"] = Label("Ready to download free servers...")
        self["info_label"] = Label("Free Server Downloader")
        
        # Add the support label with colored text
        support_txt = "Please Support if you like the plugin"
        self["support"] = Label(support_txt)
        
        self["actions"] = ActionMap(["ColorActions", "OkCancelActions", "MenuActions"],
        {
            "blue": self.start_download_cccam,
            "green": self.start_download_oscam,
            "yellow": self.start_download_ncam,
            "red": self.force_immediate_download,
            "cancel": self.close,
            "menu": self.open_settings,
        }, -1)
        
        self.servers_to_save = []
        self.scrape_index = 0
        self.format_choice = "cccam"
        
        # Fix for eTimer - Dreambox compatible
        self.check_timer = eTimer()
        
        # Try different methods for connecting the timer
        try:
            # Method 1: Modern Enigma2 (callback attribute)
            if hasattr(self.check_timer, 'callback'):
                self.check_timer.callback.append(self.check_auto_download)
            # Method 2: Older Enigma2 (timeout connect)
            elif hasattr(self.check_timer, 'timeout'):
                self.check_timer.timeout.get().append(self.check_auto_download)
            # Method 3: Alternative approach (using connect)
            elif hasattr(self.check_timer, 'timeout'):
                self.check_timer_conn = self.check_timer.timeout.connect(self.check_auto_download)
        except:
            # Method 4: Fallback - use the most common approach
            try:
                self.check_timer.timeout.get().append(self.check_auto_download)
            except:
                try:
                    self.check_timer.callback.append(self.check_auto_download)
                except:
                    log("Could not set up timer callback - auto-download disabled")
        
        self.check_timer.start(60000, False)
        
        try:
            with open(LOG_FILE, "a") as f:
                f.write("[{}] Plugin started\n".format(datetime.datetime.now()))
                enabled = is_autodownload_enabled()
                f.write("[{}] Auto-download enabled: {}\n".format(datetime.datetime.now(), enabled))
                f.write("[{}] Last download time: {}\n".format(datetime.datetime.now(), last_download_time))
                f.write("[{}] Background cron: {}\n".format(datetime.datetime.now(), 
                    "Enabled" if config.plugins.Levi45FreeServer.enablecron.value == "1" else "Disabled"))
        except Exception as e:
            print("Failed to write to log file: {}".format(e))

    def check_auto_download(self):
        """Check if it's time for auto-download"""
        if not is_autodownload_enabled():
            return
        
        now = datetime.datetime.now()
        
        download_time = config.plugins.Levi45FreeServer.downloadtime.value
        scheduled_time_today = datetime.datetime(now.year, now.month, now.day, 
                                           download_time[0], download_time[1])
        
        if last_download_time is None or last_download_time.date() != now.date():
            if now >= scheduled_time_today:
                log("Time for scheduled daily download (plugin open)")
                self.auto_download()
                return
        
        interval_hours = config.plugins.Levi45FreeServer.downloadinterval.value
        if last_download_time:
            time_diff = (now - last_download_time).total_seconds()
            if time_diff >= interval_hours * 3600:
                log("Time for interval download ({} hours elapsed, plugin open)".format(time_diff // 3600))
                self.auto_download()
                return

    def force_immediate_download(self):
        global last_download_time
        log("Force immediate download triggered")
        last_download_time = None
        self.auto_download()

    def open_settings(self):
        self.session.openWithCallback(self.settingsClosed, Levi45FreeServerSettings)
    
    def settingsClosed(self, result):
        """Callback when settings screen is closed"""
        if result:
            # Settings were saved
            enabled = is_autodownload_enabled()
            status_text = "Settings saved. Auto-download {}.".format("ENABLED" if enabled else "DISABLED")
            
            # Update the status label safely
            try:
                if "status_label" in self:
                    self["status_label"].setText(status_text)
            except Exception as e:
                log("Error updating status label: {}".format(e))
                # Fallback to message box
                from Screens.MessageBox import MessageBox
                self.session.open(MessageBox, status_text, MessageBox.TYPE_INFO, timeout=5)

    def auto_download(self):
        global last_download_time
        log("Starting auto-download...")
        if "status_label" in self:
            self["status_label"].setText("Auto-download in progress...")
        softcam_type = config.plugins.Levi45FreeServer.softcam.value
        last_download_time = datetime.datetime.now()
        save_last_download_time()
        self.start_download(softcam_type)

    def start_download_cccam(self):
        self.start_download("cccam")

    def start_download_oscam(self):
        self.start_download("oscam")
        
    def start_download_ncam(self):
        self.start_download("ncam")

    def start_download(self, format_choice):
        if "status_label" in self:
            self["status_label"].setText("Starting download for {}... Please wait.".format(format_choice.upper()))
        self.servers_to_save = []
        self.scrape_index = 0
        self.format_choice = format_choice
        self.scrape_next_url()

    def scrape_next_url(self):
        if self.scrape_index < len(SCRAPE_URLS):
            url_entry = SCRAPE_URLS[self.scrape_index]
            
            if isinstance(url_entry, dict) and "url_generator" in url_entry:
                url = url_entry["url_generator"]()
                parser_name = url_entry["parser_name"]
            else:
                url = url_entry[0]
                parser_name = url_entry[1]
                
            if "status_label" in self:
                self["status_label"].setText("Scraping from: {}".format(url))
            log("Attempting to scrape from {} with parser '{}'".format(url, parser_name))
            
            try:
                cmd = ["curl", "-k", "-s", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36", url]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate()
                
                if process.returncode == 0:
                    html_content_decoded = stdout.decode('utf-8')
                    log("Raw HTML from {}:\n{}...".format(url, html_content_decoded[:500]))
                    
                    parser_func = PARSERS.get(parser_name)
                    if parser_func:
                        new_servers = parser_func(html_content_decoded)
                        self.servers_to_save.extend(new_servers)
                        if "status_label" in self:
                            self["status_label"].setText("Found {} servers from this site.".format(len(new_servers)))
                    else:
                        if "status_label" in self:
                            self["status_label"].setText("No parser found for {}. Skipping.".format(parser_name))
                else:
                    error_msg = stderr.decode('utf-8')
                    if "status_label" in self:
                        self["status_label"].setText("Failed to download from {}.".format(url))
                    log("Failed to download from {}: {}".format(url, error_msg))

            except Exception as e:
                if "status_label" in self:
                    self["status_label"].setText("Error executing curl: {}".format(e))
                log("Error executing curl: {}".format(e))
            
            self.scrape_index += 1
            self.scrape_next_url()
        else:
            self.save_servers_to_file()
            
    def save_servers_to_file(self):
        if not self.servers_to_save:
            if "status_label" in self:
                self["status_label"].setText("No servers were found. Check the log file for details.")
            log("No servers were found. Exiting.")
            return
            
        start_marker = "# >>>>>> BEGIN AUTO-GENERATED BY Levi45FreeServer <<<<<<\n"
        end_marker = "# >>>>>> END AUTO-GENERATED BY Levi45FreeServer <<<<<<\n"

        if is_autodownload_enabled():
            output_files = [config.plugins.Levi45FreeServer.outputfile.value]
        else:
            if self.format_choice == "cccam":
                output_files = ["/etc/CCcam.cfg", "/etc/tuxbox/config/CCcam.cfg"]
            elif self.format_choice == "oscam":
                output_files = ["/etc/tuxbox/config/oscam.server"]
            elif self.format_choice == "ncam":
                output_files = ["/etc/tuxbox/config/ncam.server"]

        for file_path in output_files:
            try:
                existing_content = ""
                if os.path.exists(file_path):
                    with open(file_path, "r") as f:
                        existing_content = f.read()

                start_index = existing_content.find(start_marker)
                end_index = existing_content.find(end_marker)
                
                if start_index != -1 and end_index != -1:
                    log("Deleting previous content in {}".format(file_path))
                    before = existing_content[:start_index]
                    after = existing_content[end_index + len(end_marker):]
                    cleaned_content = before.rstrip() + "\n" + after.lstrip()
                else:
                    cleaned_content = existing_content
                
                with open(file_path, "w") as f:
                    f.write(cleaned_content.strip())
                    
                    if self.format_choice in ["oscam", "ncam"]:
                        oscam_servers = [convert_to_oscam_reader(s) for s in self.servers_to_save]
                        if oscam_servers:
                            f.write("\n\n" + start_marker)
                            for server_line in oscam_servers:
                                f.write(server_line + "\n\n")
                            f.write(end_marker)
                    elif self.format_choice == "cccam":
                        if self.servers_to_save:
                            f.write("\n\n" + start_marker)
                            for server_line in self.servers_to_save:
                                f.write(server_line + "\n")
                            f.write(end_marker)

                if "status_label" in self:
                    self["status_label"].setText("Success! Appended {} servers to {}.".format(len(self.servers_to_save), file_path))
                log("Success! Appended {} servers to {}.".format(len(self.servers_to_save), file_path))

            except Exception as e:
                if "status_label" in self:
                    self["status_label"].setText("Failed to write to file {}: {}".format(file_path, e))
                log("Failed to write to file {}: {}".format(file_path, e))

# Create cron script on plugin load
try:
    create_cron_script()
    setup_cron_job()
except Exception as e:
    log("Error during plugin initialization: {}".format(e))

# ============================================================================
# Main Plugin Descriptor
# ============================================================================

def main(session, **kwargs):
    session.open(Levi45FreeServerScreen)

def Plugins(**kwargs):
    return [PluginDescriptor(name="Levi45FreeServer", description="Satellite-Forum.Com V 2.2",
                             where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main, icon="plugin.png")]
