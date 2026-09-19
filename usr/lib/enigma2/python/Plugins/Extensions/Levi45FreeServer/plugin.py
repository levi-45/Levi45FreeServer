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
    # Dynamic URLs for testious.com
    {"url_generator": lambda: "https://testious.com/old-free-cccam-servers/{}/".format((datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')),
     "parser_name": "parser_testious_servers"},
    {"url_generator": lambda: "https://testious.com/old-free-cccam-servers/{}/".format(datetime.date.today().strftime('%Y-%m-%d')),
     "parser_name": "parser_testious_servers"}
]

# Log file location - /tmp is cleared on reboot
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
    default="oscam"
)
config.plugins.Levi45FreeServer.outputfile = ConfigText(default="/etc/tuxbox/config/oscam.server", fixed_size=False)
config.plugins.Levi45FreeServer.downloadinterval = ConfigInteger(default=1, limits=(1, 168))  # hours
config.plugins.Levi45FreeServer.enablecron = ConfigSelection(choices=[("0", "Disabled"), ("1", "Enabled")], default="0")


def log(message):
    """Simple logging function to help with debugging."""
    try:
        with open(LOG_FILE, "a") as f:
            f.write("[{}] {}\n".format(datetime.datetime.now(), message))
    except Exception as e:
        print("Failed to write to log file: {}".format(e))


# ============================================================================
# Parser Functions
# ============================================================================

def parse_servers_curl_regex(html_content):
    """
    Parses an HTML page using a flexible regex pattern.
    Assumes the format is like: 'C: host port user pass'
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


PARSERS = {
    "parser_curl_regex": parse_servers_curl_regex,
    "parser_testious_servers": parse_testious_servers,
}


# ============================================================================
# Conversion Function for OSCam and NCam
# ============================================================================

def convert_to_oscam_reader(server_line):
    """
    Converts a CCcam or Newcamd line to an OSCam or NCam reader configuration.
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


# ============================================================================
# State file helpers (shared with cron_download.sh)
# ============================================================================

AUTODOWNLOAD_FILE = "/etc/levi45_autodownload.txt"
SOFTCAM_STATE_FILE = "/etc/levi45_softcam.txt"
OUTPUTFILE_STATE_FILE = "/etc/levi45_outputfile.txt"

# Global variable to track last download time
last_download_time = None


def is_autodownload_enabled():
    """Check if auto-download is enabled."""
    try:
        if os.path.exists(AUTODOWNLOAD_FILE):
            with open(AUTODOWNLOAD_FILE, "r") as f:
                return f.read().strip() == "True"
        return False
    except:
        return False


def set_autodownload_enabled(enabled):
    """Set auto-download status."""
    try:
        with open(AUTODOWNLOAD_FILE, "w") as f:
            f.write("True" if enabled else "False")
        return True
    except:
        return False


def write_softcam_state():
    """Write the current softcam type for cron to read."""
    try:
        with open(SOFTCAM_STATE_FILE, "w") as f:
            f.write(config.plugins.Levi45FreeServer.softcam.value)
        return True
    except Exception as e:
        log("Failed to write softcam state: {}".format(e))
        return False


def write_outputfile_state():
    """Write the current output file path for cron to read."""
    try:
        with open(OUTPUTFILE_STATE_FILE, "w") as f:
            f.write(config.plugins.Levi45FreeServer.outputfile.value)
        return True
    except Exception as e:
        log("Failed to write outputfile state: {}".format(e))
        return False


def get_last_download_time():
    """Get the last download time from a file to persist across restarts."""
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
    """Save the last download time to a file."""
    global last_download_time
    try:
        time_file = "/tmp/levi45_last_download.txt"
        with open(time_file, "w") as f:
            f.write(str(last_download_time.timestamp()))
    except:
        pass


# ============================================================================
# Cron script generator (writes the FIXED, POSIX-safe shell script)
# ============================================================================

