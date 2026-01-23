#!/bin/bash
# test-boot-api.sh - Quick HTTP API testing for boot monitor

BASE_URL="${1:-http://localhost:2370}"

echo "Boot Monitor API Testing"
echo "======================="
echo "Base URL: $BASE_URL"
echo ""

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}1. Get Boot Status${NC}"
echo "curl $BASE_URL/api/boot/status"
curl -s "$BASE_URL/api/boot/status" | jq '.is_complete, .is_boot_failed, .current_phase, (.services | length), (.recent_logs | length)'
echo ""

echo -e "${BLUE}2. Check if Boot Complete${NC}"
echo "curl $BASE_URL/api/boot/status | jq .is_complete"
curl -s "$BASE_URL/api/boot/status" | jq '.is_complete'
echo ""

echo -e "${BLUE}3. List Current Services Status${NC}"
echo "curl $BASE_URL/api/boot/status | jq '.services[] | {name, status}'"
curl -s "$BASE_URL/api/boot/status" | jq '.services[] | {name: .name, status: .status}'
echo ""

echo -e "${BLUE}4. Get Recent Logs (last 20)${NC}"
echo "curl '$BASE_URL/api/boot/logs?limit=20'"
curl -s "$BASE_URL/api/boot/logs?limit=20" | jq '.logs[] | {service, message}'
echo ""

echo -e "${BLUE}5. Get Logs for Specific Service${NC}"
echo "curl '$BASE_URL/api/boot/logs?service=setup-storage'"
curl -s "$BASE_URL/api/boot/logs?service=setup-storage" | jq '.logs[] | {service, message}'
echo ""

echo -e "${BLUE}6. Get Failed Services${NC}"
echo "curl $BASE_URL/api/boot/status | jq '.failed_services'"
curl -s "$BASE_URL/api/boot/status" | jq '.failed_services'
echo ""

echo -e "${GREEN}Testing complete!${NC}"
echo ""
echo "For real-time monitoring, use:"
echo "  wscat -c ws://${BASE_URL#http://}/api/boot/stream"
