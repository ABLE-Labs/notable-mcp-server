# NOTABLE MCP Server

AI agents can control the [NOTABLE liquid handler](https://ablelabs.co.kr) through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

```
AI Agent (Claude, GPT, Cursor, ...)
    ↓ MCP Protocol
NOTABLE MCP Server  ← this project
    ↓ REST API
NOTABLE Neon (robot controller)
    ↓
Robot
```

## Quick Start (3 steps)

### 1. Install

```bash
git clone https://github.com/ablelabsinc/notable-mcp-server.git
cd notable-mcp-server
pip install .
```

### 2. Connect to your AI client

**Claude Desktop** — edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "notable-mcp": {
      "command": "notable-mcp",
      "args": ["--simulate"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add notable-mcp -- notable-mcp --simulate
```

**Cursor / VS Code** — add to `.cursor/mcp.json` or `.vscode/mcp.json`:

```json
{
  "servers": {
    "notable-mcp": {
      "command": "notable-mcp",
      "args": ["--simulate"]
    }
  }
}
```

### Update

```bash
cd notable-mcp-server
git pull
pip install .
```

### 3. Talk to your AI

> "Show me available pipettes and labware"

> "Configure deck: plate on slot 1, tip box on slot 4, trash on slot 12"

> "Transfer 500 uL from Deck1 A1 to Deck2 B3"

That's it. The AI discovers the tools automatically.

## Simulation Mode vs Real Robot

| Mode | Command | Robot needed? |
|------|---------|--------------|
| Simulation | `notable-mcp --simulate` | No |
| Real robot | `notable-mcp --host localhost --port 7777` | Yes (notable-neon running) |

Simulation mode responds with realistic data without moving hardware.
Use it for development, testing, and demos.

## Available Tools (23)

### Status & Config

| Tool | Description |
|------|-------------|
| `get_robot_status` | Check robot state, pipette config, deck config, module status |
| `get_available_resources` | List available pipettes, labware, modules, adapters |
| `configure_pipette` | Set pipette types on left/right mount |
| `configure_deck` | Assign labware and modules to deck slots (1-12) |

### Robot Control

| Tool | Description |
|------|-------------|
| `initialize_robot` | Home axes, initialize modules. **Must call before any action.** |
| `emergency_stop` | Immediately halt all motion. Requires re-initialization after. |
| `pause_robot` | Pause current operation |
| `resume_robot` | Resume after pause |

### Liquid Handling

| Tool | Description |
|------|-------------|
| `transfer_liquid` | Transfer liquid between wells (auto tip pick-up/drop). Supports custom flow rate and Z height. |
| `distribute_liquid` | Distribute from one source to multiple destinations (new tip per transfer) |
| `mix_liquid` | Mix liquid in a well by repeated aspirate/dispense cycles |

### Modules

| Tool | Description |
|------|-------------|
| `run_thermocycler` | Run ODTC thermocycler method |
| `shake_plate` | Shake plate on orbital shaker |
| `control_odtc_door` | Open/close thermocycler door |

### Tip Management

| Tool | Description |
|------|-------------|
| `reset_tip_tracking` | Reset tip count for a deck slot (e.g. after replacing a tip rack) |

### Diagnostics

| Tool | Description |
|------|-------------|
| `diagnose_error` | Analyze an error message and suggest fixes |
| `get_error_log` | Retrieve recent error history |

### Protocols

| Tool | Description |
|------|-------------|
| `save_protocol` | Save a sequence of steps as a reusable protocol |
| `list_protocols` | List all saved protocols |
| `get_protocol` | Get details of a saved protocol |
| `run_protocol` | Run a saved protocol (supports `dry_run` mode) |
| `delete_protocol` | Delete a saved protocol |
| `get_protocol_run_history` | View past run results for a protocol |

## Example Workflow

A typical experiment in natural language:

```
1. "What pipettes and labware are available?"
2. "Mount 1ch 1000uL pipette on left, 8ch 20uL on right"
3. "Put a 96-well plate on deck 1 and 2, tip box on deck 4, trash on deck 12"
4. "Initialize the robot"
5. "Transfer 500 uL from deck 1 well A1 to deck 2 well B3"
6. "Distribute 100 uL from deck 1 A1 to deck 2 wells A1 through A6"
7. "Mix well B3 on deck 2 with 200 uL, 5 cycles"
8. "Emergency stop!"
```

## Safety

The server enforces safety at every level:

**Stateful guards** (server tracks robot state across calls):
- Robot must be initialized before any movement command
- Pipette must be configured before liquid handling
- Deck slot must have labware assigned before access
- Concurrent commands are rejected while robot is busy
- Emergency stop resets state, requiring re-initialization

**Parameter validation:**
- Volume range per pipette type (e.g. 1-1000 uL for 1ch_1000ul, 0.5-20 uL for 8ch_20ul)
- Deck slot range (1-12)
- Well format (A1-H12)
- Pipette number (1=left, 2=right)
- Shaker RPM range (100-3000)
- Tip rack overflow detection (max 96 tips)

Invalid parameters return a clear error message instead of executing.

## Project Structure

```
src/notable_mcp/
├── server.py       # MCP server + CLI entry point (23 tools)
├── client.py       # REST API client (httpx, no SDK needed)
├── state.py        # Server state tracking (init, config, lock)
├── safety.py       # Parameter validation guardrails
├── simulator.py    # Simulation mode (no robot needed)
└── tools/
    ├── status.py   # get_robot_status, get_available_resources
    ├── config.py   # configure_pipette, configure_deck
    ├── control.py  # initialize_robot, emergency_stop, pause, resume
    ├── liquid.py   # transfer_liquid, distribute_liquid, mix_liquid
    └── modules.py  # run_thermocycler, shake_plate, control_odtc_door
```

## Requirements

- Python 3.10+
- No hardware or proprietary SDK required for simulation mode

## License

Apache 2.0 — see [LICENSE](LICENSE)

## About

Built by [ABLE Labs](https://ablelabs.co.kr), makers of the NOTABLE liquid handler.