def create_cron_script():
    """
    Write the standalone cron_download.sh file. This is a POSIX / BusyBox-safe
    script that reads state files written by the plugin, and produces output
    matching the plugin's own scraping logic (same regex coverage, same
    newcamd key handling).
    """
    # NOTE: We use a raw triple-quoted string so shell backslashes and ${...}
    # are preserved literally.
    cron_script = r'''#!/bin/sh
# Cron script for Levi45FreeServer - POSIX / BusyBox compatible
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

LOG_FILE="/tmp/downloader.log"
AUTODOWNLOAD_FILE="/etc/levi45_autodownload.txt"

log() {
    echo "$(date '+[%Y-%m-%d %H:%M:%S]') $1" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Check auto-download flag
# ---------------------------------------------------------------------------
if [ ! -f "$AUTODOWNLOAD_FILE" ] || [ "$(cat "$AUTODOWNLOAD_FILE" | tr -d '\r\n ')" != "True" ]; then
    log "Auto-download disabled, skipping"
    exit 0
fi

log "Cron job started"

# ---------------------------------------------------------------------------
# Read settings (prefer dedicated state files written by the plugin)
# ---------------------------------------------------------------------------
get_setting() {
    key="$1"
    if [ -f /etc/enigma2/settings ]; then
        val=$(grep "^${key}=" /etc/enigma2/settings | head -n1 | cut -d= -f2-)
        val=$(echo "$val" | sed 's/^"//; s/"$//; s/[[:space:]]*$//' | tr -d '\r')
        echo "$val"
    fi
}

if [ -f /etc/levi45_softcam.txt ]; then
    SOFTCAM_TYPE=$(cat /etc/levi45_softcam.txt | tr -d '\r\n ')
else
    SOFTCAM_TYPE=$(get_setting "config.plugins.Levi45FreeServer.softcam")
fi
[ -z "$SOFTCAM_TYPE" ] && SOFTCAM_TYPE="cccam"

if [ -f /etc/levi45_outputfile.txt ]; then
    OUTPUT_FILE=$(cat /etc/levi45_outputfile.txt | tr -d '\r\n')
else
    OUTPUT_FILE=$(get_setting "config.plugins.Levi45FreeServer.outputfile")
fi
[ -z "$OUTPUT_FILE" ] && OUTPUT_FILE="/etc/CCcam.cfg"

log "Softcam type: $SOFTCAM_TYPE, Output file: $OUTPUT_FILE"

# ---------------------------------------------------------------------------
# URLs to scrape
# ---------------------------------------------------------------------------
URLS="
https://cccam-premium.pro/free-cccam/
https://cccamsate.com/free
https://cccamiptv.tv/cccamfree/#page-content
https://cccam.net/freecccam
https://cccamia.com/cccam-free/
https://cccamhub.com/cccamfree/
https://cccamgalaxy.com/
https://cccamfree48h.yolasite.com/server-2.php
https://cccamx.com/free-cccam
https://bosscccam.co/Test.php
https://iptv-15days.blogspot.com/
https://raw.githubusercontent.com/levi-45/free-cccam/main/servers.txt
"

# ---------------------------------------------------------------------------
# Dynamic testious URLs (yesterday + today). Try python3, python, then date.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    PY=""
fi

if [ -n "$PY" ]; then
    YESTERDAY=$($PY -c "from datetime import datetime, timedelta; print((datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))" 2>/dev/null)
    TODAY=$($PY -c "from datetime import datetime; print(datetime.now().strftime('%Y-%m-%d'))" 2>/dev/null)
fi

if [ -z "$YESTERDAY" ]; then
    YESTERDAY=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -D "%s" -d "@$(( $(date +%s) - 86400 ))" +%Y-%m-%d 2>/dev/null)
fi
if [ -z "$TODAY" ]; then
    TODAY=$(date +%Y-%m-%d)
fi

log "Yesterday: $YESTERDAY, Today: $TODAY"

URLS="$URLS
https://testious.com/old-free-cccam-servers/$YESTERDAY/
https://testious.com/old-free-cccam-servers/$TODAY/
"

TEMP_FILE="/tmp/servers_temp.txt"
FINAL_FILE="/tmp/servers_final.txt"
CONVERTED_FILE="/tmp/servers_converted.txt"
DOWNLOAD_FILE="/tmp/servers_dl.tmp"

: > "$FINAL_FILE"
: > "$TEMP_FILE"
: > "$CONVERTED_FILE"

# ---------------------------------------------------------------------------
# Convert a single C:/N: line to OSCam reader format (POSIX-safe)
# ---------------------------------------------------------------------------
convert_to_oscam() {
    line="$1"
    # shellcheck disable=SC2086
    set -- $line

    proto=$(echo "$1" | tr -d ':')
    host="$2"
    port="$3"
    user="$4"
    password="$5"

    # Everything from field 6 onwards is the newcamd key (may be split by
    # whitespace). Join all remaining fields into a single hex string.
    shift 5
    key=""
    for part in "$@"; do
        key="${key}${part}"
    done

    [ -z "$host" ] || [ -z "$port" ] || [ -z "$user" ] || [ -z "$password" ] && return

    if [ "$proto" = "C" ]; then
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
    elif [ "$proto" = "N" ] && [ -n "$key" ]; then
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

# ---------------------------------------------------------------------------
# Parse a downloaded page for C:/N: server lines.
# Character classes match the Python parser: letters, digits, dot, dash,
# underscore. Newcamd key may contain spaces and is normalised later.
# ---------------------------------------------------------------------------
parse_servers() {
    content="$1"
    url="$2"

    clean=$(echo "$content" \
        | sed 's/<[^>]*>//g' \
        | tr -d '\r' \
        | sed 's/&nbsp;/ /g; s/&amp;/\&/g; s/&#58;/:/g')

    # CCcam
    echo "$clean" | grep -oE 'C:[[:space:]]*[A-Za-z0-9._-]+[[:space:]]+[0-9]+[[:space:]]+[A-Za-z0-9._-]+[[:space:]]+[A-Za-z0-9._-]+' >> "$TEMP_FILE"

    # Newcamd (key may contain spaces)
    echo "$clean" | grep -oE 'N:[[:space:]]*[A-Za-z0-9._-]+[[:space:]]+[0-9]+[[:space:]]+[A-Za-z0-9._-]+[[:space:]]+[A-Za-z0-9._-]+[[:space:]]+[0-9a-fA-F ]+' \
        | sed 's/[[:space:]]*$//' >> "$TEMP_FILE"

    before=$(wc -l < "$TEMP_FILE" 2>/dev/null || echo 0)
    log "Parsed $url (temp lines now: $before)"
}

# ---------------------------------------------------------------------------
# Main download loop
# ---------------------------------------------------------------------------
log "URL count: $(echo "$URLS" | grep -c '^https')"

echo "$URLS" | while IFS= read -r URL; do
    [ -z "$URL" ] && continue
    case "$URL" in
        http*) ;;
        *) continue ;;
    esac

    log "Downloading from $URL"

    if curl -k -s -L --max-time 30 -A "Mozilla/5.0" "$URL" -o "$DOWNLOAD_FILE"; then
        if [ -s "$DOWNLOAD_FILE" ]; then
            CONTENT=$(cat "$DOWNLOAD_FILE")
            parse_servers "$CONTENT" "$URL"
        else
            log "Empty response from $URL"
        fi
    else
        log "Failed to download from $URL"
    fi
    rm -f "$DOWNLOAD_FILE"
done

# ---------------------------------------------------------------------------
# Process results
# ---------------------------------------------------------------------------
if [ -s "$TEMP_FILE" ]; then
    grep -E '^[CN]:' "$TEMP_FILE" \
        | sed 's/#.*//' \
        | sed 's/[[:space:]]*$//' \
        | sort -u > "$FINAL_FILE"

    FINAL_COUNT=$(wc -l < "$FINAL_FILE")
    log "Found $FINAL_COUNT unique servers after processing"

    if [ "$FINAL_COUNT" -gt 0 ]; then
        START_MARKER="# >>>>>> BEGIN AUTO-GENERATED BY Levi45FreeServer <<<<<<"
        END_MARKER="# >>>>>> END AUTO-GENERATED BY Levi45FreeServer <<<<<<"

        if [ -f "$OUTPUT_FILE" ]; then
            cp "$OUTPUT_FILE" "$OUTPUT_FILE.backup"
            sed -i "/$START_MARKER/,/$END_MARKER/d" "$OUTPUT_FILE"
        else
            mkdir -p "$(dirname "$OUTPUT_FILE")" 2>/dev/null
            touch "$OUTPUT_FILE"
        fi

        {
            echo ""
            echo "$START_MARKER"
            echo "# Generated on $(date '+%Y-%m-%d %H:%M:%S')"
            echo "# Total servers: $FINAL_COUNT"

            if [ "$SOFTCAM_TYPE" = "oscam" ] || [ "$SOFTCAM_TYPE" = "ncam" ]; then
                log "Converting servers to OSCam/NCam format"
                : > "$CONVERTED_FILE"
                while IFS= read -r line; do
                    convert_to_oscam "$line" >> "$CONVERTED_FILE"
                done < "$FINAL_FILE"
                cat "$CONVERTED_FILE"
            else
                cat "$FINAL_FILE"
            fi

            echo "$END_MARKER"
        } >> "$OUTPUT_FILE"

        log "Success! Added $FINAL_COUNT servers to $OUTPUT_FILE"

        if [ -f "$OUTPUT_FILE" ]; then
            if [ "$SOFTCAM_TYPE" = "oscam" ] || [ "$SOFTCAM_TYPE" = "ncam" ]; then
                WRITTEN_COUNT=$(grep -c '^\[reader\]' "$OUTPUT_FILE")
            else
                WRITTEN_COUNT=$(grep -cE '^[CN]:' "$OUTPUT_FILE")
            fi
            log "Verification: $WRITTEN_COUNT readers/servers in output file"
        fi
    else
        log "No valid servers found after processing"
    fi
else
    log "No servers found from any source"
fi

rm -f "$TEMP_FILE" "$FINAL_FILE" "$CONVERTED_FILE" "$DOWNLOAD_FILE" 2>/dev/null

log "Cron job completed"
'''
    try:
        script_path = os.path.join(PLUGIN_PATH, "cron_download.sh")
        with open(script_path, "w") as f:
            f.write(cron_script)
        os.chmod(script_path, 0o755)
        log("Cron download script created at {}".format(script_path))
        return True
    except Exception as e:
        log("Error creating cron script: {}".format(e))
        return False


