# Obsidian Auto Tasks

Extract actionable tasks from your Obsidian notes and automatically add them to your todo list/calendar via CalDAV.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Overview

Obsidian Auto Tasks is a Python script that automatically extracts actionable tasks from your Obsidian vault notes and adds them to any CalDAV-compatible task list (like Nextcloud, Apple Reminders, etc.). It uses a local LLM via Ollama (default) or OpenAI to intelligently identify genuine tasks from your notes while filtering out philosophical musings or non-actionable content.

### Key Features

- 🤖 **Smart Task Extraction** - Uses an LLM to distinguish between genuine tasks and non-actionable notes/musings
- 📅 **Date Recognition** - Parses natural language date references (e.g., "tomorrow", "next Friday", "in 3 days")
- 🔄 **CalDAV Integration** - Works with any CalDAV-compatible service (Nextcloud, Apple Reminders, etc.)
- 🔄 **Duplicate Prevention** - Avoids adding the same task twice
- ⚙️ **Customizable** - Extensive configuration options through YAML file or command line
- 🗓️ **Priority Support** - Assigns high/medium/low priorities to tasks based on urgency

## Requirements

- Python 3.7+
- An Obsidian vault with markdown notes
- Access to a CalDAV-compatible task service
- Either:
  - [Ollama](https://ollama.com/) running locally (default)
  - Or an OpenAI API key

## Installation

1. Clone this repository:

```bash
git clone https://github.com/undergroundpost/obsidian-auto-tasks.git
cd obsidian-auto-tasks
```

2. Install required dependencies:

```bash
pip install -r requirements.txt
```

3. Configure settings (see Configuration section below)

## Configuration

Copy `config.yaml` to one of these locations:
- Same directory as the script
- `~/.config/extract_tasks/config.yaml`
- `/etc/extract_tasks/config.yaml`

### Configuration Options

```yaml
# Folder settings
INPUT_FOLDER: "/path/to/your/obsidian/vault"      # Main notes folder

# Folders to exclude (list format - add as many as needed)
EXCLUDE_FOLDERS:
  - "/path/to/your/obsidian/vault/AI"
  - "/path/to/your/obsidian/vault/Extras"

# LLM Provider settings
LLM_PROVIDER: "ollama"                            # Options: "ollama" or "openai"

# Ollama settings (used when LLM_PROVIDER is "ollama")
OLLAMA_MODEL: "gemma3:12b"                        # Model to use
OLLAMA_SERVER_ADDRESS: "http://localhost:11434"   # Ollama server address
OLLAMA_CONTEXT_WINDOW: 32000                      # Context window size

# OpenAI settings (used when LLM_PROVIDER is "openai")
OPENAI_API_KEY: ""                                # Your OpenAI API key
OPENAI_MODEL: "gpt-3.5-turbo"                     # OpenAI model to use
OPENAI_MAX_TOKENS: 4000                           # Maximum tokens for responses

# CalDAV server settings
# You can use either the generic CalDAV settings OR the Nextcloud-specific settings

# Generic CalDAV settings (for any CalDAV server)
CALDAV_URL: ""                                    # Your CalDAV server URL
CALDAV_USERNAME: ""                               # Your CalDAV username
CALDAV_PASSWORD: ""                               # Your CalDAV password

# Nextcloud-specific settings (leave empty if using generic CalDAV)
NEXTCLOUD_TODO_URL: "https://your-nextcloud.com/remote.php/dav/calendars/username/tasks"
NEXTCLOUD_USERNAME: "username"
NEXTCLOUD_PASSWORD: "password"

# Common CalDAV settings
CALDAV_TODO_LIST: "tasks"                         # Name of your todo list/calendar

# Task settings
CHECK_EXISTING_TASKS: true                        # Check for duplicate tasks before adding
```

## Usage

### Basic Usage

```bash
python extract_tasks.py
```

This will process notes modified yesterday and extract tasks to your configured CalDAV todo list.

### Command Line Options

```
--date DATE           Override date to check (YYYY-MM-DD format)
--debug               Enable detailed debug logging
--input PATH          Override input folder
--exclude PATH        Override exclude folders (can use multiple times)
--model NAME          Override model name
--server URL          Override Ollama server address
--provider {ollama,openai}
                      Override LLM provider
--api-key KEY         Override OpenAI API key
--caldav-url URL      Override CalDAV URL
--caldav-user USER    Override CalDAV username
--caldav-pass PASS    Override CalDAV password
--todo-list NAME      Override todo list name
--no-duplicate-check  Disable checking for duplicate tasks
--delay SECONDS       Delay between processing files (seconds)
--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                      Set logging level
```

### Examples

Process notes from a specific date:
```bash
python extract_tasks.py --date 2025-05-01
```

Use OpenAI instead of Ollama:
```bash
python extract_tasks.py --provider openai --api-key sk-your-openai-key
```

## How It Works

1. **File Discovery** - Finds notes modified within the target date range
2. **Content Preparation** - Cleans Obsidian-specific syntax
3. **Task Extraction** - Sends note content to the LLM with specific instructions on identifying tasks
4. **Date Parsing** - Parses natural language date references
5. **Task Creation** - Adds extracted tasks to your CalDAV todo list with proper dates and priorities

### Task Criteria

The script instructs the LLM to identify tasks that meet specific criteria:

1. **Object Test** - Must include a clear object of the action
2. **Context Test** - Must include specific context or target
3. **Actionability Test** - Must be completable with a specific physical action

Tasks like "Write report" pass, while vague statements like "Improve my position" fail.

## Automating with Cron

You can automatically run this script daily to process your notes:

```bash
# Add to crontab (run 'crontab -e')
0 8 * * * cd /path/to/obsidian-auto-tasks && /usr/bin/python3 extract_tasks.py
```

This will run the script daily at 8 AM.

## Logs

Logs are stored in the `logs` directory with filenames like `extract_tasks_YYYY-MM-DD.log`.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Acknowledgements

- Uses [Ollama](https://ollama.com/) or [OpenAI](https://openai.com/) for task extraction
- [CalDAV](https://en.wikipedia.org/wiki/CalDAV) for task synchronization
- [Obsidian](https://obsidian.md/) for note-taking
