#!/bin/sh
# Stop the servers started by dev-up.sh.
pkill -f "tinystudio serve" 2>/dev/null || true
pkill -f "mlx_lm.server --model" 2>/dev/null || true
echo "stopped"
