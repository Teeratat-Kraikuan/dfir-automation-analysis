#!/bin/sh
set -e

# รอให้ TCP ของ postgres:5432 ตอบก่อน (ไม่พึ่ง Django settings)
MAX_RETRIES=60
RETRY_COUNT=0

echo "Waiting for Postgres (postgres:5432)..."
python - <<'PY'
import socket, time, sys
host, port = "postgres", 5432
for i in range(60):   # 60 รอบ x 2 วินาที ~ 2 นาที
    try:
        with socket.create_connection((host, port), timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(2)
sys.exit(1)
PY

if [ $? -ne 0 ]; then
  echo "Database connection timed out"
  exit 1
fi

echo "Database is ready!"

python manage.py makemigrations
python manage.py migrate
python manage.py collectstatic --no-input

# ปล่อยให้ command จาก docker-compose.yml ทำงาน (python manage.py runserver ...)
exec "$@"
