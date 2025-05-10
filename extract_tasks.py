#!/usr/bin/env python3
# ==============================================================================
# Script: extract_tasks.py
#
# Description:
#   Extracts tasks from notes modified the previous day and adds them to a CalDAV todo list.
# ==============================================================================

import os, sys, re, json, logging, requests, caldav, uuid, hashlib
from datetime import datetime, timedelta
import yaml
from pathlib import Path
from dateutil import parser
import time

# Configure logging
logger = logging.getLogger(__name__)

# ===== Core Functions =====

def load_config(args):
    """Load configuration with command line overrides."""
    # Default configuration (minimal defaults, expanded only if needed)
    default_config = {
        "INPUT_FOLDER": os.path.expanduser("~/Documents/Notes"),
        "EXCLUDE_FOLDERS": [os.path.expanduser("~/Documents/Notes/AI")],
        "LLM_PROVIDER": "ollama",
        "OLLAMA_MODEL": "gemma:7b",
        "OLLAMA_SERVER_ADDRESS": "http://localhost:11434",
        "CALDAV_TODO_LIST": "tasks",
        "CHECK_EXISTING_TASKS": True
    }
    
    # Try to load from config file (search in common locations)
    config_locations = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"),
        os.path.expanduser("~/.config/extract_tasks/config.yaml"),
        "/etc/extract_tasks/config.yaml"
    ]
    
    for config_path in config_locations:
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = {**default_config, **(yaml.safe_load(f) or {})}
                logger.info(f"Loaded configuration from {config_path}")
                break
            except Exception as e:
                logger.warning(f"Error loading config from {config_path}: {e}")
    else:
        logger.info("No config file found, using default configuration")
        config = default_config
    
    # Apply command line overrides
    if args.input: config["INPUT_FOLDER"] = args.input
    if args.exclude: config["EXCLUDE_FOLDERS"] = args.exclude
    if args.model:
        if config.get("LLM_PROVIDER") == "openai" or args.provider == "openai":
            config["OPENAI_MODEL"] = args.model
        else:
            config["OLLAMA_MODEL"] = args.model
    if args.server: config["OLLAMA_SERVER_ADDRESS"] = args.server
    if args.provider: config["LLM_PROVIDER"] = args.provider
    if args.api_key: config["OPENAI_API_KEY"] = args.api_key
    if args.caldav_url: config["CALDAV_URL"] = args.caldav_url
    if args.caldav_user: config["CALDAV_USERNAME"] = args.caldav_user
    if args.caldav_pass: config["CALDAV_PASSWORD"] = args.caldav_pass
    if args.todo_list: config["CALDAV_TODO_LIST"] = args.todo_list
    if args.no_duplicate_check: config["CHECK_EXISTING_TASKS"] = False
    if args.delay is not None: config["DELAY"] = args.delay
    
    return config

def find_recent_notes(input_folder, exclude_folders, start_date, end_date):
    """Find notes modified between two dates."""
    md_files = []
    
    logger.info(f"Searching for files modified between {start_date.date()} and {end_date.date()}")
    
    for root, _, files in os.walk(input_folder):
        # Skip excluded folders
        if any(root.startswith(exclude) for exclude in exclude_folders):
            continue
            
        for file in files:
            if not file.endswith('.md'):
                continue
                
            file_path = os.path.join(root, file)
            
            # Check file modification time
            try:
                # Get file timestamps
                file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                
                # Special handling for macOS creation time if needed
                if sys.platform == 'darwin':
                    try:
                        import subprocess
                        result = subprocess.run(['stat', '-f', '%B', file_path], 
                                               capture_output=True, text=True, check=True)
                        file_ctime = datetime.fromtimestamp(float(result.stdout.strip()))
                    except:
                        file_ctime = datetime.fromtimestamp(os.path.getctime(file_path))
                else:
                    file_ctime = datetime.fromtimestamp(os.path.getctime(file_path))
                
                # Check if timestamps are in range
                if ((start_date <= file_mtime <= end_date) or 
                    (start_date <= file_ctime <= end_date)):
                    md_files.append(file_path)
                    continue
                
                # Check frontmatter dates
                frontmatter = get_frontmatter(file_path)
                for date_field in ['created', 'date', 'creation_date', 'createdAt']:
                    if date_field in frontmatter and frontmatter[date_field]:
                        try:
                            fm_date = parser.parse(str(frontmatter[date_field]))
                            if start_date.date() <= fm_date.date() <= end_date.date():
                                md_files.append(file_path)
                                break
                        except:
                            pass
                            
            except Exception as e:
                logger.debug(f"Error checking file dates for {file}: {e}")
    
    if md_files:
        logger.info(f"Found {len(md_files)} files modified in the target period")
        for file_path in md_files:
            logger.info(f"  - {os.path.basename(file_path)}")
    else:
        logger.warning("No files found matching the date criteria")
        
    return md_files