def setup_cron_job():
    """Setup or remove cron job based on settings."""
    try:
        # Remove any existing Levi45FreeServer cron entries
        os.system("crontab -l 2>/dev/null | grep -v 'Levi45FreeServer' | crontab -")
        
        if (is_autodownload_enabled() and 
            config.plugins.Levi45FreeServer.enablecron.value == "1"):
            
            download_time = config.plugins.Levi45FreeServer.downloadtime.value
            hour = download_time[0]
            minute = download_time[1]
            
            cron_cmd = "{} {} * * * /bin/sh {}/cron_download.sh\n".format(
                minute, hour, PLUGIN_PATH)
            
            os.system("(crontab -l 2>/dev/null; echo \"{}\") | crontab -".format(cron_cmd))
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
        self.setup_title = "Levi45FreeServer Settings"
        
        self.list = []
        ConfigListScreen.__init__(self, self.list, session=session)
        
        self.current_softcam = config.plugins.Levi45FreeServer.softcam.value
        self.autodownload_enabled = is_autodownload_enabled()
        
        self.original_paths = {
            "cccam": "/etc/CCcam.cfg",
            "oscam": "/etc/tuxbox/config/oscam.server",
            "ncam": "/etc/tuxbox/config/ncam.server"
        }
        
        self.customized_paths = {}
        
        self.current_paths = {
            "cccam": config.plugins.Levi45FreeServer.outputfile.value if self.current_softcam == "cccam" else self.original_paths["cccam"],
            "oscam": config.plugins.Levi45FreeServer.outputfile.value if self.current_softcam == "oscam" else self.original_paths["oscam"],
            "ncam": config.plugins.Levi45FreeServer.outputfile.value if self.current_softcam == "ncam" else self.original_paths["ncam"]
        }
        
        current_output = config.plugins.Levi45FreeServer.outputfile.value
        if current_output != self.original_paths[self.current_softcam]:
            self.customized_paths[self.current_softcam] = current_output
            self.current_paths[self.current_softcam] = current_output
        
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
        
        log("Settings screen opened - current autodownload: {}, current paths: {}".format(
            self.autodownload_enabled, self.current_paths))
    
    def keyLeft(self):
        ConfigListScreen.keyLeft(self)
        self.update_autodownload_value()
        self.checkSoftcamChange()
    
    def keyRight(self):
        ConfigListScreen.keyRight(self)
        self.update_autodownload_value()
        self.checkSoftcamChange()
    
    def update_autodownload_value(self):
        current = self["config"].getCurrent()
        if current and current[0] == "Auto Download":
            self.autodownload_value = int(current[1].value)
            log("Updated autodownload value: {}".format(self.autodownload_value))
    
    def checkSoftcamChange(self):
        """Check if softcam type changed and update output file path appropriately."""
        new_softcam = config.plugins.Levi45FreeServer.softcam.value
        
        if self.current_softcam != new_softcam:
            log("Softcam changed from {} to {}".format(self.current_softcam, new_softcam))
            
            current_output = config.plugins.Levi45FreeServer.outputfile.value
            
            if current_output != self.original_paths[self.current_softcam]:
                self.customized_paths[self.current_softcam] = current_output
                log("Saved customized path for {}: {}".format(self.current_softcam, current_output))
            
            self.current_paths[self.current_softcam] = current_output
            
            if new_softcam in self.customized_paths:
                new_path = self.customized_paths[new_softcam]
                log("Using previously customized path for {}: {}".format(new_softcam, new_path))
            else:
                new_path = self.original_paths[new_softcam]
                log("Using default path for {}: {}".format(new_softcam, new_path))
            
            config.plugins.Levi45FreeServer.outputfile.value = new_path
            
            for i, entry in enumerate(self.list):
                if entry[0] == "Output File":
                    self.list[i] = getConfigListEntry("Output File", config.plugins.Levi45FreeServer.outputfile)
                    break
            
            self["config"].setList(self.list)
            self.current_softcam = new_softcam
    
    def save(self):
        current_output = config.plugins.Levi45FreeServer.outputfile.value
        if current_output != self.original_paths[self.current_softcam]:
            self.customized_paths[self.current_softcam] = current_output
            log("Saving customized path for {}: {}".format(self.current_softcam, current_output))
        
        for x in self["config"].list:
            x[1].save()
        
        enabled = (self.autodownload_value == 1)
        log("Saving autodownload setting: {}".format(enabled))
        
        success = set_autodownload_enabled(enabled)
        log("Save autodownload result: {}".format(success))
        
        # Write state files the cron script reads directly
        write_softcam_state()
        write_outputfile_state()
        
        # Regenerate the cron script (always the correct version)
        create_cron_script()
        
        # Setup cron job
        setup_cron_job()
        
        # Save the main config file
        configfile.save()
        
        log("All settings saved - autodownload: {}, softcam: {}, output file: {}".format(
            enabled,
            config.plugins.Levi45FreeServer.softcam.value,
            config.plugins.Levi45FreeServer.outputfile.value))
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
        <screen name="Levi45FreeServerScreen" position="center,center" size="550,630" title="Satellite-Forum.Com V 2.6">
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
        
        self["status_label"] = Label("Ready to download free servers...")
        self["info_label"] = Label("Free Server Downloader")
        
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
        
        try:
            if hasattr(self.check_timer, 'callback'):
                self.check_timer.callback.append(self.check_auto_download)
            elif hasattr(self.check_timer, 'timeout'):
                self.check_timer.timeout.get().append(self.check_auto_download)
        except:
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
        """Check if it's time for auto-download."""
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
        """Callback when settings screen is closed."""
        if result:
            enabled = is_autodownload_enabled()
            status_text = "Settings saved. Auto-download {}.".format("ENABLED" if enabled else "DISABLED")
            
            try:
                if "status_label" in self:
                    self["status_label"].setText(status_text)
            except Exception as e:
                log("Error updating status label: {}".format(e))
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
        config.plugins.Levi45FreeServer.softcam.value = "cccam"
        config.plugins.Levi45FreeServer.outputfile.value = "/etc/CCcam.cfg"
        self.start_download("cccam")

    def start_download_oscam(self):
        config.plugins.Levi45FreeServer.softcam.value = "oscam"
        config.plugins.Levi45FreeServer.outputfile.value = "/etc/tuxbox/config/oscam.server"
        self.start_download("oscam")
        
    def start_download_ncam(self):
        config.plugins.Levi45FreeServer.softcam.value = "ncam"
        config.plugins.Levi45FreeServer.outputfile.value = "/etc/tuxbox/config/ncam.server"
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
                cmd = ["curl", "-k", "-s", "-L", "--max-time", "30",
                       "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
                       url]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate()
                
                if process.returncode == 0:
                    html_content_decoded = stdout.decode('utf-8', 'ignore')
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
                    error_msg = stderr.decode('utf-8', 'ignore')
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

        # Determine target file dynamically based on button/format selection
        if self.format_choice == "cccam":
            file_path = "/etc/CCcam.cfg"
        elif self.format_choice == "ncam":
            file_path = "/etc/tuxbox/config/ncam.server"
        elif self.format_choice == "oscam":
            file_path = "/etc/tuxbox/config/oscam.server"
        else:
            file_path = config.plugins.Levi45FreeServer.outputfile.value

        output_files = [file_path]

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
                
                parent = os.path.dirname(file_path)
                if parent and not os.path.exists(parent):
                    try:
                        os.makedirs(parent)
                    except:
                        pass
                
                with open(file_path, "w") as f:
                    f.write(cleaned_content.strip())
                    
                    if self.format_choice in ["oscam", "ncam"]:
                        oscam_servers = [convert_to_oscam_reader(s) for s in self.servers_to_save]
                        oscam_servers = [s for s in oscam_servers if s]
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


# ============================================================================
# Plugin initialization
# ============================================================================
# On every plugin load:
#   1. Regenerate cron_download.sh with the correct, working contents
#   2. Sync state files with current config
#   3. Install / remove cron entry to match current settings

try:
    create_cron_script()
    write_softcam_state()
    write_outputfile_state()
    setup_cron_job()
except Exception as e:
    log("Error during plugin initialization: {}".format(e))


# ============================================================================
# Main Plugin Descriptor
# ============================================================================

def main(session, **kwargs):
    session.open(Levi45FreeServerScreen)

def Plugins(**kwargs):
    return [PluginDescriptor(name="Levi45FreeServer", description="Satellite-Forum.Com V 2.6",
                             where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main, icon="plugin.png")]
