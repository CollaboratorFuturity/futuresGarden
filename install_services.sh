#!/bin/bash
# AIflow Service Installation Script
# Sets up systemd services with proper permissions and environment

set -e  # Exit on error

echo "=========================================="
echo "AIflow Service Installation"
echo "=========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# Paths
AIFLOW_DIR="/home/orb/AIflow"          # Python scripts and .service_env
SERVICE_DIR="/home/orb/services"        # Service files location
SERVICE_ENV="$AIFLOW_DIR/.service_env"
SYSTEMD_DIR="/etc/systemd/system"

# Step 1: Verify files exist
echo "Step 1: Verifying files..."
if [ ! -f "$SERVICE_ENV" ]; then
    echo "ERROR: .service_env not found at $SERVICE_ENV"
    echo "Please create it first and configure your DEVICE_ID and API keys"
    exit 1
fi

if [ ! -f "$SERVICE_DIR/battery_log.service" ]; then
    echo "ERROR: battery_log.service not found in $SERVICE_DIR"
    exit 1
fi

if [ ! -f "$SERVICE_DIR/config_fetcher.service" ]; then
    echo "ERROR: config_fetcher.service not found in $SERVICE_DIR"
    exit 1
fi

echo "✓ All required files found"
echo ""

# Step 2: Set proper permissions
echo "Step 2: Setting file permissions..."
chmod 600 "$SERVICE_ENV"  # Sensitive - only root can read
chmod 644 "$SERVICE_DIR"/*.service
chown root:root "$SERVICE_ENV"
chown root:root "$SERVICE_DIR"/*.service

# Make Python scripts executable
chmod +x "$AIFLOW_DIR/battery_log.py"
chmod +x "$AIFLOW_DIR/config_fetcher.py"

echo "✓ Permissions set"
echo ""

# Step 3: Stop existing services if running
echo "Step 3: Stopping existing services (if any)..."
systemctl stop battery_log.service 2>/dev/null || true
systemctl stop config_fetcher.service 2>/dev/null || true
echo "✓ Services stopped"
echo ""

# Step 4: Copy service files to systemd directory
echo "Step 4: Installing service files..."
cp "$SERVICE_DIR/battery_log.service" "$SYSTEMD_DIR/"
cp "$SERVICE_DIR/config_fetcher.service" "$SYSTEMD_DIR/"
echo "✓ Service files installed"
echo ""

# Step 5: Reload systemd daemon
echo "Step 5: Reloading systemd daemon..."
systemctl daemon-reload
echo "✓ Systemd reloaded"
echo ""

# Step 6: Enable services (auto-start on boot)
echo "Step 6: Enabling services for auto-start..."
systemctl enable battery_log.service
systemctl enable config_fetcher.service
echo "✓ Services enabled"
echo ""

# Step 7: Start services
echo "Step 7: Starting services..."
systemctl start battery_log.service
sleep 2  # Let battery_log start first
systemctl start config_fetcher.service
echo "✓ Services started"
echo ""

# Step 8: Check status
echo "Step 8: Service status check..."
echo ""
echo "--- Battery Log Service ---"
systemctl status battery_log.service --no-pager -l | head -n 10
echo ""
echo "--- Config Fetcher Service ---"
systemctl status config_fetcher.service --no-pager -l | head -n 10
echo ""

echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "Service order:"
echo "  1. battery_log.service (started first)"
echo "  2. config_fetcher.service (waits for battery_log)"
echo ""
echo "Useful commands:"
echo "  View logs:     sudo journalctl -u battery_log.service -f"
echo "                 sudo journalctl -u config_fetcher.service -f"
echo "                 tail -f /tmp/config_fetcher.log"
echo ""
echo "  Restart:       sudo systemctl restart battery_log.service"
echo "  Stop:          sudo systemctl stop battery_log.service"
echo "  Status:        sudo systemctl status battery_log.service"
echo ""
echo "  Edit env:      sudo nano /home/orb/AIflow/.service_env"
echo "                 sudo systemctl daemon-reload"
echo "                 sudo systemctl restart battery_log.service"
echo ""