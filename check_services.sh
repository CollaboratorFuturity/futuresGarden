#!/bin/bash
# AIflow Service Status Checker
# Quick overview of all AIflow services

echo "=========================================="
echo "AIflow Service Status"
echo "=========================================="
echo ""

# Check if services are enabled
echo "--- Service Status ---"
printf "%-25s %s\n" "Service" "Status"
printf "%-25s %s\n" "-------" "------"
printf "%-25s %s\n" "battery_log.service" "$(systemctl is-active battery_log.service) / $(systemctl is-enabled battery_log.service)"
printf "%-25s %s\n" "config_fetcher.service" "$(systemctl is-active config_fetcher.service) / $(systemctl is-enabled config_fetcher.service)"
echo ""

# Show recent logs
echo "--- Recent Logs (last 5 lines each) ---"
echo ""
echo "[battery_log.service]"
sudo journalctl -u battery_log.service -n 5 --no-pager
echo ""
echo "[config_fetcher.service]"
tail -n 5 /tmp/config_fetcher.log 2>/dev/null || echo "No logs yet"
echo ""

# Environment check
echo "--- Environment Variables ---"
if [ -f /home/orb/AIflow/.service_env ]; then
    echo "✓ .service_env exists"
    grep -E "^[A-Z]" /home/orb/AIflow/.service_env | grep -v "^#" | sed 's/=.*/=***/'
else
    echo "✗ .service_env not found"
fi
echo ""

# Runtime environment check
if [ -f /tmp/aiflow.env ]; then
    echo "✓ /tmp/aiflow.env exists (created by config_fetcher)"
    grep -E "^[A-Z]" /tmp/aiflow.env | grep -v "^#" | sed 's/=.*/=***/'
else
    echo "✗ /tmp/aiflow.env not found (config_fetcher may not have run yet)"
fi
echo ""

echo "=========================================="