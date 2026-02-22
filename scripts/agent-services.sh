#!/bin/bash

# 🛠️ Agent Services CLI
# A generic wrapper for Docker Compose to help Agents manage infrastructure.

COMPOSE_FILE="docker-compose.yml"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

function show_help {
    echo "Usage: ./agent-services.sh [command] [service]"
    echo ""
    echo "Commands:"
    echo "  up [service]      Start services (detached)"
    echo "  down              Stop all services"
    echo "  logs [service]    View logs"
    echo "  status            Check container status"
    echo "  health            Run health checks"
}

if [ ! -f "$COMPOSE_FILE" ]; then
    echo -e "${RED}Error: $COMPOSE_FILE not found in current directory.${NC}"
    exit 1
fi

COMMAND=$1
SERVICE=$2

case $COMMAND in
    up)
        echo -e "${GREEN}Starting services...${NC}"
        docker compose -f $COMPOSE_FILE up -d $SERVICE
        ;;
    down)
        echo -e "${GREEN}Stopping services...${NC}"
        docker compose -f $COMPOSE_FILE down
        ;;
    logs)
        docker compose -f $COMPOSE_FILE logs -f $SERVICE
        ;;
    status)
        docker compose -f $COMPOSE_FILE ps
        ;;
    health)
        # Generic health check (checks if containers are running)
        echo -e "${GREEN}Checking container status...${NC}"
        docker compose -f $COMPOSE_FILE ps --format "table {{.Name}}\t{{.State}}\t{{.Status}}"
        ;;    
    *)
        show_help
        ;;esac
