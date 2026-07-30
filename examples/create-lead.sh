#!/usr/bin/env sh

set -eu

: "${API_KEY:?Укажите API_KEY в окружении}"
BASE_URL="${BASE_URL:-http://localhost:8000}"

curl --fail-with-body \
  --request POST "$BASE_URL/api/v1/leads" \
  --header "Content-Type: application/json" \
  --header "X-API-Key: $API_KEY" \
  --data '{
    "name": "Анна",
    "contact": "+79990000000",
    "comment": "Нужна консультация",
    "source": "website"
  }'
