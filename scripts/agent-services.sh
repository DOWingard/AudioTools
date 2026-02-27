#!/bin/bash

# 🛠️ Agent Services CLI
# Docker Compose wrapper + ngrok webhook tunnels for local dev.

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────────────── #

function update_env_var {
    local key="$1"
    local val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
        echo -e "${GREEN}✔ Updated ${key} in ${ENV_FILE}${NC}"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
        echo -e "${GREEN}✔ Appended ${key} to ${ENV_FILE}${NC}"
    fi
}

function show_help {
    echo "Usage: ./agent-services.sh [command] [service]"
    echo ""
    echo "Commands:"
    echo "  up [service]      Start Docker services (detached)"
    echo "  down              Stop all Docker services"
    echo "  logs [service]    View Docker logs"
    echo "  status            Check container status"
    echo "  health            Run health checks"
    echo "  webhooks          Start ngrok tunnel + Stripe listener (foreground)"
}

# ── Webhook Tunnel ────────────────────────────────────────────────── #

function start_webhooks {
    echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  🔌 Starting Webhook Tunnels (ngrok + Stripe CLI)${NC}"
    echo -e "${CYAN}══════════════════════════════════════════════════════${NC}"
    echo ""

    # ── Preflight checks ──────────────────────────────────────────── #
    if ! command -v ngrok &>/dev/null; then
        echo -e "${RED}Error: 'ngrok' not found.${NC}"
        echo -e "${RED}Install: https://ngrok.com/download  or  snap install ngrok${NC}"
        return 1
    fi

    local has_stripe=true
    if ! command -v stripe &>/dev/null; then
        echo -e "${YELLOW}⚠  'stripe' CLI not found — Stripe listener will be skipped.${NC}"
        has_stripe=false
    fi

    # ── Start ngrok ───────────────────────────────────────────────── #
    echo -e "${CYAN}🔗 Starting ngrok tunnel to localhost:8000...${NC}"
    ngrok http 8000 --log=stdout --log-format=json > /tmp/ngrok_webhooks.log 2>&1 &
    local ngrok_pid=$!
    sleep 4

    local public_url
    public_url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
        | grep -oP '"public_url"\s*:\s*"https://[^"]+' \
        | head -1 | sed 's/"public_url"\s*:\s*"//')

    if [ -z "$public_url" ]; then
        echo -e "${RED}✘ Could not detect ngrok public URL.${NC}"
        echo -e "${RED}  Make sure ngrok is authenticated: ngrok config add-authtoken YOUR_TOKEN${NC}"
        kill $ngrok_pid 2>/dev/null
        return 1
    fi

    echo ""
    echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ ngrok tunnel is live!${NC}"
    echo -e "${GREEN}  🌐 Public URL: ${CYAN}${public_url}${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
    echo ""

    # ── Clerk webhook ─────────────────────────────────────────────── #
    local clerk_endpoint="${public_url}/api/webhooks/clerk"
    echo -e "${CYAN}� Clerk Webhook Endpoint:${NC}"
    echo -e "${CYAN}   ${clerk_endpoint}${NC}"
    echo ""
    echo -e "${YELLOW}  👉 Go to Clerk Dashboard → Developers → Webhooks${NC}"
    echo -e "${YELLOW}     1. Click '+ Add Endpoint' (or edit existing)${NC}"
    echo -e "${YELLOW}     2. Paste: ${clerk_endpoint}${NC}"
    echo -e "${YELLOW}     3. Subscribe to: user.created, user.updated, user.deleted${NC}"
    echo -e "${YELLOW}     4. Copy the Signing Secret (whsec_...)${NC}"
    echo ""
    echo -ne "${CYAN}  Paste your Clerk Signing Secret here (Enter to skip): ${NC}"
    read -r clerk_secret
    if [ -n "$clerk_secret" ]; then
        update_env_var "CLERK_WEBHOOK_SECRET" "$clerk_secret"
    else
        echo -e "${YELLOW}  Skipped — update CLERK_WEBHOOK_SECRET in .env manually.${NC}"
    fi

    # ── Stripe webhook ────────────────────────────────────────────── #
    echo ""
    if [ "$has_stripe" = true ]; then
        local stripe_endpoint="${public_url}/api/webhooks/stripe"
        echo -e "${CYAN}� Starting Stripe webhook listener → ${stripe_endpoint}${NC}"

        local stripe_secret
        stripe_secret=$(stripe listen --forward-to "${stripe_endpoint}" --print-secret 2>/dev/null)

        if [ -n "$stripe_secret" ]; then
            echo -e "${GREEN}📋 Stripe Webhook Secret: ${stripe_secret}${NC}"
            update_env_var "STRIPE_WEBHOOK_SECRET" "$stripe_secret"
        fi

        stripe listen --forward-to "${stripe_endpoint}" &
        local stripe_pid=$!
    fi

    echo ""
    echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🎧 All listeners running. Press Ctrl+C to stop.${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"

    # ── Cleanup on exit ───────────────────────────────────────────── #
    trap "echo -e '\n${YELLOW}Shutting down...${NC}'; kill $ngrok_pid ${stripe_pid:-} 2>/dev/null; exit 0" INT TERM
    wait $ngrok_pid
}

# ── Docker Compose ─────────────────────────────────────────────────── #

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
        echo -e "${GREEN}Checking container status...${NC}"
        docker compose -f $COMPOSE_FILE ps --format "table {{.Name}}\t{{.State}}\t{{.Status}}"
        ;;
    webhooks)
        start_webhooks
        ;;
    *)
        show_help
        ;;
esac