def get_frontmatter(file_path):
    """Extract frontmatter from a markdown file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Parse frontmatter if present
        frontmatter_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
        if frontmatter_match:
            try:
                return yaml.safe_load(frontmatter_match.group(1)) or {}
            except:
                pass
    except:
        pass
        
    return {}

def clean_content(content):
    """Clean note content by removing Obsidian-specific syntax."""
    # Remove dataview blocks
    content = re.sub(r'```dataview(?:js)?\n.*?```', '', content, re.DOTALL)
    
    # Remove inline Obsidian templating code
    content = re.sub(r'<%.*?%>', '', content, re.DOTALL)
    content = re.sub(r'<<.*?>>', '', content, re.DOTALL)
    content = re.sub(r'\{\{.*?\}\}', '', content, re.DOTALL)
    
    return content

def parse_date_phrase(phrase, base_date):
    """Parse natural language date into a datetime.date object."""
    if not phrase or phrase == "null":
        return None
        
    try:
        import dateparser
        import calendar
        
        logger.debug(f"Parsing date phrase: '{phrase}' relative to {base_date.strftime('%Y-%m-%d')}")
        
        # Dictionary of special case handlers
        handlers = {
            # Extract day from "on the X" or "the Xth"
            r'(?:on|the)\s+(?:the\s+)?(\d+)(?:st|nd|rd|th)?': lambda m: get_future_day(base_date, int(m.group(1))),
            
            # End of month
            r'end\s+of\s+(?:the\s+)?month': lambda _: get_month_end(base_date),
            
            # Beginning of next month
            r'beginning\s+of\s+(?:the\s+)?next\s+month': lambda _: get_next_month_start(base_date),
            
            # Next weekday
            r'next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)': 
                lambda m: get_next_weekday(base_date, m.group(1), True),
            
            # This weekday
            r'this\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)': 
                lambda m: get_next_weekday(base_date, m.group(1), False),
            
            # Tomorrow
            r'\btomorrow\b': lambda _: (base_date + timedelta(days=1)).date(),
            
            # Today
            r'\btoday\b': lambda _: base_date.date(),
            
            # X days from now
            r'(\d+)\s+days?\s+from\s+now': lambda m: (base_date + timedelta(days=int(m.group(1)))).date(),
            
            # Next week
            r'next\s+week': lambda _: (base_date + timedelta(days=7)).date(),
            
            # In X days
            r'in\s+(\d+)\s+days?': lambda m: (base_date + timedelta(days=int(m.group(1)))).date(),
        }
        
        # Try each special case handler
        for pattern, handler in handlers.items():
            match = re.search(pattern, phrase, re.IGNORECASE)
            if match:
                return handler(match)
        
        # Use dateparser as fallback
        parsed_date = dateparser.parse(
            phrase,
            settings={
                'RELATIVE_BASE': base_date,
                'PREFER_DATES_FROM': 'future'
            }
        )
        
        if parsed_date:
            return parsed_date.date()
            
        logger.warning(f"Could not parse date phrase: '{phrase}'")
        return None
            
    except Exception as e:
        logger.error(f"Error parsing date phrase '{phrase}': {e}")
        return None

# Helper functions for date parsing
def get_future_day(base_date, day):
    """Get the date with the specified day, in current or next month."""
    result = datetime(base_date.year, base_date.month, day)
    if result.date() < base_date.date():
        if base_date.month == 12:
            result = datetime(base_date.year + 1, 1, day)
        else:
            result = datetime(base_date.year, base_date.month + 1, day)
    return result.date()

def get_month_end(date):
    """Get the last day of the month."""
    import calendar
    last_day = calendar.monthrange(date.year, date.month)[1]
    return datetime(date.year, date.month, last_day).date()

def get_next_month_start(date):
    """Get the first day of next month."""
    if date.month == 12:
        return datetime(date.year + 1, 1, 1).date()
    return datetime(date.year, date.month + 1, 1).date()

def get_next_weekday(base_date, weekday_name, next_week):
    """
    Get the date of the next occurrence of a weekday.
    If next_week is True, get the occurrence after the next one.
    """
    weekday_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3, 
                   'friday': 4, 'saturday': 5, 'sunday': 6}
                   
    target_weekday = weekday_map[weekday_name.lower()]
    current_weekday = base_date.weekday()
    
    # Days until the next occurrence
    days_ahead = (target_weekday - current_weekday) % 7
    
    # Handle current day
    if days_ahead == 0 and not next_week:
        return base_date.date()
    
    # Calculate next occurrence
    next_occurrence = base_date + timedelta(days=days_ahead)
    
    # Add a week if "next" is specified
    if next_week:
        next_occurrence += timedelta(days=7)
        
    return next_occurrence.date()

def call_llm(content, system_prompt, config):
    """Extract tasks using the configured LLM provider."""
    provider = config.get("LLM_PROVIDER", "ollama")
    
    logger.debug(f"Sending content ({len(content)} chars) to {provider}")
    
    if provider.lower() == "openai":
        return call_openai(content, system_prompt, config)
    else:
        return call_ollama(content, system_prompt, config)

def call_ollama(content, system_prompt, config):
    """Call Ollama API."""
    model = config.get("OLLAMA_MODEL", "gemma:7b")
    server = config.get("OLLAMA_SERVER_ADDRESS", "http://localhost:11434")
    context_window = config.get("OLLAMA_CONTEXT_WINDOW", 32000)
    
    logger.info(f"Calling Ollama API with model: {model}")
    
    payload = {
        "model": model,
        "prompt": system_prompt + "\n\n" + content,
        "stream": False,
        "options": {
            "num_ctx": context_window,
            "temperature": 0.1
        }
    }
    
    try:
        response = requests.post(
            f"{server}/api/generate",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'response' in result:
                return result['response'].strip()
        
        logger.error(f"API call failed: {response.status_code} - {response.text}")
        return ""
    except Exception as e:
        logger.error(f"Error calling Ollama API: {e}")
        return ""

def call_openai(content, system_prompt, config):
    """Call OpenAI API."""
    try:
        import openai
    except ImportError:
        logger.error("OpenAI package not installed. Run: pip install openai")
        return ""
    
    api_key = config.get("OPENAI_API_KEY")
    model = config.get("OPENAI_MODEL", "gpt-3.5-turbo")
    max_tokens = config.get("OPENAI_MAX_TOKENS", 4000)
    
    if not api_key:
        logger.error("No OpenAI API key provided")
        return ""
    
    logger.info(f"Calling OpenAI API with model: {model}")
    
    try:
        client = openai.OpenAI(api_key=api_key)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content}
        ]
        
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1
        )
        
        if response.choices and len(response.choices) > 0:
            return response.choices[0].message.content.strip()
            
        logger.error("No response content from OpenAI API")
        return ""
    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}")
        return ""

def parse_llm_response(response_text):
    """Parse JSON from LLM response."""
    if not response_text:
        return []
        
    try:
        # Clean up code blocks
        cleaned = response_text.strip()
        if cleaned.startswith('```'):
            start_idx = cleaned.find('\n') + 1
            end_idx = cleaned.rfind('```')
            if end_idx > start_idx:
                cleaned = cleaned[start_idx:end_idx].strip()
            else:
                cleaned = cleaned[start_idx:].strip()
        
        # Parse JSON
        tasks = json.loads(cleaned)
        if not isinstance(tasks, list):
            logger.warning(f"Invalid JSON format (not an array)")
            return []
            
        return tasks
    except json.JSONDecodeError:
        # Try extracting just the JSON array
        try:
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start >= 0 and end > start:
                return json.loads(response_text[start:end])
        except:
            pass
            
        logger.error(f"Failed to parse JSON response")
        logger.debug(f"Raw response: {response_text}")
        return []

def connect_to_caldav(config):
    """Connect to CalDAV server and get the todo list."""
    # Get connection settings
    url = config.get("CALDAV_URL") or config.get("NEXTCLOUD_TODO_URL")
    username = config.get("CALDAV_USERNAME") or config.get("NEXTCLOUD_USERNAME")
    password = config.get("CALDAV_PASSWORD") or config.get("NEXTCLOUD_PASSWORD")
    
    if not url or not username or not password:
        logger.error("Missing CalDAV connection details in configuration")
        return None, None
    
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
        
    logger.info(f"Connecting to CalDAV server: {url}")
    
    try:
        # Try with SSL verification
        client = caldav.DAVClient(url=url, username=username, password=password)
        try:
            client.principal()
            logger.info("Connected to CalDAV server successfully")
        except:
            # Try without SSL verification
            client = caldav.DAVClient(url=url, username=username, password=password, ssl_verify_cert=False)
            client.principal()
            logger.info("Connected to CalDAV server with SSL verification disabled")
        
        # Get the todo list
        calendar_name = config.get("CALDAV_TODO_LIST", "tasks")
        calendars = client.principal().calendars()
        
        # Find the specified calendar
        for calendar in calendars:
            if calendar.name.lower() == calendar_name.lower():
                logger.info(f"Using todo list: {calendar.name}")
                return client, calendar
        
        # Use first calendar as fallback
        if calendars:
            logger.warning(f"Todo list '{calendar_name}' not found, using '{calendars[0].name}'")
            return client, calendars[0]
            
        logger.error("No calendars found on the CalDAV server")
        return client, None
    except Exception as e:
        logger.error(f"Error connecting to CalDAV server: {e}")
        return None, None

def task_exists(todo_list, task_text, existing_tasks=None):
    """Check if a task already exists in the todo list."""
    if existing_tasks is None:
        try:
            existing_tasks = todo_list.todos()
        except Exception as e:
            logger.error(f"Error getting existing tasks: {e}")
            return False
    
    # Normalize the task text
    if isinstance(task_text, dict):
        normalized = task_text.get('task', '').lower().strip()
    else:
        normalized = task_text.lower().strip()
    
    # Generate a hash for comparison
    task_hash = hashlib.md5(normalized.encode()).hexdigest()
    
    for task in existing_tasks:
        try:
            task_data = task.data
            if task_data:
                # Extract summary
                summary_match = re.search(r'SUMMARY:(.*?)(\r?\n)', task_data, re.IGNORECASE)
                if summary_match:
                    existing_summary = summary_match.group(1).strip()
                    existing_summary = existing_summary.replace('\\,', ',').replace('\\;', ';').replace('\\n', '\n')
                    
                    # Compare text or hash
                    if existing_summary.lower().strip() == normalized or task_hash in task_data:
                        return True
        except:
            continue
    
    return False

def add_task_to_caldav(todo_list, task_data, file_mod_date, existing_tasks=None, check_duplicates=True):
    """Add a task to the CalDAV todo list."""
    task_text = task_data.get('task', '')
    if not task_text.strip():
        return False
    
    date_phrase = task_data.get('date_phrase')
    priority = task_data.get('priority', 'medium')
    
    # Check for duplicate
    if check_duplicates and task_exists(todo_list, task_text, existing_tasks):
        logger.info(f"Task already exists, skipping: {task_text}")
        return False
    
    try:
        # Parse the date phrase
        due_date = None
        if date_phrase and date_phrase != "null":
            due_date = parse_date_phrase(date_phrase, file_mod_date)
            if due_date:
                logger.debug(f"Parsed '{date_phrase}' to {due_date.isoformat()}")
        
        # Create task
        uid = str(uuid.uuid4())
        hash_val = hashlib.md5(task_text.lower().strip().encode()).hexdigest()
        summary = task_text.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')
        timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        
        # Convert priority to iCal format
        ical_priority = {"high": 1, "medium": 5, "low": 9}.get(priority.lower(), 5)
        
        # Build iCalendar format
        vcal_parts = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Extract Tasks//EN",
            "BEGIN:VTODO",
            f"UID:{uid}",
            f"DTSTAMP:{timestamp}",
            f"SUMMARY:{summary}",
            "STATUS:NEEDS-ACTION",
            f"PRIORITY:{ical_priority}",
            f"X-TASK-HASH:{hash_val}"
        ]
        
        # Add due date if available
        if due_date:
            vcal_parts.append(f"DUE;VALUE=DATE:{due_date.strftime('%Y%m%d')}")
            if date_phrase:
                # Escape special characters - cannot use backslashes directly in f-strings
                escaped_phrase = date_phrase.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
                vcal_parts.append(f"X-DATE-PHRASE:{escaped_phrase}")
        
        vcal_parts.extend(["END:VTODO", "END:VCALENDAR"])
        vcal = "\r\n".join(vcal_parts) + "\r\n"
        
        # Add to CalDAV
        todo_list.add_todo(vcal)
        
        # Log with details
        log_msg = f"Added task: {task_text}"
        if date_phrase:
            log_msg += f" (Phrase: '{date_phrase}')"
        if due_date:
            log_msg += f" [Due: {due_date.isoformat()}]"
        
        logger.info(log_msg)
        return True
    except Exception as e:
        logger.error(f"Error adding task to CalDAV: {e}")
        return False

def process_notes(note_files, task_prompt, config):
    """Process notes and extract tasks."""
    # Connect to CalDAV
    client, todo_list = connect_to_caldav(config)
    if not client or not todo_list:
        return 0, 0, []
    
    # Get existing tasks if needed
    existing_tasks = None
    if config.get("CHECK_EXISTING_TASKS", True):
        try:
            existing_tasks = todo_list.todos()
            logger.info(f"Found {len(existing_tasks)} existing tasks in the todo list")
        except Exception as e:
            logger.error(f"Error getting existing tasks: {e}")
            config["CHECK_EXISTING_TASKS"] = False
    
    tasks_added = 0
    files_with_errors = 0
    all_tasks = []
    
    # Process each file
    for index, file_path in enumerate(note_files):
        filename = os.path.basename(file_path)
        progress = f"[{index+1}/{len(note_files)}]"
        logger.info(f"{progress} Processing file: {filename}")
        
        try:
            # Read and prepare content
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Get note title from filename or frontmatter
            note_title = os.path.basename(file_path).replace('.md', '')
            frontmatter, content_without_frontmatter = extract_frontmatter(content)
            if 'title' in frontmatter:
                note_title = frontmatter['title']
            
            # Clean content
            clean_note_content = clean_content(content_without_frontmatter)
            file_mod_date = datetime.fromtimestamp(os.path.getmtime(file_path))
            
            # Prepare content for LLM
            content_for_llm = f"# {note_title}\n\n{clean_note_content}"
            
            # Extract tasks
            logger.info(f"{progress} Sending content to {config.get('LLM_PROVIDER')} for task extraction")
            llm_response = call_llm(content_for_llm, task_prompt, config)
            
            if not llm_response:
                logger.info(f"{progress} No response from LLM for {filename}")
                continue
            
            # Parse tasks
            tasks = parse_llm_response(llm_response)
            
            if not tasks:
                logger.info(f"{progress} No tasks extracted from {filename}")
                continue
                
            logger.info(f"{progress} Extracted {len(tasks)} tasks from {filename}")
            
            # Add tasks to CalDAV
            for task_data in tasks:
                if not isinstance(task_data, dict) or 'task' not in task_data:
                    logger.warning(f"Invalid task format: {task_data}")
                    continue
                    
                if add_task_to_caldav(todo_list, task_data, file_mod_date, existing_tasks, config.get("CHECK_EXISTING_TASKS", True)):
                    tasks_added += 1
                    task_info = {
                        'text': task_data.get('task', ''),
                        'date_phrase': task_data.get('date_phrase'),
                        'priority': task_data.get('priority', 'medium')
                    }
                    all_tasks.append(task_info)
            
            # Add delay between files if using OpenAI
            if config.get("LLM_PROVIDER") == "openai" and index < len(note_files) - 1 and config.get("DELAY", 0) > 0:
                time.sleep(config.get("DELAY"))
                
        except Exception as e:
            logger.error(f"{progress} Error processing {filename}: {e}")
            files_with_errors += 1
    
    return tasks_added, files_with_errors, all_tasks

def extract_frontmatter(content):
    """Extract frontmatter and content separately."""
    frontmatter_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
    
    if frontmatter_match:
        frontmatter_content = frontmatter_match.group(1)
        rest_content = content[frontmatter_match.end():]
        try:
            frontmatter = yaml.safe_load(frontmatter_content) or {}
            return frontmatter, rest_content
        except yaml.YAMLError:
            pass
    
    return {}, content

def main():
    """Main function."""
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Extract tasks from notes and add to CalDAV todo list')
    parser.add_argument('--date', help='Override date to check (YYYY-MM-DD format)')
    parser.add_argument('--debug', action='store_true', help='Enable detailed debug logging')
    parser.add_argument('--input', help='Override input folder')
    parser.add_argument('--exclude', action='append', help='Override exclude folders')
    parser.add_argument('--model', help='Override model name')
    parser.add_argument('--server', help='Override Ollama server address')
    parser.add_argument('--provider', choices=['ollama', 'openai'], help='Override LLM provider')
    parser.add_argument('--api-key', help='Override OpenAI API key')
    parser.add_argument('--caldav-url', help='Override CalDAV URL')
    parser.add_argument('--caldav-user', help='Override CalDAV username')
    parser.add_argument('--caldav-pass', help='Override CalDAV password')
    parser.add_argument('--todo-list', help='Override todo list name')
    parser.add_argument('--no-duplicate-check', action='store_true', help='Disable checking for duplicate tasks')
    parser.add_argument('--delay', type=float, default=0, help='Delay between processing files (seconds)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        default='INFO', help='Set logging level')
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.debug else getattr(logging, args.log_level)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(script_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, f"extract_tasks_{datetime.now().strftime('%Y-%m-%d')}.log")
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)]
    )
    logger = logging.getLogger(__name__)
    logging.getLogger('caldav').setLevel(logging.WARNING)  # Reduce caldav noise
    
    logger.info(f"Logging to file: {log_file}")
    logger.info("=== Starting extract_tasks.py ===")
    
    # Load configuration
    config = load_config(args)
    
    # Log key configuration settings
    logger.info(f"Using configuration:")
    logger.info(f"  INPUT_FOLDER: {config.get('INPUT_FOLDER')}")
    logger.info(f"  EXCLUDE_FOLDERS: {config.get('EXCLUDE_FOLDERS')}")
    logger.info(f"  LLM_PROVIDER: {config.get('LLM_PROVIDER')}")
    logger.info(f"  CALDAV_TODO_LIST: {config.get('CALDAV_TODO_LIST')}")
    
    if config.get('LLM_PROVIDER') == 'ollama':
        logger.info(f"  OLLAMA_MODEL: {config.get('OLLAMA_MODEL')}")
        logger.info(f"  OLLAMA_SERVER_ADDRESS: {config.get('OLLAMA_SERVER_ADDRESS')}")
    elif config.get('LLM_PROVIDER') == 'openai':
        logger.info(f"  OPENAI_MODEL: {config.get('OPENAI_MODEL')}")
        if not config.get('OPENAI_API_KEY'):
            logger.warning("  OPENAI_API_KEY: Not set!")
            
    # Load prompt file
    prompt_file_path = os.path.join(script_dir, "extract_tasks.md")
    if not os.path.exists(prompt_file_path):
        logger.error(f"ERROR: Prompt file not found: {prompt_file_path}")
        logger.error(f"Please create an extract_tasks.md file in the script directory.")
        return
        
    with open(prompt_file_path, 'r') as f:
        task_prompt = f.read()
    
    # Get time boundaries
    if args.date:
        try:
            target_date = parser.parse(args.date).date()
            logger.info(f"Using override date: {target_date}")
        except:
            logger.error(f"Invalid date format: {args.date}")
            target_date = (datetime.now() - timedelta(days=1)).date()
    else:
        target_date = (datetime.now() - timedelta(days=1)).date()
        
    start_boundary = datetime.combine(target_date, datetime.min.time())
    end_boundary = datetime.combine(target_date, datetime.max.time())
    
    logger.info(f"Target date: {target_date}")
    logger.info(f"Time range: {start_boundary} to {end_boundary}")
    
    # Find modified files
    md_files = find_recent_notes(
        config.get("INPUT_FOLDER"),
        config.get("EXCLUDE_FOLDERS", []),
        start_boundary,
        end_boundary
    )
    
    if not md_files:
        logger.info("No files found matching the date criteria. Exiting.")
        return
    
    # Process notes and extract tasks
    tasks_added, files_with_errors, all_tasks = process_notes(md_files, task_prompt, config)
    
    # Log summary
    logger.info("=== Processing Summary ===")
    logger.info(f"Total files processed: {len(md_files)}")
    logger.info(f"Tasks added to CalDAV: {tasks_added}")
    logger.info(f"Files with errors: {files_with_errors}")
    
    if tasks_added > 0:
        logger.info(f"Tasks added:")
        for task_info in all_tasks:
            task_desc = f"  - {task_info['text']}"
            if task_info['date_phrase']:
                task_desc += f" (Phrase: '{task_info['date_phrase']}')"
            task_desc += f" [Priority: {task_info['priority']}]"
            logger.info(task_desc)
    
    logger.info("=== Script completed successfully ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        sys.exit(1)