# Latarnia - Self-Hosted Platform for Exposing Apps

Latarnia is a self-hosted platform for exposing apps. Drop in an app folder with a `latarnia.json` manifest and Latarnia provisions what it declares (Postgres database, Redis Streams, secrets, ports), runs it under systemd, and publishes its web UI and MCP tools behind a single authenticated ingress (Caddy + TOTP / JWT). It runs on a Raspberry Pi 5 (8GB RAM).

## Overview

Latarnia provides centralized provisioning, lifecycle, access control and exposure for apps while maintaining complete independence between them. It supports two types of applications:

- **Service Apps**: Long-running background services with REST APIs
- **Streamlit Apps**: On-demand interactive UIs with TTL management

## Architecture

The system follows a modular architecture with these core components:

- **Main Application**: FastAPI backend with web dashboard
- **App Manager**: Auto-discovery and registry management  
- **Service Manager**: systemd integration for background services
- **UI Manager**: On-demand Streamlit app management with TTL
- **Redis Message Bus**: Inter-app communication
- **UI Proxy**: Service app REST API rendering in modals

## Tech Stack

- **Backend**: FastAPI + Python 3.9+
- **Frontend**: Bootstrap 5 (responsive web interface)
- **Message Bus**: Redis
- **Process Management**: systemd
- **Target Platform**: Raspberry Pi 5 with 8GB RAM (Raspberry Pi OS)

## Project Structure

```
/opt/latarnia/
├── config/
│   └── config.json              # Main configuration file
├── src/latarnia/
│   ├── core/                    # Core infrastructure
│   │   ├── config.py           # Configuration management
│   │   └── redis_client.py     # Redis message bus client
│   ├── managers/               # App and service managers
│   ├── utils/                  # Utilities (logging, monitoring)
│   │   ├── logging.py          # Shared logging utilities
│   │   └── system_monitor.py   # System monitoring
│   └── main.py                 # FastAPI application
├── apps/                       # Deployed applications (gitignored, populated from examples/)
├── examples/                   # Source of truth for example apps (committed)
│   ├── example_full_app/       # Full-featured demo exercising all platform features
│   └── example_companion/      # Minimal companion app (dependency target)
├── data/                       # Shared data directory (per-app subdirs)
├── logs/                       # Shared logs directory (per-app subdirs)
├── docs/                       # Documentation
├── tests/                      # Unit and integration tests
└── migrations/                 # Database migration scripts
```

## Features

### Core Infrastructure ✅ COMPLETED
- Configuration management with JSON config and environment overrides
- Redis message bus integration with pub/sub capabilities
- System monitoring (CPU, memory, disk, temperature, processes)
- FastAPI application with health and metrics endpoints
- Shared logging utilities with per-app organization
- Comprehensive unit test suite

### Planned Features
- **App Management**: Auto-discovery of apps with manifest parsing
- **Service Management**: systemd integration for lifecycle control
- **Web Dashboard**: Bootstrap 5 responsive interface
- **UI Integration**: Modal-based app UIs and Streamlit integration
- **System Integration**: Production deployment and monitoring

## Quick Start

### Prerequisites
- Raspberry Pi 5 with 8GB RAM
- Raspberry Pi OS (Debian-based)
- Python 3.9+
- Redis server

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Latarnia
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure the system:
```bash
# Edit config/config.json as needed
# Default configuration works for development
```

4. Run the application:
```bash
cd src
python -m latarnia.main
```

5. Access the dashboard:
```
http://localhost:8000
```

## Configuration

The main configuration is stored in `config/config.json`:

```json
{
  "redis": {
    "host": "localhost",
    "port": 6379,
    "db": 0
  },
  "system": {
    "main_port": 8000,
    "host": "0.0.0.0"
  },
  "process_manager": {
    "data_dir": "/opt/latarnia/data",
    "logs_dir": "/opt/latarnia/logs",
    "port_range": {
      "start": 8100,
      "end": 8199
    }
  }
}
```

Configuration can be overridden using environment variables with the `LATARNIA_` prefix.

## API Endpoints

### Health and Status
- `GET /` - Root endpoint with version info
- `GET /health` - Application health check
- `GET /api/system/metrics` - System hardware metrics
- `GET /api/system/redis` - Redis connection status
- `GET /api/config` - Current configuration (sanitized)

### App Management (Planned)
- `GET /api/apps` - List all discovered apps
- `POST /api/apps/{app_id}/start` - Start an app
- `POST /api/apps/{app_id}/stop` - Stop an app
- `GET /api/apps/{app_id}/status` - Get app status

## Development

### Running Tests
```bash
# Run all unit tests
python -m pytest tests/unit/ -v

# Run with coverage
python -m pytest tests/unit/ --cov=latarnia --cov-report=html
```

### Project Planning
This project follows the ShapeUp methodology. See `Projects/P-001.md` for detailed scope and task tracking.

### Contributing
1. Follow the existing code structure and patterns
2. Add unit tests for new functionality
3. Update documentation for significant changes
4. Use conventional commit messages

## App Development

Apps are discovered automatically from the `apps/` directory. Each app must include a `latarnia.json` manifest file. See `docs/System/app-specification.md` for detailed requirements.

> **Important:** The `apps/` directory is gitignored. Example apps live in `examples/` (committed) and are copied to `apps/` during deployment. All changes to example apps must be made in `examples/`, never in `apps/`.

### Service App Example
```json
{
  "name": "camera-detection",
  "type": "service",
  "description": "AI-powered camera detection service",
  "version": "1.0.0",
  "main": "main.py",
  "requirements": "requirements.txt",
  "endpoints": {
    "health": "/health",
    "ui": "/ui"
  }
}
```

### Streamlit App Example
```json
{
  "name": "data-visualizer",
  "type": "streamlit",
  "description": "Interactive data visualization dashboard",
  "version": "1.0.0",
  "main": "app.py",
  "requirements": "requirements.txt"
}
```

## Monitoring

Latarnia provides comprehensive system monitoring:

- **Hardware Metrics**: CPU usage, memory, disk space, temperature
- **Process Monitoring**: App status, resource usage, health checks
- **Redis Monitoring**: Connection status, message bus health
- **Log Management**: Centralized logging with per-app organization

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and questions:
1. Check the documentation in `docs/`
2. Review existing issues in the project tracker
3. Create a new issue with detailed information

## Roadmap

See `Projects/P-001.md` for the complete development roadmap organized by scopes:

1. ✅ **Core Infrastructure** - Foundation components
2. 🔄 **App Management System** - Discovery and registry
3. 📋 **Service Management** - systemd integration
4. 📋 **Web Dashboard** - Bootstrap 5 interface
5. 📋 **UI Integration** - Modal-based app UIs
6. 📋 **Sample Applications** - Integration testing
7. 📋 **System Integration** - Production deployment
